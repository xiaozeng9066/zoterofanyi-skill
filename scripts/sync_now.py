#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zoterofanyi 云端同步：每次使用前后让本地与 GitHub 收敛到同一版本。

用户 2026-11-14 第四个要求：「连接 GitHub 建个仓库，每次使用前后本地端都与云端交互，
让云端永远是最新的，本地端可以从云端更新数据」。

三个动作（全部只用标准库 + git，零第三方依赖，系统 Python 即可跑）：

  --status              只读对比本地 / origin 的 HEAD、tag、领先落后、工作区状态
  --start               使用前：fetch → 只允许 ff-only 合并 → 报告版本对齐
  --end                 使用后：push main + tags → 用 ls-remote 回读校验远端确实等于本地

安全门禁（硬性，规则 57）：
  * 绝不 force push / reset --hard / commit --amend / tag -f / 自动 merge 冲突；
  * 历史分叉、远端有本机没有的提交时一律 BLOCK，交人工裁决；
  * 拉取前不丢弃任何本地改动（ff-only 做不到就失败，不覆盖）；
  * 推送后必须回读远端 HEAD，不能把「命令没报错」当成「云端已最新」；
  * 无 origin：--start 降级为「离线使用本地版本」（不阻断翻译），--end 默认 BLOCK，
    只有 --allow-offline 才降级（供 iterate_commit.py 在无远端时收尾）；
  * 网络失败：--start 只 WARN（本地版本照用），--end 明确报错并给出重试命令。

用法：
  python scripts/sync_now.py --status
  python scripts/sync_now.py --start
  python scripts/sync_now.py --end
  python scripts/sync_now.py --start --dry-run
  python scripts/sync_now.py --status --route proxy    # 只验证代理这条主路径（规则 61）
  python scripts/sync_now.py --status --route direct   # 诊断用：看「不经代理」到底通不通
  python scripts/sync_now.py --end --allow-offline --json
  python scripts/sync_now.py --end --strict-proxy      # 代理不通就直接 BLOCK，不许降级
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "main"
REMOTE = "origin"
CONFIG = ROOT / "local_config.json"

# v2.11（规则 61 改写，用户 2026-11-14 明确纠正）：
# 「国内环境不可能绕过代理」——GitHub 直连可用只是偶发现象，不能当常规路径。
# 同一台机器相隔十几分钟的两次探测就自相矛盾（一次「代理 502 / 直连 3/3」，
# 一次「代理 200 / 直连 200」），而且本机存在系统级 TUN/透明代理时，
# 「直连」名义上绕过代理、实际仍被中转，结论根本不可当依据。
# 所以：代理是**前置条件**，先代理、代理内重试；直连只留两用——
# ① 诊断（--route direct 看代理挂了没有）；② 代理彻底不通时临时兜底，
# 且必须大声告警、写进报告、标成 degraded，绝不能静默当默认。
DEFAULT_MODE = "proxy-required"     # 可选 auto（旧行为：谁先通走谁），仅非受限网络使用
DEFAULT_FALLBACK = "warn"           # 代理彻底不通时：warn=降级直连并告警 / block=直接阻断
# v2.13：代理端点会漂移（实测一天内 14126 → 4661 → 7890），所以重试单位是「端点」，
# 每个端点 2 次、退避 1.5s；换端点仍是走代理，不是绕过代理（常量见 ENDPOINT_*）。


class SyncError(Exception):
    """需要人工裁决或阻断的情况（不是普通网络故障）。"""


# v2.6 实测（2026-11-14）：本机 HTTPS_PROXY 对 api.github.com 可用，
# 但对 github.com:443 的 CONNECT 隧道偶发 502 / schannel 提前断连，
# 导致 git over HTTPS 临时失败。这类故障通常是瞬时的，值得有限重试；
# 同时给出可执行的诊断，而不是笼统报「离线」。
NET_ERR_RE = re.compile(
    r"CONNECT tunnel|Empty reply|schannel|server closed abruptly|"
    r"Could not connect|timed out|Connection was reset|50[23]\b", re.I
)


def net_hint(text: str) -> str:
    if not NET_ERR_RE.search(text or ""):
        return ""
    pol = net_policy()
    return (
        "\n[诊断] 代理路径失败。代理是本环境唯一标准路径，直连不是备选方案。"
        + (f"当前 HTTPS_PROXY={pol['proxy']}。" if pol["proxy"]
           else "当前未设置 HTTPS_PROXY —— 国内环境几乎必然失败，先配代理再同步。")
        + "\n        ① 查代理进程与端口：sync_now.py --status --route proxy；"
        + "\n        ② 只有要确认「是不是代理把 GitHub 打挂了」才跑 --route direct（诊断用，不作常态）；"
        + "\n        ③ 网络恢复后重跑 --start / --end 补同步：不要重新提交、不要改 .git/config、不要强推。"
    )


def _short_err(proc: subprocess.CompletedProcess) -> str:
    lines = [ln.strip() for ln in out(proc).splitlines() if ln.strip()]
    return (lines[-1] if lines else f"exit {proc.returncode}")[:110]


def git_retry(*args: str, attempts: int | None = None, delay: float | None = None,
              **kw) -> subprocess.CompletedProcess:
    """网络命令：按 route_plan() 走 —— 代理内重试 → （可选）应急降级不经代理。

    只对网络类错误重试；分叉 / 权限错误立即返回交上层裁决。
    应急降级一定会打印 [WARN] 并把本次标记为 degraded，不许静默当成正常路径。
    """
    last: subprocess.CompletedProcess | None = None
    plan = route_plan()
    for idx, (route, endpoint, tries, waits) in enumerate(plan):
        if route == "direct" and idx > 0:
            print("[WARN] 所有代理端点均失败，本次临时改走「不经代理」路径："
                  "国内环境不可依赖，属应急降级；请尽快修好代理（端口/节点/进程）。")
            _DEGRADED[0] = True
        label = f"代理 {_port_of(endpoint)}" if route == "proxy" else "直连"
        if attempts and route == "proxy":
            tries = max(tries, attempts)
        for i in range(tries):
            proc = git(*args, route=route, endpoint=endpoint, **kw)
            last = proc
            if proc.returncode == 0 or not NET_ERR_RE.search(out(proc)):
                _LAST_ROUTE[0] = route
                _LAST_ENDPOINT[0] = endpoint
                if route == "proxy" and idx > 0:
                    print(f"[INFO] 前面的代理端点不通，已换到 {_port_of(endpoint)}（仍是走代理）")
                return proc
            if i < tries - 1:
                wait = (waits[i] if i < len(waits) else (waits[-1] if waits else 1.5))
                if delay:
                    wait = delay
                print(f"[WARN] {label} 第 {i + 1}/{tries} 次失败（{_short_err(proc)}），{wait:g}s 后重试")
                time.sleep(wait)
        if route == "proxy" and idx + 1 < len(plan) and plan[idx + 1][0] == "proxy":
            print(f"[WARN] {label} 不通，换下一个代理端点（不是绕过代理）")
    return last  # type: ignore[return-value]


def load_network() -> dict:
    """读 local_config.json 的 network 段；缺失或损坏时退化为空（走 git 默认）。"""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    net = cfg.get("network")
    return net if isinstance(net, dict) else {}


_ROUTE_OVERRIDE: list[str | None] = [None]
_LAST_ROUTE: list[str | None] = [None]   # 记录本次实际成功的路由，供 --status 显示
_LAST_ENDPOINT: list[str | None] = [None]  # 本次实际成功的代理端点（v2.13）
_DEGRADED: list[bool] = [False]          # 本次是否发生过「绕过代理」的应急降级（规则 61）
_FORCED_FALLBACK: list[str | None] = [None]   # --strict-proxy 的运行时覆盖

# v2.13：本机代理软件端口会漂移（实测一天内 14126 → 4661 → 7890），
# 端口死了 ≠ 代理不可用。换端点仍是走代理，不是绕过代理。
ENDPOINT_ATTEMPTS = 2
ENDPOINT_BACKOFF = (1.5,)


def proxy_endpoints() -> list[str]:
    """代理端点候选，**全部是代理**，绝不含直连。

    顺序：`local_config.network.proxy_candidates`（detect_env.py 实测可用的排前面）
    → 环境变量 HTTPS_PROXY。去重保序。
    """
    net = load_network()
    env = ((net.get("proxy_env") or {}).get("https_proxy")
           or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    ordered: list[str] = []
    for item in (net.get("proxy_candidates") or []):
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str) and url.strip():
            ordered.append(url.strip())
    if env:
        ordered.append(env.strip())
    seen: set[str] = set()
    uniq: list[str] = []
    for url in ordered:
        if url not in seen:
            seen.add(url)
            uniq.append(url)
    return uniq


def _port_of(endpoint: str | None) -> str:
    """把代理 URL 压成 host:port 便于日志阅读（不含任何凭据）。"""
    if not endpoint:
        return "?"
    return re.sub(r"^[a-z]+://", "", endpoint).rstrip("/")


def net_policy() -> dict:
    """本机网络政策：mode / fallback / proxies / force（全部来自 local_config.json）。"""
    net = load_network()
    mode = net.get("mode") if net.get("mode") in ("proxy-required", "auto") else DEFAULT_MODE
    fallback = (net.get("fallback") if net.get("fallback") in ("warn", "block") else DEFAULT_FALLBACK)
    if _FORCED_FALLBACK[0]:
        fallback = _FORCED_FALLBACK[0]
    proxies = proxy_endpoints()
    force = net.get("force") if net.get("force") in ("direct", "proxy") else None
    return {"mode": mode, "fallback": fallback, "proxies": proxies,
            "proxy": proxies[0] if proxies else None, "force": force}


def route_args(route: str = "auto", endpoint: str | None = None) -> list[str]:
    """把路由选择变成 git 的 -c 参数，插在子命令之前（显式覆盖 env 与全局配置）。"""
    if route == "direct":
        return ["-c", "http.proxy=", "-c", "https.proxy="]
    if route == "proxy":
        proxy = endpoint or net_policy()["proxy"]
        if proxy:
            return ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    return []


def route_plan() -> list[tuple[str, str | None, int, tuple]]:
    """执行计划：[(路由, 代理端点, 尝试次数, 退避秒数)]。

    proxy-required（默认）：逐个代理端点试（每端点 2 次，退避 1.5s）→ 全部失败且
    `fallback=warn` 才应急降级一次直连；`fallback=block` / `--strict-proxy` 时没有第二段。
    **换代理端口仍然是走代理**，只有最后那段 `direct` 才算绕过（规则 61）。
    auto 模式才恢复 v2.10 的「谁探测通走谁」。
    """
    pol = net_policy()
    proxies = pol["proxies"]
    forced = _ROUTE_OVERRIDE[0] or pol["force"]

    def proxy_segment() -> list[tuple[str, str | None, int, tuple]]:
        return [("proxy", url, ENDPOINT_ATTEMPTS, ENDPOINT_BACKOFF) for url in proxies]

    if forced == "direct":
        return [("direct", None, 1, ())]
    if forced == "proxy":
        return proxy_segment() or [("direct", None, 1, ())]
    if pol["mode"] == "auto" and load_network().get("chosen") == "direct":
        return [("direct", None, 1, ())] + proxy_segment()
    plan = proxy_segment()
    if not plan:
        return [("direct", None, 1, ())]
    if pol["fallback"] != "block":
        plan.append(("direct", None, 1, ()))
    return plan


def route_candidates() -> list[str]:
    """兼容旧调用：只要路由顺序。"""
    return [route for route, _, _, _ in route_plan()]


def route_desc() -> str:
    pol = net_policy()
    used = _LAST_ROUTE[0]
    if used == "proxy":
        ep = _LAST_ENDPOINT[0] or pol["proxy"]
        extra = f"（候选 {len(pol['proxies'])} 个端点，每端点重试 {ENDPOINT_ATTEMPTS} 次）"
        return f"走代理 {_port_of(ep)}{extra}"
    if used == "direct":
        if pol["mode"] == "proxy-required":
            return ("不经代理（⚠ 应急降级：国内环境不可依赖，本次不是正常路径；请修代理）"
                    + ("［诊断模式］" if _ROUTE_OVERRIDE[0] == "direct" else ""))
        return "不经代理（auto 模式按探测结果选择）"
    tail = ("；彻底不通时应急降级并告警" if pol["fallback"] != "block" else "；不通即 BLOCK")
    eps = "、".join(_port_of(u) for u in pol["proxies"]) or "（未配置代理）"
    return f"代理优先 {eps}，每端点重试 {ENDPOINT_ATTEMPTS} 次{tail}"


def git(*args: str, check: bool = True, timeout: int = 90,
        route: str = "auto", endpoint: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *route_args(route, endpoint), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise SyncError(
            f"命令失败：git {' '.join(args)}\n{(proc.stdout + proc.stderr).strip()[-900:]}"
        )
    return proc


def out(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout + proc.stderr).strip()


def safe_url(url: str) -> str:
    """打印时抹掉 URL 里可能带的 token，绝不把凭据写进日志。"""
    return re.sub(r"https://[^/@]+@", "https://***@", url)


def has_remote() -> bool:
    return REMOTE in git("remote", check=False).stdout.split()


def remote_url() -> str:
    p = git("remote", "get-url", REMOTE, check=False)
    return safe_url(p.stdout.strip()) if p.returncode == 0 else "(未配置)"


def ensure_repo() -> None:
    if not (ROOT / ".git").exists():
        raise SyncError(
            f"{ROOT} 不是 git 仓库。先用 bootstrap.py --remote 从 GitHub clone，"
            "或 git init -b main 建本地仓库。"
        )


def remote_has_branch() -> bool:
    return git("rev-parse", "--verify", f"{REMOTE}/{BRANCH}", check=False).returncode == 0


def counts() -> tuple[int, int]:
    """(落后远端几个提交, 领先远端几个提交)；远端分支不存在时返回 (0, 0)。"""
    if not remote_has_branch():
        return 0, 0
    behind = git("rev-list", "--count", f"HEAD..{REMOTE}/{BRANCH}", check=False).stdout.strip()
    ahead = git("rev-list", "--count", f"{REMOTE}/{BRANCH}..HEAD", check=False).stdout.strip()
    return int(behind or 0), int(ahead or 0)


def snapshot(fetched: bool = False) -> dict:
    info = {
        "repo": ROOT.as_posix(),
        "branch": git("branch", "--show-current", check=False).stdout.strip(),
        "head": git("rev-parse", "--short", "HEAD", check=False).stdout.strip(),
        "tag": git("describe", "--tags", "--abbrev=0", check=False).stdout.strip() or "(无 tag)",
        "origin": remote_url(),
        "fetched": fetched,
        "dirty": bool(git("status", "--porcelain", check=False).stdout.strip()),
    }
    info["behind"], info["ahead"] = counts()
    info["remote_branch"] = remote_has_branch()
    if not has_remote():
        info["state"] = "no-remote"
    elif not info["remote_branch"]:
        info["state"] = "no-remote-branch"
    elif info["behind"] and info["ahead"]:
        info["state"] = "diverged"
    elif info["behind"]:
        info["state"] = "behind"
    elif info["ahead"]:
        info["state"] = "ahead"
    else:
        info["state"] = "aligned"
    return info


def do_fetch(dry_run: bool) -> bool:
    """返回是否成功连上远端；--start 的离线只是 WARN，不阻断本地使用。"""
    if dry_run:
        print(f"[dry-run] 将执行 git fetch {REMOTE} --prune --tags（本次不联网、不写文件）")
        return False
    p = git_retry("fetch", REMOTE, "--prune", "--tags", check=False, timeout=90)
    if p.returncode != 0:
        print(f"[WARN] 连不上远端，本次使用本地版本：{out(p).splitlines()[-1] if out(p) else '网络失败'}"
              + net_hint(out(p)))
        return False
    return True


def remote_head_sha() -> str | None:
    p = git_retry("ls-remote", REMOTE, f"refs/heads/{BRANCH}", check=False, timeout=90,
                  attempts=2, delay=1.5)
    if p.returncode != 0:
        return None
    line = p.stdout.strip().splitlines()
    return line[0].split("\t")[0] if line else None


def cmd_status(dry_run: bool = False) -> int:
    ensure_repo()
    if not has_remote():
        info = snapshot(False)
        info["state"] = "no-remote"
        info["hint"] = "先跑 connect_github.py 或 bootstrap.py --remote 连接云端。"
        print_report(info, dry_run)
        return 0
    ok = do_fetch(dry_run)
    info = snapshot(ok)
    info["remote_head"] = (remote_head_sha() or "(不可读)")[:12] if ok else "(离线)"
    print_report(info, dry_run)
    return 0


def print_report(info: dict, dry_run: bool) -> None:
    if "json" in info:
        return
    label = {
        "aligned": "本地与云端一致",
        "behind": "本地落后（需要拉取）",
        "ahead": "本地领先（需要推送）",
        "diverged": "历史分叉（必须人工裁决）",
        "no-remote": "尚未连接云端（本地版本可照常使用）",
        "no-remote-branch": "云端还没有 main（空仓库，待首次推送）",
    }.get(info.get("state", ""), info.get("state", ""))
    print("=== zoterofanyi 同步状态 ===")
    print(f"版本 tag：{info['tag']}    本地 HEAD：{info['head']}    分支：{info['branch']}")
    print(f"同步源：{info['origin']}")
    print(f"网络路由：{route_desc()}")
    if _DEGRADED[0]:
        print("[WARN] 本次同步走了「不经代理」的应急路径：功能上成功，但它不是本环境的正常路径，"
              "请检查代理进程 / 端口后重跑一次确认。")
    if "remote_head" in info:
        print(f"远端 HEAD：{info['remote_head']}")
    print(f"状态：{label}（落后 {info['behind']} / 领先 {info['ahead']}）")
    print(f"工作区：{'有未提交改动' if info['dirty'] else '干净'}"
          f"    远端读取：{'成功' if info['fetched'] else ('预演' if dry_run else '失败/离线')}")
    if info.get("hint"):
        print("提示：" + info["hint"])


def cmd_start(dry_run: bool = False) -> int:
    ensure_repo()
    if not has_remote():
        print("[WARN] 尚未配置 origin —— 本次使用本地版本，云端不会更新。")
        print("       连接 GitHub：python scripts/connect_github.py --user <用户名> --repo <仓库名>")
        return 0
    if not do_fetch(dry_run):
        print("[WARN] 离线开工：请确认当前 tag 与云端是否一致；恢复网络后再跑一次 --start。")
        return 0
    info = snapshot(True)
    if info["state"] == "no-remote-branch":
        print("[WARN] 云端是空仓库（还没有 main 分支）；本地版本照常可用，"
              "收尾 sync_now.py --end 会做首次推送。")
        return 0
    if info["state"] == "diverged":
        raise SyncError(
            "本地与云端历史分叉（两端各有对方没有的提交）。禁止强推/自动合并。\n"
            "人工处理：git -C \"%s\" fetch origin && git -C \"%s\" merge origin/main\n"
            "确认无冲突后再提交，并跑 sync_now.py --end。" % (ROOT, ROOT)
        )
    if info["behind"]:
        if dry_run:
            print(f"[dry-run] 将执行 git merge --ff-only {REMOTE}/{BRANCH}（快进 {info['behind']} 个提交）")
        else:
            if info["dirty"]:
                print("[WARN] 有未提交改动；若与待拉取文件冲突，git 会自行拒绝，不会覆盖。")
            merged = git("merge", "--ff-only", f"{REMOTE}/{BRANCH}", check=False)
            if merged.returncode != 0:
                raise SyncError(
                    "ff-only 合并失败（本地改动冲突或历史异常），本地文件未被覆盖。\n"
                    + out(merged)[-700:] + "\n人工裁决后再继续，禁止 reset --hard。"
                )
            print(f"已快进 {info['behind']} 个提交 → {git('describe', '--tags', '--always', check=False).stdout.strip()}")
    if info["ahead"]:
        print(f"[WARN] 本地有 {info['ahead']} 个提交尚未推送；收尾时 iterate_commit.py 会自动推送，"
              "也可现在跑 sync_now.py --end。")
    final = snapshot(True)
    print(f"开工检查完成：{final['tag']} · {final['head']} · "
          f"{'本地与云端一致' if final['state'] == 'aligned' else final['state']}")
    return 0


def cmd_end(dry_run: bool = False, allow_offline: bool = False) -> int:
    ensure_repo()
    if not has_remote():
        if allow_offline:
            print("[WARN] 尚未配置 origin：本次改动只留在本地，云端不是最新。")
            print("       连接后跑 python scripts/sync_now.py --end 即可补推全部历史与 tag。")
            return 0
        raise SyncError("尚未配置 origin，无法让云端保持最新。先跑 connect_github.py / bootstrap.py --remote。")
    if dry_run:
        info = snapshot(False)
        print(f"[dry-run] 将 fetch + push {REMOTE} {BRANCH} --follow-tags，并回读远端 HEAD 校验")
        print(f"          当前本地领先 {info['ahead']} / 落后 {info['behind']}")
        return 0
    if not do_fetch(False):
        raise SyncError("连不上远端，本地提交与 tag 完好但云端未更新。恢复网络后重跑 sync_now.py --end。")
    info = snapshot(True)
    if info["dirty"]:
        raise SyncError(
            "有未提交改动，无法保证「云端 == 本地」。\n"
            "先用 iterate_commit.py -v <版本> -m \"<说明>\" 提交并打 tag，再跑 --end。"
        )
    if info["state"] == "diverged" or info["behind"]:
        raise SyncError(
            "远端有本机没有的提交（落后 %d，分叉=%s）。先跑 sync_now.py --start 合并，"
            "人工裁决后再推送；禁止强推。" % (info["behind"], info["state"] == "diverged")
        )
    need_push = (not info["remote_branch"]) or info["ahead"] > 0
    if not need_push:
        # 即便没有新提交，也回读一次，确认 tag/HEAD 确实对齐
        sha = remote_head_sha()
        local = git("rev-parse", "HEAD").stdout.strip()
        if sha and sha != local:
            raise SyncError("远端 HEAD 与本地不一致但计数显示无差异，请人工检查；未执行强推。")
        print(f"云端已是最新：{info['tag']} · {info['head']}（无需推送）")
        return 0
    pushed = git_retry("push", "-u", REMOTE, BRANCH, "--follow-tags", check=False, timeout=180)
    if pushed.returncode != 0:
        raise SyncError(
            "push 失败（本地提交与 tag 完好，云端未更新）：\n" + out(pushed)[-900:]
            + net_hint(out(pushed)) +
            "\n若是认证问题：`git credential-manager github login --device --no-ui` 登录一次；"
            "然后重跑 sync_now.py --end（不需要再提交一次）。"
        )
    sha = remote_head_sha()
    local = git("rev-parse", "HEAD").stdout.strip()
    if sha is None:
        print("[WARN] push 已返回成功，但无法回读远端 HEAD；请稍后跑 --status 复核。")
    elif sha != local:
        raise SyncError(f"push 后远端 HEAD({sha[:12]}) != 本地 HEAD({local[:12]})，请人工复核，不要强推。")
    else:
        print(f"云端已更新并与本地一致：{info['tag']} · {local[:12]}")
    if _DEGRADED[0]:
        print("[WARN] 本次推送未经代理（应急降级），内容是对的但路径不合规："
              "请确认代理恢复后重跑 `sync_now.py --status --route proxy` 复核。")
    print("其他电脑开工前：python scripts/sync_now.py --start")
    return 0


def warn_stale_bypass() -> None:
    """检测「GitHub 不走代理」的全局 git 残留项（v2.10 曾写入 ~/.gitconfig）。

    这类 `http.https://github.com.proxy = ""` 会**静默覆盖** `-c http.proxy=`，
    让 --route proxy 名义走代理、实际直连 —— v2.11 排查时正是被它骗过一次。
    只告警、不自动改用户全局配置（规则 61：禁止擅自改全局代理设置）。
    """
    try:
        proc = subprocess.run(
            ["git", "config", "--global", "--get-regexp", r"^http\.https://.*\.proxy$"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0 or not proc.stdout.strip():
        return
    empties = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 1 or not parts[1].strip():
            empties.append(parts[0])
    if not empties:
        return
    print("[WARN] 全局 git 配置里有「GitHub 不走代理」的残留项（会静默覆盖本项目选路）：")
    for key in empties:
        print(f"        {key}  = (空)")
    print("        清理：git config --global --unset-all '<对应 key>'；"
          "随后用 --status --route proxy 复核（规则 61）。")


def main() -> int:
    ap = argparse.ArgumentParser(description="zoterofanyi 云端同步：开工拉 / 收尾推 / 状态对比")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true", help="使用前：fetch + ff-only 拉取")
    g.add_argument("--end", action="store_true", help="使用后：push main + tags 并回读校验")
    g.add_argument("--status", action="store_true", help="只读对比本地与云端")
    ap.add_argument("--dry-run", action="store_true", help="打印将执行的动作，不联网、不写仓库")
    ap.add_argument("--allow-offline", action="store_true", help="--end 在无 origin / 离线时降级为 WARN（供收尾脚本用）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出状态（供脚本消费）")
    ap.add_argument("--route", choices=["auto", "direct", "proxy"],
                    help="临时指定网络路由（默认代理优先；direct 仅作诊断，规则 61）")
    ap.add_argument("--strict-proxy", action="store_true",
                    help="本次严格要求走代理：代理不通直接 BLOCK，不做应急降级")
    args = ap.parse_args()
    if args.route:
        _ROUTE_OVERRIDE[0] = args.route
        if args.route == "direct":
            print("[WARN] --route direct 是诊断路径：它绕开代理，国内环境不可依赖；"
                  "成功后请务必再跑一次 --route proxy 确认代理本身是好的。")
    if args.strict_proxy:
        net = load_network()
        net["fallback"] = "block"
        _FORCED_FALLBACK[0] = "block"
    warn_stale_bypass()
    try:
        if args.status:
            if args.json:
                ensure_repo()
                ok = do_fetch(args.dry_run) if has_remote() else False
                info = snapshot(ok)
                info["json"] = True
                info["route"] = _LAST_ROUTE[0]
                info["endpoint"] = _LAST_ENDPOINT[0]   # v2.13：实际用的代理端点
                info["degraded"] = _DEGRADED[0]   # 本次是否绕过代理（v2.11 声称、v2.12 补齐）
                info["remote_head"] = (remote_head_sha() or "")[:12] if ok else None
                print(json.dumps(info, ensure_ascii=False, indent=2))
                return 0
            return cmd_status(args.dry_run)
        rc = cmd_start(args.dry_run) if args.start else cmd_end(args.dry_run, args.allow_offline)
        return rc
    except (SyncError, subprocess.TimeoutExpired) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"\n[BLOCK] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
