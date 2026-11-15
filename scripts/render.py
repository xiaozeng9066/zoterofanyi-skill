#!/usr/bin/env python3
"""阶段3：把"块级双语 JSON"渲染成 HTML（再由 Edge/Chromium 打印成 PDF）。

为什么走 HTML→PDF 而不是 LaTeX：
  - 公式用 KaTeX、表格用 HTML 原生结构、图片直接引用，容错率远高于 md→LaTeX；
  - 版式（英文灰、中文黑、段落交错）用 CSS 几分钟就能调到满意；
  - 反过来 LaTeX 路线在"表格 + 中文 + 公式"三者同时出现时极易编译失败。
"""
import argparse
import html
import json
import re
import struct
from pathlib import Path


def png_size(path):
    """读 PNG 文件头得到 (宽, 高)。纯标准库，无需 PIL。失败返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    except Exception:
        return None

CSS = """
@page { size: A4; margin: 17mm 15mm 17mm 15mm; }
@page landscape { size: A4 landscape; margin: 15mm 17mm 15mm 17mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Songti SC", "STSong", "SimSun", "Microsoft YaHei", "Noto Serif CJK SC", serif;
       color: #111; -webkit-font-smoothing: antialiased; }
.meta { border: 1px solid #d8d8d8; background: #fafafa; border-radius: 4px;
        padding: 10px 12px; margin-bottom: 18px; font-size: 9pt; line-height: 1.65; color: #333; }
.meta h1 { font-size: 13pt; margin: 0 0 6px 0; font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
.meta table { border-collapse: collapse; margin: 6px 0 0 0; font-size: 8.5pt; }
.meta td { border: 1px solid #e2e2e2; padding: 2px 6px; }
.meta .warn { color: #8a4b00; }
.srcmark { font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 8pt; color: #7a7a7a;
           border-bottom: 1px solid #eee; padding-bottom: 3px; margin: 22px 0 12px 0; letter-spacing: .5px; break-after: avoid; page-break-after: avoid; }
h1.doc-title { font-size: 17pt; text-align: center; margin: 6px 0 2px 0;
               font-family: "PingFang SC", "Microsoft YaHei", "Songti SC", "SimSun", sans-serif; line-height: 1.4; }
h1.doc-title .sub { display: block; font-size: 12pt; color: #444; font-weight: 400; margin-top: 4px; }
h2.sec { font-size: 13pt; font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; margin: 22px 0 4px 0; }
h2.sec .zh { font-size: 12pt; color: #333; font-weight: 400; display: block; margin-top: 2px; }
h2.sec { break-inside: avoid; break-after: avoid; }
.pair { margin: 0 0 16px 0; }
.pair.short { break-inside: avoid; page-break-inside: avoid; }
.idtag { float: right; font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 7pt; color: #b0b0b0; }
.en { color: #3d3d3d; font-family: Georgia, "Times New Roman", serif;
      font-size: 10.1pt; line-height: 1.58; text-align: justify; }
.zh { color: #111; font-size: 10.7pt; line-height: 1.9; margin-top: 7px; text-align: justify; }
.zh::before { content: ""; display: block; width: 26px; border-top: 2px solid #c8c8c8; margin-bottom: 7px; }
figure.tbl { margin: 16px 0; break-inside: avoid; page-break-inside: avoid; }
figure.tbl.landscape { page: landscape; break-before: page; page-break-before: always; }
figure.tbl.landscape img { width: auto; max-width: 100%; height: auto; max-height: 96vh;
                           object-fit: contain; }
figure.tbl img { width: auto; max-width: 100%; height: auto; max-height: 820px;
                 display: block; margin: 0 auto; border: 1px solid #ddd; }
figcaption .capzh { font-size: 10.5pt; margin-top: 8px; }
figcaption .capen { font-size: 9pt; color: #666; font-family: Georgia, serif; margin-top: 2px; }
.legend { border-collapse: collapse; margin-top: 8px; font-size: 9pt; width: 100%; }
.legend td { border: 1px solid #e6e6e6; padding: 3px 7px; }
.legend td.k { color: #666; font-family: Georgia, serif; width: 42%; }
.note { font-size: 8.8pt; color: #333; line-height: 1.75; margin-top: 8px; }
.note .flag { color: #8a4b00; }
.qa { margin-top: 8px; padding-left: 16px; }
.qa li { margin-bottom: 3px; }
.fn { margin: 10px 0 12px 0; padding: 6px 0 6px 10px; border-left: 2px solid #cfcfcf;
      break-inside: avoid; page-break-inside: avoid; }
.fn .fnmark { font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 8pt; color: #7a7a7a;
              display: block; margin-bottom: 2px; letter-spacing: .5px; }
.fn .fn-en { font-family: Georgia, serif; font-size: 8.6pt; line-height: 1.5; color: #555; }
.fn .fn-zh { font-family: "Songti SC", "STSong", "SimSun", "Noto Serif CJK SC", serif; font-size: 9pt; line-height: 1.7; color: #222; margin-top: 3px; }
.fnindex { margin-top: 18px; }
.fnindex table { border-collapse: collapse; width: 100%; font-size: 8.6pt; }
.fnindex th, .fnindex td { border: 1px solid #e2e2e2; padding: 3px 6px; text-align: left; vertical-align: top; }
.fnindex th { background: #f7f7f7; font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
.refitem { font-family: Georgia, serif; font-size: 9.2pt; line-height: 1.5; color: #333;
           text-indent: -14px; padding-left: 14px; margin-bottom: 5px; }
"""


SUB_RE = re.compile(r"_\{([^}]*)\}|\^\{([^}]*)\}")


def esc(t):
    """转义 HTML，同时支持学术文本里常见的 _{下标} / ^{上标} 写法。"""
    out = html.escape(t or "")
    return SUB_RE.sub(
        lambda m: f"<sub>{m.group(1)}</sub>" if m.group(1) is not None else f"<sup>{m.group(2)}</sup>",
        out,
    )


def render(data, basedir=None):
    m = data["meta"]
    basedir = Path(basedir) if basedir else Path.cwd()
    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8">',
             f"<style>{CSS}</style></head><body>"]

    # —— 头部：溯源信息（正式版可裁剪）——
    label = esc(m.get("doc_kind_label") or "中英对照全文")
    parts.append(f'<div class="meta"><h1>{label} · 生成信息</h1>')
    parts.append(
        f"原文：{esc(m['title_en'])}（{esc(m['author_year'])}）<br>"
        f"译名：{esc(m['title_zh'])}<br>"
        f"Zotero 条目：{esc(m['item_key'])} ／ 附件：{esc(m['attachment_key'])} ／ 本样本覆盖 {('共 %d 页（%s–%s）' % (len(m['pages_in_sample']), m['pages_in_sample'][0], m['pages_in_sample'][-1]) if isinstance(m.get('pages_in_sample'), (list, tuple)) else esc(m.get('pages_in_sample')))}<br>"
        f"翻译：{esc(m['translator'])} ｜ 术语表：{esc(m['glossary_version'])} ｜ 版式：{esc(m['layout'])}"
    )
    if data.get("glossary") and m.get("print_glossary", True):
        parts.append('<table><tr><td colspan="2"><b>术语表（节选）</b></td></tr>')
        for en, zh in data["glossary"]:
            parts.append(f'<tr><td>{esc(en)}</td><td>{esc(zh)}</td></tr>')
        parts.append("</table>")
    if m.get("qa_notes"):
        parts.append('<div class="qa"><b class="warn">已知问题 / 待人工核对</b><ul>')
        for n in m["qa_notes"]:
            parts.append(f"<li>{esc(n)}</li>")
        parts.append("</ul></div>")
    parts.append("</div>")

    last_page = None
    for b in data["blocks"]:
        pages = b.get("pages") or []
        page = pages[0] if pages else None
        if page is not None and page != last_page and b["kind"] not in ("title",):
            label = f"原文 p.{pages[0]}" + (f"–{pages[-1]}" if len(pages) > 1 else "")
            parts.append(f'<div class="srcmark">{label}</div>')
            last_page = pages[-1] if len(pages) > 1 else page

        if b["kind"] == "title":
            parts.append(f'<h1 class="doc-title">{esc(b["zh"])}<span class="sub">{esc(b["en"])}</span></h1>')
        elif b["kind"] == "heading":
            parts.append(f'<h2 class="sec">{esc(b["zh"])}<span class="zh">{esc(b["en"])}</span></h2>')
        elif b["kind"] == "footnote":
            parts.append('<div class="fn">')
            parts.append(f'<span class="fnmark">注 {esc(str(b.get("marker", "")))} ｜ 原文 p.{"–".join(map(str, b.get("pages", [])))}</span>')
            parts.append(f'<div class="fn-en">{esc(b.get("en"))}</div>')
            if b.get("zh"):
                parts.append(f'<div class="fn-zh">{esc(b.get("zh"))}</div>')
            parts.append("</div>")
        elif b["kind"] == "fn_index":
            parts.append('<div class="fnindex"><h2 class="sec">脚注编号对照表<span class="zh">Footnote index</span></h2>')
            parts.append('<table><tr><th>编号</th><th>原文页</th><th>主题（中文首句摘录）</th><th>英文首句摘录</th></tr>')
            for row in b.get("rows", []):
                parts.append("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in row) + "</tr>")
            parts.append("</table></div>")
        elif b["kind"] == "para" and b.get("no_translate"):
            # 参考文献：按学术惯例保留原文，不做翻译
            parts.append(f'<div class="refitem"><span class="idtag">{esc(b["id"])}</span>{esc(b["en"]).replace(chr(10), "<br>")}</div>')
        elif b["kind"] == "para":
            cls = "pair short" if len(b.get("en", "")) < 2000 else "pair"
            cont = " …（原文续下页）" if b.get("continues") else ""
            parts.append(f'<div class="{cls}"><div class="en"><span class="idtag">{esc(b["id"])}</span>{esc(b["en"])}</div>')
            parts.append(f'<div class="zh">{esc(b["zh"])}{cont}</div>')
            if b.get("flag"):
                parts.append(f'<div class="note flag">⚠ {esc(b["flag"])}</div>')
            parts.append("</div>")
        elif b["kind"] in ("equation", "figure", "table"):
            cls = "tbl eq" if b["kind"] == "equation" else "tbl"
            # 宽表/宽图自动转横向：读 PNG 尺寸，宽高比 > 1.25 视为横版
            landscape = False
            img_path = b.get("image")
            if img_path and b["kind"] in ("table", "figure"):
                ip = Path(img_path)
                if not ip.is_absolute():
                    ip = basedir / ip
                sz = png_size(ip)
                if sz and sz[0] / max(sz[1], 1) > 1.25:
                    landscape = True
                    cls += " landscape"
            parts.append(f'<figure class="{cls}">')
            parts.append(f'<img src="{esc(b["image"])}" alt="{esc(b["kind"])}">')
            if b["kind"] == "table" and not (b.get("caption_zh") or b.get("caption_en")):
                parts.append('<figcaption><div class="capzh">（上接前页表格，同一表格的续页）</div></figcaption>')
            if b.get("caption_zh") or b.get("caption_en"):
                parts.append("<figcaption>")
                if b.get("caption_zh"):
                    parts.append(f'<div class="capzh">{esc(b["caption_zh"])}</div>')
                if b.get("caption_en"):
                    parts.append(f'<div class="capen">{esc(b["caption_en"])}</div>')
                parts.append("</figcaption>")
            if b.get("legend"):
                parts.append('<table class="legend">')
                for k, v in b["legend"]:
                    parts.append(f'<tr><td class="k">{esc(k)}</td><td>{esc(v)}</td></tr>')
                parts.append("</table>")
            for key in ("stat_zh", "note_zh"):
                if b.get(key):
                    parts.append(f'<div class="note">{esc(b[key])}</div>')
            if b.get("note_en"):
                parts.append(f'<div class="note capen">{esc(b["note_en"])}</div>')
            if b.get("note_flag"):
                parts.append(f'<div class="note flag">⚠ {esc(b["note_flag"])}</div>')
            parts.append("</figure>")
        else:
            raise ValueError(f"渲染器不认识块类型: {b['kind']}（id={b.get('id')}）—— 拒绝静默丢内容")

    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    basedir = Path(args.json_path).resolve().parent
    out.write_text(render(data, basedir=basedir), encoding="utf-8")
    print(f"wrote {out}  blocks={len(data['blocks'])}")


if __name__ == "__main__":
    main()
