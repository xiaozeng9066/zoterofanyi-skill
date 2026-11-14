#!/usr/bin/env python3
"""阶段3+4：双语 JSON -> HTML -> (Edge 打印) -> PDF -> 盖页脚 + 文本层自检。

跨平台 Python 版（Windows 无需 Git Bash）；与 build_pdf.sh 行为一致。
用法: python build_pdf.py <bilingual.json> <out.pdf> [页脚标签]
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_edge() -> str:
    """按 EDGE_BIN > Windows 常见路径 > macOS > PATH 的顺序找 Chromium 内核浏览器。"""
    cands = []
    env_edge = os.environ.get("EDGE_BIN")
    if env_edge:
        cands.append(env_edge)
    cands += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    for c in cands:
        if c and Path(c).exists():
            return c
    raise SystemExit("找不到 Edge/Chrome，请设置 EDGE_BIN 指向浏览器可执行文件")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("out_pdf")
    ap.add_argument("label", nargs="?", default="")
    args = ap.parse_args()

    json_in = Path(args.json_path).resolve()
    out = Path(args.out_pdf).resolve()
    if not json_in.is_file():
        raise SystemExit(f"找不到输入 JSON: {json_in}")
    workdir = json_in.parent
    out.parent.mkdir(parents=True, exist_ok=True)

    edge = find_edge()
    html = workdir / "sample.html"
    raw = workdir / "sample_raw.pdf"
    for stale in (html, raw):
        if stale.exists():
            stale.unlink()

    # 1) 渲染 HTML
    r = subprocess.run([sys.executable, str(HERE / "render.py"), str(json_in), "--out", str(html)])
    if r.returncode != 0:
        raise SystemExit("render.py 失败")

    # 2) Edge/Chromium 无头打印。受限环境下必须 --no-sandbox，否则 GPU/沙箱初始化直接失败。
    cmd = [
        edge,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-breakpad",
        "--disable-crash-reporter",
        f"--user-data-dir={workdir / '.edge-profile'}",
        "--virtual-time-budget=20000",
        "--no-pdf-header-footer",
        f"--print-to-pdf={raw}",
        html.as_uri(),
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    if tail:
        print(tail[-1])
    if not raw.is_file() or raw.stat().st_size == 0:
        hint = (
            "\n提示：Edge/Chromium 在 DSH 的 workspace-write 沙箱内会以 STATUS_BREAKPOINT 立即退出"
            "（进程令牌受限，浏览器自身沙箱无法初始化）。请用 danger-full-access 重跑本命令，"
            "或设置 EDGE_BIN 指向可用的浏览器。"
        )
        raise SystemExit(f"Edge 未生成 PDF（exit={r.returncode}）{hint}\n" + "\n".join(tail[-5:]))

    # 3) 盖页脚 + 中文文本层自检（--check 不通过会非零退出）
    r = subprocess.run([sys.executable, str(HERE / "stamp.py"), str(raw),
                        "--label", args.label, "--check"])
    if r.returncode != 0:
        raise SystemExit("stamp.py 自检失败（中文文本层不可检索？）")

    os.replace(raw, out)
    print(f"OK -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
