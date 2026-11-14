#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zoterofanyi 的本机路径单一入口（零第三方依赖，系统 Python 即可运行）。

skill 的 40+ 脚本本身不写死任何绝对路径，全部由调用方注入 $PY / $S / $W。
本模块负责把这些变量从「本机配置」里取出来，让 SKILL.md / ADAPTER 的命令
在任何电脑上都能原样使用。

  python scripts/paths.py --json                 # 查看本机配置
  python scripts/paths.py --check               # 检查组件是否齐全
  python scripts/paths.py --shell bash          # 输出可 source 的 export 语句
  python scripts/paths.py --shell powershell    # 输出 PowerShell 变量
  python scripts/paths.py --get python          # 取单个值（支持 a.b 点号路径）

环境变量优先级高于 local_config.json：
  ZF_PYTHON / ZB_PY, ZF_MINERU_PY / MINERU_PY, ZF_BROWSER,
  ZF_ZOTERO_MCP, ZF_WORKSPACE, ZF_RUNS_DIR, ZF_SKILL_ROOT
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "local_config.json"
TEMPLATE = ROOT / "templates" / "local_config.template.json"

ENV_MAP = {
    "python": ("ZF_PYTHON", "ZB_PY"),
    "mineru_python": ("ZF_MINERU_PY", "MINERU_PY"),
    "browser": ("ZF_BROWSER",),
    "zotero_mcp": ("ZF_ZOTERO_MCP",),
    "workspace": ("ZF_WORKSPACE",),
    "runs_dir": ("ZF_RUNS_DIR",),
    "skill_root": ("ZF_SKILL_ROOT",),
}
REQUIRED = ("skill_root", "scripts_dir", "python", "workspace", "runs_dir")
OPTIONAL = ("mineru_python", "browser", "zotero_mcp")


def load() -> dict:
    if CONFIG.is_file():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        cfg["_config_source"] = str(CONFIG)
    elif TEMPLATE.is_file():
        cfg = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        cfg["_config_source"] = "template (run detect_env.py)"
    else:
        cfg = {"_config_source": "builtin defaults"}
    cfg.setdefault("schema_version", 1)
    cfg.setdefault("home", Path.home().as_posix())
    cfg.setdefault("skill_root", ROOT.as_posix())
    cfg.setdefault("scripts_dir", (ROOT / "scripts").as_posix())
    cfg.setdefault("workspace", (Path.home() / "zoterofanyi").as_posix())
    cfg.setdefault("runs_dir", str(Path(cfg["workspace"]) / "zotero-translate"))
    cfg.setdefault("sync", {})

    for key, names in ENV_MAP.items():
        for name in names:
            value = os.environ.get(name)
            if value:
                cfg[key] = value
                cfg.setdefault("overrides", {})[key] = f"env:{name}"
                break
    if "runs_dir" not in cfg.get("overrides", {}) and "workspace" in cfg.get("overrides", {}):
        cfg["runs_dir"] = str(Path(cfg["workspace"]) / "zotero-translate")
    return cfg


def get(cfg: dict, dotted: str):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def shell_lines(cfg: dict, flavor: str) -> list[str]:
    def q(v) -> str:
        return f'"{v or ""}"'

    if flavor == "powershell":
        return [
            "# zoterofanyi 本机变量（由 paths.py 生成；<key> 换成 Zotero 条目 key）",
            f'$K = {q(cfg.get("skill_root"))}',
            '$S = Join-Path $K "scripts"',
            f'$PY = {q(cfg.get("python"))}',
            f'$MINERU_PY = {q(cfg.get("mineru_python"))}',
            f'$EDGE = {q(cfg.get("browser"))}',
            f'$ZOTERO_MCP = {q(cfg.get("zotero_mcp"))}',
            f'$WORKSPACE = {q(cfg.get("workspace"))}',
            '$W = Join-Path $WORKSPACE "zotero-translate\\<key>"',
        ]
    return [
        "# zoterofanyi 本机变量（由 paths.py 生成；<key> 换成 Zotero 条目 key）",
        "# 用法：eval \"$(python scripts/paths.py --shell bash)\"",
        f'export K={q(cfg.get("skill_root"))}',
        'export S="$K/scripts"',
        f'export PY={q(cfg.get("python"))}',
        f'export MINERU_PY={q(cfg.get("mineru_python"))}',
        f'export EDGE={q(cfg.get("browser"))}',
        f'export ZOTERO_MCP={q(cfg.get("zotero_mcp"))}',
        f'export WORKSPACE={q(cfg.get("workspace"))}',
        'export W="$WORKSPACE/zotero-translate/<key>"',
    ]


def check(cfg: dict) -> int:
    print(f"配置来源：{cfg.get('_config_source')}")
    print(f"机器：{cfg.get('machine') or '未知'}  OS：{cfg.get('os') or '未知'}")
    missing_required = 0
    for key in (*REQUIRED, *OPTIONAL):
        value = cfg.get(key)
        ok = bool(value) and Path(str(value)).exists()
        tag = "OK  " if ok else ("MISS" if key in REQUIRED else "WARN")
        if key == "python" and ok:
            tag += " (fitz: %s)" % ("yes" if cfg.get("python_has_fitz") else "unknown")
        print(f"  [{tag}] {key:<15} {value or '(未配置)'}")
        if not ok and key in REQUIRED:
            missing_required += 1
    if cfg.get("warnings"):
        print("\n待处理：")
        for w in cfg["warnings"]:
            print("  - " + w)
    if missing_required:
        print(f"\n{BAD} {missing_required} 个必需项缺失：先跑 detect_env.py，或按 PORTABILITY.md 补齐组件。")
        return 1
    print("\n必需组件齐全，可进入流水线；可选组件缺失只会影响对应环节。")
    return 0


BAD = "[BLOCK]"


def main() -> int:
    ap = argparse.ArgumentParser(description="zoterofanyi 本机路径解析")
    ap.add_argument("--json", action="store_true", help="输出完整配置 JSON")
    ap.add_argument("--get", metavar="KEY", help="取单个值，支持 sync.remote_url 这类点号路径")
    ap.add_argument("--check", action="store_true", help="检查本机组件是否齐全")
    ap.add_argument("--shell", choices=("bash", "powershell"), help="输出可直接使用的环境/变量语句")
    args = ap.parse_args()
    cfg = load()
    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    elif args.get:
        value = get(cfg, args.get)
        print("" if value is None else value)
    elif args.shell:
        print("\n".join(shell_lines(cfg, args.shell)))
    elif args.check:
        return check(cfg)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
