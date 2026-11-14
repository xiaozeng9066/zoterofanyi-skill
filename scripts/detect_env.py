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

# v2.10 实测（2026-11-14）：本机 HTTPS_PROXY=127.0.0.1:14126 对 api.github.com 正常，
# 但对 github.com:443 的 CONNECT 隧道约 50% 返回 502；直连 github.com 反而 3/3 稳定（~0.5s）。
# 结论：GitHub 这几个 host 应绕过代理（白名单），其余流量照旧走代理。
DEFAULT_BYPASS_HOSTS = [
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
]
PROBE_URL_FALLBACK = "https://github.com/git/git.git"


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
    """探测 GitHub 直连 / 代理两条路，决定本机该用哪条（规则 61）。"""
    proxy = _env_proxy()
    url = remote_url or PROBE_URL_FALLBACK
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    prev = old.get("network", {}) if isinstance(old.get("network"), dict) else {}

    direct_ok, direct_ms = _ls_remote(url, direct=True, proxy=proxy)
    proxy_ok, proxy_ms = (None, None)
    if proxy:
        proxy_ok, proxy_ms = _ls_remote(url, direct=False, proxy=proxy)

    if direct_ok:
        chosen = "direct"
    elif proxy_ok:
        chosen = "proxy"
    else:
        chosen = "auto"   # 两条都测不通：交给 git 默认（可能稍后网络恢复）
    # 人工可用 network.force 固定路线；auto 表示按探测结果
    force = prev.get("force") if prev.get("force") in ("direct", "proxy", "auto", None) else None
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "proxy_env": {
            "https_proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            "no_proxy": no_proxy,
        },
        "github_direct_ok": direct_ok,
        "github_direct_ms": direct_ms,
        "github_proxy_ok": proxy_ok,
        "github_proxy_ms": proxy_ms,
        "chosen": chosen,
        # direct 可用就把这几个 host 加进白名单（NO_PROXY + git 按域空代理）
        "bypass_hosts": DEFAULT_BYPASS_HOSTS if (direct_ok and force != "proxy") else [],
        "force": force,
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
        route = {"direct": "直连 GitHub（绕过代理）", "proxy": "走代理",
                 "auto": "未定（两条路都没测通，按 git 默认）"}[network["chosen"]]
        dstat = "OK" if network["github_direct_ok"] else "FAIL"
        pstat = ("未设置" if network["github_proxy_ok"] is None
                 else ("OK" if network["github_proxy_ok"] else "FAIL"))
        pms = "" if network["github_proxy_ms"] is None else f" ({network['github_proxy_ms']}s)"
        print(f"  网络: 直连 {dstat} ({network['github_direct_ms']}s)"
              f" / 代理 {pstat}{pms} → 采用 {route}")
        if network["bypass_hosts"]:
            print(f"        白名单: {', '.join(network['bypass_hosts'])}")
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
