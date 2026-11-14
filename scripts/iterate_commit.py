#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zoterofanyi 迭代收尾：带门禁的 git 提交 + 版本 tag（可选同步推送）。

用户 2026-11-14 要求「今后这个技能每次迭代都用 git 控制版本」。
本脚本把该要求变成可检查的硬约束：

  1. 版本号必须符合 vX.Y[.Z]，且与 CHANGELOG.md 的「## vX.Y」条目一一对应；
  2. 必须有实际文件改动（只在对话里总结、不改文件 → 拒绝提交）；
  3. 默认强制跑黄金测试集，非全绿 → 拒绝提交；
  4. 同版本 tag 已存在 → 拒绝覆盖（历史只追加，不重写）；
  5. 提交信息规范化，自动打 annotated tag，并打印回滚命令；
  6. 本机配置 local_config.json 绝不入库（跨机可移植性，规则 56）；
  7. README 状态表必须与 CHANGELOG / SKILL / fixtures / tm 一致（v2.14，规则 64）。

**v2.5 起默认自动同步到云端**（用户 2026-11-14 要求「云端永远是最新的」）：
提交 + tag 完成后，只要配置了 origin 就自动调用 sync_now.py --end，推送 main + 全部 tag
并回读远端 HEAD 校验；推送失败会明确报「本地已提交、云端未更新」，绝不假装对齐。
`--no-push` 可临时退回纯本地，`--push` 保留为「必须推送、无 origin 即 BLOCK」的显式模式，
`--dry-run` 一律不联网。尚未连接 GitHub 时自动降级为纯本地提交 + WARNING（不阻断迭代）。
绝不执行 reset --hard / commit --amend / tag -f / push --force。
仓库边界：只含 skill 资产本身；工作区 zotero-translate/<key>/ 的产物不入库。

用法：
  python scripts/iterate_commit.py --status
  python scripts/iterate_commit.py -v v2.5 -m "GitHub 云端同步" --dry-run
  python scripts/iterate_commit.py -v v2.5 -m "GitHub 云端同步"          # 自动推送
  python scripts/iterate_commit.py -v v2.5 -m "..." --no-push            # 本次不联网
  python scripts/iterate_commit.py -v v2.5 -m "..." --push               # 强制要求已连接云端
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
REGRESSION = ROOT / "scripts" / "regression_suite.py"
SYNC_NOW = ROOT / "scripts" / "sync_now.py"
PUBLIC_SANITIZE = ROOT / "scripts" / "public_sanitize.py"
README_CHECK = ROOT / "scripts" / "check_readme.py"
PUBLIC_MODE = ROOT / "PUBLIC-MODE.json"
FIXTURES = ROOT / "fixtures"
VERSION_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?$")


class GateError(Exception):
    """门禁未通过：不是执行故障，而是这次迭代不合格。"""


def run(cmd: list[str], *, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip()
        raise GateError(f"命令失败：{' '.join(cmd)}\n{detail[-1200:]}")
    return proc


def git(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(ROOT), *args], check=check, timeout=timeout)


def show_status() -> int:
    head = git("describe", "--tags", "--abbrev=0", check=False).stdout.strip() or "(无 tag)"
    print(f"仓库：{ROOT}")
    print(f"当前版本 tag：{head}")
    print(f"分支：{git('branch', '--show-current').stdout.strip()}")
    remotes = git("remote", "-v", check=False).stdout.strip()
    print(f"同步源：{remotes.splitlines()[0] if remotes else '(未配置 origin；连接 GitHub 用 connect_github.py)'}")
    print("云端对齐详情（只读）：python scripts/sync_now.py --status")
    print("\n最近提交：")
    for line in git("log", "-5", "--oneline", "--decorate").stdout.splitlines():
        print("  " + line)
    print("\n最近 tag：")
    for line in git("tag", "--sort=-version:refname").stdout.splitlines()[:8]:
        print("  " + line)
    status = git("status", "--short").stdout.strip()
    print("\n未提交改动：" if status else "\n工作区干净（无未提交改动）。")
    for line in status.splitlines():
        print("  " + line)
    return 0


def check_repo() -> None:
    if not (ROOT / ".git").exists():
        raise GateError(
            f"{ROOT} 不是 git 仓库。先执行：\n"
            f'  git -C "{ROOT}" init -b main\n'
            "  并参照 WORKBUDDY-ADAPTER.md 第 6 节配置身份与基线。"
        )
    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise GateError("git 仓库状态异常。")


def check_changelog(version: str) -> None:
    if not CHANGELOG.is_file():
        raise GateError("找不到 CHANGELOG.md。")
    text = CHANGELOG.read_text(encoding="utf-8")
    if not re.search(rf"^##\s+{re.escape(version)}\b", text, flags=re.M):
        raise GateError(
            f"CHANGELOG.md 里没有「## {version}」条目。\n"
            "先按四指标（准确 / 少错 / 速度 / 成本）写一行迭代记录，再提交；"
            "只在对话里总结不算迭代。"
        )


def run_regression(python_exe: str, skip: bool) -> str:
    if skip:
        print("⚠ 黄金测试集被显式跳过，提交信息将标注 SKIPPED（质量红线要求原则上不得跳过）。")
        return "SKIPPED (--skip-regression)"
    if not REGRESSION.is_file():
        raise GateError(f"找不到 {REGRESSION}，无法执行回归门禁。")
    proc = run([python_exe, str(REGRESSION), "--fixtures", str(FIXTURES), "--verbose"], check=False)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    tail = "\n".join(output.splitlines()[-8:])
    if proc.returncode != 0:
        raise GateError(f"黄金测试集未通过，禁止提交。\n{tail}")
    # 尽力从输出里抽出 "N/N PASS" 之类的结论，抽不到就记 exit 0。
    m = re.findall(r"(\d+)\s*/\s*(\d+)\s*(?:PASS|passed|通过)", output, flags=re.I)
    if m:
        passed, total = m[-1]
        if passed != total:
            raise GateError(f"黄金测试集 {passed}/{total}，不是全绿，禁止提交。\n{tail}")
        print(f"黄金测试集：{passed}/{total} PASS")
        return f"{passed}/{total} PASS"
    print(f"黄金测试集：exit 0（{tail.splitlines()[-1] if tail else 'OK'}）")
    return "PASS (exit 0)"


def public_mode_gate(python_exe: str) -> str:
    """公开主库护栏（v2.8，规则 59）：提交前确保没有真实论文正文混进 fixtures/tm。

    私有真源没有 PUBLIC-MODE.json，因此不触发；公开库 clone 后自带该文件，
    每次迭代收尾强制检查。原因：公开库一旦成为日常同步目标，下一篇论文的
    `snapshot_case.py` 新案例会被自动推送，隐私会在一次迭代后失效。
    """
    if not PUBLIC_MODE.is_file():
        return "private（无公开模式标记）"
    try:
        cfg = json.loads(PUBLIC_MODE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateError(f"PUBLIC-MODE.json 无法解析：{exc}")
    if cfg.get("mode") != "public" or not cfg.get("require_sanitize_gate", True):
        return f"mode={cfg.get('mode')}（未启用门禁）"
    if not PUBLIC_SANITIZE.is_file():
        raise GateError("公开模式要求 scripts/public_sanitize.py 存在，当前缺失。")
    proc = run([python_exe, str(PUBLIC_SANITIZE), "--gate"], check=False, timeout=120)
    if proc.returncode != 0:
        raise GateError(
            "公开主库提交门禁失败：fixtures/ 或 tm/ 混入真实论文正文。\n"
            + (proc.stdout + "\n" + proc.stderr).strip()[-1200:] +
            "\n处理：真实全强度快照放 fixtures_full/（gitignored），"
            "fixtures/ 只保留合成案例（PUBLIC-RELEASE.md / 规则 59）。禁止绕过。"
        )
    print("公开模式门禁：合成数据检查通过")
    return "PUBLIC gate PASS"


def readme_gate(python_exe: str) -> str:
    """README 一致性门禁（v2.14，规则 64）：首页不许撒谎。

    公开仓库的 README 是唯一入口，写死的「v2.10 / 14 案例 / 规则 61」会在几次迭代后
    变成误导，而误导的首页比没有首页更糟。这里强制 `check_readme.py` 通过，
    它比对的是 CHANGELOG 最新版本、SKILL.md 最大规则编号、fixtures 案例数、
    tm 记忆条数与文档里提到的路径是否存在。数字可用 `--fix` 刷，文字仍需人工。
    """
    if not README_CHECK.is_file():
        raise GateError("找不到 scripts/check_readme.py，README 门禁无法执行。")
    proc = run([python_exe, str(README_CHECK)], check=False, timeout=60)
    out_text = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        raise GateError(
            "README 与真实来源不一致，禁止提交。\n" + out_text[-1200:] +
            "\n处理：python scripts/check_readme.py --fix（刷新数字）+ 人工同步说明文字。"
        )
    print("README 一致性门禁：通过")
    return "README gate PASS"


def changed_files(dry_run: bool) -> list[str]:
    if dry_run:
        out = git("add", "-n", "-A").stdout
        return [ln.split("'", 1)[1].rsplit("'", 1)[0] for ln in out.splitlines() if "'" in ln]
    git("add", "-A")
    return [p for p in git("diff", "--cached", "--name-only").stdout.splitlines() if p.strip()]


def has_origin() -> bool:
    return "origin" in git("remote", check=False).stdout.split()


def sync_mode(explicit_push: bool, no_push: bool, dry_run: bool) -> tuple[str, bool]:
    """返回 (提交信息里的 Sync 描述, 提交后是否真的推送)。"""
    if dry_run:
        return "dry-run (no network)", False
    if no_push:
        return "disabled by --no-push (local only)", False
    if not has_origin():
        if explicit_push:
            raise GateError(
                "--push 要求已连接云端，但当前没有 origin。"
                "先跑 connect_github.py / bootstrap.py --remote，再重跑本次提交。"
            )
        return "no origin (local only, cloud NOT updated)", False
    return ("explicit push to origin (verify remote HEAD)" if explicit_push
            else "auto-push to origin (verify remote HEAD)"), True


def push_to_cloud() -> str:
    """委托 sync_now.py --end：fetch → 分叉检查 → push main+tags → 回读远端 HEAD。"""
    if not SYNC_NOW.is_file():
        raise GateError(f"找不到 {SYNC_NOW}，无法完成云端同步门禁。")
    proc = run([sys.executable, str(SYNC_NOW), "--end"], check=False, timeout=300)
    text = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise GateError(
            "本地 commit + tag 已完成，但云端未更新：\n" + text[-1000:] +
            "\n修复后重跑：python scripts/sync_now.py --end（不需要再提交一次）。"
        )
    return text or "已推送 origin/main + tags"


def main() -> int:
    ap = argparse.ArgumentParser(description="zoterofanyi 迭代收尾：门禁 + git commit + tag")
    ap.add_argument("-v", "--version", help="版本号，必须与 CHANGELOG 条目一致，如 v2.3")
    ap.add_argument("-m", "--message", help="一句话说明本次迭代（中文即可）")
    ap.add_argument("--status", action="store_true", help="只看版本坐标与工作区状态，不提交")
    ap.add_argument("--dry-run", action="store_true", help="跑完所有门禁并打印将提交的内容，但不写入仓库")
    ap.add_argument("--no-tag", action="store_true", help="只提交不打 tag（默认打 annotated tag）")
    ap.add_argument("--push", action="store_true", help="显式要求必须推送；无 origin 直接 BLOCK（默认已自动推送）")
    ap.add_argument("--no-push", action="store_true", help="本次退回纯本地提交，不联网")
    ap.add_argument("--skip-regression", action="store_true", help="跳过黄金集（会在提交信息标注 SKIPPED）")
    ap.add_argument("--python", default=sys.executable, help="跑黄金集的 Python 解释器路径")
    args = ap.parse_args()

    if args.status:
        return show_status()
    if not args.version or not args.message:
        ap.error("提交模式需要 -v/--version 与 -m/--message；只看状态用 --status")
    if args.push and args.no_push:
        ap.error("--push 与 --no-push 不能同时使用")

    try:
        if not VERSION_RE.match(args.version):
            raise GateError(f"版本号格式不合法：{args.version}（应为 vX.Y 或 vX.Y.Z）")
        check_repo()
        sync_desc, do_push = sync_mode(args.push, args.no_push, args.dry_run)
        if git("tag", "-l", args.version).stdout.strip():
            raise GateError(
                f"tag {args.version} 已存在。迭代只能追加新版本，禁止覆盖历史；"
                "请改用下一个版本号，或先确认 CHANGELOG 的版本坐标。"
            )
        status = git("status", "--porcelain").stdout.strip()
        if not status:
            raise GateError("没有任何文件改动 —— 只在对话里总结不算迭代，拒绝空提交。")
        check_changelog(args.version)
        readme_status = readme_gate(args.python)
        public_gate = public_mode_gate(args.python)
        regression = run_regression(args.python, args.skip_regression)

        files = changed_files(args.dry_run)
        if not files:
            raise GateError("暂存区为空，没有可提交的文件。")
        ignored_hits = [
            p for p in files
            if "__pycache__" in p
            or p.endswith((".pyc", ".pdf", ".sqlite"))
            or p == "local_config.json"
        ]
        if ignored_hits:
            raise GateError(
                f"发现不应入库的文件：{ignored_hits}；请检查 .gitignore。"
                "本机配置 local_config.json 必须由 .gitignore 排除（规则 56）。"
            )

        shortstat = git("diff", "--cached", "--shortstat").stdout.strip() if not args.dry_run else ""
        msg = (
            f"iterate({args.version}): {args.message}\n\n"
            f"Date: {date.today().isoformat()}\n"
            f"CHANGELOG: {args.version} entry present\n"
            f"Regression: {regression}\n"
            f"Readme: {readme_status}\n"
            f"PublicGate: {public_gate}\n"
            f"Sync: {sync_desc}\n"
            f"Changed: {len(files)} files" + (f", {shortstat}" if shortstat else "") + "\n"
        )

        print("\n=== 门禁全部通过 ===")
        print(f"版本：{args.version}    变更文件：{len(files)}")
        for p in files:
            print("  " + p)
        print("\n=== 提交信息 ===")
        print(msg.rstrip())
        if args.dry_run:
            print("\n[dry-run] 未写入仓库，未打 tag，也不会联网推送。")
            return 0

        git("commit", "-m", msg)
        head = git("rev-parse", "--short", "HEAD").stdout.strip()
        if args.no_tag:
            print(f"\n提交完成：{head}（按 --no-tag 未打 tag）")
        else:
            git("tag", "-a", args.version, "-m", f"{args.version} · {args.message} · {date.today().isoformat()}")
            print(f"\n提交完成：{head}  tag: {args.version}")
        if do_push:
            print("\n=== 自动同步到云端 ===")
            print(push_to_cloud())
        elif args.no_push:
            print("按 --no-push 未联网；需要补推时跑 python scripts/sync_now.py --end。")
        else:
            print("\n[WARN] 尚未连接 GitHub：本次改动只在本机，云端不是最新。")
            print("       连接后跑 python scripts/sync_now.py --end 即补推全部历史与 tag。")
        print(f"版本链：git -C \"{ROOT}\" log --oneline --decorate -5")
        print(f"本次改动：git -C \"{ROOT}\" diff {args.version}~1..{args.version} --stat")
        print(f"如需回滚：git -C \"{ROOT}\" revert {head}（追加反向提交，不改写历史）")
        return 0
    except (GateError, subprocess.TimeoutExpired) as exc:
        print(f"\n[BLOCK] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
