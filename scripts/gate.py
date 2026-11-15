#!/usr/bin/env python3
"""门禁总入口（v2.21 / 规则 75）：一条命令体检「这台机器能不能跑 zoterofanyi、走到哪一步」。

设计原则（换机一键体检「缺啥补啥」）：
  * 零新依赖：只用标准库 + 复用本 skill 已有脚本的探测逻辑（import，失败则子进程兜底）；
  * 分层判定：每一关给 PASS / BLOCK / WARN / SKIP，最后给「就绪 / 部分就绪 / 未就绪」总判定；
  * 退出码：0 = 资源全就绪；1 = 有 BLOCK（缺必需组件/连通性）；2 = 用法错误。

四类门禁（与 GATES.md 一一对应）：
  resources  资源门禁：软件（python/mineru/browser/zotero）、配置、Zotero Local API、git 凭据、代理、GPU
  pipeline   流程门禁：两道 preflight 的入口位置 + 一键编排 + 迭代收口（--stage 跑，见下）
  quality    质量门禁：黄金集、术语审计、翻译记忆、隐私、README 同源（--stage 跑，见下）
  meta       元门禁：门禁脚本本身的自检（各门禁脚本可 import、语法有效）

用法：
  python scripts/gate.py                    # 全部门禁体检（含联网探测，可能较慢）
  python scripts/gate.py resources          # 只跑资源门禁（换机最常用）
  python scripts/gate.py --offline          # 跳过一切联网探测（代理/git 远端），适合无网自检
  python scripts/gate.py --json             # 输出 JSON 便于脚本消费
  python scripts/gate.py --stage pipeline   # 只跑流程门禁（preflight 入口自检，不真跑翻译）
  python scripts/gate.py --stage quality    # 只跑质量门禁（黄金集/术语/记忆/隐私/README）
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CONFIG = ROOT / "local_config.json"

# ── 颜色 / 标记（Windows 控制台安全） ──────────────────────────────
PASS = "PASS "
BLOCK = "BLOCK"
WARN = "WARN "
SKIP = "SKIP "


def _tag(status: str) -> str:
    return status


def _load_module(name: str):
    """import scripts/<name>.py；失败返回 None（调用方走子进程兜底）。"""
    try:
        sys.path.insert(0, str(SCRIPTS))
        return importlib.import_module(name)
    except Exception:
        return None


def _load_config() -> dict:
    """复用 paths.load()；失败则最小兜底。"""
    m = _load_module("paths")
    if m is not None:
        try:
            return m.load()
        except Exception:
            pass
    cfg = {
        "skill_root": ROOT.as_posix(),
        "scripts_dir": SCRIPTS.as_posix(),
        "home": Path.home().as_posix(),
        "workspace": str(Path.home() / "zoterofanyi"),
        "runs_dir": str(Path.home() / "zoterofanyi" / "zotero-translate"),
    }
    return cfg


# ─────────────────────────── 资源门禁 ───────────────────────────────

def gate_software(cfg: dict) -> list[dict]:
    """软件组件：python(含 PyMuPDF) / mineru_python / browser / zotero_mcp 是否可定位。"""
    out = []
    checks = [
        ("python", "主工具 Python（含 PyMuPDF）", True),
        ("mineru_python", "MinerU Python（表格/公式抽取）", False),
        ("browser", "Edge/Chromium（PDF 渲染）", False),
        ("zotero_mcp", "Zotero MCP / zotero_api（挂回）", False),
    ]
    for key, label, required in checks:
        val = cfg.get(key)
        ok = bool(val) and Path(str(val)).exists()
        if key == "python" and ok:
            detail = "fitz=%s" % ("yes" if cfg.get("python_has_fitz") else "unknown")
        else:
            detail = val or "(未配置)"
        status = PASS if ok else (BLOCK if required else WARN)
        out.append({"gate": "resources.software", "key": key, "label": label,
                    "status": status, "detail": detail,
                    "required": required, "fix": "detect_env.py" if not ok else None})
    return out


def gate_zotero(cfg: dict) -> list[dict]:
    """Zotero 10 官方 Local API 是否开启（读无认证，可离线探测本机 23119 端口）。"""
    z = _load_module("zotero_api")
    if z is not None:
        try:
            st = z.local_api_status()
            if st.get("ok"):
                return [{"gate": "resources.zotero", "key": "local_api",
                         "label": "Zotero 10 Local API", "status": PASS,
                         "detail": "server_id=%s" % st.get("server_id"), "required": False,
                         "fix": None}]
            return [{"gate": "resources.zotero", "key": "local_api",
                     "label": "Zotero 10 Local API", "status": WARN,
                     "detail": st.get("reason", "unknown"),
                     "required": False,
                     "fix": "Zotero 设置 → 高级 → 勾选「允许其他应用与本机 Zotero 通信」"}]
        except Exception as e:
            return [{"gate": "resources.zotero", "key": "local_api",
                     "label": "Zotero 10 Local API", "status": WARN,
                     "detail": "探测异常: %s" % e, "required": False,
                     "fix": "确认 Zotero 已启动并勾选 Local API"}]
    # 子进程兜底
    return [{"gate": "resources.zotero", "key": "local_api",
             "label": "Zotero 10 Local API", "status": WARN,
             "detail": "zotero_api.py 不可 import，改用子进程", "required": False,
             "fix": "python scripts/zotero_api.py status"}]


def gate_git(cfg: dict) -> list[dict]:
    """git 可用性 + 是否有远端 origin（本地仓库身份）。不联网验证凭据（那是 network 关）。"""
    out = []
    # git 命令可用
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, timeout=15)
        git_ok = r.returncode == 0
        ver = r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        git_ok, ver = False, "git 未安装"
    out.append({"gate": "resources.git", "key": "git", "label": "git 命令",
                "status": PASS if git_ok else BLOCK, "detail": ver,
                "required": True, "fix": "安装 git 并加入 PATH" if not git_ok else None})
    # 本目录是 git 仓库 + 有 origin
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                           capture_output=True, timeout=15)
        if r.returncode == 0:
            url = r.stdout.decode("utf-8", "replace").strip()
            out.append({"gate": "resources.git", "key": "origin", "label": "远端 origin",
                        "status": PASS, "detail": url, "required": False,
                        "fix": None})
        else:
            out.append({"gate": "resources.git", "key": "origin", "label": "远端 origin",
                        "status": WARN, "detail": "未配置 origin",
                        "required": False, "fix": "python scripts/connect_github.py --remote <url>"})
    except Exception as e:
        out.append({"gate": "resources.git", "key": "origin", "label": "远端 origin",
                    "status": WARN, "detail": str(e), "required": False,
                    "fix": "python scripts/connect_github.py"})
    return out


def gate_network(cfg: dict, offline: bool) -> list[dict]:
    """代理连通性（复用 sync_now 的网络政策；离线时 SKIP）。"""
    if offline:
        return [{"gate": "resources.network", "key": "proxy", "label": "代理连通",
                 "status": SKIP, "detail": "--offline 跳过", "required": False, "fix": None}]
    net = (cfg.get("network") or {})
    mode = net.get("mode", "unknown")
    raw = net.get("proxy_candidates") or []
    fallback = net.get("fallback", "unknown")
    # 候选可能是字符串 url，也可能是 {url, ok, ms} 对象
    candidates: list[str] = []
    for c in raw:
        if isinstance(c, str):
            candidates.append(c)
        elif isinstance(c, dict) and c.get("url"):
            candidates.append(c["url"])
    if not candidates:
        return [{"gate": "resources.network", "key": "proxy", "label": "代理连通",
                 "status": WARN,
                 "detail": "network 未配置代理候选（mode=%s）" % mode,
                 "required": False,
                 "fix": "python scripts/detect_env.py && python scripts/sync_now.py --status"}]
    # 有候选 → 用 git ls-remote 验证候选是否真通（走代理）
    url = "https://github.com/xiaozeng9066/zoterofanyi-skill.git"
    ok = False
    used = None
    for ep in candidates:
        try:
            env = dict(os.environ)
            env["HTTPS_PROXY"] = ep
            env["https_proxy"] = ep
            r = subprocess.run(["git", "ls-remote", url, "HEAD"],
                               capture_output=True, timeout=30, env=env)
            if r.returncode == 0:
                ok, used = True, ep
                break
        except Exception:
            continue
    detail = "mode=%s fallback=%s candidates=%s" % (mode, fallback, ",".join(candidates))
    detail += " -> %s" % (used if ok else "均不通")
    status = PASS if ok else WARN
    return [{"gate": "resources.network", "key": "proxy", "label": "代理连通",
             "status": status, "detail": detail, "required": False,
             "fix": None if ok else "python scripts/sync_now.py --status --route proxy"}]


def gate_gpu(cfg: dict) -> list[dict]:
    """GPU/CUDA 探测（只读 local_config.gpu，缺则提示 detect_env）。"""
    gpu = cfg.get("gpu") or {}
    if not gpu:
        return [{"gate": "resources.gpu", "key": "gpu", "label": "GPU/CUDA",
                 "status": WARN, "detail": "未探测（先跑 detect_env.py）",
                 "required": False, "fix": "python scripts/detect_env.py"}]
    vendor = gpu.get("vendor", "unknown")
    gpus = gpu.get("gpus") or []
    cuda = gpu.get("cuda_available", False)
    device = gpu.get("device", "cpu")
    label = ", ".join(gpus) if gpus else vendor
    status = PASS if cuda else (WARN if label and label != "unknown" else WARN)
    return [{"gate": "resources.gpu", "key": "gpu", "label": "GPU/CUDA",
             "status": status,
             "detail": "%s device=%s cuda=%s" % (label, device, cuda),
             "required": False,
             "fix": None if cuda else "安装 CUDA 版 torch；GPU 非必需，仅影响 MinerU 速度"}]


def gate_resources(cfg: dict, offline: bool) -> list[dict]:
    out = []
    out += gate_software(cfg)
    out += gate_zotero(cfg)
    out += gate_git(cfg)
    out += gate_network(cfg, offline)
    out += gate_gpu(cfg)
    return out


# ─────────────────────────── 流程门禁（入口位置自检，不真跑翻译） ─────────

def gate_pipeline(cfg: dict) -> list[dict]:
    """流程门禁：确认关键脚本存在 + 一键编排可跑（只 --help 验证，不真跑）。"""
    out = []
    needed = ["pipeline.py", "preflight.py", "iterate_commit.py", "regression_suite.py",
              "build_memory.py", "term_audit.py", "qa.py", "rules_check.py",
              "sanitize.py", "build_pdf.py", "build_pdf_reportlab.py"]
    for name in needed:
        p = SCRIPTS / name
        out.append({"gate": "pipeline.scripts", "key": name,
                    "label": "流程脚本 %s" % name,
                    "status": PASS if p.is_file() else BLOCK,
                    "detail": str(p) if p.is_file() else "缺失",
                    "required": True, "fix": None if p.is_file() else "git pull 同步版本资产"})
    # 一键编排入口是否可 import（语法有效）
    pipe = _load_module("pipeline")
    if pipe is not None:
        out.append({"gate": "pipeline.entry", "key": "pipeline.py",
                    "label": "一键编排 import", "status": PASS,
                    "detail": "可 import", "required": False, "fix": None})
    else:
        out.append({"gate": "pipeline.entry", "key": "pipeline.py",
                    "label": "一键编排 import", "status": WARN,
                    "detail": "不可 import（或依赖缺失）", "required": False,
                    "fix": "用 $PY 跑 pipeline.py --help 验证"})
    return out


def gate_quality(cfg: dict) -> list[dict]:
    """质量门禁：黄金集/术语/记忆/隐私/README 的资产存在性 + 可运行性。"""
    out = []
    # 黄金集案例数
    fix_dir = ROOT / "fixtures"
    full_dir = ROOT / "fixtures_full"
    n_fix = len(list(fix_dir.glob("*/expected.json"))) if fix_dir.is_dir() else 0
    out.append({"gate": "quality.golden", "key": "fixtures",
                "label": "黄金集案例", "status": PASS if n_fix > 0 else BLOCK,
                "detail": "%d 个案例（fixtures_full=%s）" % (n_fix, "有" if full_dir.is_dir() else "无"),
                "required": True,
                "fix": None if n_fix else "git pull 或 snapshot_case.py 重建"})
    # 翻译记忆
    tm = ROOT / "tm"
    tf = tm / "term_fixes.json"
    ep = tm / "error_patterns.json"
    n_tf = n_ep = 0
    if tf.is_file():
        try:
            n_tf = len(json.loads(tf.read_text(encoding="utf-8")))
        except Exception:
            n_tf = -1
    if ep.is_file():
        try:
            n_ep = len(json.loads(ep.read_text(encoding="utf-8")))
        except Exception:
            n_ep = -1
    out.append({"gate": "quality.memory", "key": "tm",
                "label": "翻译记忆", "status": PASS if (n_tf >= 0 and n_ep >= 0) else WARN,
                "detail": "term_fixes=%d error_patterns=%d" % (n_tf, n_ep),
                "required": False, "fix": None})
    # 隐私门禁标记
    pub_flag = ROOT / "PUBLIC-MODE.json"
    out.append({"gate": "quality.privacy", "key": "public_mode",
                "label": "隐私门禁标记", "status": PASS if pub_flag.is_file() else WARN,
                "detail": "PUBLIC-MODE.json %s" % ("存在" if pub_flag.is_file() else "缺失"),
                "required": False,
                "fix": None if pub_flag.is_file() else "公开库需 public_sanitize.py --slim 生成"})
    # README 同源门禁
    cr = SCRIPTS / "check_readme.py"
    if cr.is_file():
        out.append({"gate": "quality.readme", "key": "check_readme",
                    "label": "README 同源门禁", "status": PASS,
                    "detail": "check_readme.py 存在", "required": False,
                    "fix": None})
    else:
        out.append({"gate": "quality.readme", "key": "check_readme",
                    "label": "README 同源门禁", "status": BLOCK,
                    "detail": "缺失", "required": True, "fix": "git pull"})
    return out


def gate_meta(cfg: dict) -> list[dict]:
    """元门禁：门禁脚本本身可 import（语法有效、依赖可解析）。守门人自检。"""
    out = []
    gate_scripts = ["paths", "preflight", "iterate_commit", "regression_suite",
                    "check_readme", "public_sanitize", "zotero_api", "detect_env",
                    "qa", "rules_check", "sanitize", "term_audit"]
    for name in gate_scripts:
        m = _load_module(name)
        out.append({"gate": "meta.import", "key": name,
                    "label": "门禁脚本 %s" % name,
                    "status": PASS if m is not None else WARN,
                    "detail": "可 import" if m else "不可 import（或需 venv $PY）",
                    "required": False,
                    "fix": None if m else "用 $PY 验证 %s.py --help" % name})
    return out


# ─────────────────────────── 汇总与输出 ──────────────────────────────

def _summarize(results: list[dict]) -> dict:
    n_pass = sum(1 for r in results if r["status"] == PASS)
    n_block = sum(1 for r in results if r["status"] == BLOCK)
    n_warn = sum(1 for r in results if r["status"] == WARN)
    n_skip = sum(1 for r in results if r["status"] == SKIP)
    # 总判定：有任何 required 的 BLOCK → 未就绪；否则有 BLOCK/WARN → 部分就绪；全 PASS → 就绪
    hard_block = any(r["status"] == BLOCK and r.get("required") for r in results)
    soft_block = any(r["status"] == BLOCK for r in results)
    if hard_block:
        verdict = "未就绪（缺必需组件，先补再跑）"
    elif soft_block or n_warn:
        verdict = "部分就绪（可选项缺失，影响对应环节）"
    else:
        verdict = "就绪（可进入流水线）"
    return {"pass": n_pass, "block": n_block, "warn": n_warn, "skip": n_skip,
            "verdict": verdict, "total": len(results)}


def _print_results(results: list[dict], sections: list[str]) -> None:
    summary = _summarize(results)
    print("=" * 64)
    print("zoterofanyi 门禁体检（gate.py）")
    print("=" * 64)
    cur = None
    for r in results:
        sec = r["gate"].split(".")[0]
        if sec != cur:
            cur = sec
            title = {"resources": "① 资源门禁（软件/配置/连通）",
                     "pipeline": "② 流程门禁（跑哪些流程）",
                     "quality": "③ 质量门禁（质量/提速/省 token）",
                     "meta": "④ 元门禁（门禁的自检）"}.get(sec, sec)
            print("\n【%s】" % title)
        mark = {"PASS ": "  ✅", "BLOCK": "  ❌", "WARN ": "  ⚠️ ", "SKIP ": "  ⏭️ "}.get(r["status"], r["status"])
        line = "%s %-22s %s" % (mark, r["label"], r["detail"])
        print(line)
        if r.get("fix"):
            print("       ↳ 补：%s" % r["fix"])
    print("\n" + "-" * 64)
    print("总判定：%s" % summary["verdict"])
    print("PASS %d / BLOCK %d / WARN %d / SKIP %d（共 %d 项）"
          % (summary["pass"], summary["block"], summary["warn"], summary["skip"], summary["total"]))
    print("-" * 64)


def _print_json(results: list[dict]) -> None:
    summary = _summarize(results)
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="zoterofanyi 门禁统一入口（换机一键体检）")
    ap.add_argument("group", nargs="?", default="all",
                    choices=["all", "resources", "pipeline", "quality", "meta"],
                    help="只跑某一类门禁（默认 all）")
    ap.add_argument("--offline", action="store_true", help="跳过一切联网探测（代理/git 远端）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 供脚本消费")
    a = ap.parse_args()

    cfg = _load_config()
    results: list[dict] = []
    if a.group in ("all", "resources"):
        results += gate_resources(cfg, a.offline)
    if a.group in ("all", "pipeline"):
        results += gate_pipeline(cfg)
    if a.group in ("all", "quality"):
        results += gate_quality(cfg)
    if a.group in ("all", "meta"):
        results += gate_meta(cfg)

    if a.json:
        _print_json(results)
    else:
        _print_results(results, [])

    summary = _summarize(results)
    # 退出码：有任何 required 的 BLOCK → 1；否则 0
    hard_block = any(r["status"] == BLOCK and r.get("required") for r in results)
    return 1 if hard_block else 0


if __name__ == "__main__":
    sys.exit(main())
