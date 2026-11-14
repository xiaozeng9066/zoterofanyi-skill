#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README 一致性检查（v2.14，规则 64）：让首页永远不腐坏。

问题：README 一旦写死「v2.10 / 14 案例 / 规则 61」，几次迭代后就会撒谎，
而撒谎的首页比没有首页更糟 —— 新人（或下一次会话的 AI）会按过时信息操作。

做法：README 里放一块机器可读的状态表（`<!-- readme-state:start -->` 之间），
本脚本把它与真实来源逐项对齐：

  | README 声称 | 真实来源 |
  |---|---|
  | 当前版本      | `CHANGELOG.md` 的第一个 `## vX.Y` |
  | 工程规则编号  | `SKILL.md`「必须遵守的工程规则」段的最大编号 |
  | 黄金测试集    | `fixtures/*/expected.json` 的目录数（与 regression_suite 同口径） |
  | 翻译记忆      | `tm/term_fixes.json` / `tm/error_patterns.json` 的条目数 |
  | 文档内路径    | 行内反引号里出现的相对路径必须真实存在 |

`--fix` 只改上面四处**机械数字**，不改任何说明文字（文字对错仍需人判断）。

用法：
  python scripts/check_readme.py            # 校验，不过则退出码 2
  python scripts/check_readme.py --fix      # 自动刷新四处数字后再校验
  python scripts/check_readme.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
SKILL = ROOT / "SKILL.md"
FIXTURES = ROOT / "fixtures"
TM_DIR = ROOT / "tm"

STATE_START = "<!-- readme-state:start -->"
STATE_END = "<!-- readme-state:end -->"
VERSION_RE = re.compile(r"^##\s+(v\d+\.\d+(?:\.\d+)?)\b", re.M)
# 规则编号散落在多个章节（主规则段 + 后续各版新增章节），统一按全文取最大值。
# 只认「行首数字 + 句点 + 双星号加粗」这一种形态，避免把普通有序列表算成规则。
RULE_NUM_RE = re.compile(r"^(\d+)\.\s+\*\*", re.M)
# 从行内反引号内容里抽出路径片段：既支持纯路径 `scripts/qa.py`，
# 也支持整条命令 `python scripts/qa.py --help`（早期只认第一种，漏检了命令里的路径）。
PATH_FRAG_RE = re.compile(r"(?<![\w-])([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|py|json|jsonl|sh))(?![\w-])")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# 正文里散落的「N 条工程规则」也必须与 SKILL.md 对齐：状态表改了、正文没改，
# 是最典型的「只刷数字不改文字」腐坏形态。
BODY_RULES_RE = re.compile(r"(\d+)\s*条(?:实测)?工程规则")
KNOWN_DIRS = ("scripts", "templates", "tm", "fixtures", "fixtures_full", "dist")
# 只在「本机真源」存在、公开 clone 里没有的路径（.gitignore 排除）。
# README 必须能提到它们（否则说不清 fixtures_full 是什么），但不能因此判为过时。
OPTIONAL_PATHS = ("fixtures_full", "dist", "local_config.json")


class CheckError(Exception):
    pass


def truth() -> dict:
    """从真实来源采集四项指标。"""
    if not CHANGELOG.is_file():
        raise CheckError("找不到 CHANGELOG.md")
    versions = VERSION_RE.findall(CHANGELOG.read_text(encoding="utf-8"))
    if not versions:
        raise CheckError("CHANGELOG.md 里没有任何 `## vX.Y` 版本标题")

    rules_max = 0
    if SKILL.is_file():
        nums = [int(n) for n in RULE_NUM_RE.findall(SKILL.read_text(encoding="utf-8"))]
        rules_max = max(nums) if nums else 0

    cases = len([p for p in FIXTURES.glob("*/expected.json")]) if FIXTURES.is_dir() else 0

    def count(name: str) -> int:
        p = TM_DIR / name
        if not p.is_file():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return 0
        return len(data) if isinstance(data, (list, dict)) else 0

    return {
        "version": versions[0],
        "rules_max": rules_max,
        "rules_next": rules_max + 1,
        "cases": cases,
        "term_fixes": count("term_fixes.json"),
        "error_patterns": count("error_patterns.json"),
    }


def read_state(text: str) -> str:
    if STATE_START not in text or STATE_END not in text:
        raise CheckError(
            f"README.md 缺少状态表标记（{STATE_START} … {STATE_END}）。\n"
            "这块表是自动校验的锚点，删掉它等于关掉 README 门禁。"
        )
    return text.split(STATE_START, 1)[1].split(STATE_END, 1)[0]


def parse_state(block: str) -> dict:
    """从状态表里抽出四个数字（按表格行 label 匹配，不依赖列宽）。"""
    got: dict[str, object] = {}

    m = re.search(r"当前版本\s*\|\s*`?(v\d+\.\d+(?:\.\d+)?)`?", block)
    if m:
        got["version"] = m.group(1)

    m = re.search(r"工程规则\s*\|\s*[^\n]*?(\d+)\s*\*\*\s*条[^\n]*?(\d+)", block)
    if m:
        got["rules_range_end"] = int(m.group(1))   # 「1–**63** 条」里的 63
        got["rules_next"] = int(m.group(2))        # 「下一条 64」

    m = re.search(r"黄金测试集\s*\|\s*\*\*(\d+)\*\*\s*个案例[，,]\s*(\d+)\s*PASS", block)
    if m:
        got["cases"] = int(m.group(1))
        got["cases_pass"] = int(m.group(2))

    m = re.search(r"翻译记忆\s*\|\s*\*\*(\d+)\*\*\s*条词级纠正\s*\+\s*\*\*(\d+)\*\*\s*条整句模式", block)
    if m:
        got["term_fixes"] = int(m.group(1))
        got["error_patterns"] = int(m.group(2))

    return got


def check_paths(text: str) -> tuple[list[str], list[str]]:
    """行内反引号里提到的仓库内路径必须存在（gitignored 的除外，单独返回）。

    裸文件名（如 `qa.py`）按「顶层 → scripts/」两处解析：README 正文里
    写全路径太啰嗦，但校验不能因此放过。
    返回 (真错, 本机才有所以可缺)。
    """
    bad: list[str] = []
    optional_missing: list[str] = []
    tokens: set[str] = set()
    for span in INLINE_CODE_RE.findall(text):
        tokens.update(PATH_FRAG_RE.findall(span))
    for token in sorted(tokens):
        head = token.split("/", 1)[0]
        if "/" in token and head not in KNOWN_DIRS:
            continue          # 外部 URL 片段、包名之类，不归本仓库管
        candidates = ((ROOT / token,) if "/" in token
                      else (ROOT / token, ROOT / "scripts" / token))
        if any(p.exists() for p in candidates):
            continue
        if head in OPTIONAL_PATHS or token in OPTIONAL_PATHS:
            optional_missing.append(token)
        else:
            bad.append(token)
    return bad, optional_missing


def diff_entries(t: dict, got: dict) -> list[str]:
    problems = []
    if got.get("version") != t["version"]:
        problems.append(f"版本：README 说 {got.get('version') or '(未写)'}，CHANGELOG 最新是 {t['version']}")
    if t["rules_max"]:
        if got.get("rules_range_end") != t["rules_max"]:
            problems.append(
                f"工程规则：README 说 1–{got.get('rules_range_end') or '(未写)'}，"
                f"SKILL.md 实际最大编号是 {t['rules_max']}")
        if got.get("rules_next") != t["rules_next"]:
            problems.append(
                f"下一条规则：README 说 {got.get('rules_next') or '(未写)'}，应为 {t['rules_next']}")
    if got.get("cases") != t["cases"]:
        problems.append(f"黄金案例数：README 说 {got.get('cases') or '(未写)'}，fixtures/ 实际 {t['cases']}")
    if got.get("cases_pass") != t["cases"]:
        problems.append(f"黄金集 PASS 数：README 说 {got.get('cases_pass')}，案例总数是 {t['cases']}")
    if got.get("term_fixes") != t["term_fixes"]:
        problems.append(
            f"词级纠正：README 说 {got.get('term_fixes') or '(未写)'}，tm/term_fixes.json 实际 {t['term_fixes']}")
    if got.get("error_patterns") != t["error_patterns"]:
        problems.append(
            f"整句模式：README 说 {got.get('error_patterns') or '(未写)'}，"
            f"tm/error_patterns.json 实际 {t['error_patterns']}")
    return problems


def apply_fix(text: str, t: dict) -> tuple[str, list[str]]:
    """只替换四处机械数字；命中不到锚点就报错，绝不瞎改正文。"""
    block = read_state(text)
    new = block

    new, n1 = re.subn(r"(当前版本\s*\|\s*)`v\d+\.\d+(?:\.\d+)?`",
                      rf"\1`{t['version']}`", new)
    new, n2 = re.subn(r"(工程规则\s*\|\s*1–\*\*)\d+(\*\*\s*条（下一条 )\d+(）)",
                      rf"\g<1>{t['rules_max']}\g<2>{t['rules_next']}\g<3>", new)
    new, n3 = re.subn(r"(黄金测试集\s*\|\s*\*\*)\d+(\*\*\s*个案例，)\d+( PASS)",
                      rf"\g<1>{t['cases']}\g<2>{t['cases']}\g<3>", new)
    new, n4 = re.subn(r"(翻译记忆\s*\|\s*\*\*)\d+(\*\*\s*条词级纠正\s*\+\s*\*\*)\d+(\*\* 条整句模式)",
                      rf"\g<1>{t['term_fixes']}\g<2>{t['error_patterns']}\g<3>", new)

    hits = {"版本": n1, "规则": n2, "案例": n3, "记忆": n4}
    missed = [k for k, v in hits.items() if v == 0]
    if missed:
        raise CheckError(
            "状态表格式被改动，--fix 找不到这些锚点：" + "、".join(missed)
            + "\n请先恢复成 README 里的标准表格行，再跑 --fix。")
    return text.replace(block, new), [f"{k}×{v}" for k, v in hits.items()]


def main() -> int:
    ap = argparse.ArgumentParser(description="检查 README 与 CHANGELOG / SKILL / fixtures / tm 是否一致")
    ap.add_argument("--fix", action="store_true", help="自动刷新状态表里的四处机械数字")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    try:
        if not README.is_file():
            raise CheckError("README.md 不存在 —— 公开库必须有首页。")
        t = truth()
        text = README.read_text(encoding="utf-8")
        fixed = []
        if args.fix:
            text, fixed = apply_fix(text, t)
            README.write_text(text, encoding="utf-8")
        problems = diff_entries(t, parse_state(read_state(text)))
        if t["rules_max"]:
            body = sorted({int(n) for n in BODY_RULES_RE.findall(text)} - {t["rules_max"]})
            if body:
                problems.append(
                    "正文里的规则条数与 SKILL.md 不一致：" +
                    "、".join(f"写的是 {n}" for n in body) +
                    f"，应为 {t['rules_max']}（只改状态表不算更新）")
        bad_paths, optional_missing = check_paths(text)
        if bad_paths:
            problems.append("README 提到的这些路径不存在：" + "、".join(bad_paths))
    except CheckError as exc:
        print(f"[BLOCK] {exc}", file=sys.stderr)
        return 2

    ok = not problems
    if args.json:
        print(json.dumps({"ok": ok, "truth": t, "problems": problems,
                          "optional_missing": optional_missing, "fixed": fixed},
                         ensure_ascii=False, indent=2))
    else:
        if fixed:
            print(f"已自动刷新：{'，'.join(fixed)}")
        if optional_missing:
            # 公开 clone 里没有这些（.gitignore），说明清楚即可，不算过时
            print(f"提示：{'、'.join(optional_missing)} 仅本机真源存在（已 gitignore），"
                  "公开 clone 中缺失属正常")
        if ok:
            print(f"README 一致：版本 {t['version']} · 规则 1–{t['rules_max']} · "
                  f"案例 {t['cases']} · 记忆 {t['term_fixes']}+{t['error_patterns']}")
        else:
            print("README 已过时：", file=sys.stderr)
            for p in problems:
                print("  - " + p, file=sys.stderr)
            print("\n修复：python scripts/check_readme.py --fix"
                  "（数字）＋ 人工同步变动的说明文字", file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
