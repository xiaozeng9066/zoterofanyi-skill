#!/usr/bin/env python3
"""pipeline.py —— zoterofanyi 一键编排：把 16 个脚本收成 3 条命令。

为什么要有它
------------
流水线原本是一步一个脚本，每步都要停下来看输出、再决定下一步。
在对话式 Agent 里，这等于每步一次模型往返——一篇论文 40+ 次，既慢又费 token，
而且每次往返都要把上下文重新读一遍。真正需要模型智能的只有两件事：**翻译、审校**。
其余（抽取 / 装配 / 标记 / 门禁 / 合并 / 索引 / 排版）全是确定性文件处理，
应该由一个本地进程一口气跑完，几秒钟，零模型调用。

三个阶段
--------
  prepare  源 PDF → Gate 1 通过 → 译前分块 + 翻译指南        （纯本地，0 token）
  check    合并译文 → QA → 规则检查 → 审校分块 + 必查清单    （纯本地，0 token）
  finish   应用修订 → 脚注索引 → Gate 2 → 生成并校验 PDF     （纯本地，0 token）

典型用法
--------
  python pipeline.py prepare --work <论文工作区> --pdf <src.pdf>
  #   派 N 个翻译子代理，各读 work/chunks/chunk_NNN.json + TRANSLATION_GUIDE.md
  #   写出 work/chunks/trans_NNN.json
  python pipeline.py check   --work <论文工作区>
  #   派 M 个审校子代理，各读 work/review/review_NNN.json + CHECK_FINDINGS.md
  #   写出 work/fixes/fix_NNN.json
  python pipeline.py finish  --work <论文工作区> --out-pdf <成品.pdf> --label "Flamme et al., 2020"

任何一步失败都以非零码退出并把该步输出尾部打印出来，不会静默跳过。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PY = sys.executable

# ── 阶段计时：把真实 wall-clock 写进 <work>/reports/timings.json（度量闭环用）──
def _timings_path(work):
    return Path(work) / "reports" / "timings.json"


def _read_timings(work):
    p = _timings_path(work)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_timings(work, data):
    p = _timings_path(work)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class StageTimer:
    """在某个 pipeline 命令内记录各子阶段耗时，退出时累积进 timings.json。"""

    def __init__(self, work, stage):
        self.work = Path(work)
        self.stage = stage  # prepare / check / finish
        self.start = time.time()
        self.marks = []

    def mark(self, label):
        self.marks.append((label, time.time() - self.start))

    def done(self):
        dur = time.time() - self.start
        data = _read_timings(self.work)
        # 阶段累计（同名阶段取最近一次）
        data[self.stage] = round(dur, 2)
        data[self.stage + "_detail"] = [[l, round(t, 2)] for l, t in self.marks]
        data["_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_timings(self.work, data)
        return dur


# ────────────────────────────── 基础设施 ──────────────────────────────
def sh(cmd, needed=True, tail=0, env=None):
    """跑一个子进程；needed=True 时失败即中止。"""
    cmd = [str(c) for c in cmd]
    print("  $ " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if tail and out:
        for line in out.splitlines()[-tail:]:
            print("    " + line)
    if p.returncode != 0 and needed:
        print("\n[FAIL] 退出码 %d\n%s\n" % (p.returncode, out[-3000:]))
        raise SystemExit(p.returncode)
    return p.returncode, out


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def blocks_of(path):
    d = load_json(path, {})
    if isinstance(d, dict):
        return d.get("blocks") or []
    return d or []


def gate(path):
    """读 preflight 报告，返回 (BLOCK 数, WARN 数, issues)。"""
    d = load_json(path, {}) or {}
    counts = d.get("counts") or {}
    blk = counts.get("BLOCK")
    warn = counts.get("WARN")
    issues = d.get("issues") or []
    if blk is None:  # 兼容无 counts 的旧格式
        blk = sum(1 for x in issues if str(x.get("level", "")).upper() == "BLOCK")
        warn = sum(1 for x in issues if str(x.get("level", "")).upper() == "WARN")
    return int(blk or 0), int(warn or 0), issues


def print_issues(issues, limit=40):
    for x in issues[:limit]:
        lv = str(x.get("level", "")).upper()
        if lv not in ("BLOCK", "FAIL", "WARN"):
            continue
        print("    [%s] %s %s | %s" % (lv, x.get("code") or x.get("check") or x.get("rule") or "",
                                      x.get("id", ""), str(x.get("msg") or x.get("message") or "")[:160]))


def write_guide(work, meta_path):
    """生成译前指南：本篇术语表 + 历史词级纠正 + 整句错误模式 + 硬规则。"""
    gm = load_json(meta_path, {}) or {}
    gl = gm.get("glossary") or []
    tf = load_json(SKILL / "tm" / "term_fixes.json", {}) or {}
    ep = load_json(SKILL / "tm" / "error_patterns.json", {}) or {}
    title = gm.get("title_en") or "本文"
    L = ["# 翻译指南 · %s" % title, "",
         "> 本文件由 pipeline.py 自动生成，含本篇术语表与历史审校记忆。逐条遵守。", "",
         "## 一、本篇术语表（严格统一，value 是最小可匹配词）", ""]
    for row in gl:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            L.append("- %s -> %s" % (row[0], row[1]))
        elif isinstance(row, dict):
            L.append("- %s -> %s" % (row.get("en") or row.get("term"), row.get("zh")))
    L += ["", "## 二、历史审校词级纠正（tm/term_fixes.json，必须遵守）", "",
          json.dumps(tf, ensure_ascii=False, indent=1), "",
          "## 三、历史整句错误模式（tm/error_patterns.json，避免重犯）", "",
          json.dumps(ep, ensure_ascii=False, indent=1), "",
          "## 四、硬性规则", "",
          "1. 学术级中文，不是机翻；句子完整、术语统一，按中文习惯重组长句，但不得增删信息。",
          "2. 保留上标编号 ^{1} ^{2} 25^{th} 原样；数字、百分比、单位、p 值、DOI 与原文完全一致。",
          "3. 参考文献区、表格/插图原图不译；标题、图注、表题、表注、正文段落须译。",
          "4. 机构名/期刊缩写/法规机构缩写按术语表或保留英文缩写。",
          "5. 不确定术语保留英文并加简短译者注，不硬造译名。",
          "6. 输出每个 item 的 id 原样，zh 为对应中文；不改动 en。",
          "7. 中文里汉字与阿拉伯数字之间留一个空格（如「增加 10.8 个百分点」）。", ""]
    out = Path(work) / "chunks" / "TRANSLATION_GUIDE.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out, len(gl)


def find_reportlab_python():
    """找一个能 import reportlab 的解释器。

    优先级：local_config.json 的 mineru_python（detect_env 探测，换机最稳）
    → 历史硬编码路径（旧机兼容）→ 当前 $PY。每条都先验证能 import reportlab。
    """
    cands = []
    try:
        cfg = json.loads((SKILL / "local_config.json").read_text(encoding="utf-8"))
        mp = cfg.get("mineru_python")
        if mp:
            cands.append(mp)
    except Exception:
        pass
    cands += [
        os.path.expanduser("~/.dsh/runtimes/mineru/venv310/Scripts/python.exe"),
        os.path.expanduser("~/.dsh/runtimes/mineru/venv/Scripts/python.exe"),
        PY,
    ]
    for c in cands:
        if not c or not os.path.isfile(c):
            continue
        try:
            subprocess.run([c, "-c", "import reportlab"], capture_output=True, timeout=30, check=True)
            return c
        except Exception:
            continue
    return None


# ────────────────────────────── prepare ──────────────────────────────
def cmd_prepare(a):
    W = Path(a.work).resolve()
    work, reports = W / "work", W / "reports"
    work.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    timer = StageTimer(W, "prepare")
    pdf = Path(a.pdf).resolve() if a.pdf else W / "source.pdf"
    meta = Path(a.meta).resolve() if a.meta else work / "glossary_meta.json"
    if not pdf.is_file():
        raise SystemExit("源 PDF 不存在：%s" % pdf)
    if not meta.is_file():
        raise SystemExit("缺少 meta（术语表/标题）：%s" % meta)

    blocks = work / "blocks.json"
    regions = work / "regions.json"
    assets = work / "assets"
    fn = work / "footnotes.json"
    skel = work / "skeleton.json"
    skel_fn = work / "skeleton_fn.json"

    print("\n[1/8] MinerU 抽取")
    mids = list((work / "mineru").rglob("*_middle.json"))
    if mids and not a.force:
        print("  复用已有：%s" % mids[0])
        middle = mids[0]
    else:
        if a.no_mineru:
            raise SystemExit("没有 MinerU 输出且指定了 --no-mineru")
        sh([PY, HERE / "run_mineru.py", pdf, work / "mineru", "--lang", a.lang], tail=6)
        mids = list((work / "mineru").rglob("*_middle.json"))
        if not mids:
            raise SystemExit("MinerU 跑完但没找到 *_middle.json")
        middle = mids[0]

    print("\n[2/8] 结构化抽取（正文块 / 区域 / 裁图）")
    sh([PY, HERE / "extract_mineru.py", pdf, middle,
        "--out-blocks", blocks, "--out-regions", regions, "--assets", assets,
        "--dpi", a.dpi], tail=4)

    print("\n[3/8] 脚注抽取")
    sh([PY, HERE / "extract_footnotes.py", pdf, "--out", fn], tail=3)

    print("\n[4/8] 装配骨架")
    sh([PY, HERE / "assemble.py", blocks, regions, "--out", skel], tail=4)

    print("\n[5/8] 注入脚注 / 标记章节 / 套用元数据")
    sh([PY, HERE / "inject_footnotes.py", skel, fn, "--out", skel_fn], tail=3)
    sh([PY, HERE / "mark_sections.py", skel_fn, "--out", skel_fn], tail=3)
    sh([PY, HERE / "apply_meta.py", skel_fn, meta, "--out", skel_fn], tail=3)

    if a.layout_fix:
        print("\n[5b] 本篇版面修补：%s" % a.layout_fix)
        sh([PY, Path(a.layout_fix).resolve()], tail=8)

    print("\n[6/8] 清洗 + Gate 1（skeleton）")
    sh([PY, HERE / "sanitize.py", skel_fn, "--report", reports / "sanitize.json"], tail=3)
    sh([PY, HERE / "preflight.py", skel_fn, "--stage", "skeleton",
        "--assets", assets, "--out", reports / "preflight_skeleton.json"], tail=4)
    blk, warn, issues = gate(reports / "preflight_skeleton.json")
    print("  Gate 1: BLOCK=%d WARN=%d" % (blk, warn))
    if blk:
        print_issues(issues)
        print("\n[STOP] Gate 1 未通过，先修版面再继续（不要带病翻译）。")
        return 2

    print("\n[7/8] 译前分块")
    chunks = work / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)
    for old in chunks.glob("chunk_*.json"):
        old.unlink()
    sh([PY, HERE / "translate.py", "emit", skel_fn, "--outdir", chunks,
        "--chars", str(a.chars), "--cache", work / "cache.jsonl"], tail=4)

    print("\n[8/8] 生成翻译指南")
    gpath, ngl = write_guide(work, meta)

    chunk_files = sorted(chunks.glob("chunk_*.json"))
    n_items = 0
    n_chars = 0
    for cf in chunk_files:
        c = load_json(cf, {}) or {}
        items = c.get("items") or []
        n_items += len(items)
        n_chars += sum(len(x.get("en") or "") for x in items)

    print("\n" + "=" * 62)
    print("PREPARE 完成 — Gate 1 通过（0 BLOCK）")
    print("  骨架块数      : %d" % len(blocks_of(skel_fn)))
    print("  待译条目      : %d 条 / %d 英文行" % (n_items, n_chars))
    print("  分块          : %d 个（%s）" % (len(chunk_files), ", ".join(p.name for p in chunk_files)))
    print("  术语表        : %d 条" % ngl)
    print("  翻译指南      : %s" % gpath)
    timer.done()
    print("\n  下一步：并行派 %d 个子代理，每个读" % len(chunk_files))
    print("    %s/chunk_NNN.json  +  TRANSLATION_GUIDE.md" % chunks)
    print("    写出 %s/trans_NNN.json  （{\"items\":[{\"id\":..,\"zh\":..}]}）" % chunks)
    print("  全部写完后运行：python pipeline.py check --work %s" % W)
    print("=" * 62)
    return 0


# ────────────────────────────── check ──────────────────────────────
def collect_level_items(obj, out):
    """把任意嵌套结构里带 level 的条目收集出来。"""
    if isinstance(obj, dict):
        lv = str(obj.get("level") or obj.get("severity") or "").upper()
        if lv in ("FAIL", "WARN", "BLOCK", "PASS", "INFO"):
            out.append(obj)
            return
        for v in obj.values():
            collect_level_items(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_level_items(v, out)


def write_findings(work, qa_p, rules_p):
    q = load_json(qa_p, {}) or {}
    r = load_json(rules_p, {}) or {}
    # qa.py 的 issues 是 [level, msg] 数组对（不是对象），必须单独解析，
    # 否则整类线索会被静默丢掉——审校清单少一半，属于必须拦住的质量事故。
    groups = {}
    for res in q.get("results") or []:
        bid = res.get("id") or ""
        for iss in res.get("issues") or []:
            if isinstance(iss, (list, tuple)) and len(iss) >= 2:
                lv, msg = str(iss[0]).upper(), str(iss[1])
            elif isinstance(iss, dict):
                lv = str(iss.get("level") or iss.get("severity") or "").upper()
                msg = str(iss.get("msg") or iss.get("message") or "")
            else:
                lv, msg = "WARN", str(iss)
            if lv not in ("FAIL", "BLOCK", "WARN"):
                continue
            m = re.match(r"^([^:：]{1,24})[:：]", msg)
            ck = m.group(1).strip() if m else "qa"
            groups.setdefault((lv, ck), []).append((bid, msg[:220]))

    rule_items = []
    for f in r.get("findings") or []:
        lv = str(f.get("level") or "").upper()
        if lv in ("FAIL", "BLOCK", "WARN"):
            rule_items.append((lv, f.get("rule") or f.get("check") or "rule",
                               f.get("id") or "",
                               str(f.get("msg") or f.get("message") or f.get("evidence") or "")[:220]))

    n_fail = sum(len(v) for k, v in groups.items() if k[0] in ("FAIL", "BLOCK"))
    n_warn = sum(len(v) for k, v in groups.items() if k[0] == "WARN")
    # 与 qa.py 自报的计数交叉校验，避免解析口径变化再次静默丢条目
    expect_f, expect_w = q.get("fail"), q.get("warn")
    if isinstance(expect_f, int) and isinstance(expect_w, int) and (expect_f, expect_w) != (n_fail, n_warn):
        print("  [!] qa 计数不一致：自报 FAIL %s / WARN %s，实际解析 %d / %d（已按解析结果生成清单）"
              % (expect_f, expect_w, n_fail, n_warn))

    L = ["# 二遍审校必查清单", "",
         "> 由 pipeline.py 从 qa.py / rules_check.py 实测结果自动生成。",
         "> 清单内每条必须逐条处理：能修的给 fix；确认属误报的也要在回复里点名说明。", ""]
    L.append("## 一、qa.py 命中（FAIL %d / WARN %d）" % (n_fail, n_warn))
    L.append("")
    for (lv, ck), items in sorted(groups.items()):
        L.append("### [%s] %s —— %d 条" % (lv, ck, len(items)))
        for bid, msg in items[:60]:
            L.append("- %s : %s" % (bid, msg))
        L.append("")
    L += ["## 二、rules_check.py 命中（%d 条）" % len(rule_items), ""]
    n_rule = 0
    for lv, ck, bid, msg in rule_items:
        L.append("- [%s %s] %s | %s" % (lv, ck, bid, msg))
        n_rule += 1
    if not n_rule:
        L.append("- （无）")
    L += ["", "## 三、审校重点（除清单外还要通读全块）", "",
          "1. 术语统一：以 TRANSLATION_GUIDE.md 第一节术语表为准。",
          "2. 因果词：原文是相关/预测/一致表述时，译文不得出现「导致/造成/使得」。",
          "3. 比较级：原文 less/more/higher/lower than，译文必须体现比较。",
          "4. 数字一致：数字、百分比、单位、p 值、DOI 必须与原文一致；不得凭空新增。",
          "5. 上标 ^{1} ^{2} 原样保留；汉字与数字间留一个空格。",
          "6. 段末无终止标点的 WARN 多为作者行/单位行/跨页续句，属原文形态则不改。",
          "7. 机构名、期刊名、单位符号（dB/kHz 等）保留英文属正常，不算漏译。",
          "8. 英文数词转阿拉伯数字（Four -> 4）属正常，不算凭空新增数字。",
          "9. 只做最小必要修改，不要整段重写（除非确实错得离谱）；不改 en。", ""]
    out = Path(work) / "review" / "CHECK_FINDINGS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    n_fail = sum(len(v) for k, v in groups.items() if k[0] in ("FAIL", "BLOCK"))
    n_warn = sum(len(v) for k, v in groups.items() if k[0] == "WARN")
    return out, n_fail, n_warn, n_rule


def cmd_check(a):
    W = Path(a.work).resolve()
    work, reports = W / "work", W / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    timer = StageTimer(W, "check")
    skel_fn = work / "skeleton_fn.json"
    chunks = work / "chunks"
    trans = sorted(chunks.glob("trans_*.json"))
    need = sorted(chunks.glob("chunk_*.json"))
    if not need:
        raise SystemExit("没有分块文件，先跑 prepare")
    if len(trans) < len(need):
        missing = [c.stem.replace("chunk_", "") for c in need
                   if not (chunks / ("trans_" + c.stem.replace("chunk_", "") + ".json")).is_file()]
        print("[!] 还有 %d 块没译：%s" % (len(missing), ", ".join(missing)))
        if not a.allow_partial:
            raise SystemExit("译文不全，拒绝合并（防止静默丢段）。")

    bil = work / "bilingual.json"

    print("\n[1/5] 合并译文")
    sh([PY, HERE / "translate.py", "merge", skel_fn, "--chunks", chunks,
        "--out", bil, "--cache", work / "cache.jsonl"], tail=4)

    print("\n[2/5] QA")
    sh([PY, HERE / "qa.py", bil, "--out", reports / "qa.json"], tail=3, needed=False)

    print("\n[3/5] 规则检查")
    rc = [PY, HERE / "rules_check.py", bil, "--out", reports / "rules_check.json",
          "--augmented", reports / "rules_augmented.json"]
    exc = SKILL / "templates" / "term_exceptions.json"
    if exc.is_file():
        rc += ["--term-exceptions", exc]
    sh(rc, tail=4, needed=False)

    print("\n[4/5] 生成审校分块（patch 模式）")
    review = work / "review"
    review.mkdir(parents=True, exist_ok=True)
    for old in review.glob("review_*.json"):
        old.unlink()
    sh([PY, HERE / "emit_review.py", bil, "--outdir", review,
        "--per-chunk", str(a.per_chunk), "--mode", "patch"], tail=4)

    print("\n[5/5] 汇总必查清单")
    fpath, n_fail, n_warn, n_rule = write_findings(
        work, reports / "qa.json", reports / "rules_check.json")

    rfiles = sorted(review.glob("review_*.json"))
    n_rev = sum(len((load_json(p, {}) or {}).get("items") or []) for p in rfiles)
    print("\n" + "=" * 62)
    print("CHECK 完成")
    print("  合并条目      : %d" % len(blocks_of(bil)))
    print("  qa            : FAIL %d / WARN %d" % (n_fail, n_warn))
    print("  规则检查      : %d 条命中" % n_rule)
    print("  审校分块      : %d 个 / %d 条" % (len(rfiles), n_rev))
    print("  必查清单      : %s" % fpath)
    print("\n  下一步：并行派 %d 个审校子代理，各读 review_NNN.json + CHECK_FINDINGS.md" % len(rfiles))
    print("    写出 %s/fixes/fix_NNN.json" % work)
    print("  全部写完后运行：python pipeline.py finish --work %s" % W)
    timer.done()
    print("=" * 62)
    return 0


# ────────────────────────────── finish ──────────────────────────────
def cmd_finish(a):
    W = Path(a.work).resolve()
    work, reports = W / "work", W / "reports"
    timer = StageTimer(W, "finish")
    bil = work / "bilingual.json"
    fixed = work / "bilingual_fixed.json"
    final = work / "bilingual_final.json"
    if not bil.is_file():
        raise SystemExit("缺少 bilingual.json，先跑 check")

    print("\n[1/5] 应用修订")
    fixdir = work / "fixes"
    if fixdir.is_dir() and any(fixdir.glob("fix_*.json")):
        sh([PY, HERE / "apply_fixes.py", bil, "--fixdir", fixdir,
            "--out", fixed, "--log", reports / "changes.json"], tail=6)
    else:
        print("  没有 fix 文件，直接沿用合并结果")
        fixed.write_text(bil.read_text(encoding="utf-8"), encoding="utf-8")

    print("\n[2/5] 脚注索引")
    sh([PY, HERE / "build_fn_index.py", fixed, "--out", final,
        "--report", reports / "fn_index.json"], tail=3, needed=False)
    if not final.is_file():
        final.write_text(fixed.read_text(encoding="utf-8"), encoding="utf-8")

    print("\n[3/5] Gate 2（full）")
    sh([PY, HERE / "preflight.py", final, "--stage", "full",
        "--assets", work / "assets", "--out", reports / "preflight_full.json"], tail=4)
    blk, warn, issues = gate(reports / "preflight_full.json")
    print("  Gate 2: BLOCK=%d WARN=%d" % (blk, warn))
    if blk:
        print_issues(issues)
        print("\n[STOP] Gate 2 未通过，禁止交付。")
        return 2

    if a.skip_pdf:
        print("\n[4/5] 跳过 PDF（--skip-pdf）")
        timer.done()
        return 0

    print("\n[4/5] 生成双语 PDF")
    out_pdf = Path(a.out_pdf).resolve() if a.out_pdf else (W / (W.name + "_双语.pdf"))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    if out_pdf.is_file():
        out_pdf.unlink()
    label = a.label or ""

    # 首选 HTML->PDF（版式最好）；本机 Edge 常被沙箱打死，失败自动回退 reportlab
    ok_pdf = False
    if not a.no_edge:
        sh([PY, HERE / "build_pdf.py", final, out_pdf, label], tail=4, needed=False)
        ok_pdf = out_pdf.is_file() and out_pdf.stat().st_size > 10240
        if not ok_pdf:
            print("  [!] HTML 渲染路线没产出有效 PDF（Edge/Chromium 不可用），回退 reportlab")

    if not ok_pdf:
        rp = find_reportlab_python()
        if not rp:
            print("  [FAIL] 找不到带 reportlab 的解释器。请 pip install reportlab 或用 --no-edge 之外的机器生成。")
            return 3
        sh([rp, HERE / "build_pdf_reportlab.py", final, out_pdf, label], tail=4)
        ok_pdf = out_pdf.is_file() and out_pdf.stat().st_size > 10240

    if not ok_pdf:
        print("  [FAIL] PDF 生成失败")
        return 3

    print("\n[5/5] 校验成品")
    info = {"path": str(out_pdf), "bytes": out_pdf.stat().st_size}
    try:
        import fitz
        d = fitz.open(str(out_pdf))
        txt = "".join(pg.get_text() for pg in d)
        info["pages"] = d.page_count
        info["cjk_chars"] = len(re.findall(r"[\u4e00-\u9fff]", txt))
        info["has_text_layer"] = info["cjk_chars"] > 500
    except Exception as e:
        info["verify_error"] = str(e)

    print("\n" + "=" * 62)
    print("FINISH 完成 — Gate 2 通过（0 BLOCK）")
    for k in ("path", "bytes", "pages", "cjk_chars", "has_text_layer"):
        if k in info:
            print("  %-14s: %s" % (k, info[k]))
    if "verify_error" in info:
        print("  verify        : %s" % info["verify_error"])

    # 度量埋点：采集本篇硬指标落盘（跨篇/跨版本对比用）
    timer.done()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("metrics", HERE / "metrics.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        data = m.collect(W, skill_version=m._git_version(), label=label)
        data.update(m.estimate_tokens(data))
        data["pdf_pages"] = info.get("pages", data.get("pdf_pages", 0))
        data["cjk_chars"] = info.get("cjk_chars", data.get("cjk_chars", 0))
        m.record(W, data)
        print("\n  metrics       : 已记录（%d 块 / %d 译条 / %d 修订 / 门禁 %d-%d / %d 图）"
              % (data.get("blocks", 0), data.get("translated_items", 0),
                 data.get("fixes", 0), data.get("gate1_block", 0), data.get("gate2_block", 0),
                 data.get("images", 0)))
        # 自动迭代审计：本篇指标 vs 历史，产出趋势 + 迭代建议（保证「自动迭代」）
        try:
            spec2 = importlib.util.spec_from_file_location("iterate_audit", HERE / "iterate_audit.py")
            ia = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(ia)
            audit_path = ia.audit(W, W.parent)
            print("  iterate_audit : %s" % audit_path)
        except Exception as e:
            print("  iterate_audit : 失败（%s）" % e)
    except Exception as e:
        print("\n  metrics       : 采集失败（%s）" % e)

    print("\n  下一步：挂回 Zotero（zotero_mcp.py cli attach / note / verify），然后走迭代协议。")
    print("=" * 62)
    return 0


# ────────────────────────────── status ──────────────────────────────
def cmd_status(a):
    W = Path(a.work).resolve()
    work, reports = W / "work", W / "reports"
    def mark(p):
        p = Path(p)
        return "OK  " if p.is_file() and p.stat().st_size > 0 else " -- "
    print("\n工作区：%s" % W)
    print("  %s source.pdf" % mark(W / "source.pdf"))
    print("  %s work/blocks.json" % mark(work / "blocks.json"))
    print("  %s work/skeleton_fn.json" % mark(work / "skeleton_fn.json"))
    print("  %s reports/preflight_skeleton.json (Gate 1)" % mark(reports / "preflight_skeleton.json"))
    chunks = sorted((work / "chunks").glob("chunk_*.json")) if (work / "chunks").is_dir() else []
    trans = sorted((work / "chunks").glob("trans_*.json")) if (work / "chunks").is_dir() else []
    print("  %s 分块 %d 个 / 已译 %d 个" % ("OK  " if chunks and len(trans) >= len(chunks) else " -- ", len(chunks), len(trans)))
    print("  %s work/bilingual.json" % mark(work / "bilingual.json"))
    reviews = sorted((work / "review").glob("review_*.json")) if (work / "review").is_dir() else []
    fixes = sorted((work / "fixes").glob("fix_*.json")) if (work / "fixes").is_dir() else []
    print("  %s 审校分块 %d / fix 文件 %d" % ("OK  " if reviews and len(fixes) >= len(reviews) else " -- ", len(reviews), len(fixes)))
    print("  %s work/bilingual_final.json" % mark(work / "bilingual_final.json"))
    g2 = reports / "preflight_full.json"
    if g2.is_file():
        blk, warn, _ = gate(g2)
        print("  %s reports/preflight_full.json (Gate 2: BLOCK=%d WARN=%d)" % ("OK  " if blk == 0 else "FAIL", blk, warn))
    else:
        print("  --  reports/preflight_full.json (Gate 2)")
    pdfs = sorted(W.glob("*.pdf"))
    for p in pdfs:
        print("  OK   %s (%.1f MB)" % (p.name, p.stat().st_size / 1048576))
    return 0


def main():
    ap = argparse.ArgumentParser(description="zoterofanyi 一键编排")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="源 PDF -> Gate 1 -> 分块 + 翻译指南")
    p.add_argument("--work", required=True, help="论文工作区目录")
    p.add_argument("--pdf", default="", help="源 PDF（默认 <work>/source.pdf）")
    p.add_argument("--meta", default="", help="术语表/元数据（默认 <work>/work/glossary_meta.json）")
    p.add_argument("--lang", default="en")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--chars", type=int, default=6000, help="每块英文上限字符")
    p.add_argument("--layout-fix", default="", help="本篇版面修补脚本路径（在 apply_meta 之后执行）")
    p.add_argument("--no-mineru", action="store_true", help="复用已有 MinerU 输出")
    p.add_argument("--force", action="store_true", help="忽略已有输出，全部重跑")
    p.set_defaults(func=cmd_prepare)

    c = sub.add_parser("check", help="合并译文 -> QA/规则 -> 审校分块与清单")
    c.add_argument("--work", required=True)
    c.add_argument("--per-chunk", type=int, default=20)
    c.add_argument("--allow-partial", action="store_true", help="允许译文不全时强行合并（不推荐）")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("finish", help="应用修订 -> Gate 2 -> 生成并校验 PDF")
    f.add_argument("--work", required=True)
    f.add_argument("--out-pdf", default="")
    f.add_argument("--label", default="", help="页脚标签，如 'Flamme et al., 2020'")
    f.add_argument("--skip-pdf", action="store_true")
    f.add_argument("--no-edge", action="store_true", help="跳过 HTML 渲染，直接用 reportlab")
    f.set_defaults(func=cmd_finish)

    s = sub.add_parser("status", help="显示各阶段产物就绪情况")
    s.add_argument("--work", required=True)
    s.set_defaults(func=cmd_status)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
