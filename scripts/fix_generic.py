#!/usr/bin/env python3
"""通用结构修补（第 2 版，参数化）：IZA 报告 / Cambridge / BMC 三种版式共用。

用法：python fix_generic.py <工作目录> <paper_key>
配置写在本文件 CONFIG 里（每篇论文一段），不再为每篇手写脚本。

做的事：
  1. 按几何顺序重排（列 → y），修正双栏阅读顺序；通栏块按 y 插回
  2. 删掉封面/出版社套话/页脚等指定块
  3. 参考文献：按 "N. " 切分，统一移到文末（Appendix 之前）
  4. 作者与单位 → 「注 A」字母脚注
  5. 续页题注 → 「Table N (Continued)」
  6. 文本卫生（断行连字符、空括号、多重空格）
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

CONFIG = {
    # IZA 讨论稿：前 3 页是封面与机构说明；单栏
    "3EHBMRQR": {
        "drop_pages": [1, 2, 3],
        "front_heading": None,
        "correspondence_rows": None,
        "refs_heading": "References",
        "refs_start_page": 21,
        "refs_pattern": r"(?<![0-9])(\d{1,3})\.\s+(?=[A-Z])",
        "appendix_heading": "Appendix",
        "two_column": False,
        "refs_mode": "blocks",
        "boilerplate": ["Any opinions expressed here", "The Institute for the Study of Labor (IZA) in Bonn",
                        "IZA Discussion Papers often represent", "Forschungsinstitut zur Zukunft der Arbeit"],
    },
    # Public Health Nutrition：双栏；参考文献在 p7 末尾 + p8
    "RL2P7Y6L": {
        "drop_pages": [],
        "front_heading": "Abstract",
        "correspondence_rows": (1, 0, 9999),
        "refs_heading": "References",
        "refs_start_page": 7,
        "refs_pattern": r"(?<![0-9])(\d{1,3})\.\s+(?=[A-Z])",
        "appendix_heading": "Supplementary material",
        "two_column": True,
        "boilerplate": ["For supplementary material accompanying this paper visit"],
    },
    # BMC 系统综述：双栏；参考文献 p7 末尾 + p8
    "RB7BRXRA": {
        "drop_pages": [],
        "front_heading": "Abstract",
        "correspondence_rows": (7, 0, 9999),
        "refs_heading": "References",
        "refs_start_page": 7,
        "refs_pattern": r"(?<![0-9])(\d{1,3})\.\s+(?=[A-Z])",
        "appendix_heading": None,
        "two_column": True,
        "boilerplate": ["Submit your next manuscript to BioMed Central",
                        "Convenient online submission", "Springer Nature remains neutral"],
    },
}

W = Path(sys.argv[1])
KEY = sys.argv[2]
CFG = CONFIG[KEY]

BJSON = json.loads((W / "work" / "blocks.json").read_text(encoding="utf-8"))
RJSON = json.loads((W / "work" / "regions.json").read_text(encoding="utf-8"))
SK = json.loads((W / "work" / "skeleton0.json").read_text(encoding="utf-8"))
doc = pymupdf.open(str(W / "source.pdf"))
sk_by_id = {b["id"]: b for b in SK["blocks"]}
report = {"dropped": [], "merged": [], "rebuilt": [], "kinds": {}}


def par(pno, idx):
    for pg in BJSON["pages"]:
        if pg["page"] == pno:
            return pg["paragraphs"][idx]
    raise KeyError((pno, idx))


# ─────────── 1. 每页按（列, y）重排 ───────────
ordered = []
for pg in BJSON["pages"]:
    pno = pg["page"]
    if pno in CFG["drop_pages"]:
        continue
    width = pg.get("width") or 595
    mid = width / 2.0
    regs = sorted([r for r in RJSON["pages"] if r["page"] == pno for r in r["regions"]],
                  key=lambda r: r["rect"][1])
    items = []
    for i, p in enumerate(pg["paragraphs"]):
        col = 0 if p["x"] < mid - 20 else 1
        items.append((col, p["y"], 0, "para", i))
    for r in regs:
        items.append((0, r["rect"][1], 1, "region", r))
    items.sort(key=lambda t: (t[0], t[1]))
    for _, _, _, tag, ref in items:
        ordered.append((tag, pno, ref))

# ─────────── 2. 组装 ───────────
final = []
for kind, pno, ref in ordered:
    if kind == "region":
        stem = Path(ref["file"]).stem
        b = next((dict(x) for x in SK["blocks"] if x.get("image", "").endswith(ref["file"])), None)
        if b is None:
            b = {"id": "p%d-%s" % (pno, stem),
                 "kind": {"table": "table", "figure": "figure", "equation": "equation"}[ref["kind"]],
                 "pages": [pno], "image": "assets/" + ref["file"],
                 "caption_en": (ref.get("caption_en") or "").strip(), "note_en": (ref.get("note_en") or "").strip()}
        final.append(b)
        continue
    bid = "p%d-%d" % (pno, ref)
    if bid in sk_by_id:
        final.append(dict(sk_by_id[bid]))
    else:
        final.append({"id": bid, "kind": "para", "pages": [pno], "en": par(pno, ref)["text"], "zh": ""})

# ─────────── 3. 删套话 ───────────
keep = []
for b in final:
    txt = (b.get("en") or "")
    if any(txt.strip().startswith(t) or t in txt[:120] for t in CFG["boilerplate"]):
        report["dropped"].append({"id": b["id"], "why": "boilerplate"})
        continue
    keep.append(b)
final = keep

# ─────────── 4. 参考文献：切分 + 移到文末 ───────────
rows = []
for pno in range(CFG["refs_start_page"], doc.page_count + 1):
    for blk in doc[pno - 1].get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                rows.append((round(ln["bbox"][1], 1), round(ln["bbox"][0], 1), t))
# 按页分批（y 会重置），每页再按（列,y）
raw = ""
pno_cur, buf = None, []
pages_chunks = []
for (y, x, t), pno_of in zip(rows, [None] * len(rows)):
    pass
# 简化：逐页取文本，按列排
def page_text(pno):
    rr = []
    for blk in doc[pno - 1].get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            t = "".join(s["text"] for s in ln["spans"]).strip()
            if t:
                rr.append((0 if ln["bbox"][0] < 290 else 1, ln["bbox"][1], ln["bbox"][0], t))
    rr.sort(key=lambda r: (r[0], r[1]))
    lines, cur = [], []
    for col, y, x, t in rr:
        if cur and (col != cur[-1][0] or abs(y - cur[-1][1]) > 3):
            lines.append(cur)
            cur = []
        cur.append((col, y, x, t))
    if cur:
        lines.append(cur)
    segs = [" ".join(t for _, _, _, t in sorted(ln, key=lambda r: r[2])) for ln in lines]
    out = ""
    for s in segs:
        s = s.strip()
        if not out:
            out = s
        elif out.endswith("-"):
            out = out[:-1] + s
        else:
            out = out + " " + s
    return out


raw = " ".join(page_text(p) for p in range(CFG["refs_start_page"], doc.page_count + 1))
raw = re.sub(r"\bPage \d+ of \d+", " ", raw)
if CFG.get("refs_mode") == "blocks":
    # 作者-年份制（如 IZA 讨论稿）：参考文献本身就是一段一条，直接标记不译，位置不动
    seen = False
    n_ref = 0
    for b in final:
        txt = (b.get("en") or "").strip()
        if b["kind"] == "heading" and txt.lower().rstrip(":") == CFG["refs_heading"].lower():
            seen = True
            b["no_translate"] = True
            b["zh"] = "参考文献"
            continue
        if seen and CFG["appendix_heading"] and b["kind"] == "heading" \
                and txt.lower().startswith(CFG["appendix_heading"].lower()):
            seen = False
            continue
        if seen and b["kind"] == "para" and len(txt) > 30:
            b["no_translate"] = True
            n_ref += 1
    report["rebuilt"].append({"id": "references", "what": "%d 段（作者-年份制，不译）" % n_ref})
    SK["blocks"] = final
    SK["meta"] = {"source_pdf": str(W / "source.pdf"), "glossary_version": "v0",
                  "layout": "段落交错 + 表格/插图原图保真"}
    (W / "work" / "skeleton_fx.json").write_text(json.dumps(SK, ensure_ascii=False, indent=2), encoding="utf-8")
    (W / "reports" / "fix_layout.json").write_text(json.dumps({**report, "kinds": dict(Counter(b["kind"] for b in final))},
                                                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(KEY, "blocks", len(final), dict(Counter(b["kind"] for b in final)), "refs(blocks)", n_ref)
    sys.exit(0)

starts, pos = [], 0
for n in range(1, 200):
    found = None
    for m in re.finditer(CFG["refs_pattern"], raw[pos:]):
        if int(m.group(1)) == n:
            found = (pos + m.start(), pos + m.end())
            break
    if not found:
        break
    starts.append((n, found[0], found[1]))
    pos = found[1]
refs = []
for k, (n, s, e) in enumerate(starts):
    end = starts[k + 1][1] if k + 1 < len(starts) else len(raw)
    body = raw[e:end].strip()
    if len(body) < 20:
        continue
    refs.append({"id": "ref-%d" % n, "kind": "para", "no_translate": True,
                 "pages": [CFG["refs_start_page"]], "en": "%d. %s" % (n, body), "zh": ""})
report["rebuilt"].append({"id": "references", "what": "%d 条" % len(refs)})

# 把文末的 refs / 相关块从正文里摘掉（保留 Appendix 之类）
ref_ids = set()
seen_refs_heading = False
for b in list(final):
    txt = (b.get("en") or "").strip()
    if b["kind"] == "heading" and txt.lower().rstrip(":") == CFG["refs_heading"].lower():
        ref_ids.add(b["id"])
        seen_refs_heading = True
        continue
    if seen_refs_heading:
        if CFG["appendix_heading"] and b["kind"] == "heading" and txt.lower().startswith(CFG["appendix_heading"].lower()):
            seen_refs_heading = False
            continue
        # 形如 "12. Author…" 的正文块也是参考文献
        if re.match(r"^\d{1,3}\.\s+[A-Z]", txt):
            ref_ids.add(b["id"])
            continue
        # 参考文献之后、Appendix 之前的一般段落也归入 refs 区
        if b["kind"] == "para" and len(txt) > 40:
            ref_ids.add(b["id"])
if ref_ids:
    report["dropped"].extend({"id": i, "why": "refs-rebuilt"} for i in sorted(ref_ids))
    final = [b for b in final if b["id"] not in ref_ids]

# 参考文献排在 Appendix 之前
idx_app = next((i for i, b in enumerate(final) if b["kind"] == "heading"
                and CFG["appendix_heading"] and (b.get("en") or "").strip().lower().startswith(CFG["appendix_heading"].lower())), None)
ref_heading = {"id": "refs-heading", "kind": "heading", "pages": [CFG["refs_start_page"]],
               "en": CFG["refs_heading"], "zh": "参考文献", "no_translate": True}
block_refs = [ref_heading] + refs
if idx_app is None:
    final.extend(block_refs)
else:
    final[idx_app:idx_app] = block_refs

# ─────────── 5. 作者 / 单位 → 注 A ───────────
fn = None
if CFG["correspondence_rows"]:
    pno, _, _ = CFG["correspondence_rows"]
    try:
        rows_aff = []
        for blk in doc[pno - 1].get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                t = "".join(s["text"] for s in ln["spans"]).strip()
                rows_aff.append((round(ln["bbox"][1], 1), t))
        rows_aff.sort()
        aff = " ".join(t for _, t in rows_aff)
    except Exception:
        aff = ""
    authors = next((b for b in final if b["kind"] == "para" and b["pages"] == [pno]
                    and re.search(r"[A-Z][a-z]+ [A-Z]\.", b.get("en") or "")), None)
    if authors:
        fn = {"id": "fn-a", "kind": "footnote", "marker": "A", "pages": [pno],
              "en": (authors.get("en") or "").strip(), "zh": ""}
        report["rebuilt"].append({"id": "fn-a", "what": "作者块 → 注 A"})
        final = [b for b in final if b["id"] != authors["id"]]
        final.insert(0, fn)

# ─────────── 6. 续页题注 ───────────
CONT = {}
seen = {}
for pg in RJSON["pages"]:
    for r in pg["regions"]:
        cap = (r.get("caption_en") or "").strip()
        m = re.match(r"^(Table\s+[A-Z]?\d+|Fig\.\s*\d+)", cap)
        if not m:
            continue
        head = re.sub(r"\s+", " ", m.group(1))
        seen[head] = seen.get(head, 0) + 1
        if seen[head] > 1:
            CONT[r["file"]] = head + " (Continued)"
for b in final:
    img = b.get("image", "")
    if img and Path(img).name in CONT:
        b["caption_en"] = CONT[Path(img).name]
report["rebuilt"].append({"id": "continuations", "what": str(CONT)})

# ─────────── 7. 合并续句 ───────────
TERM = re.compile(r"[.!?:;。！？：；]\s*$")
CLOS = re.compile(r"[”’)\"']\s*$")


def is_terminal(t):
    if TERM.search(t):
        return True
    m = CLOS.search(t)
    if m:
        core = t[:m.start()].rstrip()
        if core and core[-1] in ".!?。！？":
            return True
    return False


merged = []
for b in final:
    if (merged and b["kind"] == "para" and merged[-1]["kind"] == "para"
            and not merged[-1].get("no_translate") and not b.get("no_translate")):
        prev = merged[-1]
        pen, cen = (prev.get("en") or "").rstrip(), (b.get("en") or "").lstrip()
        if pen and cen and not is_terminal(pen) and cen[0].islower():
            prev["en"] = pen + " " + cen
            prev["pages"] = sorted(set(prev.get("pages", []) + b.get("pages", [])))
            prev["continued"] = True
            report["merged"].append({"into": prev["id"], "from": b["id"]})
            continue
    merged.append(b)

# ─────────── 8. 文本卫生 ───────────
vocab = Counter()
for b in merged:
    for f in ("en", "caption_en", "note_en"):
        for w in re.findall(r"[A-Za-z]{4,}", b.get(f) or ""):
            vocab[w.lower()] += 1


def hygiene(t):
    if not t:
        return t

    def rep(m):
        j = (m.group(1) + m.group(2)).lower()
        if vocab.get(j, 0) >= 1 and vocab.get(m.group(0).lower(), 0) == 0:
            return m.group(1) + m.group(2)
        return m.group(0)
    t = re.sub(r"\b([A-Za-z]{2,})-([a-z]{2,})\b", rep, t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"_{([^}]*)\s*}", r"_{\1}", t)
    return re.sub(r"[ \t\u00a0]{2,}", " ", t).strip()


for b in merged:
    for f in ("en", "caption_en", "note_en"):
        if b.get(f):
            b[f] = hygiene(b[f])

report["kinds"] = dict(Counter(b["kind"] for b in merged))
SK["blocks"] = merged
SK["meta"] = {"source_pdf": str(W / "source.pdf"), "glossary_version": "v0",
              "layout": "段落交错 + 表格/插图原图保真"}
(W / "work" / "skeleton_fx.json").write_text(json.dumps(SK, ensure_ascii=False, indent=2), encoding="utf-8")
(W / "reports" / "fix_layout.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(KEY, "blocks", len(merged), report["kinds"], "refs", len(refs), "merged", len(report["merged"]),
      "dropped", len(report["dropped"]))
