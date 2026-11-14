#!/usr/bin/env python3
"""按篇记账：从 DSH 会话日志里把"这一篇"真实消耗的 token 与费用算出来。

为什么必须有它：`usage_report.py` 只数译文/审校产物（约 ¥0.13/篇），
而实测整条流水线约 ¥2.0/篇——差 15 倍，因为 99% 的 prompt 是缓存命中的编排上下文。
账单只按天汇总、拆不到篇，所以只能自己按会话记。

数据来源：`~/.dsh/sessions/<bucket>/<session-id>/session.v4.jsonl.zstd`
  - 每条 `assistant/message` 带 usage{inputTokens, outputTokens, cacheReadTokens}（按 message.id 去重）；
  - session 头有 cwd / parentSession / origin，子代理会话靠 parentSession 挂到主会话；
  - subagent/descriptor.label 给出"Translate chunk 003"这类标签，用于分会话归因。

归属判据：
  主会话 = origin 不是 subagent，且日志（前 400 行）里出现 --item-key；
  子会话 = parentSession 指向已入选会话（递归）。

费用：按每次调用的时间戳判定高峰/空闲（北京时间周一至周五 9-12、14-18 为高峰），逐调用累加。

用法：
  usage_from_sessions.py --workdir . --item-key X7BKI2HB --pages 19 \
      --out reports/usage.md --json reports/usage.json
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import zstandard
except ImportError:
    zstandard = None

SESSIONS_ROOT = Path.home() / ".dsh" / "sessions"
CST = timezone(timedelta(hours=8))

PRICES = {
    "deepseek-flash": {"peak": (2.0, 0.04, 8.0), "offpeak": (1.0, 0.02, 4.0)},
    "deepseek-v4-pro": {"peak": (9.0, 0.30, 27.0), "offpeak": (4.5, 0.15, 13.5)},
}


def read_lines(p):
    if zstandard is None:
        return subprocess.run(["zstd", "-dc", str(p)], capture_output=True) \
                         .stdout.decode("utf-8", "ignore").splitlines()
    with open(p, "rb") as f:
        return zstandard.ZstdDecompressor().stream_reader(f).read().decode("utf-8", "ignore").splitlines()


def is_peak(ts_ms):
    if not ts_ms:
        return False
    t = datetime.fromtimestamp(ts_ms / 1000, CST)
    return t.weekday() < 5 and (9 <= t.hour < 12 or 14 <= t.hour < 18)


def load_session(f):
    header, label, msgs = {}, "", {}
    lines = read_lines(f)
    for line in lines:
        if not header and '"type":"session"' in line[:40]:
            try:
                header = json.loads(line)
            except Exception:
                pass
            continue
        if not label and '"subagent/descriptor"' in line:
            try:
                label = (json.loads(line).get("data") or {}).get("label") or ""
            except Exception:
                label = ""
            continue
        if '"type":"assistant/message"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        data = rec.get("data") or {}
        m, u = data.get("message") or {}, data.get("usage") or {}
        if not m.get("id") or not u or m["id"] in msgs:
            continue
        reasoning = sum(len(c.get("text") or "") for c in (m.get("content") or [])
                        if c.get("type") == "reasoning")
        msgs[m["id"]] = {"time": rec.get("time") or header.get("createdAt") or 0,
                         "in": u.get("inputTokens", 0), "out": u.get("outputTokens", 0),
                         "cache": u.get("cacheReadTokens", 0), "reasoning_chars": reasoning}
    return header, label, list(msgs.values()), "\n".join(lines[:400])


def scan():
    out = []
    for bucket in sorted(SESSIONS_ROOT.iterdir()):
        if not bucket.is_dir():
            continue
        for sd in sorted(bucket.iterdir()):
            f = sd / "session.v4.jsonl.zstd"
            if not f.exists():
                continue
            try:
                header, label, msgs, head = load_session(f)
            except Exception:
                continue
            if not header or not msgs:
                continue
            out.append({"id": header.get("id"), "cwd": header.get("cwd", ""),
                        "parent": header.get("parentSession"), "origin": header.get("origin", ""),
                        "label": label, "msgs": msgs, "head": head})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--item-key", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--pages", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--since", default="", help="只统计此时间之后（ISO，如 2026-10-18T10:00）")
    ap.add_argument("--until", default="", help="只统计此时间之前（交付时刻，排除交付后的返工/答疑）")
    ap.add_argument("--peak", default="")
    ap.add_argument("--offpeak", default="")
    a = ap.parse_args()

    workdir = Path(a.workdir).expanduser().resolve()
    sessions = scan()
    if not sessions:
        sys.exit("没找到任何会话日志（~/.dsh/sessions 为空？）")

    if a.session:
        mains = [s for s in sessions if s["id"] == a.session]
    else:
        needle = a.item_key or workdir.name
        mains = [s for s in sessions if s["origin"] != "subagent" and needle in s["head"]]
        if not mains:
            mains = [s for s in sessions if s["origin"] != "subagent"
                     and workdir.name in (s["cwd"] or "") and needle in s["head"]]
    if not mains:
        sys.exit("没定位到主会话（item-key=%r）；可用 --session 指定" % a.item_key)

    ids = {s["id"] for s in mains}
    changed = True
    while changed:
        changed = False
        for s in sessions:
            if s["id"] not in ids and s["parent"] in ids:
                ids.add(s["id"]); changed = True
    picked = [s for s in sessions if s["id"] in ids]

    price = dict(PRICES.get(a.model, PRICES["deepseek-flash"]))
    if a.peak:
        price["peak"] = tuple(float(x) for x in a.peak.split("/"))
    if a.offpeak:
        price["offpeak"] = tuple(float(x) for x in a.offpeak.split("/"))

    def in_window(ts):
        if not a.since and not a.until:
            return True
        t = datetime.fromtimestamp((ts or 0) / 1000, CST)
        if a.since and t < datetime.fromisoformat(a.since).replace(tzinfo=CST):
            return False
        if a.until and t > datetime.fromisoformat(a.until).replace(tzinfo=CST):
            return False
        return True

    per = []
    for s in picked:
        c = {"id": s["id"], "label": s["label"] or ("主会话" if s["origin"] != "subagent" else "子代理"),
             "role": "编排" if s["origin"] != "subagent" else "子代理",
             "calls": 0, "in": 0, "out": 0, "cache": 0, "cost_peak": 0.0, "cost_offpeak": 0.0,
             "reasoning_chars": 0}
        for m in s["msgs"]:
            if not in_window(m["time"]):
                continue
            c["calls"] += 1
            c["in"] += m["in"]; c["out"] += m["out"]; c["cache"] += m["cache"]
            c["reasoning_chars"] += m["reasoning_chars"]
            band = "peak" if is_peak(m["time"]) else "offpeak"
            pin, pcache, pout = price[band]
            cost = m["in"] / 1e6 * pin + m["cache"] / 1e6 * pcache + m["out"] / 1e6 * pout
            c["cost_" + band] += cost
        per.append(c)

    per.sort(key=lambda x: -(x["cost_peak"] + x["cost_offpeak"]))
    orch = [x for x in per if x["role"] == "编排"]
    subs = [x for x in per if x["role"] == "子代理"]

    def agg(rows, k):
        return sum(r[k] for r in rows)

    oi, oo, oca = agg(orch, "in"), agg(orch, "out"), agg(orch, "cache")
    si, so, sca = agg(subs, "in"), agg(subs, "out"), agg(subs, "cache")
    ocp, oco = agg(orch, "cost_peak"), agg(orch, "cost_offpeak")
    scp, sco = agg(subs, "cost_peak"), agg(subs, "cost_offpeak")
    tin, tout, tcache = oi + si, oo + so, oca + sca
    cost_peak, cost_off = ocp + scp, oco + sco
    cost = cost_peak + cost_off
    prompt = tin + tcache
    hit = tcache / prompt * 100 if prompt else 0.0
    reason = sum(r["reasoning_chars"] for r in per)

    md = ["# 用量与费用 · 会话实测\n",
          "> %d 个会话（编排 %d + 子代理 %d）；价格 `%s` 空闲 %s、高峰 %s 元每百万 tokens；"
          "高峰/空闲按每次调用时间戳判定。\n" % (
              len(picked), len(orch), len(subs), a.model,
              "/".join(map(str, price["offpeak"])), "/".join(map(str, price["peak"]))),
          "| 口径 | 编排（主会话） | 子代理 | 合计 |", "|---|---|---|---|",
          "| 模型调用 | %d | %d | **%d** |" % (agg(orch, "calls"), agg(subs, "calls"), agg(per, "calls")),
          "| 输入（缓存未命中） | %s | %s | **%s** |" % (f"{oi:,}", f"{si:,}", f"{tin:,}"),
          "| 输入（缓存命中） | %s | %s | **%s** |" % (f"{oca:,}", f"{sca:,}", f"{tcache:,}"),
          "| 输出 | %s | %s | **%s** |" % (f"{oo:,}", f"{so:,}", f"{tout:,}"),
          "| prompt 总量 | %s | %s | **%s**（命中率 %.1f%%） |" % (f"{oi+oca:,}", f"{si+sca:,}", f"{prompt:,}", hit),
          "| 费用 | ¥%.2f | ¥%.2f | **¥%.2f** |" % (oco + ocp, sco + scp, cost),
          "| 　高峰 / 空闲 | ¥%.2f / ¥%.2f | ¥%.2f / ¥%.2f | ¥%.2f / ¥%.2f |" % (ocp, oco, scp, sco, cost_peak, cost_off)]
    if a.pages:
        md.append("\n**单页 ≈ ¥%.3f**（%d 页）；子代理占 %.0f%%，编排占 %.0f%%。" % (
            cost / a.pages, a.pages,
            (sco + scp) / cost * 100 if cost else 0, (oco + ocp) / cost * 100 if cost else 0))
    if reason:
        md.append("\n思考过程文本 %s 字符 ≈ %s tokens，约占全部输出 token 的 %.0f%% —— "
                  "机械步骤（翻译/审校/跑脚本）关思考是最大单项杠杆。" % (
                      f"{reason:,}", f"{reason/3.5:,.0f}", reason / 3.5 / max(tout, 1) * 100))
    md += ["", "## 分会话明细（按费用降序）", "",
           "| 会话 | 角色 | 调用 | 未命中输入 | 缓存命中 | 输出 | 费用 |", "|---|---|---|---|---|---|---|"]
    for r in per[:18]:
        md.append("| %s | %s | %d | %s | %s | %s | ¥%.2f |" % (
            (r["label"] or r["id"][:8])[:30], r["role"], r["calls"],
            f"{r['in']:,}", f"{r['cache']:,}", f"{r['out']:,}", r["cost_offpeak"] + r["cost_peak"]))
    text = "\n".join(md) + "\n"

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print("用量报告 -> %s" % a.out)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({
            "model": a.model, "price": price,
            "total": {"sessions": len(picked), "calls": agg(per, "calls"), "in": tin, "cache": tcache,
                      "out": tout, "prompt": prompt, "cache_hit_pct": round(hit, 1),
                      "cost": round(cost, 4), "cost_peak": round(cost_peak, 4),
                      "cost_off": round(cost_off, 4), "reasoning_chars": reason},
            "sessions": per,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("用量 JSON -> %s" % a.json)
    print(text)


if __name__ == "__main__":
    main()
