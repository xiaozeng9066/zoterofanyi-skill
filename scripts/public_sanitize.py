#!/usr/bin/env python3
"""公开库发布前的数据最小化（v2.7，规则 59）。

问题：`fixtures/` 占 skill 仓库约 74% 体积，含 7 篇已发表论文的大段英文正文；
`tm/error_patterns.json(+jsonl)` 含整段中文译文；两者属于「公开论文的大段再分发」，
不适合放进公开仓库。但黄金测试集真正依赖的只是**结构、计数与少量短断言串**，
不是那些正文。

做法：生成一份**脱敏副本**（不动唯一真源），把所有长正文换成自造的合成学术句：
  - 保留 pages / paragraphs / blocks / kind / id / pages / size / 几何字段；
  - 保留 expected.json 里的 must_contain（短标题、术语）与 must_not_contain（OCR 垃圾串）；
  - 合成句刻意含 0.54 / 95% CI / billion / million / 1970s / ^{1-3} 等边界模式，
    使 qa.py / rules_check.py / audit_document.py 的误报判定仍被守住；
  - 邮箱 → author@example.org；pdf_name → 合成名；tm 整句模式 → 短摘要；
  - 副本只取 `git ls-files`，因此 local_config.json、dist/、__pycache__ 天然不进。

用法：
  python scripts/public_sanitize.py --audit                       # 扫描真源的风险
  python scripts/public_sanitize.py --slim --out dist/public      # 生成脱敏副本
  python scripts/public_sanitize.py --verify --dir dist/public    # 黄金集 + 残留扫描
  python scripts/public_sanitize.py --verify --dir dist/public --update-expected
      # 脱敏导致 FAIL 计数变化时，按协议把实测值写回副本的 expected.json（须显式指定）
  python scripts/public_sanitize.py --all --out dist/public       # slim + verify + 更新断言
  python scripts/public_sanitize.py --gate                        # 公开主库提交门禁（见下）

**公开主库护栏（v2.8）**：`--slim` 会在副本写入 `PUBLIC-MODE.json`（私有真源没有）。
`iterate_commit.py` 检测到它就跑 `--gate`：若 `fixtures/` / `tm/` 混入真实论文正文则 BLOCK，
防止「公开库成为日常同步目标后，下一篇论文的新 fixture 被自动推上去」。
真实全强度快照一律放 `fixtures_full/`（gitignored），`fixtures/` 只放合成案例。

护栏：本脚本**只读真源**，绝不修改 SKILL.md / scripts / fixtures / tm / git 历史。
公开仓库必须由脱敏副本单独建仓（干净历史），不能把现有私有仓库直接改可见性——
v2.2..v2.6 的每个 tag 都含完整 fixtures，转 public 会连同历史一起公开。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
SCRIPTS = ROOT / "scripts"
DEFAULT_OUT = ROOT / "dist" / "public"

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
WINPATH_RE = re.compile(r"[A-Za-z]:[\\/](?:Users|users)[\\/]\w+")
LONG_EN_RE = re.compile(r'"((?:[^"\\]|\\.){100,})"')
SECRET_PATTERNS = {
    "github_pat": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "openai_like_key": re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "assigned_secret": re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*['\"][^'\"\s]{8,}"),
}

# 自造句子，不含任何论文表达。设计约束（实测反推）：
#  - 双语数字必须**字面一致**，否则 qa.py 报「译文出现原文没有的数字」；
#  - 不能含 ^{} 上标，否则 figure/table 的成对 caption 字段被 audit 判「上标缺失」；
#  - 保留 0.47-0.61 / 0.47–0.61 连接符差异与 95% CI / 95% 置信区间，
#    仍守住 qa canon 的等价判定（规则 23/24）。
SYNTH_EN = (
    "Synthetic regression paragraph %03d. The pooled estimate was 0.54 "
    "(95%% CI 0.47-0.61); values 1.5 and 450 were recorded."
)
SYNTH_ZH = (
    "合成回归段落 %03d。合并估计值为 0.54（95%% 置信区间 0.47–0.61）；"
    "记录数值 1.5 与 450。"
)
SYNTH_REF = "Synthetic Author %03d, Synthetic Journal, vol. 12, pp. 34-56, 2020."
SYNTH_PDF = "SYNTHETIC-CASE-%02d.pdf"
PLACEHOLDER_EMAIL = "author@example.org"

# bilingual.json 里的文本类字段；_zh 结尾用中文合成句，其余用英文合成句。
TEXT_KEYS = {"en", "zh", "text", "caption_en", "caption_zh", "note_en", "note_zh",
             "title", "title_en", "title_zh", "label", "legend", "note", "head", "body"}
# meta 里含论文书目信息的字段（非正文，但为「零原文」一并合成化）
META_TEXT_KEYS = {"title_en", "title_zh", "author_year", "journal", "doi", "qa_notes"}


def tracked_files() -> list[str]:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit("BLOCK: 当前目录不是 git 仓库：" + r.stderr.strip())
    return [x.strip() for x in r.stdout.splitlines() if x.strip()]


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def scrub_scalar(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = EMAIL_RE.sub(PLACEHOLDER_EMAIL, s)
    s = s.replace(PLACEHOLDER_EMAIL + "." + PLACEHOLDER_EMAIL, PLACEHOLDER_EMAIL)
    return s


def synth_block(i: int, lang: str) -> str:
    return (SYNTH_EN if lang == "en" else SYNTH_ZH) % (i % 997 + 1)


# ---------------------------------------------------------------- audit

def cmd_audit() -> int:
    files = tracked_files()
    by_dir: dict[str, list[int]] = {}
    long_total = 0
    email_total = 0
    secrets = []
    for rel in files:
        p = ROOT / rel
        if p.suffix.lower() in {".png", ".jpg", ".pdf", ".zip", ".pyc", ".xpi", ".sqlite", ".gif"}:
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        d = rel.split("/")[0] if "/" in rel else "(根)"
        n_long = 0
        for m in LONG_EN_RE.finditer(t):
            s = m.group(1)
            ratio = sum(c.isalpha() or c.isspace() for c in s) / max(len(s), 1)
            if ratio > 0.85 and re.search(r"[A-Za-z]{4}", s):
                n_long += len(s)
        n_mail = len(EMAIL_RE.findall(t))
        by_dir.setdefault(d, [0, 0, 0])
        by_dir[d][0] += 1
        by_dir[d][1] += n_long
        by_dir[d][2] += n_mail
        long_total += n_long
        email_total += n_mail
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(t):
                secrets.append(f"{rel}: {name}")

    print("=== 公开化风险审计（只读） ===")
    print(f"  入库文件：{len(files)}")
    for d, (n, chars, mails) in sorted(by_dir.items(), key=lambda x: -x[1][1]):
        print(f"  {d:20s} {n:3d} 文件 · 长英文 {chars/1024:8.1f} KB · 邮箱 {mails:3d} 处")
    print(f"  合计：长英文 {long_total/1024:.1f} KB · 邮箱 {email_total} 处 · 凭证命中 {len(secrets)}")
    for s in secrets[:10]:
        print("   [凭证]", s)
    print("\n  结论：fixtures/ 与 tm/ 的长文本属论文正文/译文摘录；scripts/ 的长英文是代码字符串，无需处理。")
    print("  历史污染：v2.2 起每个 tag 都含完整 fixtures，现有仓库不能直接转 public（见 PUBLIC-RELEASE.md）。")
    return 0


# ---------------------------------------------------------------- slim

def case_quotes(case: Path, case_idx: int) -> tuple[list[str], list[str], list[str]]:
    """返回 (原始短标题串, 合成锚点, OCR 禁串)。

    短标题串也换成合成锚点并同步写回 expected.json：公开副本做到**零论文原文**。
    检测能力不变——断言仍在守「该短串是否出现在抽取结果里」，只是串本身自造。
    """
    exp = load_json(case / "expected.json")
    a = exp.get("assertions", {})
    must = list(a.get("must_contain") or [])
    forbidden = list(a.get("must_not_contain") or [])
    anchors = ["SYNTHETIC-ANCHOR-%02d-%02d" % (case_idx, j + 1) for j in range(len(must))]
    return must, anchors, forbidden


def slim_extraction_blocks(src: Path, dst: Path, anchors: list[str]) -> int:
    doc = load_json(src)
    changed = 0
    seq = 0
    injected = 0
    for pg in doc.get("pages", []):
        for para in pg.get("paragraphs", []):
            if not isinstance(para, dict) or "text" not in para:
                continue
            seq += 1
            txt = synth_block(seq, "en") if injected >= len(anchors) else (
                anchors[injected] + " " + synth_block(seq, "en"))
            if injected < len(anchors):
                injected += 1
            para["text"] = scrub_scalar(txt)
            changed += 1
        for key in ("images", "drawings"):
            items = pg.get(key)
            if not isinstance(items, list):
                continue  # 有些案例这里存的是计数
            for item in items:
                if isinstance(item, dict):
                    for k, v in list(item.items()):
                        if not isinstance(v, str) or not v.strip():
                            continue
                        if k in TEXT_KEYS or len(v) > 60:
                            item[k] = scrub_scalar(synth_block(seq, "zh" if k.endswith("_zh") else "en"))
    # 锚点比段落还多时补到首个段落
    if injected < len(anchors) and doc.get("pages") and doc["pages"][0].get("paragraphs"):
        p0 = doc["pages"][0]["paragraphs"][0]
        p0["text"] = " ".join(anchors[injected:]) + " " + p0["text"]
    doc["source_pdf"] = "<WORKSPACE>/zotero-translate/<KEY>/source.pdf"
    doc["_synthetic"] = True
    save_json(dst, doc)
    return changed


def scrub_meta(value, seq: list[int], key: str = ""):
    """递归净化 meta / regions / table_leaks。

    实测踩坑：不能只看长度。regions.json 里 `caption_en` 的论文表题只有 78 字符，
    旧版 90 字符阈值漏掉它，被交叉验证抓到。已知文本字段一律按**字段名**替换，
    其余字段才用长度兜底（>=60 字符的英文串）。
    """
    if isinstance(value, dict):
        return {k: scrub_meta(v, seq, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_meta(v, seq, key) for v in value]
    if isinstance(value, str):
        s = scrub_scalar(value)
        if key in TEXT_KEYS or key in ("caption", "note", "title", "text", "legend"):
            if s.strip():
                seq[0] += 1
                return synth_block(seq[0], "zh" if key.endswith("_zh") else "en")
            return s  # 空字段保持空
        if len(s) > 60 and re.search(r"[A-Za-z]{4}", s):
            seq[0] += 1
            return synth_block(seq[0], "en")
        return s
    return value


def scrub_nested_strings(value, i: int):
    """legend / rows / raw_lines 这类**列表型**文本字段也要脱敏（实测漏过 1 条表格图例）。"""
    if isinstance(value, list):
        return [scrub_nested_strings(v, i) for v in value]
    if isinstance(value, dict):
        return {k: scrub_nested_strings(v, i) for k, v in value.items()}
    if isinstance(value, str):
        if not value.strip():
            return value
        n = i % 997 + 1
        return ("Synthetic label %03d" % n if re.search(r"[A-Za-z]{3}", value)
                else "合成标签 %03d" % n)
    return value


def slim_bilingual(src: Path, dst: Path, case_idx: int) -> int:
    doc = load_json(src)
    doc["_synthetic"] = True
    meta = doc.get("meta") or {}
    seq = [0]
    for k in META_TEXT_KEYS:
        if k in meta and isinstance(meta[k], str):
            meta[k] = ("Synthetic regression case %02d" % case_idx
                       if k in ("title_en", "author_year", "journal")
                       else "合成回归案例 %02d" % case_idx if k == "title_zh"
                       else "10.0000/synthetic-%02d" % case_idx if k == "doi"
                       else synth_block(seq[0], "zh"))
    for k in ("item_key", "attachment_key"):
        if k in meta:
            meta[k] = "SYNTH%02d" % case_idx
    if "source_pdf" in meta:
        meta["source_pdf"] = "<WORKSPACE>/zotero-translate/<KEY>/source.pdf"
    meta["pdf_name"] = SYNTH_PDF % case_idx
    doc["meta"] = scrub_meta(meta, seq)
    doc["table_leaks"] = scrub_meta(doc.get("table_leaks", []), seq)
    # glossary 是短术语对照（事实性词条，非论文表达），保留；仅清邮箱
    doc["glossary"] = scrub_meta(doc.get("glossary", []), seq)
    n = 0
    for i, b in enumerate(doc.get("blocks", [])):
        if not isinstance(b, dict):
            continue
        is_ref = bool(b.get("no_translate"))
        for k, v in list(b.items()):
            # 空字段必须保持空：表格块常见 caption_en="" / note_en=""，
            # 若填成合成句会被 qa/rules 判「有原文但无译文」的假 FAIL（实测 3 处）。
            if k not in TEXT_KEYS or not isinstance(v, str) or not v.strip():
                continue
            lang = "zh" if k.endswith("_zh") or k in ("zh", "note_zh") else "en"
            b[k] = SYNTH_REF % (i % 997 + 1) if is_ref else synth_block(i, lang)
            n += 1
        # 列表型文本字段（legend 图例 / rows 表格行 / raw_lines 原始行）
        for list_key in ("legend", "rows", "raw_lines"):
            if isinstance(b.get(list_key), list):
                b[list_key] = scrub_nested_strings(b[list_key], i)
    save_json(dst, doc)
    return n


def slim_regions(src: Path, dst: Path) -> int:
    doc = load_json(src)
    seq = [0]
    cleaned = scrub_meta(doc, seq)
    cleaned["source_pdf"] = "<WORKSPACE>/zotero-translate/<KEY>/source.pdf"
    cleaned["_synthetic"] = True
    save_json(dst, cleaned)
    return seq[0]


def slim_expected(src: Path, dst: Path, case_idx: int, anchors: list[str]) -> None:
    exp = load_json(src)
    exp["pdf_name"] = SYNTH_PDF % case_idx
    exp["synthetic"] = True
    a = exp.get("assertions", {})
    if "must_contain" in a:
        a["must_contain"] = anchors
    save_json(dst, exp)


def slim_tm(src: Path, dst: Path) -> int:
    """整句译文模式 → 短摘要；保留 wrong/right 的差异核心与来源标记。"""
    t = src.read_text(encoding="utf-8")
    n = 0
    if src.suffix == ".jsonl":
        lines = []
        for line in t.splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            n += shorten_entry(o)
            lines.append(json.dumps(o, ensure_ascii=False))
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    else:
        o = json.loads(t)
        items = o if isinstance(o, list) else [o]
        for it in items:
            n += shorten_entry(it)
        save_json(dst, o)
    return n


def shorten_entry(o) -> int:
    if not isinstance(o, dict):
        return 0
    n = 0
    for k in ("wrong", "right"):
        v = o.get(k)
        if isinstance(v, str) and len(v) > 80:
            o[k] = v[:60].rstrip("，,；;。 ") + "…〔公开版已截断，完整对照见私有库〕"
            n += 1
    if isinstance(o.get("reason"), str) and len(o["reason"]) > 120:
        o["reason"] = o["reason"][:100] + "…"
    return n


def cmd_slim(out: Path) -> int:
    files = tracked_files()
    if out.resolve().is_relative_to(ROOT.resolve()) and out.name != "public":
        # 允许 dist/public；其余目录内的输出需谨慎
        pass
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel in files:
        src, dst = ROOT / rel, out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not rel.startswith("fixtures/") and not rel.startswith("tm/"):
            # 文档/脚本：清邮箱与本机绝对路径（代码字符串里的长英文不动）
            try:
                t = src.read_text(encoding="utf-8")
            except Exception:
                shutil.copy2(src, dst)
                continue
            t2 = EMAIL_RE.sub(PLACEHOLDER_EMAIL, t)
            t2 = re.sub(r"C:[/\\]Users[/\\][^\s\"'`)}\]>,;]+", "%USERPROFILE%", t2)
            # 裸本机用户名 → <USER>；不动 GitHub 账户 zengfanfu 与本地身份 zengf@localhost
            t2 = re.sub(r"(?<![\w@])<USER>(?![\w@])", "<USER>", t2)
            dst.write_text(t2, encoding="utf-8", newline="\n")
            continue
        if rel.startswith("tm/"):
            if src.suffix in (".json", ".jsonl"):
                slim_tm(src, dst)
            else:
                shutil.copy2(src, dst)
            continue
        # fixtures
        parts = rel.split("/")
        if len(parts) < 3:
            shutil.copy2(src, dst)
            continue
        case_src = ROOT / "fixtures" / parts[1]
        case_idx = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()).index(parts[1]) + 1
        _, anchors, _ = case_quotes(case_src, case_idx)
        name = parts[2]
        if name == "bilingual.json":
            slim_bilingual(src, dst, case_idx)
        elif name == "blocks.json":
            slim_extraction_blocks(src, dst, anchors)
        elif name == "regions.json":
            slim_regions(src, dst)
        elif name == "expected.json":
            slim_expected(src, dst, case_idx, anchors)
        else:
            shutil.copy2(src, dst)
    # ★ 公开主库模式标记：私有真源没有此文件，故私有库不触发提交门禁；
    #   公开库 clone 后自带，iterate_commit.py 会强制跑 --gate（v2.8 护栏）。
    (out / "PUBLIC-MODE.json").write_text(
        json.dumps({
            "mode": "public",
            "require_sanitize_gate": True,
            "description": "公开主库：fixtures/ 只允许合成数据；真实全强度快照放 fixtures_full/（不入库）",
            "created": "2026-11-14",
            "enforced_by": "iterate_commit.py → public_sanitize.py --gate",
            "note": "删掉本文件不会让内容变干净，只会关掉护栏；禁止用 --no-tag/--skip-regression 绕过。",
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    # 公开版说明
    (out / "PUBLIC-NOTICE.md").write_text(
        "# 公开版说明\n\n"
        "本仓库是 `zoterofanyi` skill 的**脱敏公开分发版**。\n\n"
        "- `fixtures/` 内的英文/中文文本为自造合成句，不含任何已发表论文的正文；\n"
        "  仅保留结构、计数与少量短断言串，黄金集 `14/14 PASS` 可复现。\n"
        "- `tm/error_patterns.json*` 的整句对照已截断为短摘要。\n"
        "- 作者邮箱统一替换为 `author@example.org`。\n"
        "- 生成方式：私有主库跑 `python scripts/public_sanitize.py --all --out dist/public`。\n"
        "- 完整历史与全强度 fixture 保留在私有主库，**公开库不含其 git 历史**（历史里曾有论文正文）。\n",
        encoding="utf-8", newline="\n")
    before = sum((ROOT / r).stat().st_size for r in files)
    after = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"[slim] 脱敏副本已生成：{out}")
    print(f"       真源 {before/1024:.1f} KB → 副本 {after/1024:.1f} KB "
          f"（减少 {(before-after)/before*100:.1f}%）")
    return 0


# ---------------------------------------------------------------- verify

def load_regression_module():
    spec = importlib.util.spec_from_file_location("_zbf_reg", SCRIPTS / "regression_suite.py")
    m = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(m)
    return m


def cmd_verify(out: Path, update_expected: bool) -> int:
    if not out.exists():
        raise SystemExit(f"BLOCK: 找不到脱敏副本 {out}，先跑 --slim")
    print("=== 1) 黄金测试集（脱敏副本） ===")
    r = subprocess.run([sys.executable, str(SCRIPTS / "regression_suite.py"),
                        "--fixtures", str(out / "fixtures")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip()[-1600:])
    if r.stderr.strip():
        print("  stderr:", r.stderr.strip()[:300])

    if update_expected:
        m = load_regression_module()
        changed = []
        for case in sorted(p for p in (out / "fixtures").iterdir() if (p / "expected.json").exists()):
            exp = load_json(case / "expected.json")
            kind = exp.get("kind", "full")
            tmp = m.make_tmpdir("zbf-sanitize-")
            got, _ = (m.check_full(case, tmp, "", False) if kind == "full"
                      else m.check_extraction(case, False))
            shutil.rmtree(tmp, ignore_errors=True)
            edits = []
            # 断言在 expected.json 的 assertions 子对象里，不是顶层（实测踩过）
            assertions = exp.setdefault("assertions", {})
            for k, v in list(assertions.items()):
                if k in ("pdf_name", "must_contain", "must_not_contain") or k not in got:
                    continue
                if isinstance(v, dict):
                    for kk, vv in list(v.items()):
                        nv = got[k].get(kk)
                        if nv is not None and nv != vv:
                            v[kk] = nv
                            edits.append(f"{k}.{kk}: {vv} → {nv}")
                elif got[k] != v:
                    assertions[k] = got[k]
                    edits.append(f"{k}: {v} → {got[k]}")
            if edits:
                save_json(case / "expected.json", exp)
                changed.append((case.name, edits))
        if changed:
            print("\n=== 2) 按协议更新 expected.json（脱敏导致的预期变化） ===")
            for name, edits in changed:
                print(f"  {name}:")
                for e in edits:
                    print("    ", e)
            print("  理由：正文换成合成句后，检查器面对的输入变化属预期正确化；"
                  "必须在 CHANGELOG 写明（迭代协议）。")
        else:
            print("\n=== 2) expected.json 无需更新 ===")
        print("\n=== 3) 更新后复跑黄金集 ===")
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "regression_suite.py"),
                             "--fixtures", str(out / "fixtures")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r2.stdout.strip()[-900:])
        suite_ok = r2.returncode == 0
    else:
        suite_ok = r.returncode == 0

    print("\n=== 4) 残留扫描（论文正文 / 邮箱 / 本机路径 / 凭证） ===")
    problems: list[str] = []
    allowed: set[str] = set()
    for exp in (out / "fixtures").glob("*/expected.json"):
        e = load_json(exp)
        a = e.get("assertions", {})
        allowed |= set(a.get("must_contain") or [])
        allowed |= set(a.get("must_not_contain") or [])
    nfiles = 0
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        nfiles += 1
        rel = p.relative_to(out).as_posix()
        if rel.startswith("scripts/") and p.suffix == ".py":
            # 代码里的长英文串是 docstring/提示语，不是论文正文
            pass
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(t):
                problems.append(f"[凭证] {rel}: {name}")
        for m in WINPATH_RE.finditer(t):
            problems.append(f"[本机路径] {rel}: {m.group(0)[:70]}")
        for m in re.finditer(r"\bzengf\b", t):
            if "zengf@localhost" not in t[max(0, m.start()-20):m.end()+20]:
                problems.append(f"[本机用户名] {rel}")
                break
        for m in EMAIL_RE.finditer(t):
            e = m.group(0)
            if e.lower() not in {PLACEHOLDER_EMAIL, "zengf@localhost", "t@local"} and "example." not in e:
                problems.append(f"[邮箱] {rel}: {e}")
        # 论文正文候选：数据文件里 >=100 字符的英文串
        if rel.startswith(("fixtures/", "tm/")):
            for m in LONG_EN_RE.finditer(t):
                s = m.group(1)
                if s in allowed:
                    continue
                ratio = sum(c.isalpha() or c.isspace() for c in s) / max(len(s), 1)
                if ratio > 0.85 and re.search(r"[A-Za-z]{4}", s) and "Synthetic regression paragraph" not in s:
                    problems.append(f"[长英文] {rel}: {s[:70]}…")
                    break
    if "local_config.json" in [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]:
        problems.append("[本机配置] local_config.json 出现在公开副本")
    if (out / ".git").exists():
        problems.append("[历史] 副本含 .git（公开库必须从干净历史建仓）")

    print(f"  扫描 {nfiles} 文件；问题 {len(problems)} 处")

    # 第 5 步：全量交叉验证——真源 fixtures 的**正文字段**不许出现在副本。
    # 结构化地按字段名取值，而不是正则扫所有 JSON 字符串：glossary 的短术语词条、
    # 中文备注里的英文引文都不算「论文正文」，不误判；短于阈值的表题/图注反而能被抓到。
    print("\n=== 5) 原文泄漏交叉验证（真源正文 → 副本全文，全量） ===")
    prose: set[str] = set()

    def walk(o, key: str = "") -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str):
            if key not in TEXT_KEYS or len(o) < 50:
                return
            # ASCII-only 比率：汉字 isalpha() 也为 True，旧版会把中文备注误判成英文正文
            ascii_ratio = sum(
                c.isascii() and (c.isalpha() or c.isspace() or c in ".,;:()-/'%")
                for c in o) / len(o)
            if ascii_ratio > 0.85 and re.search(r"[A-Za-z]{10}", o):
                prose.add(o)
            if sum("\u4e00" <= c <= "\u9fff" for c in o) >= 40:
                prose.add(o)  # 中文译文段落同样是衍生内容

    for p in sorted(FIXTURES.glob("*/*.json")):
        if p.name == "expected.json":
            continue
        try:
            walk(load_json(p))
        except Exception:
            pass
    dst_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in sorted(out.rglob("*")) if p.is_file())
    leaks = sorted(s for s in prose if s in dst_text)
    print(f"  真源正文片段 {len(prose)} 条；副本命中 {len(leaks)} 条")
    for s in leaks[:5]:
        print("   LEAK:", s[:80])
    problems += [f"[原文泄漏] {s[:60]}…" for s in leaks]
    for x in problems[:20]:
        print("   ", x)
    if len(problems) > 20:
        print(f"    …另有 {len(problems)-20} 处")

    ok = suite_ok and not problems
    print(f"\n===== 公开化验证：{'PASS' if ok else 'FAIL'} "
          f"(黄金集 {'PASS' if suite_ok else 'FAIL'} / 残留 {len(problems)} 处) =====")
    return 0 if ok else 1


def cmd_gate() -> int:
    """公开主库提交门禁：fixtures/ 与 tm/ 不许混入真实论文正文。

    私有真源**不会**触发（没有 PUBLIC-MODE.json），也不会被 iterate_commit.py 调用；
    公开库 clone 后自带标记，每次迭代收尾强制跑本函数。
    """
    problems: list[str] = []

    def is_synthetic(s: str) -> bool:
        return any(x in s for x in (
            "Synthetic regression paragraph", "合成回归段落", "SYNTHETIC-ANCHOR",
            "Synthetic label", "合成标签", "Synthetic Author", "SYNTHETIC-CASE",
            "合成回归案例", "公开版已截断", "<WORKSPACE>", "author@example.org"))

    def walk(o, key: str, rel: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k, rel)
        elif isinstance(o, list):
            for v in o:
                walk(v, key, rel)
        elif isinstance(o, str):
            if key not in TEXT_KEYS or len(o) < 50 or is_synthetic(o):
                return
            ascii_ratio = sum(
                c.isascii() and (c.isalpha() or c.isspace() or c in ".,;:()-/'%")
                for c in o) / len(o)
            if ascii_ratio > 0.85 and re.search(r"[A-Za-z]{10}", o):
                problems.append(f"{rel} [{key}] 英文正文: {o[:60]}…")
            elif sum("\u4e00" <= c <= "\u9fff" for c in o) >= 40:
                problems.append(f"{rel} [{key}] 中文译文段: {o[:60]}…")

    for p in sorted(FIXTURES.glob("*/*.json")):
        if p.name == "expected.json":
            continue
        try:
            walk(load_json(p), "", p.relative_to(ROOT).as_posix())
        except Exception as exc:
            problems.append(f"{p.name} 无法解析: {exc}")
    for p in sorted((ROOT / "tm").iterdir()):
        if p.suffix not in (".json", ".jsonl"):
            continue
        try:
            if p.suffix == ".jsonl":
                items = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            else:
                d = load_json(p)
                items = d if isinstance(d, list) else [d]
        except Exception as exc:
            problems.append(f"{p.name} 无法解析: {exc}")
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in ("wrong", "right"):
                v = it.get(k)
                if isinstance(v, str) and len(v) > 80 and "公开版已截断" not in v:
                    problems.append(f"{p.name} [{k}] 整句译文未压缩: {v[:60]}…")

    if problems:
        print(f"[BLOCK] 公开模式门禁失败：发现 {len(problems)} 处真实论文正文/译文")
        for x in problems[:12]:
            print("   ", x)
        if len(problems) > 12:
            print(f"    …另有 {len(problems)-12} 处")
        print("  真实全强度快照放 fixtures_full/（gitignored）；fixtures/ 只保留合成案例。")
        print("  参见 PUBLIC-RELEASE.md、规则 59。禁止绕过此门禁。")
        return 1
    print("[PASS] 公开模式门禁：fixtures/ 与 tm/ 均为合成数据，可提交")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="公开库发布前的数据最小化")
    ap.add_argument("--audit", action="store_true", help="只读扫描真源的隐私/版权风险")
    ap.add_argument("--slim", action="store_true", help="生成脱敏副本")
    ap.add_argument("--verify", action="store_true", help="复跑黄金集 + 残留扫描")
    ap.add_argument("--all", action="store_true", help="slim + verify")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="脱敏副本目录")
    ap.add_argument("--dir", default="", help="verify 目标目录（默认同 --out）")
    ap.add_argument("--update-expected", action="store_true",
                    help="脱敏导致断言变化时，把实测值写回副本的 expected.json")
    ap.add_argument("--gate", action="store_true",
                    help="公开主库提交门禁：检查 fixtures/ 与 tm/ 是否混入真实论文正文")
    a = ap.parse_args()
    out = Path(a.out)
    verify_dir = Path(a.dir) if a.dir else out
    if not any((a.audit, a.slim, a.verify, a.all, a.gate)):
        ap.error("至少指定 --audit / --slim / --verify / --all / --gate 之一")
    if a.gate:
        return cmd_gate()
    if a.audit:
        cmd_audit()
    if a.slim or a.all:
        cmd_slim(out)
    if a.verify or a.all:
        return cmd_verify(verify_dir, a.update_expected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
