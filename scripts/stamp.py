#!/usr/bin/env python3
"""阶段4：给渲染出的 PDF 盖页脚（页码 + 原文锚点），并自检中文文本层是否可检索。

自检很关键：Zotero 的高亮/搜索依赖 PDF 文本层与 ToUnicode 映射。
若这里抽不出中文，说明字体嵌入方式有问题，必须换渲染方案。
"""
import argparse
import re
import sys
from pathlib import Path

import pymupdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--label", default="")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    n = doc.page_count
    last_anchor = ""
    for i, page in enumerate(doc, start=1):
        # 找出本页出现过的原文页锚点，如 "原文 p.4–5"；本页没有则沿用上一页的锚点
        anchors = sorted({m.group(0) for m in re.finditer(r"原文 p\.[0-9–\-]+", page.get_text())})
        if anchors:
            # 同一页可能同时出现 "原文 p.7" 与 "原文 p.7–9"，取信息量最大的一个
            last_anchor = max(anchors, key=len)
        foot = f"{args.label}  ·  第 {i} / {n} 页"
        if last_anchor:
            foot = f"{last_anchor}  ·  第 {i} / {n} 页" + (f"  ·  {args.label}" if args.label else "")
        page.insert_text((page.rect.width / 2 - 60, page.rect.height - 22),
                         foot, fontname="china-s", fontsize=7.5, color=(0.45, 0.45, 0.45))
    doc.saveIncr()
    print(f"stamped {n} pages -> {args.pdf}")

    if args.check:
        doc = pymupdf.open(args.pdf)
        text = "\n".join(p.get_text() for p in doc)
        zh = len(re.findall(r"[\u4e00-\u9fff]", text))
        en_words = len(re.findall(r"\b[A-Za-z]{3,}\b", text))
        sample = "".join(re.findall(r"[\u4e00-\u9fff]{6,}", text)[:5])
        print(f"check: 中文字符={zh} 英文词={en_words}")
        print(f"check: 中文可检索样例 -> {sample}")
        # 原文把校验词写死为另一篇的术语（"同伴效应"），对本篇必然失败。
        # 这里改成与篇目无关的判据：中文字符足够多 + 存在 >=6 字的连续中文串。
        ok = zh > 200 and len(sample) >= 6
        print("check: 文本层可检索" if ok else "check: 文本层异常（中文提取失败）")
        if not ok:
            sys.exit(2)


if __name__ == "__main__":
    main()
