#!/usr/bin/env python3
"""检查 MinerU 的实际输出结构（不猜 schema），并给出可复核的质量指标。

MinerU 2.x 典型输出：
  <outdir>/<pdf_stem>/auto/<pdf_stem>.md         阅读顺序的 Markdown
  <outdir>/<pdf_stem>/auto/<pdf_stem>_middle.json 版面块（含 bbox、type）
  <outdir>/<pdf_stem>/auto/images/*.jpg           裁出的图/表

本脚本只读不写（除 --report 指定的 JSON），用于：
  1) 打印 middle.json 的顶层键与各 type 的样例块，确认真实字段名；
  2) 统计 pipe 表格数、$$ 公式数、图片数 —— 用来判断无框线表格/公式是否被还原；
  3) 按页打印正文，人工核对双栏阅读顺序。
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", help="MinerU 的输出目录（其下应有 <stem>/auto/）")
    ap.add_argument("--pages", default="", help="只打印这些页的正文，如 7-9,16")
    ap.add_argument("--report", default="", help="把统计写入该 JSON")
    args = ap.parse_args()

    root = Path(args.outdir)
    autos = list(root.glob("*/auto")) or ([root] if (root / "middle.json").exists() else [])
    if not autos:
        autos = [p for p in root.rglob("auto") if p.is_dir()]
    if not autos:
        print(f"在 {root} 下找不到 auto 目录，实际内容：")
        for p in sorted(root.rglob("*"))[:40]:
            print("   ", p.relative_to(root))
        return

    auto = autos[0]
    print(f"auto 目录: {auto}")
    files = sorted(p.name for p in auto.iterdir())
    print(f"文件: {files}\n")

    md = next(auto.glob("*.md"), None)
    if md:
        text = md.read_text(encoding="utf-8", errors="replace")
        n_tables = len(re.findall(r"^\|.*\|$", text, re.M))
        n_formula_block = len(re.findall(r"\$\$", text)) // 2
        n_formula_inline = len(re.findall(r"(?<!\$)\$(?!\$)", text)) // 2
        n_imgs = len(re.findall(r"!\[", text))
        print("=== Markdown 质量指标 ===")
        print(f"  表格行(|...|)      : {n_tables}")
        print(f"  块级公式 $$...$$   : {n_formula_block}")
        print(f"  行内公式 $...$     : {n_formula_inline}")
        print(f"  图片引用 ![...]    : {n_imgs}")
        print(f"  字符数             : {len(text)}")

    mj = next(auto.glob("*middle.json"), None) or next(auto.glob("*.json"), None)
    if mj:
        data = json.loads(mj.read_text(encoding="utf-8"))
        print(f"\n=== middle.json: {mj.name} ===")
        print(f"  顶层类型: {type(data).__name__}")
        if isinstance(data, dict):
            print(f"  顶层键: {list(data.keys())}")
            for k, v in data.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    print(f"  {k}: list[{len(v)}] 首元素键 = {list(v[0].keys())}")
        # 统计块类型
        types = Counter()
        pages = []
        if isinstance(data, dict) and isinstance(data.get("pdf_info"), list):
            pages = data["pdf_info"]
        for pg in pages:
            for blk in pg.get("preproc_blocks", []) or pg.get("blocks", []):
                types[blk.get("type", "?")] += 1
        if types:
            print(f"  版面块类型统计: {dict(types)}")

    if args.pages and md:
        wanted = set()
        for part in args.pages.split(","):
            if "-" in part:
                a, b = part.split("-")
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(part))
        print("\n=== 正文按页预览（人工核对阅读顺序/表格/公式）===")
        chunks = re.split(r"\n(?=#{1,6}\s)", md.read_text(encoding="utf-8", errors="replace"))
        for ch in chunks:
            m = re.search(r"page[_\s]*(\d+)", ch, re.I)
            if m and int(m.group(1)) in wanted:
                print(f"\n----- 命中 page {m.group(1)} -----\n{ch[:1200]}")

    if args.report:
        Path(args.report).write_text(json.dumps({"auto_dir": str(auto), "files": files},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.report}")


if __name__ == "__main__":
    main()
