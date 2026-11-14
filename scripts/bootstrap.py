#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zoterofanyi 跨机引导：同步 / 本地化 / 离线分发（只用标准库 + git）。

四件事，都是显式触发，绝不自动上传：
  --remote URL            新机从远端 clone；本机则是设置 origin 并 ff-only 拉取
  --init-sync CLOUD_DIR   在云同步盘（OneDrive / 坚果云 / Dropbox…）建 bare 仓库，
                          设为 origin 并把当前 main + tags 推上去
  --sync                  拉取远端更新（有未提交改动则 BLOCK，先提交再同步）
  --bundle OUT.zip        导出当前版本为离线 zip，供 WorkBuddy 资料库 / 网盘手动分发

v2.5 起：日常「开工拉 / 收尾推」统一交给 sync_now.py（带分叉门禁与远端回读校验），
`--sync` 只是它的兼容入口；连接 GitHub 用 connect_github.py。
无论哪种，最后都会跑一遍本地化：detect_env.py → paths.py --check → 黄金集。
详细方案与风险见 PORTABILITY.md、WORKBUDDY-ADAPTER.md 第 8 节。

  python scripts/bootstrap.py                       # 本机引导 / 环境自检
  python scripts/bootstrap.py --remote <url>       # 换电脑第一次
  python scripts/bootstrap.py --sync               # 兼容入口 → sync_now.py --start
  python scripts/bootstrap.py --init-sync "%USERPROFILE%"
  python scripts/bootstrap.py --bundle /tmp/zoterofanyi.zip
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "local_config.json"
DETECT = ROOT / "scripts" / "detect_env.py"
PATHS = ROOT / "scripts" / "paths.py"
REGRESSION = ROOT / "scripts" / "regression_suite.py"
SYNC_NOW = ROOT / "scripts" / "sync_now.py"
FIXTURES = ROOT / "fixtures"


class BootstrapError(Exception):
    pass


def run(cmd, *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise BootstrapError(
            f"命令失败：{' '.join(str(c) for c in cmd)}\n{(proc.stdout + proc.stderr).strip()[-1000:]}"
        )
    return proc


def git(*args, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(ROOT), *args], check=check)


def is_repo() -> bool:
    return (ROOT / ".git").exists()


def describe() -> str:
    if not is_repo():
        return "no-git (bundle 解压或尚未初始化)"
    return git("describe", "--tags", "--always", check=False).stdout.strip() or "unknown"


def set_remote(url: str) -> None:
    existing = git("remote", check=False).stdout.split()
    if "origin" in existing:
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)


def update_sync_config(mode: str, remote_url: str, cloud_bare: str | None = None) -> None:
    """把同步通道写回本机配置（不覆盖其它探测结果）。"""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
    sync = cfg.setdefault("sync", {})
    sync["mode"] = mode
    sync["remote_name"] = "origin"
    sync["remote_url"] = remote_url
    if cloud_bare:
        sync["cloud_bare"] = cloud_bare
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def cmd_remote(url: str) -> str:
    target = Path.home() / ".workbuddy/skills/zoterofanyi"
    if not is_repo():
        if target.exists() and any(target.iterdir()):
            raise BootstrapError(
                f"目标目录已存在且非空：{target}。\n"
                "若这是 --bundle 解压出来的目录，请换到临时目录重跑 --remote；\n"
                "若已是完整 skill，先 `git init -b main` 建本地仓库再用 --sync，或先备份后重新 clone。"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", url, str(target)], timeout=300)
        return f"已 clone 到 {target}；请在该目录内重跑 `python scripts/bootstrap.py`。"
    set_remote(url)
    update_sync_config("git-remote", url)
    note = f"已把 origin 设为 {url}。"
    status = git("status", "--porcelain").stdout.strip()
    if status:
        return note + " 检测到本地未提交改动，未自动 pull；先提交再跑 --sync。"
    git("fetch", "origin", check=False)
    pulled = git("pull", "--ff-only", "origin", "main", check=False)
    if pulled.returncode != 0:
        return note + " pull 未成功（可能历史分叉），请人工合并，禁止 reset --hard。"
    return note + " 已 ff-only 拉取最新版本。"


def cmd_init_sync(cloud_dir: str) -> str:
    if not is_repo():
        raise BootstrapError("当前目录不是 git 仓库，无法初始化同步；先从远端 clone 或 bundle 后 git init。")
    cloud = Path(cloud_dir)
    if not cloud.is_dir():
        raise BootstrapError(f"云同步目录不存在：{cloud}")
    bare = cloud / "zoterofanyi-sync.git"
    pending = git("status", "--porcelain").stdout.strip()
    if pending:
        print("提示：有未提交改动，本次推送只包含已提交的版本；先 iterate_commit.py 提交再 --init-sync 更稳妥。")
    if not bare.exists():
        run(["git", "init", "--bare", str(bare)])
    # v2.5 端到端实测遗留：本地 bare 默认 HEAD 指向 master，而 GitHub / 本仓库用 main，
    # 不修正会让另一台 clone 后没有 checkout。此处幂等，已存在的 bare 也一并对齐。
    run(["git", "--git-dir=" + str(bare), "symbolic-ref", "HEAD", "refs/heads/main"])
    url = bare.as_posix()
    set_remote(url)
    pushed = git("push", "-u", "origin", "main", "--follow-tags", check=False)
    if pushed.returncode != 0:
        raise BootstrapError(f"push 失败：{(pushed.stdout + pushed.stderr).strip()[-800:]}")
    update_sync_config("cloud-bare", url, url)
    return (
        f"已在 {bare} 创建 bare 仓库并推上 main + 所有 tag。\n"
        "其他电脑：等云盘同步完成后运行 "
        "`python scripts/bootstrap.py --remote <云盘上的 zoterofanyi-sync.git 路径>`。\n"
        "注意：多机错峰 push/pull，不要两台同时写；云盘客户端同步期间不要强推。"
    )


def cmd_sync() -> str:
    """兼容入口：委托 sync_now.py --start（ff-only + 分叉 BLOCK + 离线 WARN）。"""
    if not is_repo():
        raise BootstrapError("当前目录不是 git 仓库；用 --remote 先 clone。")
    if not SYNC_NOW.is_file():
        raise BootstrapError(f"找不到 {SYNC_NOW}，无法执行开工同步。")
    proc = run([sys.executable, str(SYNC_NOW), "--start"], check=False, timeout=180)
    text = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise BootstrapError("开工同步未通过：\n" + text[-900:])
    return text or "已同步到远端最新版本：" + describe()


def cmd_bundle(out_zip: str) -> str:
    if not is_repo():
        raise BootstrapError("bundle 需要 git 仓库（用 git ls-files 取版本内的文件）。")
    files = [f for f in git("ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
             if f.strip() and f != "local_config.json" and not f.startswith(".git/")]
    out = Path(out_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    version = describe()
    commit = git("rev-parse", "HEAD").stdout.strip()
    readme = (
        "zoterofanyi 离线引导包\n"
        f"版本：{version}\ncommit：{commit}\n导出时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        "来源：当前工作区快照（含未提交改动时版本号仍显示 HEAD）；正式分发前先 iterate_commit.py 提交。\n\n"
        "用法：\n"
        "1) 先解压到任意临时目录（不要直接覆盖已有的 skill 目录）\n"
        "2) 有远端：python scripts/bootstrap.py --remote <url>  → 自动 clone 到\n"
        "   ~/.workbuddy/skills/zoterofanyi/，再到那个目录重跑 bootstrap.py\n"
        "   无远端：把内容放到 ~/.workbuddy/skills/zoterofanyi/，执行\n"
        "   git init -b main && git add -A && git commit -m 'baseline(bundle)'\n"
        "3) 系统 Python 运行：python scripts/bootstrap.py（探测本机 + 生成配置 + 黄金集）\n"
        "4) 按输出补齐缺失组件，再跑 python scripts/paths.py --check\n\n"
        "本包不含 .git，也不含 local_config.json（本机配置由 detect_env.py 生成，绝不跨机复用）。\n"
        "含 tm/ 与 fixtures/ 的论文片段，请只放在自己可见的空间。\n"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BOOTSTRAP.txt", readme)
        for rel in files:
            path = ROOT / rel
            if path.is_file():
                zf.write(path, rel)
    return f"已导出 {len(files)} 个文件 → {out}（版本 {version}，不含 .git / 本机配置）"


def localize(skip_regression: bool) -> dict:
    detect = run([sys.executable, str(DETECT)], check=False, timeout=180)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
    checked = run([sys.executable, str(PATHS), "--check"], check=False, timeout=60)
    result = {
        "detect_ok": detect.returncode == 0,
        "check_ok": checked.returncode == 0,
        "python": cfg.get("python"),
        "machine": cfg.get("machine"),
        "warnings": cfg.get("warnings", []),
        "regression": "SKIPPED",
    }
    py = cfg.get("python")
    if not skip_regression and py and Path(py).exists() and REGRESSION.is_file():
        proc = subprocess.run(
            [py, str(REGRESSION), "--fixtures", str(FIXTURES), "--verbose"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
        result["regression"] = "PASS (exit 0)" if proc.returncode == 0 else "FAIL"
        result["regression_tail"] = tail
    elif not skip_regression:
        result["regression"] = "SKIPPED (缺可用 Python 或回归套件)"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="zoterofanyi 跨机引导与同步")
    ap.add_argument("--remote", metavar="URL", help="新机 clone / 本机设置 origin 并 ff-only 拉取")
    ap.add_argument("--init-sync", metavar="CLOUD_DIR", help="在云同步盘创建 bare 仓库并首次推送")
    ap.add_argument("--sync", action="store_true", help="拉取远端最新版本（有本地改动则 BLOCK）")
    ap.add_argument("--bundle", metavar="OUT_ZIP", help="导出离线 zip 供资料库/网盘分发")
    ap.add_argument("--no-regression", action="store_true", help="跳过黄金集（新机未装 PyMuPDF 时可用）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出引导报告")
    args = ap.parse_args()

    report: dict = {"version": describe(), "actions": [], "error": None}
    try:
        if args.init_sync:
            report["actions"].append(cmd_init_sync(args.init_sync))
        if args.remote:
            report["actions"].append(cmd_remote(args.remote))
        if args.sync:
            report["actions"].append(cmd_sync())
        if args.bundle:
            report["actions"].append(cmd_bundle(args.bundle))
        report["version"] = describe()
        report["localize"] = localize(args.no_regression)
    except (BootstrapError, subprocess.TimeoutExpired, OSError) as exc:
        report["error"] = str(exc)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["error"] else 0

    print("=== zoterofanyi 本机引导报告 ===")
    print(f"版本：{report['version']}    目录：{ROOT.as_posix()}")
    for action in report["actions"]:
        print("\n[同步动作] " + action)
    if report["error"]:
        print(f"\n[BLOCK] {report['error']}")
        return 1
    loc = report["localize"]
    print(f"\n[本地化] 机器 {loc['machine']} · Python {loc['python'] or '(缺失)'}")
    print(f"         组件检查 {'全齐' if loc['check_ok'] else '有缺项'} · 黄金集 {loc['regression']}")
    if loc.get("regression_tail"):
        print("         " + loc["regression_tail"].replace("\n", "\n         "))
    for w in loc["warnings"]:
        print("  待处理 - " + w)
    print("\n下一步：")
    print('  eval "$(python scripts/paths.py --shell bash)"   # 取到 $PY/$S/$K/$W')
    print("  python scripts/paths.py --check                 # 环境自检")
    print("  python scripts/sync_now.py --start              # 每次开工前拉最新 skill")
    print("  python scripts/sync_now.py --status             # 看本地 / 云端是否对齐")
    print("  python scripts/iterate_commit.py --status       # 收尾前看版本坐标（提交后自动推送）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
