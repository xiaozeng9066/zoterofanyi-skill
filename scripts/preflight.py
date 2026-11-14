#!/usr/bin/env python3
"""交付前自检：把本篇踩过的 8 类"返工"变成一次自动检查。

两个阶段（--stage）：
  skeleton：**翻译前**验骨架——抽取残渣、图注混进正文、跨页续句未合并、图片/图注缺失、
            字符卫生（零宽/私用区/URL 断行/上标未闭合）、参考文献区误标。
            **这一步跑通再花翻译钱**，是最省的顺序。
  full（默认）：译完 + 渲染后再验——覆盖、图注编号一致、汉字夹数字、空白页/图片数/中文文本层。

用法：
  preflight.py work/skeleton_fn.json --stage skeleton --assets work/assets
  preflight.py work/bilingual_idx.json --pdf out.pdf --assets work/assets --out reports/preflight.json

退出码：有 BLOCK 则非 0。
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
CAP_EN = re.compile(r"^\s*(Fig(?:ure)?\.?|Table)\s*([A-Z]?\d+)", re.I)
CAP_ZH = re.compile(r"^\s*(图|表)\s*([A-Z]?\d+)")
CJK_DIGIT = re.compile(r"[\u4e00-\u9fff](\d{1,3})[\u4e00-\u9fff]")
URL_SPLIT = re.compile(r"https?://\S+\s+(?:[\w-]+\.)+(?:com|org|uk|cn|net|edu|gov)\b")
HEAD_FRAG = re.compile(r"^(?:[a-z]{1,3}\s+){1,3}(?=[A-Z\u4e00-\u9fff])")
TAIL_LETTER = re.compile(r"[.!?]\s*[a-z]$")
TERM = re.compile(r"[.!?:;。！？：；\"”’)]\s*$")
REF_LIKE = re.compile(r"^(?:\d{1,3}\.\s|[A-Z][^.]{0,60}\(\d{4})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--stage", choices=["skeleton", "full"], default="full")
    ap.add_argument("--pdf", default="")
    ap.add_argument("--assets", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    doc = json.loads(Path(a.doc).read_text(encoding="utf-8"))
    blocks = doc["blocks"]
    assets = Path(a.assets) if a.assets else Path(a.doc).parent / "assets"
    issues = []

    def add(level, code, bid, msg):
        issues.append({"level": level, "check": code, "id": bid, "msg": msg})

    figs = [b for b in blocks if b["kind"] in ("figure", "table")]
    last_para = None  # (block, index)
    for idx, b in enumerate(blocks):
        bid = b.get("id", "")
        en = b.get("en") or ""
        zh = b.get("zh") or ""
        cen, czh = b.get("caption_en") or "", b.get("caption_zh") or ""

        # ---------- 两阶段都查：结构 / 字符卫生 / 抽取残渣 ----------
        if not (b.get("pages") or []):
            add("WARN", "J-页面锚点", bid, "块缺 pages（页脚标不出「原文 p.X」）")

        if b["kind"] in ("figure", "table"):
            img = b.get("image") or ""
            if not img:
                add("BLOCK", "D-图片缺失", bid, "区域块没有 image 字段")
            elif not (assets / Path(img).name).exists():
                add("BLOCK", "D-图片缺失", bid, f"图片文件不存在：{img}")
            if b["kind"] == "figure" and not cen:
                add("WARN", "D-图注缺失", bid, "图块没有图注（可能图注落在次页页首被当成正文）")
            if cen and not CAP_EN.match(cen):
                add("WARN", "C-图注形态", bid, f"图注不以 Fig./Table 编号开头：{cen[:40]!r}")

        for f in ("en", "caption_en"):
            v = b.get(f) or ""
            if "\u200b" in v:
                add("BLOCK", "B-零宽字符", bid, f"{f} 含 U+200B")
            if any(0xF000 <= ord(c) <= 0xF0FF for c in v):
                add("BLOCK", "B-私用区", bid, f"{f} 含私用区字符（渲染成豆腐块）")
            if re.search(r"\*[A-Za-z][^*]{0,40}\*", v):
                add("WARN", "B-斜体星号", bid, f"{f} 残留 markdown 星号")
            if URL_SPLIT.search(v):
                add("WARN" if b.get("no_translate") else "BLOCK", "B-URL断行", bid, f"{f} 的 URL 被空格切断")
            # 只检查"^{ 开括号后面有没有对应 }"，不再拿 ^{ 的个数去比全文 } 的个数
            # （数学文本里 _{…} 远比 ^{…} 多，旧判据会整篇误报）
            bad = 0
            for mm in re.finditer(r"\^\{", v):
                depth = 1
                for ch in v[mm.end():]:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                if depth:
                    bad += 1
            if bad:
                add("WARN", "B-上标未闭合", bid, f"{f} 有 {bad} 个 ^{{…}} 未闭合")

        # 只有"图注/表注形态"（Table 1 <大写开头短句> 或 Fig. 1. …）才判为图注混入正文；
        # 正文里正常的 "Table 1 summarizes …" / "Fig. 2 shows …" 不是问题。
        CAP_LIKE = re.match(r"^(◂)?\s*(?:(?:Fig\.|Figure)\s*[A-Z]?\d+\.?|Table\s*[A-Z]?\d+)\s*"
                            r"(?:[A-Z][a-z]+\s*){0,4}[:.]", en)
        if b["kind"] == "para" and CAP_LIKE and len(en) > 400:
            add("BLOCK", "D-图注混入正文", bid, "长正文块以 Fig./Table 开头：图注被并进正文段（应挂回图块）")
        if b["kind"] == "para" and HEAD_FRAG.match(en):
            add("BLOCK", "E-段首残片", bid, f"段首有孤立小写片段：{en[:24]!r}")
        if TAIL_LETTER.search(en):
            add("WARN", "E-段末单字母", bid, f"段末句点后有孤立字母：{en[-12:]!r}")

        # ---------- 仅 skeleton：未合并的跨页续句 / 参考文献区 ----------
        if a.stage == "skeleton":
            if b["kind"] == "para" and en:
                if en[:1].islower() and last_para is not None:
                    prev = last_para
                    pp, cp = prev.get("pages") or [], b.get("pages") or []
                    if pp and cp and cp[0] - pp[-1] <= 1 and not TERM.search((prev.get("en") or "").rstrip()):
                        add("BLOCK", "H-续句未合并", bid,
                            f"疑似上一页段落的下半句未合并（前块 {prev['id']} 无终止标点）")
                last_para = b
        else:
            # ---------- 仅 full：译文侧 ----------
            if not b.get("no_translate"):
                if b["kind"] in ("para", "heading", "title", "footnote") and en and not zh.strip():
                    add("BLOCK", "A-覆盖", bid, "有英文但无译文")
                if b["kind"] in ("figure", "table") and cen and not czh.strip():
                    add("BLOCK", "A-覆盖", bid, "有图注/表注英文但无译文")
            elif b["kind"] == "heading" and not zh.strip():
                add("BLOCK", "A-覆盖", bid, "no_translate 标题缺中文（渲染后只剩英文）")
            if cen and czh:
                m_en, m_zh = CAP_EN.match(cen), CAP_ZH.match(czh)
                if m_en and not m_zh:
                    add("BLOCK", "C-图注编号", bid, "中文图注缺少「图 N/表 N」编号")
                elif m_en and m_zh:
                    want = ("表" if m_en.group(1).lower().startswith("table") else "图") + m_en.group(2).lstrip("0")
                    got = m_zh.group(1) + m_zh.group(2).lstrip("0")
                    if want.lower() != got.lower():
                        add("BLOCK", "C-图注编号", bid,
                            f"编号不一致：英文 {m_en.group(0).strip()} ↔ 中文 {m_zh.group(0).strip()}")
            hits = CJK_DIGIT.findall(zh)
            if hits:
                add("WARN", "F-汉字夹数字", bid, f"汉字之间夹裸数字：{hits[:4]}（宜加空格）")
            for f in ("zh", "caption_zh"):
                v = b.get(f) or ""
                if "\u200b" in v:
                    add("BLOCK", "B-零宽字符", bid, f"{f} 含 U+200B")
                if any(0xF000 <= ord(c) <= 0xF0FF for c in v):
                    add("BLOCK", "B-私用区", bid, f"{f} 含私用区字符")

    # skeleton 专属的"全文级"检查
    if a.stage == "skeleton":
        if not any(b["kind"] == "title" for b in blocks):
            add("WARN", "J-标题", "doc", "没有 title 块（首块被当成正文，渲染后无文档大标题）")
        heads = [b for b in blocks if b["kind"] == "heading"]
        if len(heads) < 3:
            add("WARN", "J-标题", "doc", f"heading 只有 {len(heads)} 个，可能是标题被当成正文")
        nt = [b for b in blocks if b.get("no_translate")]
        if nt:
                    # 参考文献是"整块多条目"时（arXiv/Ann. Statist. 版式），不逐条匹配，只要块内出现 ≥2 个 (年份) 就算合规
            bad = [b["id"] for b in nt if b["kind"] == "para"
                   and not REF_LIKE.search(b.get("en") or "")
                   and len(re.findall(r"\(\d{4}[a-z]?\)", b.get("en") or "")) < 2]
            if bad:
                add("WARN", "I-参考文献区", "doc",
                    f"标了 no_translate 但不像参考文献条目：{bad[:5]}（附录可能被误标）")

    # full 阶段的渲染检查
    if a.stage == "full" and a.pdf:
        try:
            import pymupdf
        except ImportError:
            add("WARN", "G-跳过", "pdf", "没有 PyMuPDF，跳过渲染检查")
        else:
            d = pymupdf.open(a.pdf)
            blank = [i + 1 for i, p in enumerate(d) if len(p.get_text().strip()) < 80 and not p.get_images()]
            if blank:
                add("BLOCK", "G-空白页", "pdf", f"空白页：{blank}（多半是图太高被 Chromium 挤出页面）")
            n_img = sum(len(p.get_images()) for p in d)
            if n_img < len(figs):
                add("BLOCK", "G-图片数", "pdf", f"PDF 里只有 {n_img} 张图，但区域块有 {len(figs)} 个")
            zh_chars = sum(len(CJK.findall(p.get_text())) for p in d)
            if zh_chars < 200:
                add("BLOCK", "G-文本层", "pdf", f"中文文本层只有 {zh_chars} 字（不可检索？）")
            else:
                add("INFO", "G-文本层", "pdf", f"{d.page_count} 页 / {n_img} 图 / 中文 {zh_chars} 字可检索")

    by = Counter(x["level"] for x in issues)
    print(f"=== 交付前自检（stage={a.stage}）===")
    print(f"块 {len(blocks)}｜BLOCK {by['BLOCK']}｜WARN {by['WARN']}｜INFO {by['INFO']}")
    for x in issues:
        if x["level"] != "INFO":
            print(f"  [{x['level']}] {x['check']} {x['id']}: {x['msg']}")
    for x in issues:
        if x["level"] == "INFO":
            print(f"  [INFO] {x['check']}: {x['msg']}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"doc": a.doc, "stage": a.stage, "pdf": a.pdf,
                                           "counts": dict(by), "issues": issues},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告 -> {a.out}")
    return 1 if by["BLOCK"] else 0


if __name__ == "__main__":
    sys.exit(main())
