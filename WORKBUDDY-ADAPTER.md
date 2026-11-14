# WorkBuddy 适配层 · zoterofanyi

> **唯一真源声明（2026-11-14 起）**：本目录 `~/.workbuddy/skills/zoterofanyi/` 是 skill 的唯一可写版本。
> `~/.dsh/skills/zoterofanyi/`（DeepSeek Harness 原版）保留为**只读历史归档**，不再修改，避免两份分叉。
> 所有迭代（改规则、改脚本、加 fixture、写 CHANGELOG、回流记忆）一律落在本目录。
> **2026-11-14 起新增硬约束**：每次迭代收尾必须用 `scripts/iterate_commit.py` 做一次
> git 提交 + 版本 tag，CHANGELOG 版本号与 tag 严格一一对应；**没有 commit hash 不算迭代完成**。见第 6 节。

## 0. 何时加载 / 加载后读什么

用户提到以下任一内容就应加载本 skill：论文翻译、中英对照、双语 PDF、Zotero 文献双语化、
术语表、二遍审校、译文返工、"把这个经验写进 skill"、"迭代一下 zoterofanyi"、
"同步到 GitHub / 其他电脑 / 更新 skill / 全局 skill"、
"公开库 / 分享给别人 / 隐私 / 加密 / 数据最小化"。

加载顺序（不要跳过）：

1. `SKILL.md` —— 全量规则：交付约定、流水线、60 条工程规则、版式特例、质量红线、迭代协议；
2. `CHANGELOG.md` —— 最近版本与四指标基线，确认当前版本号、规则编号与 git tag 坐标；
3. 本文件 —— WorkBuddy 环境映射与可执行命令；**第 6 节 git 门禁、第 7 节跨机本地化、
   第 8 节 GitHub 云端同步与全局安装、第 9 节公开库发布**；
4. `PORTABILITY.md` —— 换电脑时怎么取版本、怎么生成 `local_config.json`（首次加载或换机时读）；
5. `PUBLIC-RELEASE.md` —— 要把 skill 放到公开库 / 分享给别人时读（审计结论、加密评估、发布 SOP）；
6. `tm/term_fixes.json`、`tm/error_patterns.json` —— 历史审校纠正，翻译前注入提示词；
7. `scripts/paths.py --check` / `--shell bash` —— 取本机 `$PY/$S/$W`，确认组件齐全再开跑；
8. `scripts/iterate_commit.py --status` —— 收尾前看当前版本与未提交改动，再决定下一个版本号。

> **加载后第一件事（规则 57）**：`python scripts/sync_now.py --start` —— 与 GitHub 对齐版本。
> 分叉会 BLOCK 交人工裁决；离线只 WARN，本地版本照常可用。收尾由 `iterate_commit.py` 自动推送，
> 不需要额外记一条命令。

## 1. 环境映射（DSH → WorkBuddy）

| 项目 | DSH 原版 | WorkBuddy 用法 |
|---|---|---|
| skill 根 | `~/.dsh/skills/zoterofanyi/` | `~/.workbuddy/skills/zoterofanyi/` |
| 本机路径来源 | 文档里写死的 `%USERPROFILE%\.dsh\...` | **`local_config.json` + `paths.py`**（换机先跑 `bootstrap.py`；`$PY/$S/$W` 由 `paths.py --shell` 生成，见第 7 节 / `PORTABILITY.md`） |
| 命令语法 | PowerShell `& $PY "$S\xxx.py"` | Bash 工具：`"$PY" "$S/xxx.py" ...`（Git Bash，正斜杠）；也可用 PowerShell 工具保持 `& $PY` |
| 结构化抽取 | `run_mineru.py` | 不变，本机两个 venv 都在 |
| 翻译/审校子代理 | DSH 子代理 | `Agent` 工具，`subagent_type: general-purpose`；每个 chunk 一个 agent，提示词给**绝对路径**与输出文件名 |
| 翻译子代理降本 | 主会话切 `reasoningEffort=off` | WorkBuddy 无此开关；改用 `Agent` 的 `model: "lite"` 派翻译子代理。**审校与主会话不降级** |
| Zotero 挂回 | `mcp__zotero__attach_file` 等 MCP 工具 | WorkBuddy 无对应 MCP；用同实现的 CLI（见下） |
| 用量/费用 | `usage_from_sessions.py` 读 `~/.dsh/sessions` | **不可直接读**（WorkBuddy 会话日志格式不同）。改用 `usage_report.py` 产物估算并明确标注"非账单口径"；实际费用只能由用户提供 `--actual-cost`，拿不到就写「未记录」，**不得用估算冒充** |
| PDF 渲染沙箱 | `danger-full-access` 审批 | Bash 工具：若 Edge 报 `STATUS_BREAKPOINT` / "Edge 未生成 PDF"，该步用 `dangerouslyDisableSandbox: true` 重跑 |
| 工作目录 `$W` | 当前工作区内 `zotero-translate/<key>/` | 沿用 `%USERPROFILE%>/` |

两个 Python 解释器（本机已验证可用）：

```bash
PY="$HOME/.dsh/runtimes/zb-translate/venv/Scripts/python.exe"      # 主工具：PyMuPDF / zstandard
MPY="$HOME/.dsh/runtimes/mineru/venv310/Scripts/python.exe"        # MinerU（由 run_mineru.py 自动定位）
S="$HOME/.workbuddy/skills/zoterofanyi/scripts"
K="$HOME/.workbuddy/skills/zoterofanyi"
W="%USERPROFILE%>"
```

Zotero CLI（与 DSH MCP 同一实现，标准库）：

```bash
ZCLI="$HOME/.dsh/mcp/zotero_mcp.py"
"$PY" "$ZCLI" cli bridge                                    # 自检：期望 {"ok":true,"zotero":"9.0.6"}
"$PY" "$ZCLI" cli attach <KEY> <成品.pdf> --filename "<显示名>"
"$PY" "$ZCLI" cli note   <KEY> --title "翻译项目备注｜..." --text-file "$W/reports/项目备注.md"
"$PY" "$ZCLI" cli verify <KEY>                              # 交付门禁：必须 ok:true
```

> 2026-11-14 实测：`cli bridge` 返回 `ok:false / HTTP 404`，说明 **Zotero 未运行或 Bridge 插件未加载**。
> 挂回前先启动 Zotero；仍不通则按 SKILL.md「Zotero 写回」一节退回 sqlite 路径（此时必须完全退出 Zotero）。
> 笔记只能挂**父条目**，附件必须有 `title`。

## 2. 最短可执行流水线（Bash 版）

严格按序，**两道门禁是硬性的**：翻译前骨架 `0 BLOCK`，交付前全文 `0 BLOCK`。

```bash
mkdir -p "$W/work" "$W/reports" "$W/assets"

# 1-3 抽取（0 token）
"$PY" "$S/run_mineru.py" <原文.pdf> "$W/work/mineru" --lang en
MIDDLE=$(find "$W/work/mineru" -name "*_middle.json" | head -1)
"$PY" "$S/extract_mineru.py" <原文.pdf> "$MIDDLE" \
    --out-blocks "$W/work/blocks.json" --out-regions "$W/work/regions.json" --assets "$W/work/assets"
"$PY" "$S/extract_footnotes.py" <原文.pdf> --out "$W/work/footnotes.json"

# 4 装配 + 本篇版式修补
"$PY" "$S/assemble.py" "$W/work/blocks.json" "$W/work/regions.json" --out "$W/work/skeleton.json"
"$PY" "$S/inject_footnotes.py" "$W/work/skeleton.json" "$W/work/footnotes.json" --out "$W/work/skeleton_fn.json"
#    4b 版式修补用收编的 $S/fix_generic.py：CONFIG[paper_key] 填 8 个字段
#       （drop_pages / refs_mode / refs_pattern / appendix_heading / boilerplate / two_column ...），
#       用法 "$PY" "$S/fix_generic.py" "$W" "<KEY>"；特殊版式再复制成 $W/work/fix_layout.py 改。
#    术语表：复制 $S/make_glossaries.py 到 $W/work/ 后改 BASE + PAPERS（它是三篇批处理的模板），
#       产出 $W/work/glossary_meta.json；术语 value 只放最小可匹配词。
"$PY" "$S/mark_sections.py" "$W/work/skeleton_fn.json"          # 以下三个默认原地写回
"$PY" "$S/apply_meta.py" "$W/work/skeleton_fn.json" "$W/work/glossary_meta.json"

# 4c 门禁一：骨架必须 0 BLOCK 才允许花钱翻译
"$PY" "$S/sanitize.py" "$W/work/skeleton_fn.json" --report "$W/reports/sanitize.json"
"$PY" "$S/preflight.py" "$W/work/skeleton_fn.json" --stage skeleton \
    --assets "$W/work/assets" --out "$W/reports/preflight_skeleton.json"

# 5 分块 + 派翻译子代理（9000 字符/块；提示词只给 6 条硬规则）
"$PY" "$S/translate.py" emit "$W/work/skeleton_fn.json" \
    --outdir "$W/work/chunks" --chars 9000 --cache "$W/work/cache.jsonl"
#   → 对 chunk_NNN.json 各派一个 Agent(model="lite")，产出 trans_NNN.json
#   → 翻译提示词里注入 tm/term_fixes.json 的「✗ 错译 → ✓ 正译」清单（见第 3 节）
"$PY" "$S/translate.py" merge "$W/work/skeleton_fn.json" \
    --chunks "$W/work/chunks" --out "$W/work/bilingual.json" --cache "$W/work/cache.jsonl"

# 6 二遍审校：先跑机检，把本块 FAIL 逐条写进审校者提示词（A/B 实测：不喂就漏）
"$PY" "$S/qa.py" "$W/work/bilingual.json" --out "$W/reports/qa.json"
"$PY" "$S/rules_check.py" "$W/work/bilingual.json" --out "$W/reports/rules.json" \
    --term-exceptions "$K/templates/term_exceptions.json"
"$PY" "$S/emit_review.py" "$W/work/bilingual.json" --outdir "$W/work/review" --per-chunk 30 --mode patch
#   → 审校 Agent 用 default/reasoning，不降级；只回 edits[{find,replace,reason}]
"$PY" "$S/apply_fixes.py" "$W/work/bilingual.json" --fixdir "$W/work/review" \
    --out "$W/work/bilingual_fixed.json" --log "$W/reports/changes.json"

# 7 脚注对照表 + 渲染 + 交付前自检
"$PY" "$S/build_fn_index.py" "$W/work/bilingual_fixed.json" \
    --out "$W/work/bilingual_idx.json" --report "$W/reports/fn_crossref.json"
"$PY" "$S/build_pdf.py" "$W/work/bilingual_idx.json" <输出.pdf> "<标签>"
"$PY" "$S/preflight.py" "$W/work/bilingual_idx.json" --pdf <输出.pdf> \
    --assets "$W/work/assets" --out "$W/reports/preflight.json"

# 8 回归 + 9 挂回 Zotero + 10 用量
"$PY" "$S/regression_check.py" <原文.pdf> "$W/work/regions.json" \
    "$W/work/bilingual_idx.json" "$W/reports"
"$PY" "$S/usage_report.py" "$W/work" --pages <页数> --out "$W/reports/usage_artifacts.md"
```

**子代理提示词要点**（不要逐条罗列 53 条规则，实测精简版质量持平、费用更低）：
① 只输出中文；② 数字/百分数/引用编号/`^{}` 上标原样保留；③ 术语严格按给定术语表；
④ 表题「表 N」、图题「图 N」开头；⑤ 参考文献/R 代码/布尔检索式不译；⑥ 不写译者注、不加解释。
另附 `tm/term_fixes.json` 的历史错译清单，以及 1–2 条 `tm/error_patterns.json` 的整句范例。

## 3. 迭代协议（WorkBuddy 落地版）—— 这是本 skill 的特色，每篇必须走完

**目标函数优先级不可颠倒**：准确 → 少错 → 速度 → 成本。省钱不许碰质量红线
（全量二遍审校、术语表+术语审计、图表与脚注交叉核对、三道门禁全绿）。

### 每篇收尾的八个动作（必须改文件，只在对话里说不算迭代）

| # | 动作 | 落地位置 / 命令 |
|---|---|---|
| 1 | 归类本篇所有 FAIL/WARN：**尺子错 / 译文错 / 原文问题 / 新版面特性** | `$W/reports/` 下的 qa.json、rules.json、preflight.json、reg_*.log |
| 2 | 尺子错 → 修检查器，**改后必跑黄金集** | `$S/qa.py` / `rules_check.py` / `audit_document.py`；`$PY "$S/regression_suite.py" --fixtures "$K/fixtures" --verbose` |
| 3 | 新版面特性 → 在 SKILL.md「工程规则」加**新的编号条目**（当前到 55，下一条从 56 起） | `$K/SKILL.md` |
| 4 | 译文错 → 改译文 + 回流翻译记忆 | `$PY "$S/build_memory.py" --changes "$W/reports/changes*.json" --tm "$K/tm"`（增量合并，不覆盖历史） |
| 5 | 原文问题 → 单独立表，不得靠译文掩盖 | `$W/reports/source_issues.md`（模板 `$K/templates/source_issues_template.md`） |
| 6 | 黄金集单调增长：**每篇新增 full + extract 两个案例** | `$PY "$S/snapshot_case.py" --kind full ... --fixtures "$K/fixtures"`；`--kind extraction` 加 `--must-contain/--must-not-contain` 守读序与 OCR 污染 |
| 7 | CHANGELOG 追加一行 + 项目日志 | `$K/CHANGELOG.md`（日期 / 篇目 / 四指标实测 / 本轮学到的东西）；`.workbuddy/memory/YYYY-MM-DD.md` |
| 8 | **git 提交 + 版本 tag + 云端同步（收口，缺此不算迭代完成）** | `$PY "$S/iterate_commit.py" -v <下一版本> -m "<一句话>"`；自动校验 CHANGELOG 同版本条目 / 实际文件改动 / 黄金集全绿 / tag 不重复，通过后 add + commit + annotated tag，**并默认自动推送到 origin + 回读远端 HEAD 校验**（v2.5 / 规则 57）。先看 `--status`、`--dry-run` 预演；临时不联网用 `--no-push`，强制要求已连接用 `--push`；未连接 GitHub 时降级为本地提交 + WARNING |

> 第 8 步是第 1–7 步的**封存动作**：只有规则、术语、fixture、CHANGELOG 都已写回文件后，
> 才把它们一次性固化成一个可对照、可回滚的版本。CHANGELOG 版本号必须与 tag 同名。

### 护栏（硬性）

- 任何 `scripts/` 改动后必跑 `regression_suite.py`；**有一个案例由 PASS 变 FAIL 就不得合并**，
  要么改回，要么确认新行为是"预期的正确化"、用 `snapshot_case.py` 更新 `expected.json` 并在 CHANGELOG 写明理由；
- 禁止用 PowerShell 的 `Get-Content/Set-Content` 写 `.py` 文件（GBK 读 UTF-8 会不可逆地毁掉中文字面量，
  v2.0 事故）。改脚本一律用 `Write`/`Edit` 工具或 `python -c "open(p,'w',encoding='utf-8')"`；
- 术语表 value **只放最小可匹配词**，备选译法写进 `templates/term_exceptions.json`（带括注会被检查器判"未按约定翻译"）；
- 用户把译文给第三方审校后反馈 → 逐条判定接受/不接受（写理由）→ 接受的写进术语表与翻译提示词 → 记 CHANGELOG；
- 交付时必须明确区分 **已自动检查 / 已人工核对 / 仍待核对**；未做句级校对不得称"定稿"；
- **每次迭代必须以 `git commit` + annotated tag 收口**（第 8 步）：门禁未通过就修到通过，
  不得靠 `--skip-regression` 绕过质量红线；禁止 `--amend` / `tag -f` / `reset --hard` /
  `push --force` / 自动合并冲突。v2.5 起**默认自动推送**是用户明确要求（规则 57），
  但仍禁止擅自改仓库可见性、擅自创建远端或把 `local_config.json` 推上去。

### 记忆回流闭环（本次继承补齐的原来缺失环节）

原版的 `build_memory2.py` 散落在工作区 `_generic/` 且路径写死，没有进 skill。
现在收编为 `$S/build_memory.py`，支持参数化输入与**增量合并**：从各篇 `reports/changes*.json`
的 `applied[].edits[]` 用字符级 diff 抽出词级替换（→ `tm/term_fixes.json`）与整句级错误模式
（→ `tm/error_patterns.json`），下一篇翻译时注入子代理提示词，形成"审校纠正 → 下次不再犯"的闭环。

```bash
# 单篇
"$PY" "$S/build_memory.py" --changes "$W/reports/changes*.json" --tm "$K/tm"
# 全量重建（把所有历史篇目一起喂）
"$PY" "$S/build_memory.py" \
    --changes "%USERPROFILE%" --tm "$K/tm"
```

## 4. 版本坐标（2026-11-14，git 化后）

| 项 | 值 |
|---|---|
| 版本 / tag | **v2.10**（GitHub 双路网络策略；上一版 v2.9）；下一版从 **v2.11** 起。公开主库历史从 v2.8 干净初始提交起算，v2.2–v2.7 在私有归档库 |
| git 仓库 | `~/.workbuddy/skills/zoterofanyi/` 本身（独立仓库，分支 `main`）。**origin = `https://github.com/xiaozeng9066/zoterofanyi-skill.git`（public，公开主库）**；私有归档 `.../zoterofanyi-skill-archive`（含 v2.2–v2.8 完整历史），本地目录 `~/.workbuddy/zoterofanyi-archive` |
| 云端同步 | `sync_now.py --start / --end / --status`；开工拉、收尾 `iterate_commit.py` 自动推并回读远端 HEAD。GitHub 连接器为 App，建仓 403 / 新仓库 404，**不影响 git 同步**（规则 58） |
| 网络路由 | `local_config.json` 的 `network` 段（`detect_env.py` 探测）：GitHub 可直连则绕过代理，首选失败**自动回落**另一条；`--route direct/proxy` 可锁定验证（规则 61） |
| 工程规则编号 | 1–61，下一条 **62**（v2.10 新增「GitHub 直连优先 + 自动回落」） |
| 黄金测试集 | 14 案例，**14/14 PASS**（真源本机实测）；脱敏副本亦 **14/14 PASS + 残留 0 处**（`public_sanitize.py --verify`） |
| 公开版 | `scripts/public_sanitize.py` 生成 `dist/public`（已 gitignore）；发布指南 `PUBLIC-RELEASE.md`、第 9 节。**私有主库仍是唯一真源，全强度 fixtures 不降级** |
| 翻译记忆 | 76 条词级纠正 + 9 条整句模式（含 3EHBMRQR / E2HE73V2 / RB7BRXRA 等篇审校纠正） |
| 术语例外 | `templates/term_exceptions.json`（`translog` 待核实） |
| 已处理篇目 | 16+ 篇（见工作区 `zotero-translate/<key>/` 与 `CHANGELOG.md`） |

## 5. 本机已知状态（2026-11-14 实测）

- 主 venv / MinerU venv / Edge 均在；黄金集 14/14 PASS，`regression_suite.py` 可直接跑；
- Zotero Bridge `cli bridge` → `ok:false / HTTP 404`，**挂回前需启动 Zotero 并确认插件加载**；
- `usage_from_sessions.py` 依赖 DSH 会话日志，WorkBuddy 会话不可直接统计 → 见第 1 节费用口径；
- 工作区 `zotero-translate/` 下已有历史产物（source.pdf / bilingual_fixed.json / 中英对照 PDF），
  可作为"续做某一篇"或"补充审校"的起点，不要重复抽取。
- **GitHub 认证（v2.5 实测）**：`api.github.com` 可达（HTTP 200），但本机 **gh CLI 未装、
  GCM 无已登录账户、无 SSH key、无 `GITHUB_TOKEN`**；系统级 `credential.helper=manager`
  （GCM 2.9.0）可用。也就是说同步机制已就绪，但**首次授权必须由用户完成**：
  启用 GitHub 连接器，或 `git credential-manager github login --device --no-ui`。详见第 8 节。

## 6. Git 版本控制（2026-11-14 起，用户明确要求）

### 仓库与边界

- **位置**：本目录 `~/.workbuddy/skills/zoterofanyi/` 本身就是一个独立 git 仓库，分支 `main`。
- **基线**：`git init -b main` → `baseline(v2.2): 继承 WorkBuddy 后的干净快照`（`db0a9c4`）
  → annotated tag `v2.2`。此后每次迭代 = 一个 commit + 一个同名 annotated tag。
- **入库**：`SKILL.md` / `CHANGELOG.md` / 本文件 / SETUP / MIGRATION / WINDOWS-INSTALL、
  `scripts/`（含 `*.py`、`*.sh`、`*.broken`）、`fixtures/`、`templates/`、`tm/`、
  `.gitignore`、`.gitattributes`、迭代提交脚本本身。
- **不入库**（`.gitignore`）：`__pycache__/`、`*.pyc`、`*.log`、`*.tmp`、`*.bak`、`*.local.*`、
  `*.pdf`、`*.sqlite`。仓库外也不碰：工作区 `zotero-translate/<key>/` 的每篇论文产物、
  DSH 只读归档 `~/.dsh/skills/zoterofanyi/`、Zotero 数据库副本、成品双语 PDF。
  **仓库只版本化 skill 自身**，不含论文全文与产物。
- **本机配置**：`.gitattributes` 固定 LF 行尾；仓库级 `core.autocrlf=false`、`core.quotepath=false`、
  `i18n.commitEncoding=utf-8`；提交身份 `<USER> <zengf@localhost>`（只作用于本仓库，未改全局）。
  改身份：`git -C "$K" config user.email "author@example.org"`。

### 每次迭代的标准收尾（第 3 节第八个动作）

```bash
K="%USERPROFILE%"
PY="%USERPROFILE%"
S="$K/scripts"

# 1) 看坐标：当前 tag、最近提交、未提交改动
"$PY" "$S/iterate_commit.py" --status

# 2) 按第 3 节把前七个动作改完文件（含 CHANGELOG 的 vX.Y 条目），再预演
"$PY" "$S/iterate_commit.py" -v v2.6 -m "修 qa 引用编号误报 + 回流术语" --dry-run

# 3) 正式提交：强制跑黄金集 → add / commit / annotated tag
#    → 已连接 GitHub 时自动 push + 回读远端 HEAD 校验（v2.5 默认，见第 8 节）
"$PY" "$S/iterate_commit.py" -v v2.6 -m "修 qa 引用编号误报 + 回流术语"
#    临时不联网：加 --no-push；强制要求已连接：加 --push（无 origin 直接 BLOCK）
```

提交信息格式 `iterate(vX.Y): <一句话>`，正文自动带 `Date / CHANGELOG / Regression / Changed`；
tag 一律与 CHANGELOG 版本号同名，用 `git tag -a`（annotated，带说明）。

### 门禁（不通过就 BLOCK，退出码 2）

| 检查 | 拦住了什么 |
|---|---|
| 版本号符合 `vX.Y[.Z]` | 乱写版本号、版本链断裂 |
| `CHANGELOG.md` 有 `## vX.Y` 条目 | 只在对话里总结、没记四指标、没改文件 |
| 工作区有实际文件改动 | 空提交 / "口头迭代" |
| `regression_suite.py` 全绿 | 改坏检查器却提交 |
| 同名 tag 尚不存在 | 覆盖 tag、篡改已发布的历史 |

`--skip-regression` 只用于确定无脚本改动的场合，且会在提交信息里标注 `SKIPPED`，
**不许用它绕过质量红线**。门禁 BLOCK 时先修到通过，别急着换版本号。

### 对照与回滚（历史只追加，不重写）

```bash
git -C "$K" log --oneline --decorate -10      # 完整迭代史
git -C "$K" show --stat v2.3                   # 某次迭代改了哪些文件
git -C "$K" diff v2.2..v2.3                   # 两个版本之间的全部差异
git -C "$K" show v2.2:SKILL.md                 # 只读取回任一历史版本的文件
git -C "$K" revert <hash>                     # 撤销一次迭代：追加反向提交，安全
git -C "$K" checkout v2.2 -- scripts/qa.py    # 单文件恢复（会覆盖工作区该文件，先确认）
```

### 禁止事项与远端策略

- **v2.5 起默认自动同步**（用户明确要求「云端永远最新」）：`iterate_commit.py` 提交 + tag 后，
  只要有 origin 就调用 `sync_now.py --end` → fetch 检查分叉 → `push origin main --follow-tags`
  → `ls-remote` 回读远端 HEAD 与本地比对；不一致就明确报「本地已提交、云端未更新」。
  绝不 `push --force` / `commit --amend` / `tag -f` / `reset --hard` / 自动合并冲突。
  未配置远端时**自动降级**为纯本地提交 + WARNING（不阻断迭代）；`--no-push` 可临时关闭，
  `--push` 则是「必须已连接，否则 BLOCK」。
- **首次连接已完成（v2.6）**：`origin = https://github.com/xiaozeng9066/zoterofanyi-skill.git`
  （**private**），首次推送 100 文件 + 4 个 tag，`sync_now.py --status` 回读一致。
  凭据用本机 **GCM 用户级 token**（scopes `repo, workflow, gist`），
  token 绝不写进 URL、`.git/config` 或日志。
  **GitHub 连接器是 GitHub App**：`create_repository` 返回 403、新建私有仓库对它 404
  （App 只授权了安装时选定的仓库），所以同步状态一律以 `sync_now.py --status` /
  `ls-remote` 回读为准，不以连接器可见性为准；要让它也管理该仓库，需用户到
  App 安装设置加入授权范围（规则 58）。
  脚本仍不擅自创建远端、不擅自改可见性；`tm/` 与 `fixtures/` 含公开论文正文摘录，
  仓库保持私有（隐私审计结论见第 8 节 / `PORTABILITY.md`）。
- 不要把 DSH 归档、工作区论文产物、成品 PDF、Zotero sqlite `git add` 进来。
- 不要长期用 `--no-tag`：tag 缺失会让 `CHANGELOG 版本 ↔ git 版本` 的对应关系断掉。
- 收尾脚本只管 skill 目录；项目日志 `.workbuddy/memory/YYYY-MM-DD.md` 仍在仓库外，按第 3 节单独写。

## 7. 跨机同步与本机本地化（2026-11-14 起，用户第三个要求）

**完整方案见 `PORTABILITY.md`**（三种同步通道、新机 5 步、配置字段、冲突处理、隐私边界）。
本节只放工作台上的常规动作。

### 三层分离

| 层 | 内容 | 是否同步 |
|---|---|---|
| 版本 | `SKILL.md` / `CHANGELOG` / `scripts` / `fixtures` / `templates` / `tm` | git 仓库同步 |
| 配置 | 本机 Python / MinerU / 浏览器 / Zotero MCP / 工作区路径 | `local_config.json`，**绝不入库** |
| 引导 | `scripts/paths.py`、`detect_env.py`、`bootstrap.py` | 入库，零第三方依赖 |

原有 40+ 个流水线脚本**零硬编码本机绝对路径**（v2.4 实测），所以换电脑**不改脚本**，
只需重新生成 `$PY/$S/$W`：`eval "$(python scripts/paths.py --shell bash)"`。

### 本机常用命令

```bash
python scripts/detect_env.py                          # 探测并增量更新本机配置
python scripts/paths.py --check                      # 组件自检（缺什么列什么）
python scripts/paths.py --shell bash                 # 输出 $PY/$S/$K/$W 供 eval
python scripts/bootstrap.py                          # 本机引导 + 黄金集自检
python scripts/sync_now.py --start                   # 开工前拉最新版本（ff-only；兼容入口 bootstrap.py --sync）
python scripts/sync_now.py --status                  # 本地 / 云端对齐状态（只读）
python scripts/sync_now.py --end                    # 手工补推（通常 iterate_commit.py 已自动做）
python scripts/bootstrap.py --bundle /tmp/zoterofanyi.zip   # 离线分发包
```

### 同步通道（一次性设置都要用户显式执行；日常开工/收尾由脚本自动）

| 通道 | 一次性设置 | 日常 |
|---|---|---|
| **GitHub 私有仓库（v2.5 起首选）** | 授权 → `connect_github.py --user <用户名> --repo <仓库名> [--create]`；已有仓库用 `--remote <url>` | 开工 `sync_now.py --start`；收尾 `iterate_commit.py` 自动推送 + 回读校验 |
| **云盘 bare 仓库**（本机有 OneDrive / 坚果云） | `bootstrap.py --init-sync "%USERPROFILE%"` | 同样用 `sync_now.py`；云盘客户端自动同步 bare 文件，多机错峰 push |
| **离线包 / 资料库** | `bootstrap.py --bundle <out.zip>` → 上传 → 新机解压按 `BOOTSTRAP.txt` 引导 | 人工换包 |

> 禁止直接同步含 `.git` 的 skill 工作目录（多机并发写会损坏 refs）；
> `tm/` 与 `fixtures/` 含论文正文摘录，同步空间必须私有；`local_config.json` 绝不上传。
> 本机 **v2.6 起已接通** GitHub 私有仓库 `xiaozeng9066/zoterofanyi-skill`；
> 连接器对该仓库返回 404 属正常（App 授权范围，见规则 58），**不影响 git 同步**。
> 换电脑用 `bootstrap.py --remote https://github.com/xiaozeng9066/zoterofanyi-skill.git`。

### 换电脑第一次

`bootstrap.py --remote <url>`（或 bundle 解压）→ 到 `~/.workbuddy/skills/zoterofanyi/` 跑
`bootstrap.py` → 按输出补齐 PyMuPDF / MinerU / 浏览器 / Zotero MCP → `paths.py --check` 全齐
→ 黄金集 14/14 PASS → 再开始翻译。规则 56 与 `SKILL.md` 顶部提示同步说明这件事。

## 8. GitHub 云端同步与全局安装（2026-11-14 起，用户第四个要求）

> 结论：**可行且已落地**。用户要求「连接 GitHub 建仓库，每次使用前后本地与云端交互，
> 云端永远最新，本地可从云端更新」，并确认「如果都是公开论文就没啥敏感数据」。
> v2.5 把这件事做成了**脚本门禁 + 协议**，不是一句约定。

### 一次性连接（必须先由用户授权）

本机现状：`api.github.com` 可达；但 **gh CLI 未装、GCM 无已登录账户、无 SSH key、
无 `GITHUB_TOKEN`**，系统级 `credential.helper=manager`（GCM 2.9.0）可用。
所以脚本能跑，但**授权这一下必须用户做**：

| 方式 | 用户动作 | 之后 |
|---|---|---|
| **GitHub 连接器（推荐）** | 在 WorkBuddy 里启用/授权 GitHub 连接器（OAuth） | 把仓库地址给 `connect_github.py --remote <url>` |
| **GCM 设备码** | `git credential-manager github login --device --no-ui`，浏览器输码 | 凭据存系统凭据管理器，git 自动取用 |
| **PAT + 自动建仓** | 设置环境变量 `GITHUB_TOKEN`（fine-grained，仅 repo 权限） | `connect_github.py --user <用户名> --repo <仓库名> --create` |
| **网页手建空仓库** | GitHub 网页建一个**空**私有仓库 | `connect_github.py --remote https://github.com/<user>/<repo>.git` |

`connect_github.py` 做四件事：探测凭据（不打印 token）→ 可选调 API 建**私有**仓库 →
设置 origin（已有不同地址则 BLOCK，不覆盖）→ 调 `sync_now.py --end` 推第一版（含全部历史与 tag）。
**绝不把 token 写进 URL / `.git/config` / 日志**；`--public` 需显式指定。

### 每次使用前后（自动，规则 57）

```bash
python scripts/sync_now.py --start      # 加载 skill 后第一件事：fetch → ff-only → 分叉 BLOCK
python scripts/sync_now.py --status     # 随时看本地/云端是否对齐（只读）
python scripts/iterate_commit.py -v <版本> -m "<说明>"   # 收尾：commit + tag + 自动 push + 回读校验
```

| 情况 | 行为 |
|---|---|
| 云端有新版本 | `--start` 只做 ff-only 快进，绝不覆盖本地改动 |
| 两端各有提交（分叉） | **BLOCK**，打印人工 merge 命令；禁止强推/自动合并 |
| 离线 | `--start` 只 WARN，本地版本照常可用；`--end` 明确报「云端未更新」 |
| 未连接 GitHub | `--start` WARN、`--end` 降级（带 `--allow-offline`），迭代提交照常完成 |
| push 后 | 用 `git ls-remote` 回读远端 HEAD，与本地比对不一致就报错，**不以「没报错」当同步成功** |

「云端永远最新」的准确边界：**每次成功收尾之后**云端与本地一致；离线期间的本地改动
会在恢复网络后由 `--end` 补推（`--start` 也会提示「本地有 N 个未推送提交」）。
脚本不会后台常驻，也不会在两台机器同时写时替你猜谁对——分叉一律交人工裁决。

### 网络路由：GitHub 白名单直连（v2.10，规则 61）

本机代理 `127.0.0.1:14126` 对 `api.github.com` 时通时断、对 `github.com:443` 的 CONNECT
约半数 502，而直连常常 3/3 稳定 —— 表现为「网页能开、git 报离线」。

```bash
python scripts/detect_env.py                 # 探测直连/代理两条路，写入 local_config.json 的 network 段
python scripts/sync_now.py --status          # 显示实际路由（直连 / 代理 / 已自动回落）
python scripts/sync_now.py --status --route direct   # 只验证直连
python scripts/sync_now.py --status --route proxy    # 只验证代理
```

- 排序依据：`network.chosen`；**首选失败自动回落另一条**，不需要人盯着重跑。
- 白名单 host：`github.com`、`api.github.com`、`codeload.github.com`、
  `objects.githubusercontent.com`；其余流量照旧走代理。
- 想固定路线：在 `local_config.json` 里设 `network.force = "direct"`（或 `"proxy"`）。
- 也可以在 git 全局按域放行（只影响 git，不改系统代理）：
  `git config --global http.https://github.com.proxy ""`（其余 host 同法）。
- 不要全局关闭代理、不要在 `.git/config` 里写死代理；同步成败仍以回读远端 HEAD 为准。

### 隐私审计（2026-11-14 对 100 个入库文件全量扫描）

| 检查项 | 结果 |
|---|---|
| GitHub PAT / `sk-` / AWS key / 私钥 / Bearer / 明文口令 | **0**（`assigned_secret` 0 处） |
| 邮箱 | 40 处 / 12 文件，全部是**论文里公开的作者通讯邮箱**与文档示例 `author@example.org` |
| 本机绝对路径 / 用户名 `<USER>` | v2.5 前 12+16 处；已把 21 个 fixture 的 `source_pdf` 清成 `<WORKSPACE>/...`，文档示例改 `%USERPROFILE%`；仅剩适配层里带「开发机示例」说明的路径 |
| 论文原文 PDF / 二进制 | **0**（`.gitignore` 排除，`fixtures/` 只有 JSON 快照） |
| 论文正文摘录 | 7 篇 `bilingual.json` 合计 **363,695 英文字符 ≈ 6 万词** |

**判断**：确实**没有机密或个人隐私**，用户的直觉基本对；但这批内容是**公开论文的大段正文再分发**
（不是摘要/元数据），放进**公开**仓库会涉及出版社版权与 text-mining 条款，也会被搜索引擎抓取。
所以 `connect_github.py` 默认 **private**，`--public` 要用户明说。这不是"怕泄密"，是"别替你决定再分发范围"。

### 全局安装（本机已核实）

- 位置：`~/.workbuddy/skills/zoterofanyi/` —— **WorkBuddy 用户级 skill**，对本机所有项目/会话生效；
  git 仓库也在同一目录，与工作区 `%USERPROFILE%>/Desktop/zoterofanyi/` 无关。
- 证据：`~/.workbuddy/skills/` 下仅此一个 skill；项目级 `.workbuddy/skills/` 不存在（无覆盖风险）；
  技能注册表「已启用」列表里能看到 `zoterofanyi`；`Skill` 工具在任意工作区都能加载。
- 项目目录里只有 `.workbuddy/memory/`（日志）与 `zotero-translate/`（论文产物），**不是 skill 本体**。
- 三条禁止：① 不把 skill 复制进项目目录；② 不在项目级建同名 skill；
  ③ 不用项目级配置覆盖用户级触发词。云端同步只认用户级目录里的那个仓库。
- 其他电脑想同样全局：`bootstrap.py --remote <url>` 会 clone 到各机自己的
  `~/.workbuddy/skills/zoterofanyi/`，再用 `detect_env.py` 生成本机配置，
  **同一份版本、每台机器各自全局**。

---

## 9. 公开库发布（v2.7 起，v2.8 迁移到公开主库；规则 59 / 60）

> 用户希望「以后好给其他人分享」，且要求**在保护隐私的前提下放到公开库**。
> 完整论证见 `PUBLIC-RELEASE.md`；这里是每次要发布时的操作入口。

**结论**：不加密，做**数据最小化 + 干净历史的新公开仓库**。审计显示 0 凭证 / 0 私钥 /
0 口令 / 0 原文 PDF，风险不是"泄密"而是 `fixtures/` 里 **608 KB 公开论文正文**的
大段再分发；加密解决不了版权问题，且**擦不掉 git 历史**（v2.2–v2.6 每个 tag 都含完整 fixtures）。

```bash
PY=python; K=~/.workbuddy/skills/zoterofanyi

# 1) 只读审计（体积 / 长英文 / 邮箱 / 凭证 / 历史污染）
"$PY" "$K/scripts/public_sanitize.py" --audit

# 2) 生成脱敏副本 + 黄金集 + 残留扫描；--update-expected 按协议更新副本断言
"$PY" "$K/scripts/public_sanitize.py" --all --out "$K/dist/public" --update-expected
#   必须看到：14/14 PASS、残留 0 处、公开化验证 PASS

# 3) 独立复验（幂等，不再改断言）
"$PY" "$K/scripts/public_sanitize.py" --verify --dir "$K/dist/public"

# 4) 从副本单独建干净历史的公开仓库（切勿在真源仓库里操作，切勿改现有私有库可见性）
cd "$K/dist/public" && git init -b main && git add -A && \
  git commit -m "release: 脱敏公开分发版" && \
  git remote add origin <公开库 URL> && git push -u origin main
```

三条硬约束：

- **`dist/` 是构建产物，已 gitignore**；禁止手工改副本再反向同步回真源。
- **禁止**把现有私有仓库直接改 public、`filter-repo` / BFG 重写历史、强推、
  `reset --hard`（规则 55）。公开库必须新建干净历史；origin 切换时若出现分叉，
  `sync_now.py --start` 会按设计 BLOCK，这是护栏不是故障。

### 用户选定拓扑 B：公开库作唯一主库（v2.8）

- 旧私有库改名 `zoterofanyi-skill-archive`（仍 private，保留 v2.2–v2.8 完整历史与全强度 fixtures）；
  同名新建**公开**库 `zoterofanyi-skill`，从 `dist/public` **单一干净初始提交**起步（tag v2.8）。
  全局 skill 目录迁到公开版；旧目录移出 `~/.workbuddy/skills/` 作本地归档，避免被识别成第二个 skill。
- **真实全强度快照放 `fixtures_full/`（gitignored，从归档库复制）**，`fixtures/` 只放合成案例。
  本机跑全强度回归：`python scripts/regression_suite.py --fixtures fixtures_full`；
  公开版默认 `--fixtures fixtures` 仍是 14/14 PASS，别人 clone 即可复现。
- **`PUBLIC-MODE.json`（规则 60，随 clone 自动生效）**：`iterate_commit.py` 检测到
  `mode=public` 就强制跑 `public_sanitize.py --gate`，`fixtures/`、`tm/` 混入真实
  论文正文即 BLOCK，**不受 `--skip-regression` 影响**。原因：公开库成为日常同步目标后，
  下一篇论文的新 fixture 会被自动推送，一次性脱敏撑不过一次迭代。
  实测私有真源被拦 1644 处 / 公开副本 PASS / 注入 1 句真实正文立刻 BLOCK。
- 禁止删除 `PUBLIC-MODE.json`、改 `mode` 或把真实快照写回 `fixtures/` 绕过门禁。

发布与迁移都**不替代** `iterate_commit.py` 的 commit + tag + 自动推送；
它们改变的是「推到哪个仓库、什么内容允许推」。
