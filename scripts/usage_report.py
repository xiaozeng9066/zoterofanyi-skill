#!/usr/bin/env python3
"""用量自报：从工作目录里可测的产物推算 token 与耗时，生成可直接贴进 Zotero 笔记的 Markdown。

诚实边界（重要）：
  - **token 是估算值**：按英文 3.2 字符/token、中文 1.6 字符/token 折算，
    只覆盖"翻译与审校"这两类可数的产物，**不含 agent 编排上下文**（那部分只有服务商账单知道）。
  - **实际费用无法程序化获取**：必须从服务商用量页抄，用 --actual-cost 传入；
    拿不到就写"未记录"，不得编造。

用法：
  usage_report.py <工作目录> [--price-in 2 --price-out 8] [--actual-cost 8.5]
                  [--model "模型名"] [--pages 46] [--out reports/usage.md]
"""
import argparse, glob, json, os, sys
from datetime import datetime
from pathlib import Path

EN_CPT, ZH_CPT = 3.2, 1.6      # 字符/token 的保守折算


def _cfg_model() -> str | None:
    """从 local_config.json 读「当前模型」（换模型不靠硬编码/人记得填）。"""
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "local_config.json"
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            return (cfg.get("llm") or {}).get("model") or cfg.get("model")
    except Exception:
        pass
    return None


def _work_model(workdir) -> str | None:
    """从 work 目录读「本篇实际用的模型」——由派子代理那一刻的 Agent 写进 work/model.txt，
    换模型 / 换客户端都不靠人记得传 --model，也不靠 local_config.json 里那个没人填的字段。"""
    for p in (Path(workdir) / "model.txt", Path(workdir) / "work" / "model.txt"):
        try:
            if p.is_file():
                v = p.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except Exception:
            pass
    return None


def _pdf_pages(workdir) -> int | None:
    """从 work 目录自动数源 PDF 页数，免去 --pages 手动填。
    找不到 PDF 或读不了就返回 None，交给上层按「未记录」处理，不报错。"""
    cands = []
    w = Path(workdir)
    cands.append(w / "source.pdf")
    cands.append(w / "work" / "source.pdf")
    cands += sorted(w.glob("*.pdf")) if w.is_dir() else []
    cands += sorted((w / "work").glob("*.pdf")) if (w / "work").is_dir() else []
    seen = set()
    for p in cands:
        p = Path(p)
        if not p.is_file() or str(p) in seen:
            continue
        seen.add(str(p))
        try:
            import fitz  # PyMuPDF（本 skill 的主工具 Python 必带）
            with fitz.open(str(p)) as doc:
                return doc.page_count
        except Exception:
            continue
    return None


def classify(workdir):
    """按文件名递归归类产物：chunk_/trans_/review_/fix_。
    用 os.walk 而不是 glob 的 **/dir/，因为后者要求至少一层子目录（实测漏掉平铺的 chunks/）。"""
    out = {"chunk": [], "trans": [], "review": [], "fix": []}
    for root, _dirs, files in os.walk(workdir):
        for fn in files:
            for key, pre, suf in (("chunk", "chunk_", ".json"), ("trans", "trans_", ".json"),
                                  ("review", "review_", ".json"), ("fix", "fix_", ".json")):
                if fn.startswith(pre) and fn.endswith(suf):
                    out[key].append(os.path.join(root, fn))
    return out


def load_items(files, key="en"):
    total = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("items", []):
            total += len(it.get(key) or "")
    return total


def span_minutes_files(files):
    ts = [os.path.getmtime(f) for f in files if os.path.exists(f)]
    if len(ts) < 2:
        return None
    return (max(ts) - min(ts)) / 60


def dir_span_minutes(pattern):
    ts = []
    for p in glob.glob(pattern):
        for f in glob.glob(os.path.join(p, "**", "*"), recursive=True):
            try:
                ts.append(os.path.getmtime(f))
            except OSError:
                pass
    if len(ts) < 2:
        return None
    return (max(ts) - min(ts)) / 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", nargs="+", help="一个或多个工作目录（可把 review/ 单独传入）")
    ap.add_argument("--price-in", type=float, default=None, help="输入单价（元/百万 token）")
    ap.add_argument("--price-out", type=float, default=None, help="输出单价（元/百万 token）")
    ap.add_argument("--actual-cost", type=float, default=None, help="实际费用（元，从服务商用量页抄）")
    ap.add_argument("--model", default=None, help="实际用的模型名（默认读 local_config.json）")
    ap.add_argument("--pages", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--mineru-minutes", type=float, default=None)
    ap.add_argument("--trans-minutes", type=float, default=None)
    ap.add_argument("--review-minutes", type=float, default=None)
    a = ap.parse_args()
    W = Path(a.workdir[0])

    cls = {"chunk": [], "trans": [], "review": [], "fix": []}
    for wd in a.workdir:
        for k, v in classify(wd).items():
            cls[k] += v
    # 翻译（首译 + 重译 + 脚注，全部计入）
    tr_in = load_items(cls["chunk"], "en")
    tr_out = load_items(cls["trans"], "zh")
    tr_files = len(cls["trans"])
    # 审校
    rv_in = 0
    for f in cls["review"]:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        rv_in += sum(len(i.get("en", "")) + len(i.get("zh", "")) for i in d.get("items", []))
    rv_out = 0
    for f in cls["fix"]:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for x in d.get("fixes", []):
            rv_out += len(x.get("zh", "")) + len(x.get("reason", ""))

    t_in = tr_in / EN_CPT + rv_in / EN_CPT
    t_out = tr_out / ZH_CPT + rv_out / ZH_CPT

    m_mineru = a.mineru_minutes if a.mineru_minutes is not None else (
        dir_span_minutes(str(W / "mineru")) or dir_span_minutes(str(W / "**/mineru")))
    m_trans = a.trans_minutes if a.trans_minutes is not None else span_minutes_files(cls["trans"])
    m_review = a.review_minutes if a.review_minutes is not None else span_minutes_files(cls["fix"])

    # 页数：命令行 > 自动数 PDF；都没有则不报错、记「未记录」（页数只影响展示，不影响 token 估算）
    pages = a.pages if a.pages else _pdf_pages(W)
    pages_str = f"{pages}" if pages else "未记录"

    # 模型名解析顺序：命令行 > work/model.txt（派子代理时自动落盘）> local_config.json.llm.model；
    # 三级都空才显式告警（换模型/换客户端后不能静默「未记录」，否则费用单价无法按模型折算）
    model = a.model or _work_model(W) or _cfg_model()
    if not model:
        print("[WARN] 模型名三级探测均空（--model / work/model.txt / local_config.json 的 llm.model）。"
              "换模型/换客户端后，请让派翻译子代理的 Agent 把当前模型写进 <work>/model.txt，"
              "或直接 --model 显式传，否则费用单价无法按模型折算。", file=sys.stderr)
        model = "未记录"

    cost = None
    if a.price_in is not None and a.price_out is not None:
        cost = t_in / 1e6 * a.price_in + t_out / 1e6 * a.price_out

    rows = [
        ("原文页数", pages_str),
        ("翻译块数", f"{tr_files}"),
        ("翻译 输入/输出", f"≈ {t_in/1000:.1f}k / {tr_out/ZH_CPT/1000:.1f}k tokens（估算）"),
        ("审校 输入/输出", f"≈ {rv_in/EN_CPT/1000:.1f}k / {rv_out/ZH_CPT/1000:.1f}k tokens（估算）"),
        ("合计（翻译+审校）", f"≈ {t_in/1000:.1f}k 输入 / {t_out/1000:.1f}k 输出 tokens（估算）"),
        ("耗时 MinerU", f"{m_mineru:.0f} 分钟" + ("（人工提供）" if a.mineru_minutes is not None else "（按产物时间推断）") if m_mineru else "未记录"),
        ("耗时 翻译", f"{m_trans:.1f} 分钟" + ("（人工提供）" if a.trans_minutes is not None else "（按产物时间推断，若目录含多轮会偏大）") if m_trans else "未记录"),
        ("耗时 审校", f"{m_review:.1f} 分钟" + ("（人工提供）" if a.review_minutes is not None else "（按产物时间推断）") if m_review else "未记录"),
        ("模型", model),
        ("按单价估算成本", (f"¥{cost:.2f}（¥{a.price_in}/¥{a.price_out} 每百万 token）"
                        if cost is not None else "未提供单价")),
        ("**实际费用**", (f"**¥{a.actual_cost:.2f}**" if a.actual_cost is not None
                        else "**未记录（须从服务商用量页抄，勿估算）**")),
    ]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"### 用量自报（{stamp}）", "", "| 项 | 值 |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in rows]
    md += ["", "> token 为按字符折算的**估算值**，仅覆盖翻译与审校产物，**不含 agent 编排上下文**；",
           "> 实际费用必须从服务商账单页填写，本工具不编造数字。"]
    text = "\n".join(md)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入 {a.out}")


if __name__ == "__main__":
    main()
