#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测本机环境并生成 local_config.json（只用标准库，新机系统 Python 即可跑）。

设计要点（规则 56）：
  * 与机器无关的资产进 git；本机路径只进 local_config.json，且被 .gitignore 排除；
  * 增量更新：sync.remote_url / sync.cloud_bare / overrides / mode 等人工字段绝不覆盖；
  * 探测不到就写 null + warnings，并给出补齐建议，不假装环境已就绪。

  python scripts/detect_env.py            # 探测并写入
  python scripts/detect_env.py --no-write # 只看结果
  python scripts/detect_env.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "local_config.json"
TEMPLATE = ROOT / "templates" / "local_config.template.json"
VENV_PY = "Scripts/python.exe" if os.name == "nt" else "bin/python"

# v2.11（规则 61 改写，用户 2026-11-14 纠正）：「国内环境不可能绕过代理」。
# 直连 GitHub 的可用性只是偶发：同一台机器相隔十几分钟的两次探测结论相反
# （一次「代理 502 / 直连 3/3」，一次「代理 200 / 直连 200」）；若本机还装了
# 系统级 TUN / 透明代理，「直连」名义上绕开代理、实际仍被中转，结论根本不可当依据。
# 所以探测只回答两件事：① 代理通不通（决定要不要先修网络）；② 不经代理通不通（纯诊断）。
# 选路永远是「代理优先」，直连只作诊断与应急降级，绝不写进白名单常态化。
DEFAULT_MODE = "proxy-required"     # auto 仅用于无代理的境外网络
DEFAULT_FALLBACK = "warn"           # warn=代理不通时应急降级并告警；block=直接阻断
PROBE_URL_FALLBACK = "https://github.com/git/git.git"
# v2.13：代理软件端口会漂移（实测一天内 14126 → 4661 → 7890），端口死了 ≠ 代理不可用。
# 探测时除环境变量外还扫这些本机常见端口，**全部是代理端口**（换端点仍是走代理）。
DEFAULT_PROXY_PORTS = (7890, 10809, 1080, 4661, 14126, 8889, 2080, 33210)


def _port_label(url: str | None) -> str:
    """host:port 形式，便于日志阅读。"""
    if not url:
        return "?"
    return url.split("://")[-1].rstrip("/")


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_proxy_candidates(primary: str | None, primary_ok: bool | None,
                           primary_ms: float | None, url: str) -> list[dict]:
    """找出本机真正能用的代理端点，写进 proxy_candidates（按可用 + 耗时排序）。

    主端点已通就只记它（不做无谓扫描）；不通才扫常见端口——
    这是「换一个代理端口」，不是「绕过代理」，符合规则 61。
    """
    found: list[dict] = []
    seen: set[str] = set()

    def add(u: str, ok: bool, ms: float | None) -> None:
        if u in seen:
            return
        seen.add(u)
        found.append({"url": u, "ok": ok, "ms": ms})

    if primary:
        add(primary, bool(primary_ok), primary_ms)
    if primary_ok:
        return found
    for port in DEFAULT_PROXY_PORTS:
        endpoint = f"http://127.0.0.1:{port}"
        if endpoint in seen or not _port_open("127.0.0.1", port):
            continue
        ok, ms = _ls_remote(url, direct=False, proxy=endpoint)
        add(endpoint, ok, ms)
    found.sort(key=lambda c: (0 if c["ok"] else 1, c["ms"] if c["ms"] is not None else 999))
    return found


def _env_proxy() -> str | None:
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))


def _ls_remote(url: str, *, direct: bool, proxy: str | None, timeout: int = 15):
    """(是否成功, 耗时秒)。用显式 -c 覆盖环境变量与全局 git 配置，保证两条路互不污染。"""
    args = ["git", "ls-remote", url, "HEAD"]
    if direct:
        args[1:1] = ["-c", "http.proxy=", "-c", "https.proxy="]
    elif proxy:
        args[1:1] = ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    started = time.time()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        return proc.returncode == 0, round(time.time() - started, 2)
    except (OSError, subprocess.TimeoutExpired):
        return False, round(time.time() - started, 2)


def probe_network(remote_url: str | None, old: dict) -> dict:
    """探测代理与「不经代理」两条路，产出本机网络政策（规则 61）。

    代理是**前置条件**：只要有 HTTPS_PROXY 就固定以代理为准，探测失败只记 WARN
    （502 多为瞬时，重试通常能过），不会因为「直连也通」就改走直连。
    直连结果只作诊断字段 `github_direct_ok` 保存。
    """
    proxy = _env_proxy()
    url = remote_url or PROBE_URL_FALLBACK
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    prev = old.get("network", {}) if isinstance(old.get("network"), dict) else {}
    notes: list[str] = []

    proxy_ok, proxy_ms = (None, None)
    if proxy:
        proxy_ok, proxy_ms = _ls_remote(url, direct=False, proxy=proxy)
    # 诊断：不经代理时通不通（不作为选路依据）
    direct_ok, direct_ms = _ls_remote(url, direct=True, proxy=proxy)
    candidates = probe_proxy_candidates(proxy, proxy_ok, proxy_ms, url)
    best = next((c for c in candidates if c["ok"]), None)

    mode = prev.get("mode") if prev.get("mode") in ("proxy-required", "auto") else DEFAULT_MODE
    fallback = prev.get("fallback") if prev.get("fallback") in ("warn", "block") else DEFAULT_FALLBACK
    force = prev.get("force") if prev.get("force") in ("direct", "proxy", None) else None

    if mode == "proxy-required":
        chosen = "proxy" if (proxy or best) else "direct"
    else:
        chosen = "proxy" if ((proxy and proxy_ok) or best) else ("direct" if direct_ok else "auto")

    if not proxy and not best:
        notes.append("未探测到可用代理端点 —— 国内环境几乎必然同步失败；先启动代理，再跑 "
                     "sync_now.py --status 验证。")
    elif not proxy:
        notes.append("环境变量里没有 HTTPS_PROXY，但本机探测到可用代理端点；"
                     "建议启动代理软件时打开「设置系统代理 / 环境变量」。")
    elif not proxy_ok and best:
        notes.append("环境变量里的代理端点 %s 不通，已自动改用 %s（仍是走代理，不是绕代理）。"
                     % (_port_label(proxy), _port_label(best["url"])))
    elif not proxy_ok:
        notes.append("代理本次探测全部失败（多为瞬时 502 / 端口已变）：仍以代理为准，重试通常可通；"
                     "还不行就查代理进程与端口。")
    if direct_ok and proxy:
        notes.append("诊断：不经代理也能通（%.2fs）。这可能只是系统级 TUN/透明代理在中转，"
                     "不代表可以长期绕过代理 —— 不要据此改走直连。" % (direct_ms or 0))
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "fallback": fallback,
        "proxy_env": {
            "https_proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            "no_proxy": no_proxy,
        },
        # v2.13：可用代理端点（sync_now.py 逐个尝试；全是代理端口，不含直连）
        "proxy_candidates": candidates,
        "github_proxy_ok": proxy_ok,
        "github_proxy_ms": proxy_ms,
        "github_direct_ok": direct_ok,
        "github_direct_ms": direct_ms,
        "github_direct_role": "diagnostic",
        "chosen": chosen,
        # v2.11 起恒为空：不再把 GitHub 排除在代理之外（用户明确：国内不可能绕代理）
        "bypass_hosts": [] if mode == "proxy-required" else prev.get("bypass_hosts", []),
        "force": force,
        "notes": notes,
    }



def px(value) -> str | None:
    if not value:
        return None
    return Path(str(value)).as_posix()


def first_existing(candidates) -> str | None:
    for item in candidates:
        if item and Path(str(item)).exists():
            return px(item)
    return None


def probe_python(candidates):
    """返回 (可用解释器, 是否已装 PyMuPDF+zstandard, 备注)。"""
    fallback = None
    for item in candidates:
        if not item or not Path(str(item)).is_file():
            continue
        exe = str(item)
        if fallback is None:
            fallback = px(exe)
        try:
            proc = subprocess.run(
                [exe, "-c", "import fitz, zstandard"],
                capture_output=True, timeout=12,
            )
            if proc.returncode == 0:
                return px(exe), True, None
        except (OSError, subprocess.TimeoutExpired):
            continue
    note = "找到 Python 解释器，但缺 PyMuPDF(fitz)/zstandard；用该 venv 执行 pip install PyMuPDF zstandard，或设 ZF_PYTHON 指向已配好的解释器。"
    return fallback, False, note


def git_remote_url() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="探测本机环境，生成 zoterofanyi 本机配置")
    ap.add_argument("--no-write", action="store_true", help="只探测，不写 local_config.json")
    ap.add_argument("--json", action="store_true", help="输出完整配置 JSON")
    args = ap.parse_args()

    home = Path.home()
    old = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
    prev_sync = old.get("sync", {}) if isinstance(old.get("sync"), dict) else {}
    warnings: list[str] = []

    py_candidates = [
        os.environ.get("ZF_PYTHON"), os.environ.get("ZB_PY"),
        home / ".dsh/runtimes/zb-translate/venv" / VENV_PY,
        home / ".workbuddy/runtimes/zb-translate/venv" / VENV_PY,
        ROOT / ".venv" / VENV_PY,
        shutil.which("python3"), shutil.which("python"), sys.executable,
    ]
    python, has_fitz, py_note = probe_python(py_candidates)
    if py_note:
        warnings.append(py_note)
    if not python:
        warnings.append("没有可用的 Python 解释器；先安装 Python 3.10+ 再重跑。")

    mineru = first_existing([
        os.environ.get("ZF_MINERU_PY"), os.environ.get("MINERU_PY"),
        home / ".dsh/runtimes/mineru/venv310" / VENV_PY,
        home / ".workbuddy/runtimes/mineru/venv310" / VENV_PY,
    ])
    if not mineru:
        warnings.append("未找到 MinerU venv（结构化抽取需要）；可设 ZF_MINERU_PY，或按 PORTABILITY.md 新建一个 venv 安装 mineru。")

    browser = first_existing([
        os.environ.get("ZF_BROWSER"),
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("msedge"), shutil.which("google-chrome"), shutil.which("chromium"),
    ])
    if not browser:
        warnings.append("未找到 Edge/Chrome（渲染 PDF 需要）；设 ZF_BROWSER 指向浏览器可执行文件。")

    zotero_mcp = first_existing([
        os.environ.get("ZF_ZOTERO_MCP"),
        home / ".dsh/mcp/zotero_mcp.py",
        home / ".workbuddy/mcp/zotero_mcp.py",
    ])
    if not zotero_mcp:
        warnings.append("未找到 Zotero MCP CLI；挂回 Zotero 前需提供 zotero_mcp.py 或设 ZF_ZOTERO_MCP。")

    workspace = first_existing([
        os.environ.get("ZF_WORKSPACE"),
        home / "Desktop/zoterofanyi", home / "zoterofanyi",
        home / "Documents/zoterofanyi",
    ]) or px(home / "zoterofanyi")
    runs_dir = px(Path(workspace) / "zotero-translate")
    if not Path(runs_dir).exists():
        warnings.append(f"运行产物目录 {runs_dir} 尚不存在；首次翻译时按 SKILL.md 流水线第 0 步创建即可。")

    cloud_dirs = [
        px(home / name) for name in (
            "OneDrive", "OneDrive - Personal", "Dropbox", "Nutstore", "坚果云",
            "WeDrive", "微云同步助手", "Google Drive", "iCloudDrive", "Seafile", "Syncthing",
        ) if (home / name).is_dir()
    ]

    remote = prev_sync.get("remote_url") or git_remote_url()
    network = probe_network(remote, old)
    warnings.extend(network.get("notes") or [])
    cfg = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "home": px(home),
        "skill_root": px(ROOT),
        "scripts_dir": px(ROOT / "scripts"),
        "python": python,
        "python_has_fitz": has_fitz,
        "mineru_python": mineru,
        "browser": browser,
        "zotero_mcp": zotero_mcp,
        "workspace": workspace,
        "runs_dir": runs_dir,
        "cloud_dirs": cloud_dirs,
        "sync": {
            "mode": prev_sync.get("mode", "git-remote"),
            "remote_name": prev_sync.get("remote_name", "origin"),
            "remote_url": remote,
            "cloud_bare": prev_sync.get("cloud_bare"),
            "auto_pull": prev_sync.get("auto_pull", True),
        },
        "network": network,
        "overrides": old.get("overrides", {}) if isinstance(old.get("overrides"), dict) else {},
        "warnings": warnings,
    }

    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    else:
        print(f"机器：{cfg['machine']}  OS：{cfg['os']}  skill：{cfg['skill_root']}")
        for key in ("python", "mineru_python", "browser", "zotero_mcp", "workspace", "runs_dir"):
            print(f"  {'OK  ' if cfg.get(key) else 'MISS'} {key:<15} {cfg.get(key) or '(未配置)'}")
        print(f"  fitz: {'yes' if has_fitz else 'NO'}   云同步目录: {', '.join(cloud_dirs) or '无'}")
        print(f"  git remote: {remote or '(未配置)'}")
        pstat = ("未设置" if network["github_proxy_ok"] is None
                 else ("OK" if network["github_proxy_ok"] else "FAIL（重试后会再试）"))
        pms = "" if network["github_proxy_ms"] is None else f" ({network['github_proxy_ms']}s)"
        dstat = "OK" if network["github_direct_ok"] else "FAIL"
        ok_eps = [_port_label(c["url"]) for c in (network.get("proxy_candidates") or []) if c["ok"]]
        print(f"  网络: 代理 {pstat}{pms} → 采用 走代理（{network['mode']}，每端点重试 2 次）")
        print(f"        可用代理端点: {', '.join(ok_eps) if ok_eps else '无（先启动代理）'}")
        print(f"        诊断（不作选路依据）: 不经代理 {dstat} ({network['github_direct_ms']}s)")
        if network["bypass_hosts"]:
            print(f"        白名单: {', '.join(network['bypass_hosts'])}")
        else:
            print("        白名单: 无（GitHub 不排除在代理之外；国内环境不能靠绕代理）")
        if warnings:
            print("\n待处理：")
            for w in warnings:
                print("  - " + w)
        else:
            print("\n本机组件齐全。")

    if not args.no_write:
        with open(CONFIG, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if not args.json:
            print(f"\n已写入 {CONFIG}（该文件被 .gitignore 排除，不会同步到其他电脑）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
