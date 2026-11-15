#!/usr/bin/env python3
"""iterate_audit.py —— 自动迭代审计：让「迭代有没有变好」变成每次收尾的强制产出。

背景
----
迭代协议（七步）一直写在文档里，但靠 Agent 自觉执行——不可靠。用户要的是
「保证在过程中自动迭代和优化」。本脚本把这件事从「靠记性」变成「靠数据驱动」：

每篇收尾时，自动把本篇 metrics 与历史基线对比，产出三样东西：
  1) 趋势结论：门禁 / 修订率 / token 效率 / 覆盖率 比历史更好还是更差；
  2) 迭代建议：哪些规则该修（尺子误报）、该加（新版面）、该删（长期零命中）；
  3) 一份可读报告 reports/iterate_audit.md，随 iteration 提交一起入库。

它不替你做决定，但保证「每篇结束时你手里一定有一份基于数据的迭代待办」，
而不是靠记忆去回忆「这篇有没有值得沉淀的东西」。

用法:
  python iterate_audit.py --work <论文工作区> [--runs <zotero-translate 根>]
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent

# 规则命中率：rules_check.py 报告里长期零命中的规则，提示「该规则可能已失效，考虑精简」。
# 这个阈值需要跨篇统计，本脚本只做单篇视角 + 历史 metrics 的横向对比。


def _load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _metrics(work):
    return _load_json(Path(work) / "reports" / "metrics.json") or {}


def _all_metrics(runs_dir):
    runs = Path(runs_dir)
    out = []
    if not runs.exists():
        return out
    for d in sorted(runs.iterdir()):
        m = _load_json(d / "reports" / "metrics.json")
        if m:
            m["_key"] = d.name
            out.append(m)
    return out


def _avg(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def _fmt(x, nd=1):
    return "—" if x is None else ("%.*f" % (nd, x))


def audit(work, runs_dir):
    work = Path(work)
    cur = _metrics(work)
    hist = _all_metrics(runs_dir)
    # 只和「完整新结构」的篇对比（blocks>0 且有修订字段），排除旧 DSH 残缺数据，
    # 否则旧数据缺字段会把均值拉失真，得出「本篇变差」的假象。
    def complete(m):
        return (m.get("blocks") or 0) > 0 and m.get("fixes") is not None
    others = [m for m in hist if m.get("_key") != work.name and complete(m)]

    L = ["# 迭代审计 · %s" % (cur.get("label") or work.name),
         "", "> 由 iterate_audit.py 自动生成：本篇指标 vs 历史基线，产出量化趋势 + 迭代建议。",
         "> 这不是替你做决定，而是保证每次收尾都有一份基于数据的迭代待办。",
         "", "生成时间：%s" % datetime.now().astimezone().isoformat(timespec="seconds"), ""]

    # ── 一、本篇硬指标 ──
    L += ["## 一、本篇硬指标", ""]
    rows = [
        ("块数", cur.get("blocks")),
        ("翻译条数", cur.get("translated_items")),
        ("中文字符", cur.get("cjk_chars")),
        ("修订条数", cur.get("fixes")),
        ("图片数", cur.get("images")),
        ("脚注数", cur.get("footnotes")),
        ("Gate1 BLOCK", cur.get("gate1_block")),
        ("Gate2 BLOCK", cur.get("gate2_block")),
        ("QA FAIL", cur.get("qa_fail")),
        ("规则 FAIL", cur.get("rule_fail")),
        ("成品页数", cur.get("pdf_pages")),
        ("token 出估", cur.get("tokens_out_est")),
        ("实际费用¥", cur.get("actual_cost")),
    ]
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    for k, v in rows:
        L.append("| %s | %s |" % (k, v if v is not None else "—"))
    L.append("")

    # ── 二、趋势对比（相对历史均值）──
    L += ["## 二、趋势对比（相对历史 %d 篇均值）" % len(others), ""]
    if not others:
        L.append("历史无其他 metrics.json，暂无基线可对比。从第二篇起自动有趋势。")
    else:
        L.append("| 指标 | 本篇 | 历史均值 | 方向 |")
        L.append("|---|---|---|---|")
        def trend(name, key, higher_better, nd=1):
            cur_v = cur.get(key)
            hist_v = _avg([m.get(key) for m in others])
            if cur_v is None or hist_v is None:
                return
            delta = cur_v - hist_v
            if higher_better:
                arrow = "▲ 更好" if delta >= 0 else "▼ 更差"
            else:
                arrow = "▼ 更差" if delta > 0 else "▲ 更好"
            L.append("| %s | %s | %s | %s |" % (name, _fmt(cur_v, nd), _fmt(hist_v, nd), arrow))
        trend("修订数", "fixes", higher_better=False, nd=0)
        trend("QA FAIL", "qa_fail", higher_better=False, nd=0)
        trend("规则 FAIL", "rule_fail", higher_better=False, nd=0)
        trend("Gate2 BLOCK", "gate2_block", higher_better=False, nd=0)
        trend("token 出估", "tokens_out_est", higher_better=False, nd=0)
        L.append("")
        L.append("> 趋势只做「值得看一眼」的提醒：修订数/FAIL 下降通常代表流水线或规则更准了；")
        L.append("> token 下降代表更省。但单篇波动大，需多篇累计才有统计意义。")

    # ── 三、自动迭代建议 ──
    L += ["", "## 三、自动迭代建议", ""]
    sugg = []

    # 门禁未过 → 最高优先级
    if cur.get("gate1_block") or cur.get("gate2_block"):
        sugg.append("⚠ **门禁未 0 BLOCK**：先修版面/译文，再谈迭代。当前 BLOCK：Gate1=%s Gate2=%s"
                    % (cur.get("gate1_block"), cur.get("gate2_block")))
    else:
        sugg.append("✅ 两道门禁均 0 BLOCK，可正常收尾。")

    # QA/规则 FAIL 是尺子误报还是译文错？提示先自证
    qa_fail = cur.get("qa_fail") or 0
    rule_fail = cur.get("rule_fail") or 0
    if qa_fail or rule_fail:
        sugg.append("⚠ 有 FAIL（QA %d / 规则 %d）：按迭代协议先自证「是尺子错还是译文错」。"
                    "尺子错→修检查器；译文错→加进 tm/；新版面→新增编号规则。"
                    % (qa_fail, rule_fail))

    # 修订数异常高 → 提示译前指南或术语表可能没覆盖到
    fixes = cur.get("fixes") or 0
    n_items = cur.get("translated_items") or 1
    fix_rate = fixes / n_items if n_items else 0
    if fix_rate > 0.3:
        sugg.append("⚠ 修订率 %.0f%%（%d/%d）偏高：审校改了太多，说明译前指南/术语表/翻译记忆"
                    "可能没覆盖到本篇的领域词。考虑把高频修正回流 tm/，或补术语表。"
                    % (fix_rate * 100, fixes, n_items))
    elif fix_rate > 0:
        sugg.append("· 修订率 %.0f%%（%d/%d），正常区间。" % (fix_rate * 100, fixes, n_items))
    else:
        sugg.append("· 本篇 0 修订：要么译文一次到位，要么审校没抓到问题（后者需警惕）。")

    # 是否有图片/脚注等版面特征值得沉淀规则
    if cur.get("images"):
        sugg.append("· 本篇有 %d 个图片块（公式/表/图），若出现新排版特例，考虑新增规则或 fixture。"
                    % cur.get("images"))

    L += [("- " + s) for s in sugg]
    L += ["", "---",
          "> 下一步（手动）：按上面建议走迭代协议的对应步骤，改文件 → 回流 tm → 加 fixture →",
          "> CHANGELOG 追加 → `check_readme.py --fix` → `iterate_commit.py -v vX.Y`。",
          "> 本报告随每次迭代提交入库，形成「每篇都留下迭代轨迹」的闭环。"]

    out = Path(work) / "reports" / "iterate_audit.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="自动迭代审计：本篇指标 vs 历史，产出趋势 + 建议")
    ap.add_argument("--work", required=True, help="论文工作区目录")
    ap.add_argument("--runs", default="", help="zotero-translate 根目录（默认 work 的上一级）")
    a = ap.parse_args()

    work = Path(a.work).resolve()
    runs_dir = Path(a.runs).resolve() if a.runs else work.parent
    out = audit(work, runs_dir)
    print("OK -> %s" % out)
    # 打印摘要
    print("\n".join(Path(out).read_text(encoding="utf-8").splitlines()[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
