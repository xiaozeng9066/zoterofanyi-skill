#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 zoterofanyi 的本地 git 仓库接到 GitHub，并推送第一版（含全部历史与 tag）。

用户 2026-11-14 第四个要求：连接 GitHub 建仓库，之后每次使用前后与云端交互。
本脚本只做「一次性连接」，日常同步交给 sync_now.py / iterate_commit.py。

凭据策略（绝不把 token 写进 URL、配置或日志）：
  1. 推荐：WorkBuddy 的 GitHub 连接器授权，或
     `git credential-manager github login --device --no-ui`（GCM 2.9+，设备码登录）；
     登录后的凭据由系统凭据管理器保管，git push 自动取用。
  2. 仅当要**用 API 自动建仓库**时，才需要 token：从环境变量读取
     （默认 GITHUB_TOKEN / GH_TOKEN，可用 --token-env 指定），用完即弃，不落盘。
  3. 也可以先在 GitHub 网页手建一个**空**私有仓库，然后把地址传给 --remote。

用法：
  python scripts/connect_github.py --user <GitHub用户名> --repo zoterofanyi-skill --create
  python scripts/connect_github.py --remote https://github.com/<user>/<repo>.git
  python scripts/connect_github.py --user <user> --repo <repo> --create --dry-run
  python scripts/connect_github.py --user <user> --repo <repo> --no-push   # 只配 origin

安全：默认建**私有**仓库（--public 才公开）；不覆盖已有 origin 的不同地址；
不 force push；推送后由 sync_now.py 回读远端 HEAD 校验。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_now.py"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
USER_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")


class ConnectError(Exception):
    pass


def _opener():
    """规则 61：探测到 GitHub 可直连时绕过代理。

    本机实测（2026-11-14）：HTTPS_PROXY 对 api.github.com 时好时坏、对
    github.com:443 的 CONNECT 隧道约半数 502；直连更稳定。选路结果写在
    local_config.json 的 network 段（detect_env.py 探测），此处只读不探测。
    """
    net = {}
    try:
        net = (json.loads((ROOT / "local_config.json").read_text(encoding="utf-8"))
               .get("network") or {})
    except (OSError, ValueError):
        net = {}
    if net.get("github_direct_ok") and net.get("force") != "proxy":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def git(*args: str, check: bool = True, timeout: int = 90) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise ConnectError(f"git {' '.join(args)} 失败：\n{(proc.stdout + proc.stderr).strip()[-800:]}")
    return proc


def run(cmd: list[str], check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise ConnectError(f"命令失败：{' '.join(str(c) for c in cmd)}\n{(proc.stdout + proc.stderr).strip()[-900:]}")
    return proc


def gcm_status() -> str:
    """只读探测 GCM 里已登录的 GitHub 账户；探测不到不视为错误。"""
    p = subprocess.run(
        ["git", "credential-manager", "github", "list"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return p.stdout.strip() if p.returncode == 0 else ""


def login_hint() -> str:
    return (
        "未检测到 GitHub 凭据。任选一种授权后再重试：\n"
        "  A) WorkBuddy 里启用 GitHub 连接器（推荐，OAuth，不需在对话里贴 token）\n"
        "  B) 设备码登录：git credential-manager github login --device --no-ui\n"
        "  C) 网页手建空私有仓库后，用 connect_github.py --remote <url> 连接\n"
        "  另：若要用 API 自动建仓库，请设置环境变量 GITHUB_TOKEN（fine-grained PAT，仅 repo 权限）"
    )


def read_token(env_name: str) -> str | None:
    for name in dict.fromkeys([env_name, "GITHUB_TOKEN", "GH_TOKEN"]):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def create_repo(token: str, repo: str, private: bool, dry_run: bool) -> str:
    payload = json.dumps({
        "name": repo,
        "private": private,
        "description": "zoterofanyi 论文双语化 skill：规则、脚本、黄金集与翻译记忆（持续迭代）",
        "auto_init": False,
        "has_issues": False,
        "has_wiki": False,
        "has_projects": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "zoterofanyi-skill/2.5",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if dry_run:
        return f"[dry-run] 将调用 POST /user/repos 创建 {'private' if private else 'public'} 仓库 {repo}"
    try:
        with _opener().open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return f"已创建{'私有' if private else '公开'}仓库：{data.get('full_name', repo)}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 422 and "already exists" in body.lower():
            return f"仓库 {repo} 已存在，直接复用（不改动其内容）"
        raise ConnectError(f"建仓库失败 HTTP {exc.code}：{body[:500]}")
    except Exception as exc:
        raise ConnectError(f"建仓库请求失败：{type(exc).__name__}: {exc}")


def current_origin() -> str | None:
    p = git("remote", "get-url", "origin", check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description="连接 GitHub 并推送 zoterofanyi 第一版")
    ap.add_argument("--user", help="GitHub 用户名")
    ap.add_argument("--repo", default="zoterofanyi-skill", help="仓库名（默认 zoterofanyi-skill）")
    ap.add_argument("--remote", metavar="URL", help="已有仓库地址；给了它就不必 --user/--create")
    ap.add_argument("--create", action="store_true", help="用 GITHUB_TOKEN 调 API 自动建仓库")
    ap.add_argument("--public", action="store_true", help="建公开仓库（默认私有，见 PORTABILITY.md 隐私审计）")
    ap.add_argument("--token-env", default="GITHUB_TOKEN", help="读取 token 的环境变量名")
    ap.add_argument("--no-push", action="store_true", help="只配置 origin，不推送")
    ap.add_argument("--dry-run", action="store_true", help="预演，不联网、不写仓库")
    args = ap.parse_args()

    try:
        if not (ROOT / ".git").exists():
            raise ConnectError(f"{ROOT} 不是 git 仓库；先用 bootstrap.py --remote 或 git init 建仓。")
        if not args.remote:
            if not args.user:
                ap.error("需要 --user <GitHub用户名>，或直接给 --remote <仓库地址>")
            if not USER_RE.match(args.user) or not REPO_RE.match(args.repo):
                raise ConnectError("用户名 / 仓库名格式不合法（只允许字母数字、. _ -）。")
        url = args.remote or f"https://github.com/{args.user}/{args.repo}.git"
        if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
            raise ConnectError("仓库地址里不要内嵌 token；凭据请交给 GCM / GitHub 连接器保管。")

        print("=== zoterofanyi → GitHub 连接 ===")
        print(f"目标仓库：{url}")
        print(f"可见性：{'公开（--public）' if args.public else '私有（默认，推荐）'}")
        accounts = gcm_status()
        print(f"GCM 已登录账户：{accounts or '(无)'}")
        if not accounts:
            print("[提示] " + login_hint().replace("\n", "\n       "))

        if args.create:
            token = read_token(args.token_env)
            if args.dry_run:
                print(f"[dry-run] 将调用 POST /user/repos 创建 {'私有' if not args.public else '公开'} 仓库 "
                      f"{args.repo}（正式运行时需环境变量 {args.token_env} 或 GITHUB_TOKEN）")
            elif not token:
                raise ConnectError(
                    "--create 需要 token 从环境变量读取，当前未设置。\n" + login_hint()
                )
            else:
                print(create_repo(token, args.repo, not args.public, False))

        existing = current_origin()
        if existing and existing.rstrip("/") != url.rstrip("/"):
            raise ConnectError(
                f"origin 已指向 {existing}，与本次目标不同。\n"
                "为避免误切换同步源，请人工确认后 `git remote set-url origin <url>`；本脚本不覆盖。"
            )
        if args.dry_run:
            print(f"[dry-run] 将把 origin 设为 {url}" + ("" if args.no_push else "，并推送 main + 全部 tag"))
            return 0
        if existing:
            print("origin 已指向该地址，跳过配置。")
        else:
            git("remote", "add", "origin", url)
            print(f"已设置 origin → {url}")

        dirty = git("status", "--porcelain", check=False).stdout.strip()
        if dirty:
            print("[WARN] 有未提交改动，它们不会进入本次推送；请先 iterate_commit.py 提交。")

        if args.no_push:
            print("按 --no-push 跳过推送；准备好后跑：python scripts/sync_now.py --end")
            return 0
        print("\n=== 首次推送（含历史与 tag） ===")
        pushed = run([sys.executable, str(SYNC), "--end"], check=False, timeout=240)
        print((pushed.stdout + pushed.stderr).strip())
        if pushed.returncode != 0:
            raise ConnectError("首次推送未完成。" + login_hint())
        print("\n连接完成。以后：")
        print("  每次使用前：python scripts/sync_now.py --start")
        print("  每次迭代收尾：python scripts/iterate_commit.py -v <版本> -m \"<说明>\"（自动推送）")
        print(f"  其他电脑一次性引导：python scripts/bootstrap.py --remote {url}")
        return 0
    except (ConnectError, subprocess.TimeoutExpired) as exc:
        print(f"\n[BLOCK] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
