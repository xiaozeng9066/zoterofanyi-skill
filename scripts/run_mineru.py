#!/usr/bin/env python3
"""用 MinerU 做结构化抽取（pipeline 后端，CPU）。

跨平台 Python 版（Windows 无需 Git Bash）；与 run_mineru.sh 行为一致。
用法: python run_mineru.py <input.pdf> <outdir> [--pages 7-9] [--lang en]

说明：
  - MinerU 2.x 的 pipeline 后端 = DocLayout-YOLO 版面分析 + OCR + 表格/公式模型，
    对"无框线表格"和"双栏阅读顺序"比 PyMuPDF 启发式可靠得多；
  - 大 PDF 先用 PyMuPDF 切出页区间再跑，避免几十分钟的等待；
  - 模型来源用 ModelScope（国内稳定），首次运行会自动下载约 1GB+。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def mineru_python() -> str:
    override = os.environ.get("MINERU_PY")
    if override and Path(override).exists():
        return override
    home = Path(os.path.expanduser("~"))
    for cand in (home / ".dsh/runtimes/mineru/venv310/Scripts/python.exe",
                 home / ".dsh/runtimes/mineru/venv310/bin/python"):
        if cand.exists():
            return str(cand)
    raise SystemExit("找不到 MinerU 环境，请设置 MINERU_PY（见 SETUP.md）")


def slice_pages(src: Path, dst: Path, spec: str) -> None:
    import pymupdf

    pages = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages += list(range(int(a), int(b) + 1))
        elif part:
            pages.append(int(part))
    doc = pymupdf.open(src)
    out = pymupdf.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
    out.save(dst)
    print(f"  sliced {len(pages)} pages")


def _cuda_ok() -> bool:
    """MinerU venv 里的 torch 是不是 CUDA 版（默认装的是 +cpu，装不了 GPU 加速）。"""
    try:
        py = mineru_python()
        r = subprocess.run([py, "-c", "import torch;print(1 if torch.cuda.is_available() else 0)"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip() == "1"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("outdir")
    ap.add_argument("--pages", default="", help="如 7-9 或 1,3,5-7")
    ap.add_argument("--lang", default="en")
    args = ap.parse_args()

    src = Path(args.input_pdf).resolve()
    outdir = Path(args.outdir).resolve()
    if not src.is_file():
        raise SystemExit(f"找不到输入 PDF: {src}")
    outdir.mkdir(parents=True, exist_ok=True)

    target = src
    if args.pages:
        target = outdir / f"_input_p{args.pages.replace(',', '_').replace('-', '_')}.pdf"
        print(f"[slice] 抽取页 {args.pages} -> {target}")
        slice_pages(src, target, args.pages)

    env = dict(os.environ)
    # 模型源：`mineru-models-download` 生成 ~/mineru.json 之后，local 模式不再联网校验权重
    # （实测 Jia 2017 7 页：GPU+modelscope 39.3s → GPU+local 26.2s）
    _local_cfg = Path(os.path.expanduser("~")) / "mineru.json"
    env.setdefault("MINERU_MODEL_SOURCE", "local" if _local_cfg.exists() else "modelscope")
    # 设备：有 CUDA 版 torch 就用 GPU（本机 RTX 3060 Ti + torch 2.14.1+cu126 已可用）
    env.setdefault("MINERU_DEVICE_MODE", "cuda" if _cuda_ok() else "cpu")
    # ★ 两项关键开关（2026-10-18 实测，Jia 2017 7 页：245s → 46s，5.3×，且结构完全一致）
    #   1) 关表格识别：我们只裁表格**图片**，不需要单元格文字；表格区域由 layout 给出，
    #      caption/footnote 子块仍会生成（已逐块比对：14 title / 59 text / 4 table / 1 image 完全相同）。
    #   2) 关公式识别：开着时 MinerU 会把公式**内联进正文并把 equation 块清空**（见
    #      model_json_to_middle_json.py:93 `if formula_enabled: interline_equation_blocks = []`），
    #      我们反而拿不到公式区域去裁图；关掉后 interline_equation 块保留 → 正好是我们要的。
    env.setdefault("MINERU_TABLE_ENABLE", "false")
    env.setdefault("MINERU_FORMULA_ENABLE", "false")
    # CPU 线程：把 16 个逻辑核用满
    try:
        import multiprocessing as _mp
        _n = str(min(16, _mp.cpu_count()))
    except Exception:
        _n = "8"
    env.setdefault("MINERU_INTRA_OP_NUM_THREADS", _n)
    env.setdefault("MINERU_INTER_OP_NUM_THREADS", "8")
    # Ultralytics / Matplotlib 默认往 %APPDATA% 写配置，沙箱内会被拒；改到输出目录
    cfg = outdir / ".mineru-cfg"
    for sub in ("ultralytics", "matplotlib"):
        (cfg / sub).mkdir(parents=True, exist_ok=True)
    env.setdefault("YOLO_CONFIG_DIR", str(cfg / "ultralytics"))
    env.setdefault("MPLCONFIGDIR", str(cfg / "matplotlib"))
    env.setdefault("MODELSCOPE_CACHE",
                   str(Path(os.path.expanduser("~")) / ".dsh" / "runtimes" / "mineru" / "modelscope"))
    print(f"[mineru] 后端=pipeline 语言={args.lang} 设备={env['MINERU_DEVICE_MODE']} "
          f"模型源={env['MINERU_MODEL_SOURCE']}")

    py = mineru_python()
    r = subprocess.run([py, "-m", "mineru.cli.client", "-p", str(target), "-o", str(outdir),
                        "-b", "pipeline", "-l", args.lang], env=env)
    if r.returncode != 0:
        return r.returncode

    print("[mineru] 输出目录：")
    seen = set()
    for pat in ("*.md", "*middle.json"):
        for p in sorted(outdir.rglob(pat)):
            if len(p.relative_to(outdir).parts) <= 3 and p not in seen:
                seen.add(p)
                print("  " + str(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
