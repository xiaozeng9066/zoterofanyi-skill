#!/usr/bin/env python3
"""metrics.py —— zoterofanyi 的度量闭环：让「迭代有没有变好」可量化。

为什么要有它
------------
CHANGELOG 一直只记「做了什么」，从没记「改了之后快了多少 / 省了多少 / 质量涨了没有」。
结果就是：不知道哪些规则是真有用、哪些是废的、哪些该补。这违背用户「多快好省」的目标。

本脚本把度量分三层：
  1) 埋点：pipeline.py 每个阶段自动把可测指标写进 <work>/reports/metrics.json（零人工）。
  2) 聚合：`metrics.py report` 扫所有论文工作区，产出一张跨篇对比表（CSV/终端表）。
  3) 回填：token 与成本无法程序化获取，`metrics.py cost` 留 --actual-cost 入口，
          从服务商用量页抄一次就永久进库，之后跨篇/跨版本可对比。

可自动测的硬指标（零误差）：页数、块数、翻译条数、修订条数、图片数、脚注数、
门禁 BLOCK/WARN、QA/规则 FAIL/WARN、中文字符数、各阶段耗时、skill 版本。
需人工回填的软指标（--actual-cost / --tokens）：真实 token、真实费用。

不追踪版本 → 迭代就是盲改；不对比 token → 「省 token」只是口号。
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent

EN_CPT = 3.2   # 英文 字符/token 保守折算（与 usage_report.py 一致）
ZH_CPT = 1.6   # 中文 字符/token


# ────────────────────────────── 埋点：自动采集 ──────────────────────────────
def _first_json(*paths):
    for p in paths:
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _blocks(path):
    d = _first_json(path)
    if isinstance(d, dict):
        return d.get("blocks") or []
    return d or []


def _count_zh(text):
    return sum(1 for c in (text or "") if "\u4e00" <= c <= "\u9fff")


def _gate_counts(path):
    d = _first_json(path) or {}
    c = d.get("counts") or {}
    issues = d.get("issues") or []
    blk = c.get("BLOCK")
    warn = c.get("WARN")
    if blk is None:
        blk = sum(1 for x in issues if str(x.get("level", "")).upper() == "BLOCK")
        warn = sum(1 for x in issues if str(x.get("level", "")).upper() == "WARN")
    return int(blk or 0), int(warn or 0)


def _count_qa_rule(path):
    """统计 qa.py / rules_check.py 的 FAIL / WARN 数（兼容 issues 为 [level,msg] 数组对）。"""
    d = _first_json(path)
    if not d:
        return 0, 0
    fail = warn = 0
    if "fail" in d and "warn" in d and isinstance(d["fail"], int):
        # 新版有自报计数，直接信它
        return int(d["fail"]), int(d["warn"])
    issues = d.get("issues") or []
    if not issues and isinstance(d.get("results"), list):
        for res in d["results"]:
            issues += (res.get("issues") or [])
    for x in issues:
        if isinstance(x, dict):
            lv = str(x.get("level", "")).upper()
        elif isinstance(x, (list, tuple)) and x:
            lv = str(x[0]).upper()
        else:
            continue
        if lv in ("FAIL", "BLOCK"):
            fail += 1
        elif lv == "WARN":
            warn += 1
    return fail, warn


def collect(work, skill_version="unknown", label=""):
    """从已完成的工作区采集一套硬指标。返回 dict。"""
    work = Path(work)
    r = {"key": work.name, "label": label, "skill_version": skill_version,
         "collected_at": datetime.now().astimezone().isoformat(timespec="seconds")}

    # 源 PDF
    pdf = work / "source.pdf"
    if pdf.exists():
        r["pdf_bytes"] = pdf.stat().st_size

    # 最终骨架 / 双语
    final = work / "work" / "bilingual_final.json"
    skeleton = work / "work" / "skeleton_fn.json"
    blocks = _blocks(final) or _blocks(skeleton)
    r["blocks"] = len(blocks)
    r["cjk_chars"] = sum(_count_zh(b.get("zh") or "") for b in blocks)
    r["images"] = sum(1 for b in blocks if b.get("image"))
    r["no_translate"] = sum(1 for b in blocks if b.get("no_translate"))

    # 翻译条数（chunks）
    n_items = 0
    chunk_dir = work / "work" / "chunks"
    if chunk_dir.exists():
        for cf in sorted(chunk_dir.glob("chunk_*.json")):
            d = _first_json(cf)
            if isinstance(d, dict):
                n_items += len(d.get("items") or [])
    r["translated_items"] = n_items

    # 修订数（fixes）
    n_fix = 0
    fix_dir = work / "work" / "fixes"
    if fix_dir.exists():
        for ff in sorted(fix_dir.glob("fix_*.json")):
            d = _first_json(ff)
            if isinstance(d, dict):
                n_fix += len(d.get("fixes") or [])
    r["fixes"] = n_fix

    # 脚注数
    fn = _first_json(work / "work" / "footnotes.json")
    if isinstance(fn, list):
        r["footnotes"] = len(fn)
    elif isinstance(fn, dict):
        r["footnotes"] = len(fn.get("footnotes") or fn.get("items") or [])

    # 门禁
    r["gate1_block"], r["gate1_warn"] = _gate_counts(work / "reports" / "preflight_skeleton.json")
    r["gate2_block"], r["gate2_warn"] = _gate_counts(work / "reports" / "preflight_full.json")
    r["qa_fail"], r["qa_warn"] = _count_qa_rule(work / "reports" / "qa.json")
    r["rule_fail"], r["rule_warn"] = _count_qa_rule(work / "reports" / "rules_check.json")

    # 成品 PDF
    out_pdfs = list(work.glob("*_双语.pdf"))
    if out_pdfs:
        p = out_pdfs[0]
        r["pdf_pages"] = _pdf_pages(p)
        r["pdf_out_bytes"] = p.stat().st_size
        r["pdf_name"] = p.name

    # 耗时：优先用 pipeline 记录的 wall-clock（timings.json），否则退回 mtime 跨度
    timings = _first_json(work / "reports" / "timings.json")
    if isinstance(timings, dict):
        for stage in ("prepare", "check", "finish"):
            v = timings.get(stage)
            if isinstance(v, (int, float)):
                r["minutes_" + stage] = round(v / 60.0, 2)
        known = [timings.get(s) for s in ("prepare", "check", "finish")
                 if isinstance(timings.get(s), (int, float))]
        if known:
            r["minutes_total"] = round(sum(known) / 60.0, 2)
    if "minutes_total" not in r:
        r["minutes_total"] = _span_minutes(work)
    # MinerU 单独一段（run_mineru 产物跨度）
    r["minutes_mineru"] = _span_minutes(work / "work" / "mineru")

    return r


def _pdf_pages(pdf_path):
    try:
        import re
        # 轻量：从 PDF 里数 /Type /Page（不依赖 PyMuPDF，任何解释器可跑）
        raw = pdf_path.read_bytes()
        return len(re.findall(rb"/Type\s*/Page[^s]", raw))
    except Exception:
        return 0


def _span_minutes(path):
    """目录内文件 mtime 跨度的分钟数；不存在或单文件返回 0。

    忽略异常时间戳（早于 2000-01-01 的损坏 mtime、或晚于当前时间的），
    否则一个 mtime=1980 的坏文件会把跨度拉到几百万分钟。
    """
    path = Path(path)
    if not path.exists():
        return 0.0
    now = time.time()
    floor = 946684800  # 2000-01-01 00:00:00 UTC
    ts = [os.path.getmtime(str(p)) for p in path.rglob("*") if p.is_file()]
    ts = [t for t in ts if floor <= t <= now]
    if not ts:
        return 0.0
    return max(0.0, (max(ts) - min(ts)) / 60.0)


# ────────────────────────────── 回填：token / 成本 ──────────────────────────────
def _metrics_path(work):
    return Path(work) / "reports" / "metrics.json"


def _load(work):
    p = _metrics_path(work)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(work, data):
    p = _metrics_path(work)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def record(work, data):
    """合并采集结果写入 metrics.json（保留已回填的 token/cost 字段）。"""
    old = _load(work)
    merged = {**old, **data}
    return _save(work, merged)


def estimate_tokens(data):
    """按字符折算 token（与 usage_report.py 同口径，标注估算）。"""
    cjk = data.get("cjk_chars", 0)
    n_items = data.get("translated_items", 0)
    # 粗略：输出 ≈ 中文字符/1.6；输入 ≈ 英文原文（无英文总量则按条目×平均估）
    out_tokens = cjk / ZH_CPT
    # 输入 token 无英文总量时无法精算，给出保守估计：条目数 × ~200 字符英文
    in_tokens = n_items * 200 / EN_CPT
    return {"tokens_out_est": round(out_tokens), "tokens_in_est": round(in_tokens)}


# ────────────────────────────── 聚合：跨篇对比 ──────────────────────────────
def _all_workspaces(runs_dir):
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    out = []
    for d in sorted(runs_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if (d / "reports").exists() or (d / "work").exists():
            out.append(d)
    return out


def _discover_workspace(arg):
    """arg 可以是具体工作区，或 zotero-translate 根目录（扫描其子目录）。"""
    p = Path(arg)
    if p.name == "zotero-translate" or not (p / "reports").exists() and not (p / "work").exists():
        if p.exists() and p.is_dir() and any(c.is_dir() for c in p.iterdir()):
            return _all_workspaces(p)
        return [p] if p.exists() else []
    return [p]


def cmd_report(a):
    """扫多个工作区，产出跨篇对比表（终端 + CSV）。"""
    works = []
    for arg in a.workdir:
        works += _discover_workspace(arg)
    rows = []
    for w in works:
        m = _load(w)
        if m:
            m["_work"] = str(w)
            rows.append(m)
    if not rows:
        print("未找到任何 metrics.json；先跑 pipeline 或 `metrics.py collect`。")
        return 1

    cols = ["key", "label", "skill_version", "blocks", "translated_items", "cjk_chars",
            "fixes", "images", "footnotes", "gate1_block", "gate2_block",
            "qa_fail", "rule_fail", "pdf_pages", "minutes_total",
            "tokens_in_est", "tokens_out_est", "actual_cost"]
    header = ["条目", "标注", "版本", "块", "译条", "中文字符", "修订", "图", "脚注",
              "门1B", "门2B", "QA-F", "规-F", "页", "耗时m",
              "tok入估", "tok出估", "实际费¥"]

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(cols)
            for r in rows:
                wr.writerow([r.get(c, "") for c in cols])
        print("已写 CSV：%s（%d 篇）" % (a.csv, len(rows)))

    # 终端表
    print("%-10s %-18s %-7s %-5s %-5s %-8s %-5s %-3s %-4s %-4s %-4s %-4s %-4s %-4s %-7s %-8s %-8s %-8s" % tuple(header))
    print("-" * 150)
    for r in sorted(rows, key=lambda x: str(x.get("key"))):
        mm = r.get("minutes_total")
        mm_s = ("%.1f" % mm) if isinstance(mm, (int, float)) else (mm or "")
        print("%-10s %-18s %-7s %-5s %-5s %-8s %-5s %-3s %-4s %-4s %-4s %-4s %-4s %-4s %-7s %-8s %-8s %-8s" % (
            r.get("key", "")[:10],
            str(r.get("label", ""))[:18],
            r.get("skill_version", ""),
            r.get("blocks", ""), r.get("translated_items", ""),
            r.get("cjk_chars", ""), r.get("fixes", ""),
            r.get("images", ""), r.get("footnotes", ""),
            r.get("gate1_block", ""), r.get("gate2_block", ""),
            r.get("qa_fail", ""), r.get("rule_fail", ""),
            r.get("pdf_pages", ""), mm_s,
            r.get("tokens_in_est", ""), r.get("tokens_out_est", ""),
            r.get("actual_cost", "")))
    print("\n说明：门1B/门2B = 两道门禁 BLOCK 数（应 0）；QA-F/规-F = QA 与规则 FAIL 数；")
    print("      耗时m = pipeline 各阶段 wall-clock 之和（prepare+check+finish，不含翻译/审校子代理）。")
    print("      tok 为字符折算估算；实际费¥ 需 `metrics.py cost` 回填。")
    return 0


def cmd_cost(a):
    """回填某篇的真实 token / 成本（从服务商用量页抄）。"""
    if a.workdir is None:
        print("需要 --workdir 指定工作区。")
        return 1
    m = _load(a.workdir)
    changed = False
    if a.tokens_in is not None:
        m["tokens_in_actual"] = a.tokens_in; changed = True
    if a.tokens_out is not None:
        m["tokens_out_actual"] = a.tokens_out; changed = True
    if a.actual_cost is not None:
        m["actual_cost"] = a.actual_cost; changed = True
    if a.model:
        m["model"] = a.model; changed = True
    if not changed:
        print("未提供任何回填值（--tokens-in/--tokens-out/--actual-cost/--model）。")
        return 1
    _save(a.workdir, m)
    print("已回填：%s" % _metrics_path(a.workdir))
    return 0


def cmd_collect(a):
    """对指定工作区采集一次并落盘（通常由 pipeline 自动调用，手动补采也走这里）。"""
    n = 0
    for w in _discover_workspace(a.workdir):
        data = collect(w, skill_version=a.skill_version or _git_version(), label=a.label)
        data.update(estimate_tokens(data))
        record(w, data)
        print("采集：%s（%d 块 / %d 译条 / %d 修订 / 门禁 %d/%d）" % (
            data["key"], data.get("blocks", 0), data.get("translated_items", 0),
            data.get("fixes", 0), data.get("gate1_block", 0), data.get("gate2_block", 0)))
        n += 1
    return 0 if n else 1


def _git_version():
    try:
        import subprocess
        out = subprocess.run(["git", "-C", str(SKILL), "describe", "--tags", "--always"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description="zoterofanyi 度量闭环：埋点 / 聚合 / 回填")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="采集工作区硬指标并落盘")
    c.add_argument("workdir", help="工作区目录，或 zotero-translate 根目录（扫描子目录）")
    c.add_argument("--label", default="", help="人类可读标注，如论文简称")
    c.add_argument("--skill-version", default="", help="覆盖 skill 版本（默认 git describe）")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("report", help="跨篇对比表")
    r.add_argument("workdir", nargs="+", help="一个或多个工作区 / zotero-translate 根目录")
    r.add_argument("--csv", default="", help="同时写 CSV 文件")
    r.set_defaults(func=cmd_report)

    c2 = sub.add_parser("cost", help="回填真实 token / 成本（从用量页抄）")
    c2.add_argument("--workdir", help="工作区目录")
    c2.add_argument("--tokens-in", type=int, help="真实输入 token")
    c2.add_argument("--tokens-out", type=int, help="真实输出 token")
    c2.add_argument("--actual-cost", type=float, help="实际费用（元）")
    c2.add_argument("--model", help="实际用的模型名")
    c2.set_defaults(func=cmd_cost)

    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
