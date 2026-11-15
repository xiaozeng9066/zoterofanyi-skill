---
name: zoterofanyi
description: 把 Zotero 文献库（或任意本地）的英文 PDF 做成「英文段 → 中文段」交错的双语 PDF，保留公式/表格/插图原样，脚注独立并附编号对照表，挂回对应 Zotero 条目，项目备注写进 Zotero 笔记。用于科研论文精读，要求学术级中文而非机翻。本 skill 会随每篇论文持续迭代（工程规则 / 术语表 / 黄金测试集 / 翻译记忆 / CHANGELOG），每篇收尾必须走完迭代协议，并以 git 提交 + 版本 tag 收口（CHANGELOG 版本号 = tag 名）。触发词：论文翻译、中英对照、双语 PDF、Zotero 文献双语化、二遍审校、术语统一、迭代 skill、GitHub 同步、跨机更新、全局 skill。
---

# Zotero 文献双语化（中英对照）

> **先读 `README.md`**：它是「这个仓库是什么 / 怎么装 / 怎么用 / 去哪查」的入口，
> 顶部状态表给出当前版本、规则编号、黄金集与翻译记忆的实测数字（规则 64 强制对账）。
> 本文是**全量规则与流水线正文**，篇幅长，按需查而不是通读。

> **WorkBuddy 继承版（2026-11-14 起；当前版本见 README 状态表）**：本目录 `~/.workbuddy/skills/zoterofanyi/` 是**唯一可写真源**，
> `~/.dsh/skills/zoterofanyi/`（DeepSeek Harness 原版）保留为**只读历史归档**，不再修改，避免两份分叉。
> WorkBuddy 环境的工具映射、Bash 版流水线、Zotero CLI 与迭代落地命令见 **`WORKBUDDY-ADAPTER.md`**——
> 加载本 skill 后应先读它。下方规则正文、脚本、黄金集、翻译记忆均为原样继承，继续按原协议迭代。

## 何时用

用户要求把**英文学术 PDF**译成中英对照，且要求：保留公式/表格/图片、术语一致、可挂回 Zotero 批注。
不适用于：纯网页、无文本层的扫描件（需 OCR 路线）、只需要摘要。

## 全局位置与路径约定（Windows 本机基线；跨机读者先看提示）

> **全局安装（v2.5 核实）**：本 skill 装在 **WorkBuddy 用户级目录**
> `~/.workbuddy/skills/zoterofanyi/`，对本机**所有项目 / 所有会话**可见，不是某个工作区的
> 项目级 skill。git 仓库也在同一用户级目录里，与 `%USERPROFILE%>/Desktop/zoterofanyi/`
> 这类论文产物文件夹**无关**：删掉或换掉工作区不会丢 skill。
> 2026-11-14 实测：`~/.workbuddy/skills/` 下只有这一个 skill，项目级 `.workbuddy/skills/`
> 不存在（不会被同名项目级 skill 覆盖），技能注册表的「已启用」列表里能看到它。
> 三条硬规矩：① 不要把 skill 复制进任何项目目录；② 同步只走用户级目录里的 git 仓库；
> ③ 项目级 `.workbuddy/memory/` 只放日志，不放 skill 资产。详见适配层第 8 节。

> **换电脑先看这里（v2.4 起，规则 56）**：下面这批 `%USERPROFILE%\.dsh\...` 是**开发机基线**，
> 不是通用常量。新机不要照抄：先跑 `python scripts/bootstrap.py` 生成 `local_config.json`，
> 再用 `python scripts/paths.py --shell bash`（或 powershell）取到本机的 `$PY / $S / $W / $K`；
> 40+ 脚本本身无硬编码路径，原样可用。同步与本地化详见 `PORTABILITY.md`、适配层第 7 节。

- 本 skill：`%USERPROFILE%\.workbuddy\skills\zoterofanyi\`（WorkBuddy 唯一真源；`~/.dsh/` 为只读归档）
- 脚本：`...\scripts\`；模板：`...\templates\`
- 主工具 Python（含 PyMuPDF）：`%USERPROFILE%\.dsh\runtimes\zb-translate\venv\Scripts\python.exe`（`ZB_PY` 可覆盖）
- MinerU Python（已装；双栏/表格/公式必需）：`%USERPROFILE%\.dsh\runtimes\mineru\venv310\Scripts\python.exe`（`MINERU_PY` 可覆盖）
- 默认工作目录：`<当前会话工作区>\zotero-translate\<条目key>\`（下含 `work\`、`reports\`、`assets\`）
  - 放在工作区内可直接写；若改用 `%USERPROFILE%\zotero-translate`，每次读写都要审批

> 本机没有 Git Bash：`.sh` 只是 macOS 入口。Windows 一律调用同名 `.py`（`build_pdf.py` / `regression_check.py` / `run_mineru.py`），
> 并且必须用上面的 venv Python；直接 `python xxx.py` 会用错解释器（缺 PyMuPDF）。

下文用 `$S`、`$PY`、`$W` 分别表示脚本目录、venv Python、本篇工作目录。

## 交付约定（默认，用户另有要求从其要求）

| 项 | 约定 |
|---|---|
| 格式 | **PDF**（Zotero 一等公民：高亮/批注/搜索/移动端同步）；块级 JSON 作真源 |
| 版式 | **段落交错**：英文段（灰）→ 中文段（黑），成对不跨页断开 |
| 公式 | **原图裁切**，绝不重排（公式字符常损坏，重排必错） |
| 表格 | **原图裁切** + 中文表注 + 中文列名对照；跨页表标注「上接前页」 |
| 插图 | 取 PDF **内嵌原图**（画质最高）+ 译图注；图内文字不译 |
| **脚注** | **独立块** + 标记「注 N ｜ 原文 p.X」 + **脚注编号对照表**（正文 `^{n}` ↔ 脚注双向核对） |
| 参考文献 | **不翻译**（`no_translate`），但**不得连带吞掉参考文献之后的附录** |
| 页码锚点 | 每页标「原文 p.X」 |
| 备注 | 写进**父条目**笔记（Zotero 不允许笔记挂附件下） |

## 一键编排（v2.15 起，首选入口；规则 66）

> **为什么**：下面的流水线原来是一步一个脚本，每步都要停下来看输出再决定下一步。
> 在对话式 Agent 里这等于每步一次模型往返——一篇论文 40+ 次，既慢又费 token。
> 真正需要模型判断的只有**翻译**和**审校**；抽取/装配/标记/门禁/合并/索引/排版全是确定性处理，
> 应该由一个本地进程一口气跑完（几秒，0 token）。

```bash
S=<skill>/scripts ; PY=<含 PyMuPDF 的 venv python> ; W=<论文工作区>

# 阶段一：源 PDF → Gate 1 通过 → 分块 + 翻译指南（0 token）
"$PY" "$S/pipeline.py" prepare --work "$W" --pdf "$W/source.pdf" \
      [--meta "$W/work/glossary_meta.json"] [--layout-fix "$W/work/fix_layout.py"]

#   → 并行派 N 个翻译子代理：读 chunks/chunk_NNN.json + chunks/TRANSLATION_GUIDE.md
#     写 chunks/trans_NNN.json（{"items":[{"id":..,"zh":..}]}）

# 阶段二：合并 → QA → 规则 → 审校分块 + 必查清单（0 token）
"$PY" "$S/pipeline.py" check --work "$W"

#   → 并行派 M 个审校子代理：读 review/review_NNN.json + review/CHECK_FINDINGS.md
#     写 fixes/fix_NNN.json

# 阶段三：应用修订 → 脚注索引 → Gate 2 → 出 PDF → 校验（0 token）
"$PY" "$S/pipeline.py" finish --work "$W" --out-pdf "$W/xxx_双语.pdf" --label "Author et al., 2026"

# 断点/进度一眼看清
"$PY" "$S/pipeline.py" status --work "$W"
```

要点：

- `prepare` 里 Gate 1 **不通过就退出码 2 并打印 BLOCK 明细**，绝不带病进入翻译。
- `check` 在译文不全时**拒绝合并**（防静默丢段）；`--allow-partial` 才能强行继续。
- `check` 生成的 `CHECK_FINDINGS.md` 已把 `qa.py` + `rules_check.py` 的 FAIL/WARN 全量归类，
  审校子代理只读这一个文件即可；它与源报告的条数**交叉校验**（规则 67），不一致会告警。
- `finish` 的 PDF 有两条路：先 `build_pdf.py`（HTML→Edge/Chromium，版式最好），
  产物无效则**自动回退** `build_pdf_reportlab.py`（reportlab + 内置 STSong-Light CID 字体，
  覆盖中文/希腊/重音，无需外部字体）。Edge 在受限沙箱里常以 STATUS_BREAKPOINT 静默失败，
  回退是常态而非异常。
- 下面这套分步命令**仍然有效**，用于排障或只重跑某一步；日常不要再逐步手工执行。

## 流水线（严格按序）

```powershell
# 环境（PowerShell；.py 必须用全局 venv 的 python 调用）
$S  = "$HOME\.dsh\skills\zoterofanyi\scripts"
$PY = "$HOME\.dsh\runtimes\zb-translate\venv\Scripts\python.exe"

# 0) 目录（放在当前工作区内，沙箱可写）
$W = "$PWD\zotero-translate\<key>"
New-Item -ItemType Directory -Force "$W\work", "$W\reports", "$W\assets" | Out-Null

# 1) 结构抽取（MinerU，本地模型，**0 token**）
#    首次运行会下模型（~1.9 GB），需要网络 + danger-full-access；之后可完全离线
#    脚本默认：GPU（有 CUDA 版 torch 时）、关表格识别、关公式识别、模型走本地（~/mineru.json）
#    ★ 2026-10-18 实测（Jia 2017，7 页）：CPU 全开 245s → GPU+关表/公式+本地模型 27s（9.3×），输出逐块一致
#    为什么关公式：开着时 MinerU 会把公式内联进正文并清空 equation 块 → 我们裁不到公式图
#    一次性准备（换机器时）：
#      pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu126
#      python -m mineru.cli.models_download -s modelscope -m pipeline     # 生成 ~/mineru.json
& $PY "$S\run_mineru.py" <原文.pdf> "$W\work\mineru" --lang en

# 2) 混合抽取：MinerU 出结构 + 原生文本层取字 + 区域裁图
#    PowerShell 不展开通配符，先解析出 middle.json 再传
$MIDDLE = (Get-ChildItem "$W\work\mineru" -Recurse -Filter "*_middle.json" | Select-Object -First 1).FullName
& $PY "$S\extract_mineru.py" <原文.pdf> $MIDDLE `
    --out-blocks "$W\work\blocks.json" --out-regions "$W\work\regions.json" --assets "$W\work\assets"

# 3) 脚注单独抽（不要依赖 MinerU 的 discarded_blocks，它只输出一部分）
& $PY "$S\extract_footnotes.py" <原文.pdf> --out "$W\work\footnotes.json"

# 4) 装配 + 注脚注入 + 参考文献标记 + 元信息
& $PY "$S\assemble.py" "$W\work\blocks.json" "$W\work\regions.json" --out "$W\work\skeleton.json"
& $PY "$S\inject_footnotes.py" "$W\work\skeleton.json" "$W\work\footnotes.json" --out "$W\work\skeleton_fn.json"
& $PY "$S\mark_sections.py" "$W\work\skeleton_fn.json"
& $PY "$S\apply_meta.py" "$W\work\skeleton_fn.json" <glossary_meta.json>

# 4b) 本篇特有的结构修补（图注挂回、跨页续句合并、作者单位补回…）→ 脚本放 $W\work\fix_layout.py
# 4c) 文本卫生 + 骨架门禁：**翻译前**必须 0 BLOCK，否则等于花钱译一份带残渣的骨架
& $PY "$S\sanitize.py" "$W\work\skeleton_fn.json" --report "$W\reports\sanitize.json"
& $PY "$S\preflight.py" "$W\work\skeleton_fn.json" --stage skeleton --assets "$W\work\assets" `
    --out "$W\reports\preflight_skeleton.json"

# 5) 分块翻译（子代理并行；缓存只重译变动块）
#    可选省钱：翻译前把 DSH 的 reasoningEffort 调低、派完立刻调回（见「成本控制」第 1 条；审校不要调低）
& $PY "$S\translate.py" emit "$W\work\skeleton_fn.json" --outdir "$W\work\chunks" --chars 9000 --cache "$W\work\cache.jsonl"
#    9000 字符/块（2026-10-18 A/B：同篇 8 块→5 块，翻译阶段费用 −56%，质量持平）
#    子代理提示词只需 6 条硬规则（数字/引用/术语/表题/标题/不写译者注），不要逐条罗列
#    ★ v2.2 起：提示词注入 tm/term_fixes.json 的历史审校纠正（✗ 错译 → ✓ 正译），
#      形成「审校纠正 → 下一篇不再犯」的闭环；每篇收尾用 scripts/build_memory.py 增量回流。
#    → 对每个 chunk 派一个子代理，产出 trans_NNN.json
& $PY "$S\translate.py" merge "$W\work\skeleton_fn.json" --chunks "$W\work\chunks" --out "$W\work\bilingual.json" --cache "$W\work\cache.jsonl"

# 6) 二遍审校（译者与审校者分离，只回需要改的条目）
#    --mode patch（默认）：审校者只回改动片段 edits[{find,replace}]，不重抄整段
& $PY "$S\emit_review.py" "$W\work\bilingual.json" --outdir "$W\work\review" --per-chunk 30 --mode patch
#    ★ 关键：派审校者之前，先跑 qa.py / rules_check.py，把**本块内的 FAIL 逐条写进该审校者的提示词**。
#      2026-10-18 A/B 实测：不喂机检结果时，`whether or not` 的否定、`could be observed` 的限定两处被漏掉；
#      喂了就会改。这一步是 0 token 的准备，却决定审校能不能抓到真问题。
#    → 子代理按 chunk 里自带的 fix_schema 产出 fix_NNN.json
& $PY "$S\apply_fixes.py" "$W\work\bilingual.json" --fixdir "$W\work\review" --out "$W\work\bilingual_fixed.json" --log "$W\reports\changes.json"

# 7) 脚注对照表 + 渲染 + PDF 自检
& $PY "$S\build_fn_index.py" "$W\work\bilingual_fixed.json" --out "$W\work\bilingual_idx.json" --report "$W\reports\fn_crossref.json"
& $PY "$S\build_pdf.py" "$W\work\bilingual_idx.json" <输出.pdf> "<标签>"
#    注意：DSH 的 workspace-write 沙箱内 Edge 无法启动（STATUS_BREAKPOINT），本步需 danger-full-access 审批
# 7b) 交付前自检（渲染 + 文本 + 图注编号 + 字符卫生，一次把返工项抓完）
& $PY "$S\preflight.py" "$W\work\bilingual_idx.json" --pdf <输出.pdf> `
    --assets "$W\work\assets" --out "$W\reports\preflight.json"

# 8) 回归检查（全绿才进入交付）
& $PY "$S\regression_check.py" <原文.pdf> "$W\work\regions.json" "$W\work\bilingual_idx.json" "$W\reports"

# 9) 挂回 Zotero（MCP 工具，serverName=zotero；**Zotero 开着也能写**）
#    mcp__zotero__attach_file(item_key=<父条目>, file_path=<pdf>, filename=<显示名>)
#    mcp__zotero__create_note(item_key=<父条目>, title="翻译项目备注｜中英对照 · <作者 年份>", content=<项目备注.md 正文>)
#    mcp__zotero__verify_item(item_key=...) / list_attachments / get_item  ← 回读验证
#    写入路径（自动选择，无需人工干预）：
#      a) **DSH Zotero Bridge 插件**（Zotero 运行时首选）：POST http://127.0.0.1:23119/dsh/bridge
#         由 Zotero 进程内自己写库 → 不碰 sqlite 锁，界面即时刷新；
#      b) Zotero 未运行 → 直接写 sqlite（老路径，仍保留）。
#    `mcp__zotero__attach_file` 的返回里 `via` 字段会写明走了哪条路径。
#    坑与恢复见下面「Zotero 写回」一节。
#    若没有 MCP 工具，退回命令行（同一实现）：
#      $PY "$HOME\.dsh\mcp\zotero_mcp.py" cli attach <条目key> <pdf> --filename <文件名>
#      $PY "$HOME\.dsh\mcp\zotero_mcp.py" cli note <条目key> --title <标题> --text-file <备注.md>
#      $PY "$HOME\.dsh\mcp\zotero_mcp.py" cli verify <条目key> / cli bridge / cli delete <key>
#    注：`python` 一律用 DSH 自带解释器
#      %USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe

# 10) 用量与费用（每篇必做，会话口径＝账单口径）
& $PY "$S\usage_from_sessions.py" --workdir $W --item-key <Zotero条目key> --pages <页数> `
    --model deepseek-flash [--until <交付时刻 ISO>] `
    --out "$W\reports\usage.md" --json "$W\reports\usage.json"
#    → 从 ~/.dsh/sessions 里把「主会话 + 其全部子代理会话」的 usage 加起来
& $PY "$S\usage_report.py" "$W\work" --pages <页数> --out "$W\reports\usage_artifacts.md"
```

## 必须遵守的工程规则（全部是实测踩坑换来的）

### 抽取与版面
1. **不能用 `line["bbox"]` 推几何**：含 CambriaMath 的行 bbox 高 67pt（真实 15.8pt）。一律用 `span["origin"]` 基线。
2. **段落判定按「栏 × 字号」分组成流**，各自估行距。
3. **文字取原生文本层，不用 MinerU 的 OCR 文本**（实测双栏页 OCR 产生 72 个错词，如 `Objective→Ojecive`）。
4. **行内公式要按基线分组 + 组内按 x 排序重组**，否则得到 `where is a vector … Xic Fic`。
5. **上下标转 `^{}` / `_{}`**；**纯小字号碎片行必须并回相邻行**，否则 `10- to 18-` + 上标 `8` 会拼成 `188`。
6. **断行连字符要保留**（`-\n` → `-`）。
7. **表格/插图有嵌套子块**（`table_caption` / `table_body` / `table_footnote`），必须在子块层面取。
8. **公式 bbox 不含右缘编号**，要向右扩到栏宽。
9. **`discarded_blocks` 不能整类丢**：既有页码（该丢）也有脚注（必须留）。
10. **脚注编号在原生文本里常单独成行**，按字号+区域取行、再看首行是否纯编号。
11. **页脚带过滤**（y > 0.95H）可防页码混入正文，但脚注在 0.88–0.94H，别一起切掉。

### 结构
12. **跨页段落合并**：判据是"前一页末块 + 后一页首块、前块无终止标点、后块以小写/数字/汉字起首"；
    合并要英文与中文分别拼接（CJK 之间不加空格）。
13. **参考文献区按"参考条目形态"终止**，否则会把之后的附录表注一起标成不翻译（实测漏译 6 条）。
14. **脚注注入位置**：插在"起始页 > P 的第一个块之前"。不要用"页末触发"（跨页合并块会漏页/错位）。
15. **作者/机构/邮箱等元数据单独处理**，按"姓名（机构）"单行输出，不要与正文一起做上下标标记
    （否则渲染成 `AdelaideXiaobo` 黏连）。

### 翻译与校验
16. **模型只输出中文**（英文从抽取结果取），按段哈希缓存；**重译前用"已审校定稿"重建缓存**，
    否则会回退审校成果。`glossary_version` 在 `meta` 里而非顶层。
17. **覆盖率卡口**：缺任何一段直接报错，不许用原文占位。
18. **校验器先自证**：每次报错先问"是译文错还是尺子错"。实测首轮 50 个 FAIL 里 45 个是尺子错
    （逗号当小数点、中文区间、中文引用体、`OLS` 命中 `contROLS`、`no_translate` 被算缺失…）。
19. **不得信任 `no_translate` 标记**：必须反向检查该块是否真的像参考文献。
20. **`city/urban/rural` 要以英文为锚**：`urban dummy`→城镇虚拟变量，`city`→城市；否则误报。
21. **术语期望值以文档术语表为准**，检查器不要硬编码第二份清单；接受简称与"故意保留英文"。
22. **`suggest` 不要译"表明"**（应为"提示"）；`whether…might` 译"能否"已表达不确定性。
23. **数值比对按正文精度四舍五入**（表 0.169 ↔ 正文 0.17 是同值）；**排除年份与引用页码**。
24. **`0.39 to 0.50 percent` 与 `0.39% 至 0.50%` 同义**：比对要忽略 `%` 后缀。
25. **不确定术语不得擅自判定**（如 `translog`）：保留英文 + 译者注 + 列入待核对，并写术语例外表。
26. **原文疑似错误不得靠译文掩盖**（`casual`→`causal`、正文称"见表 A1"但 A1 无该明细）：单独立表记录。

### 渲染
27. **渲染器对未知块类型必须抛错**，不许静默丢内容（曾因缺 `figure` 分支丢掉整张插图）。
28. **脚注标记位置检查**：句中夹裸数字是硬错误；紧贴词语若与原文一致则保留并在报告说明。
29. **生成后必须校验中文文本层**（Zotero 高亮/搜索依赖 ToUnicode），不过则退出码非 0。

## 黄金测试集与护栏（改 skill 前必读）

`fixtures/` 里存着已验收的案例快照，用来保证**改一处不会弄坏另一处**：

| 案例 | 类型 | 守护什么 |
|---|---|---|
| `nie2015_full` | 完整双语 | 183 块 / 22 脚注 / 21 表 / 4 图 / 3 式；qa、rules、audit 的 FAIL 均为 0 |
| `pang2021_extract` | 仅抽取（双栏） | 读序正确；无 OCR 污染（不得出现 `Ojecive`/`learnig`/`Hospialhilehahillh`/`Dicuss`） |

```powershell
$PY = "$HOME\.dsh\runtimes\zb-translate\venv\Scripts\python.exe"
& $PY "$HOME\.dsh\skills\zoterofanyi\scripts\regression_suite.py" `
    --fixtures "$HOME\.dsh\skills\zoterofanyi\fixtures" --verbose
```

**护栏规则（硬性）**：
1. **任何脚本改动之后必须跑本套件**；
2. **只要有一个案例由 PASS 变 FAIL，这次改动不得合并**——
   要么改回脚本，要么确认新行为是预期的、再用 `snapshot_case.py` 更新 `expected.json`；
3. 每处理完一篇新文献，就为它**新增一个案例**（快照会同时记录当时的基线），
   案例越攒，后续革新的安全边界越厚。

```powershell
# 新增"完整双语"案例
& $PY "$S\snapshot_case.py" --kind full --bilingual <bilingual.json> --regions <regions.json> `
    --name <case名> --fixtures "$HOME\.dsh\skills\zoterofanyi\fixtures" --pdf-name "<原文文件名>"
# 新增"仅抽取"案例（守护读序与 OCR 污染）
& $PY "$S\snapshot_case.py" --kind extraction --blocks <blocks.json> --name <case名> `
    --fixtures "$HOME\.dsh\skills\zoterofanyi\fixtures" `
    --must-contain "Objective:" --must-not-contain "Ojecive"
```

> 注意：现有案例不含原文 PDF（版权与体积）。需要重跑表格交叉验证时，用 `--pdf-dir` 指向你自己的 PDF 目录。

## 成本与用量（每篇必记）

`usage_report.py` 的口径**故意保守**，因为它要能被人复核：

| 项 | 来源 | 说明 |
|---|---|---|
| 翻译/审校 token | 从产物 JSON 的字符数折算 | 英文 3.2 字符/token、中文 1.6 字符/token；**只含翻译与审校，不含 agent 编排上下文** |
| 耗时 | 产物文件写入时间推断，或 `--mineru-minutes/--trans-minutes/--review-minutes` 人工提供 | 目录里混有多轮时会偏大，必须标「推断」还是「人工提供」 |
| **实际费用** | **只能从服务商用量页抄** | `--actual-cost` 传入；拿不到就写「未记录」，**不得用估算值冒充** |

单篇基准（46 页，本 skill 开发时的实测）：

- 翻译 + 审校：**约 51k 输入 / 24k 输出 tokens**（含一次根因返工）；干净跑一遍约 **34k / 16k**
- 按 ¥2/¥8 每百万 token 折算：**¥0.2–0.3/篇**；按 ¥15/¥75 折算：**¥1.7–2.6/篇**
- 墙钟：MinerU 约 28 分钟 + 翻译 5 分钟 + 审校 1 分钟 + 渲染数秒 ≈ **40–50 分钟**
- **注意**：agent 编排（读文件、跑命令、派子代理、多轮对话）本身的开销**往往是翻译的十几倍**，
  只能从账单页看到。省钱的第一杠杆是**减少往返次数**（一次把要求说全），不是压翻译。

台账模板见 `templates/usage_log_template.md`：每篇记一行（日期/条目/页数/模型/token/耗时/实际费用）。

## 交付门槛（不得跳过）

- `regression_check.py` 全绿；任何 FAIL 必须处理或**明确列入验收报告**；
- **Zotero 回读门禁**：`mcp__zotero__verify_item(item_key=...)` 必须 `ok: true`
  （每个附件都有 `title` 字段、storage 文件存在且非空、笔记有标题与正文）；
- **交付时必须在对话里直接报本篇的 token 与费用**（用户 2026-10-18 明确要求，不等追问）：
  跑 `usage_from_sessions.py --session <主会话id> --pages <页数>`，报「调用次数 / prompt（命中率）/ 输出 / 费用 / 单页成本」，
  并说明哪些是一次性开销（修流水线、搭基础设施）；
- 交付时明确区分：**已自动检查 / 已人工核对 / 仍待核对**；
- **未做句级校对、未逐页目视时，不得称"定稿"或"已核验"**；
- 段落级校对 ≠ 句级校对。

## 已知不支持 / 需用户确认

- 扫描版 PDF（无文本层）：需 OCR 或视觉模型路线
- 双栏期刊论文：阅读顺序由 MinerU 决定，需抽 1–2 页人工核对
- 公式可复制化：默认原图；若需 LaTeX 需逐条人工核对后才启用
- 表内文字翻译：默认不译；若需翻译须重建表格并逐格核对

## 自我迭代机制（每篇之后）——已升级为「迭代协议」，见文末专章

1. 跑 `regression_check.py`（本篇）与 `regression_suite.py`（黄金集），把新出现的 FAIL/WARN 归类：
   **译文错 / 尺子错 / 原文问题 / 新版面特性**；
2. 若是**尺子错** → 修检查器；若是**新版面特性** → 在"工程规则"里加一条；
3. 若是**原文问题** → 记入 `templates/source_issues_template.md` 实例；
4. 术语争议 → 记入 `templates/term_exceptions.json`；
5. 把本轮新增命令/参数写回本文件的流水线一节。

这样每处理一篇，skill 的"检测能力"就厚一层——但**不会自动发生**，必须有人（或代理）逐条裁决。

## 2026-10 新增：Springer / Scientific Reports 版式的 5 类特例（Dimitrov 2026，19 页，实测）

30. **降部残片**：`g/p/q/y/f` 在内容流里常是**独立单字 span**，bbox 探入下一段 clip 的顶部而基线在上方 → 得到 `g g Despite this…`、`p f q y p y To evaluate…` 这类前缀（实测 40 块）。
    判据：**基线在 `clip.y0` 之上 0.5pt 以外、且文本 ≤3 字符**，直接丢弃（本篇因此只掉 238 个噪声字符，正文一字未少）。
31. **图注排在次页页首**：Springer 把上页图的图注放在下一页顶部，前面带版面箭头 `◂Fig. N.`；MinerU 把它当正文块。
    必须挂回**上一页的 figure 块**（`caption_en`）并删除该正文块，否则图没有图注、正文多一段。
32. **跨页续句被图表块截断**：跨页合并不能只看相邻两块；中间隔着 figure/table 时也要合并（`…across all` + `frequencies with a 4 kHz notch`）。
    续句以**大写缩写**开头时（`…when only` + `AC thresholds are available`）小写判据失效，要按"前块无终止标点"补判。
33. **作者单位在 `discarded_blocks` 里**：既不是脚注也不是正文，漏掉就是整段丢失（作者机构 + 通讯邮箱）。
    补成 `kind=footnote` + 字母标记（如 `A`），渲染成「注 A ｜ 原文 p.1」。
34. **页脚"Received/Accepted"、DOI 断行、零宽字符**：`https://doi.org/1 0.1038/…`、`brc.nih r.ac.uk`、`Audiogram_GMM_ RNENT`、U+200B 都会原样进译文；交付前合并/清理。
    私用区符号（本篇通讯作者标记 U+F02A）在 Chromium 里渲染成豆腐块，要换成 `*`。

**脚注与正文引用必须分开判**：Sci Rep 正文的 `^{n}` **全是参考文献编号**，不是脚注；`extract_footnotes.py` 会把正文段落误判成脚注（本篇 3 条"脚注"全是正文）。
本篇结论是"无脚注"，因此 `build_fn_index` 的**数值**核对只应针对纯数字标记，缺脚注不构成 FAIL。

### 检查器/脚本的 6 处实测 bug（已修，改后必跑黄金集）

| 脚本 | 现象 | 修法 |
|---|---|---|
| `stamp.py` | 自检写死 `"同伴效应" in text`（属另一篇），任何其它论文都报"文本层异常"并 exit 2 | 改成与篇目无关的判据：中文字符 >200 且存在 ≥6 字连续中文串 |
| `build_fn_index.py` | `int(marker)` 遇字母标记（`A`）直接崩；无数字脚注时仍插空对照表 | 标记按字符串取键；数值交叉核对只对纯数字；无数字脚注不插表 |
| `qa.py` | `1.5 billion ↔ 15亿`、`450 million ↔ 4.5亿`、`1970s ↔ 20 世纪 70 年代`、`the 2 models ↔ 两个模型` 被判"数字丢失" | 加量纲/年代/中文数词等价判定（只在英文侧确实用了 billion/million 或 `Nd`s 时放行） |
| `extract_mineru.py` | 降部残片污染（见规则 30） | 丢「基线在 clip 之上且 ≤3 字符」的 span |
| `render.py` | 竖版图 `width:100%` 放大后超高 → Chromium 吐 2 页空白、整图消失；`h2` 可在页底被拆开；头部标题写死"试译样本"；术语表整表默认打印占近 2 页 | 图片改"高度封顶 820px、宽度自适应"；`h2.sec` 加 `break-inside/break-after: avoid`；标题读 `meta.doc_kind_label`；术语表读 `meta.print_glossary` |
| `regression_check.sh` | 本机 bash 把 `$name（` 里的中文括号当成变量名的一部分 → `unbound variable`，整条流水线跑不起来 | 变量一律加花括号 `${name}（` |

### 每篇用量（实测，不是估算）

Dimitrov 2026（19 页 / 11 图 3 表；翻译 11 块 + 审校 12 块；共 353 次模型调用）：

| 口径 | 数值 |
|---|---|
| prompt tokens（含缓存命中） | 34.86M（命中率 99.1%） |
| 输出 tokens | 0.25M |
| 空闲时段（deepseek-flash：1 / 0.02 / 4 元每百万） | **≈ ¥2.0 / 篇** |
| 高峰时段（2 / 0.04 / 8 元每百万） | **≈ ¥4.0 / 篇** |
| 其中翻译+审校子代理 | ¥0.82（41%） |
| 其中主会话编排（读文件、跑命令、多轮对话、返工） | ¥1.19（59%） |
| `usage_report.py` 的产物估算 | ≈ 42.5k 输入 + 16.1k 输出 ≈ **¥0.04** |

> **结论（修正本文档早先的"编排是翻译的十几倍"）**：实际是**约 50 倍**。
> `usage_report.py` 只数翻译/审校产物，**不能当成本**；真实费用只能按会话的 usage 记录或服务商账单算。
> 会话记录位置：`~/.dsh/sessions/<工作区>/<session-id>/session.v4.jsonl.zstd`，逐条 `assistant/message` 带
> `inputTokens / outputTokens / cacheReadTokens`，把本篇（主会话 + 其子代理会话）加起来即可。


## 2026-10 新增：PLOS ONE（2015 版式）的 9 类特例（Williams 2015，23 页，实测）

35. **左侧窄栏是期刊元信息**：p1–p2 的 `x<195` 区域是 Citation / Editor / Received / Copyright /
    Data Availability / Funding / Competing Interests，会与正文交错。做法：合并为**字母标记脚注**
    （`注 A`=作者/机构/邮箱，`注 B`=元信息，pages 跨 [1,2]），置于标题之后；机构名保留英文、说明性文字译中文。
36. **参考文献条目号独占一行**（`6.` / `Swinburn B, …`）。MinerU 会把整页并成一块、并与页眉黏连。
    做法：按原生文本层逐行重建，用"编号必须 == 期望值（递增）"判定新条目起点——比正则更稳。
    本篇实际 **70 条**（p23 首行是第 66 条，容易误判成 66 条）。
37. **表注（a/b/c/d）脱离表体变成正文段**，`doi:10.1371/…t00N` 单独成段。做法：按 y 位置挂回对应
    `table.note_en`，doi 行丢弃；表注在原文里的顺序要还原（不是按 MinerU 顺序）。
38. **跨页续句被整页表格截断**：`p6-4→p8-0`（表 2）、`p8-6→p9-0`（表 3）、`p10-3→p13-2`（表 5）、
    `p13-4→p16-0`（表 6）、`p18-1→p19-0`。assemble 的"相邻两块"判据失效 → 在 fix_layout 里手工合并，
    合并块保持在**首次出现的位置**（读者读完再翻表）。
39. **图表编号与文本层里的 doi 行**：`doi:10.1371/journal.pone.0145128.t003` 这类行是版式附属物，删。
40. **符号字体丢映射**：`\x01=≤`、`\x03=≥`、`\x04=≈`（实测：`1.04≤BMI-SDS<1.64`、`≥85th`、`medium (≈)`）。
    **必须裁图目视确认**再替换，不能靠猜——本篇正是靠裁那两行才定下来的。
41. **断行连字符残留**：`dis-eases`、`impor-tance`、`cross-classifi-cation` 等 13 个词的完整形式
    在本文只出现一次，词表法查不到 → 用**显式 ARTIFACTS 映射表**兜底（`fix_layout.py` 里那份）。
42. **表格题注的两种位置**：表 5/6 的"(Continued)"印在表体右上/下方并单独成段 → 并入表题成「（续）」；
    每页续段无题注 → 渲染器自动出「（上接前页表格，同一表格的续页）」。
43. **术语表条目不要写"（可作…）"这类解释**：`term_audit.py` 与 `qa.py` 按字面匹配，
    带解释的术语会被判"未按约定翻译"（本篇 `prevalence`、`BMI-SDS` 各误报一轮）。
    正确做法：术语表只放**最小可匹配词**，备选译法写进「术语例外表」。
    （2026-10-18 又踩一次：Moreira 篇把 `prevalence → 患病率（超重/肥胖语境可作…）` 写回术语表，误报 7 条。）

**本篇结论：无脚注**。`extract_footnotes.py` 检出的 6 条是表 2 的单元格文字（`0.40±0.38 …`），
`build_fn_index` 的数值核对只针对纯数字标记，缺数字脚注不构成 FAIL。

### Zotero 写回的 3 个坑（本篇实测，已修）

| 坑 | 现象 | 修法 |
|---|---|---|
| **附件缺 `title`** | 条目树里附件**显示为空**（"没有文件名"）；库里 441/449 个 PDF 附件都有 title，只有写错的那条没有 | `attach_file` 默认 `title = filename`；`verify_item` 把"有 title"设为硬门禁 |
| **stdio 编码** | Windows 上 MCP 客户端按 ANSI 解码，直接吐 UTF-8 中文 → 工具描述与结果全是乱码 | 输出一律 `ensure_ascii=True`（`\uXXXX`），输入固定按 UTF-8 解码 |
| **写锁** | Zotero **长期持有 sqlite 写锁**（实测运行期 40 次尝试 0 次成功），外部写入一律 `database is locked` | **不再要求关闭 Zotero**：走 `DSH Zotero Bridge` 插件（见下）由 Zotero 自己写；仅在 Zotero 未运行时退回直接写库 |

### DSH Zotero Bridge（2026-10-18 起，Zotero 开着也能写）

- **插件**：`%APPDATA%\Zotero\Zotero\Profiles\<profile>\extensions\author@example.org`
  （源码在 `%USERPROFILE%\.dsh\zotero-bridge\dsh-zotero-bridge@local\`，改完用 `Compress-Archive` 打包覆盖即可）
- **端点**：`POST http://127.0.0.1:23119/dsh/bridge`（复用 Zotero 自带 HTTP 服务，不额外开端口）
- **操作**：`ping` / `attach{itemKey,file,filename,title}` / `note{itemKey,title,html}` / `delete{key}`
- **关键实现细节**（踩过才知道）：
  1. Zotero 对 `application/json` **已把 body 解析成对象**放进 `data.data`，不是字符串——先判 `typeof === "object"`；
  2. 端点对象要挂 `Zotero.Server.Endpoints['/dsh/bridge']`，`init` 返回 `[status, contentType, body]`，异步操作用 Promise；
  3. 写库走 `Zotero.Attachments.importFromFile({file, parentItemID, libraryID})` 与 `new Zotero.Item('note')` + `saveTx()`，**不要**自己拼 sqlite；
  4. 安装/升级后要**重启 Zotero** 一次（插件只启动时加载）；之后再改代码也只需重启 Zotero。
- **自检**：`$PY "$HOME\.dsh\mcp\zotero_mcp.py" cli bridge` → `{"ok":true,"zotero":"9.0.6"}`；
  `cli verify <条目key>` → `ok:true` 才算挂好。
- **回退**：桥接不可用时 `attach_file` 自动退回 sqlite 路径（此时必须关 Zotero），返回的 `via` 字段会说明。

## 2026-10 新增：Nature / IJO 双栏版式的 6 类特例（Jia 2017，7 页，实测）

44. **双栏阅读顺序不能按 y 排序**：`assemble.py` 用 (page, y) 排序，双栏页会左右交错（本篇第一版骨架完全乱序，
    连摘要和方法都插错位置）。正解：**按 `blocks.json` 里 MinerU 的原始顺序**建列表，只把**通栏**图表按 y 插回。
45. **同一句出现两遍**：双栏块 bbox 重叠 + 跨页合并叠加，会得到重复段（本篇 4 处整块被包含、1 处段内尾段重复）。
    判据：块级"某块全文出现在另一块里"→ 丢短的；段内"末尾 k 字符与紧邻其前的 k 字符相同（k≥40）"→ 删后者。
    EN 与 ZH 各跑一遍（译文可能只重复其中一侧）。
46. **上标数字引用 `^{1}^{–}^{3}`**：相邻上标要合并成 `^{1–3}`；`^{19,20}Particularly` 要补空格；
    行首被甩出的裸数字（`25 have different impacts…`）要归位到**本块第一个句末**。
    可用 `^{}` 标记检验：qa 的"正文引用编号"会把它们当脚注编号，缺数字脚注不构成 FAIL。
47. **题注被 caption bbox 截断**：本篇四张表只剩 "Table 1."、图注丢了首字母。
    修法：在原文本里按 `^(Table|Figure) \d+\.` 找题注行，再**把同一 y（±3pt）的片段按 x 拼起来**（题注常被拆成"编号"+"正文"两个 span）。
    跨行图注（末尾 "Nanjing and" / 次行 "Xi'an."）要**向下多取 16pt**。
48. **表注脱离表体**：`Abbreviations: …` / `a…b…c…` 既不在 blocks.json 也不在表 bbox 内，
    要按 y 区间从原生文本重建，再挂到 `note_en/note_zh`；表注里的 a/b/c 是**上标**，抽取后成了行首裸字母，要还原成 `^{a}`。
49. **字形丢映射（务必裁图确认）**：本篇 `<` 印成 `o`（`*Po0.05`）、`>` 印成 `4`（零花钱档位 `410–30` = `>10–30`）。
    规律：只影响**符号字体**，正文数字正常；判据是上下文语义 + 300dpi 裁图目视，不能靠猜。

### 由本篇引起的 3 个流水线级修复（已改）

| 位置 | 问题 | 修法 |
|---|---|---|
| `qa.py` canon | `\s*-\s*`→`-` 无条件执行，把「—— 16 所」读成 `-16`、「~ 30%」读成 `-30%` | 只在**数字与数字之间**统一连接符；`PUNCT_MAP` 不再把 `—`/`–`/`~`/`～` 映射成 `-` |
| `qa.py` canon（同处） | 破折号当负号还导致"原文数字未出现"的假 FAIL | 同上；改后黄金集复跑 10/10 |
| `work/dedupe.py`（本篇新增，待收进 scripts） | 双栏重复段 | 块级包含 + 段内尾段重复折叠 |

### 2026-10 新增：BMC 双栏 + 通栏表的 2 类新特例（Moreira 2020，17 页，实测）

50. **通栏表可能被排成"左右两半"**：本篇 Table 3 在 p10 上左半=变量+Gross OR、右半=Adjusted Model I–III，
    MinerU 只把左半栏识别成 table → **右半（论文主结果）整块丢失**。
    判据：`table` 区域宽度明显小于版心（本篇 54–284pt，版心 57–539pt）时，**必须按通栏重裁**
    （`page.get_pixmap(clip=Rect(版心左右缘))`），否则 OR 值与变量对不上、甚至整列缺失。
51. **重建参考文献时不要整页 DROP**：用 `^p15-` 之类的正则一次性删整页，会把同页的
    Ethics approval / Consent for publication / Competing interests / Author details 一起删掉（本篇被审校者抓到）。
    正确做法：只删**参考文献段落**（从 "References" 标题之后的第一个 `N. Author` 开始），保留同页其他小节；
    删完用 `preflight` 的块数与"无章节归属块"提示核对。

**另**：题注跨行时按 y 邻接补全（`…of municipal` + `public schools in Northern Brazil, 2018`）；
表注字母数以原文为准（本篇表 1 是"首项无字母 + a–d"），不要为了对称自造字母。

### 2026-10 新增（第 3 批，3 篇一批）：两条硬规矩

52. **禁止用 PowerShell 写 `.py` 文件**。`Get-Content -Raw` 按 GBK 读 UTF-8 中文脚本、
    `Set-Content -Encoding UTF8` 再写回，会把中文字面量全部写成乱码且**不可逆**（字节已被吞）。
    2026-10-18 就是这样把 `rules_check.py` 写坏、花了一小时重建词表并把黄金集从 14/14 修回来。
    改脚本一律：`python -c` 内用 `open(p,'w',encoding='utf-8',newline='\n')`，或直接用 edit 工具。
53. **一次处理多篇时，按"每篇一个 work 目录 + 共用参数化修补脚本"组织**：
    `_generic/fix_generic.py`（CONFIG 按 paper_key 配 8 个字段）+ `_generic/make_glossaries.py`（共享术语底 + 专有术语）。
    3 篇 44 页的子代理费用 ¥1.18（单篇 ≈ ¥0.39，单篇处理时 ¥0.76）→ **批量摊薄有效**，
    但**审校 pass 不能省**：第 3 批为赶进度跳过了审校，只做到"翻译 + 机检"，不算达标交付。

## 2026-11 新增：WorkBuddy 继承 + 翻译记忆回流闭环（v2.2）

54. **翻译记忆必须增量合并，不得覆盖式重建**：`tm/term_fixes.json` / `error_patterns.json` 是历次审校纠正的
    累积资产；早期散落在工作区 `_generic/build_memory2.py` 用 `json.dump` 直接覆盖，重跑一次就会把
    未被本批 `changes.json` 覆盖的历史条目清空。现收编为 `scripts/build_memory.py`：按 `(wrong, right)`
    去重增量合并，支持 `--changes` glob 全量重建。每篇收尾跑一次，翻译前把清单注入子代理提示词——
    否则"迭代"只停在文档里，不会变成下一篇的质量提升。

**其余改动**：新增 `WORKBUDDY-ADAPTER.md`（工具映射 / Bash 版流水线 / Zotero CLI / 迭代七动作）；
确立 `~/.workbuddy/skills/zoterofanyi/` 为唯一真源，DSH 原版只读归档；
`usage_from_sessions.py` 依赖 DSH 会话日志，WorkBuddy 会话不可直接统计，费用改用
`usage_report.py` 产物估算并明确标注「非账单口径」，实际费用只能取用户提供的 `--actual-cost`。

## 2026-11 新增：每次迭代用 git 收口（v2.3，用户明确要求）

55. **每次迭代必须以 git 提交 + 版本 tag 结束**：skill 根目录本身是独立仓库，
    `CHANGELOG.md` 的版本号必须与 annotated tag 同名（如 `v2.3`）。收尾统一跑
    `scripts/iterate_commit.py -v v2.3 -m "<一句话说明>"`：脚本会检查 CHANGELOG 同版本条目、
    是否有实际文件改动、黄金集是否全绿、tag 是否重复，通过后才 add / commit / tag。
    **只在对话里说"已迭代"、没有 commit hash，一律不算完成**；禁止覆盖 tag、改写历史或擅自 push。
    理由：可迭代性依赖「能看清每次改了什么、能安全退回上一版」，口头约定跨会话会蒸发，
    脚本门禁不会。命令与版本坐标见 `WORKBUDDY-ADAPTER.md` 第 6 节。

## 2026-11 新增：跨机同步与本机本地化（v2.4，用户第三个要求）

56. **版本同步与本机配置必须分离，换机不改脚本**：git 仓库里只放与机器无关的资产
    （规则、脚本、黄金集、翻译记忆、模板）；所有本机路径只写进 `local_config.json`
    （`detect_env.py` 探测生成、`.gitignore` 排除、`paths.py` 统一读取，**绝不入库、绝不跨机复用**）。
    新机一律先跑 `python scripts/bootstrap.py`（clone/pull → 探测 → 生成配置 → 黄金集自检）再翻译；
    `$PY / $S / $W / $K` 由 `python scripts/paths.py --shell bash`（或 powershell）生成，
    禁止把本机绝对路径写回脚本、fixture 或文档示例。实测现有 40+ 脚本**零硬编码绝对路径**，
    所以新增的只是一层变量生成，不必改脚本。同步通道（私有 git 远端 / 云盘 bare 仓库 /
    离线 bundle）与隐私边界见 `PORTABILITY.md`、适配层第 7 节。
    ~~同步必须显式~~（v2.5 起被规则 57 取代）：v2.4 时 `iterate_commit.py` 默认不联网、
    只有 `--push` 才推；**用户第四个要求改为「云端永远最新」，故 v2.5 起默认自动同步**。
    不变的是：`bootstrap.py` 只在显式参数下写云盘，且绝不直接同步含 `.git` 的工作目录。

## 2026-11 新增：GitHub 云端同步与全局安装（v2.5，用户第四个要求）

57. **每次使用前后必须与云端交互，且云端最新由「回读校验」证明**：
    - **使用前（加载本 skill 后第一件事）**：`python scripts/sync_now.py --start`
      —— `fetch --prune --tags` → 只允许 `--ff-only` 快进 → 分叉立即 BLOCK 交人工裁决；
      离线只 WARN，本地版本照常可用（不阻断翻译）。
    - **使用后（每次迭代收尾）**：`scripts/iterate_commit.py -v <版本> -m "<说明>"`
      **默认自动推送**（有 origin 时），委托 `sync_now.py --end`：先查分叉 →
      `push origin main --follow-tags` → 用 `ls-remote` 回读远端 HEAD 与本地比对；
      没对齐就明确报「本地已提交、云端未更新」，绝不假装同步成功。
      `--no-push` 可临时退回纯本地；`--push` 变成「必须推送、无 origin 即 BLOCK」。
      尚未连接 GitHub 时自动降级为纯本地提交 + WARNING，**不阻断迭代**。
    - **一次性连接**：`scripts/connect_github.py`（凭据交给 GCM / GitHub 连接器，
      token 绝不写进 URL、配置或日志；默认建**私有**仓库）。其他电脑用
      `bootstrap.py --remote <url>` 引导，`sync_now.py --start` 取最新版本。
    - **全局性**：skill 与 git 仓库都在用户级目录 `~/.workbuddy/skills/zoterofanyi/`，
      本机所有项目可见；禁止把 skill 复制进项目目录，禁止用项目级同名 skill 覆盖。
    - **隐私结论（2026-11-14 全量审计）**：100 个入库文件里**无任何凭证 / token / 私钥 /
      口令**；只有公开论文片段、论文里的作者邮箱、文档示例路径。但 `fixtures/` 含
      7 篇论文约 36 万英文字符的正文摘录（≈6 万词），属于**公开论文的大段再分发**而非机密，
      仍有出版社版权/条款风险 → **建议私有仓库**，`--public` 需用户明确选择。

## 2026-11 追加：真实 GitHub 连接实测（v2.6，规则 58）

58. **GitHub App 连接器与本地 git 凭据是两套授权，互不替代**：
    - 实测（2026-11-14）：WorkBuddy 的 GitHub 连接器能取账户信息、搜索公开仓库，
      但 `POST /user/repos` 返回 `403 Resource not accessible by integration`，
      新建的**私有**仓库对它返回 `404`（App 只被授权安装时选定的仓库）。
      所以「自动建仓 + 推送」不能依赖连接器，必须走本地 **GCM 用户级凭据**
      （设备码授权一次，scopes `repo, workflow, gist`）。
    - 一次性落地：用该凭据建私有空仓 → `connect_github.py --remote <url>` 配 origin →
      `sync_now.py --end` 推 `main` + 全部 tag → `--status` 回读校验。
      token 只在系统凭据库 / 内存里，绝不写进 URL、`.git/config`、日志或入库文件。
    - **同步是否成功以 `sync_now.py --status` / `ls-remote` 回读为准，不以连接器能否看到仓库为准**；
      若要让连接器也管理新仓库，需用户到 GitHub App 安装设置把该仓库加入授权范围
      （或选 All repositories），这不是 skill 能自动完成的动作。
    - 本机已连接：`origin = https://github.com/xiaozeng9066/zoterofanyi-skill.git`（**private**，
      v2.5 首次推送全部资产 + 4 个 tag）。开工 `sync_now.py --start`，收尾 `iterate_commit.py`
      自动推；其他电脑 `bootstrap.py --remote <url>` 后 `paths.py --check` + 黄金集。
    - 设备码登录在无终端环境可能**静默完成且无任何输出**：判断凭据是否就绪要用
      `git credential fill` 实测，不能看命令 stdout；同理，push 的 exit 0 不等于云端最新，
      必须有回读校验。
    - 网络类瞬时故障（代理 CONNECT 502 / schannel 提前断连）由 `sync_now.py` **只对网络错误**
      有限重试 3 次并打印诊断；分叉 / 权限错误立即 BLOCK。仍失败时 `--start` 降级用本地版本，
      `--end` 明确报「本地已提交、云端未更新」+ 补推命令，恢复后跑 `--end` 即可，不需重新提交。
      v2.5 已把 21 个 fixture 里写死的本机 `source_pdf` 绝对路径清成
      `<WORKSPACE>/zotero-translate/<KEY>/source.pdf`，黄金集复跑仍 14/14 PASS。
    - 绝不 `push --force` / `reset --hard` / 自动合并冲突；多机同时改动造成分叉时，
      人工 merge 后再提交，云端最新以「回读 HEAD 一致」为准，不以「push 没报错」为准。

## 2026-11 新增：公开库的数据最小化（v2.7，用户第五个要求）

59. **公开库靠「删掉不必要的数据」保护，不靠加密；且必须新建干净历史**：
    - **审计结论**（102 个入库文件）：`fixtures/` 占 73.8% 体积，含 7 篇论文约
      **608 KB 英文正文**；`tm/error_patterns.json(+jsonl)` 含 9 条整句译文（最长 594 字符）；
      邮箱 42 处全是论文公开作者邮箱；**0 凭证 / 0 私钥 / 0 口令 / 0 原文 PDF**。
      风险维度是**公开论文的大段再分发（版权/条款）**，不是"机密泄露"——
      加密解决不了版权问题，**删掉才可以**。
    - **哪些必须留**：59 条规则 / 流水线 / 40+ 脚本 / 模板 / 术语表短词条 /
      CHANGELOG / **黄金集的结构与断言**（块数、页数、kind 分布、短断言串）。
      **哪些不必公开**：论文正文、整句译文、作者邮箱、PDF 名 / DOI / Zotero 条目 key。
    - **不加密的理由**：clone 后无密钥读不到 → 分享失效；密钥仍走私有通道；
      GitHub 网页/diff/CI/搜索全废；**最致命的是历史**：v2.2–v2.6 每个 tag 都含
      完整 `fixtures/`，加密当前文件擦不掉历史 blob。加密只适合**本地全量 fixture 归档**。
    - **绝不把现有私有仓库直接改 public，绝不 `filter-repo` / BFG + 强推**
      （规则 55 禁止改写历史、覆盖 tag、`push --force`）。公开仓库必须从
      `dist/public` 脱敏副本**单独建一条干净历史**；三种拓扑见 `PUBLIC-RELEASE.md` 第四节。
    - 落地：`scripts/public_sanitize.py --audit` 只读审计；
      `--all --out dist/public --update-expected` 生成副本并复跑黄金集；
      `--verify` 独立复验。副本 **14/14 PASS + 残留 0 处** 才能发布。
      `dist/` 已进 `.gitignore`；**私有主库保留全强度 fixtures，本机回归质量不降级**。
    - 合成 fixture 的六条实测约束（换了输入就会假 FAIL 或漏掉原文）：① 双语数字必须
      字面一致，否则 qa 报「译文出现原文没有的数字」；② 不能含 `^{}`，否则成对
      `caption_*` 被 audit 判「上标缺失」；③ **空字段必须保持空**（表格块
      `caption_en: ""`），否则 R5.1 判「有原文但无译文」；④ 短标题串也换成合成锚点
      并同步改 `expected.json`；⑤ **按字段名而非长度**判断（`regions.json` 里 78 字符
      的 `caption_en` 曾漏网），且 `legend`/`rows`/`raw_lines` 等**列表型**字段要递归处理；
      ⑥ 必须做「真源正文 → 副本全文」**全量交叉验证**（1374 条 / 0 命中），
      比率计算用 ASCII-only（汉字 `isalpha()` 也为 True，会误判中文备注）。
    - 5 个案例的 `qa/audit.fail` 由 1/5/7/3/5 变 0，是**脱敏后输入变干净的预期正确化**：
      按迭代协议更新**副本**的 expected.json 并在 CHANGELOG 写明理由，
      **私有主库的 expected.json 一个字节未动**。

> 完整论证、审计表、发布 SOP 与隐私边界见 `PUBLIC-RELEASE.md`（适配层第 9 节）。

60. **公开库若成为「唯一主库 / 日常同步目标」，隐私约束必须是每次提交的门禁，不是一次性发布动作**：
    - 隐患：公开主库下，每处理一篇新论文 `snapshot_case.py` 都会把**真实正文**写进 `fixtures/`，
      而 `iterate_commit.py` 默认自动推送 —— 发布时脱敏得再干净，**一次迭代后就失效**。
    - 落地：`--slim` 在副本写入 `PUBLIC-MODE.json`（私有真源不含，故不触发门禁）；
      `iterate_commit.py` 检测到 `mode=public` 就强制跑 `public_sanitize.py --gate`，
      扫描 `fixtures/` 的 `TEXT_KEYS` 与 `tm/` 整句对照，命中真实正文即 **BLOCK**，
      **不受 `--skip-regression` 影响**；提交信息记 `PublicGate:` 行。
      实测：私有真源被拦 1644 处、公开副本 PASS、注入 1 句真实正文立刻 BLOCK。
    - 真实全强度快照一律放 **`fixtures_full/`（gitignored）**，`fixtures/` 只放合成案例；
      本机跑全强度回归用 `regression_suite.py --fixtures fixtures_full`。
    - 禁止删除 `PUBLIC-MODE.json` 或改 `mode` 绕过门禁；新案例须先合成化再进 `fixtures/`。
      这条护栏随 clone 自动生效，不依赖本机配置或人的记性。

61. **代理是前置条件：GitHub 一律走代理，不能靠「直连绕过」**（v2.11 改写，v2.10 判断有误）：
    - 先把结论记死：**国内环境不可能绕过代理**。v2.10 曾把「直连更稳」写成规则，是误判：
      同一台机器相隔十几分钟的两次探测结论相反（一次「代理 502 / 直连 3/3 成功」，
      一次「代理 200 / 直连也 200」）；而且若本机装了系统级 TUN / 透明代理，
      `-c http.proxy=` 只是名义上绕开代理、实际仍被中转 —— 拿它当选路依据等于自欺。
    - 落地：`local_config.json` 的 `network` 段为 `mode: proxy-required` + `fallback: warn|block`。
      `sync_now.py` 固定「代理优先 → 逐个代理端点重试（每端点 2 次，退避 1.5s）→ 只有 `fallback=warn`
      才应急降级直连」；降级时必打印 `[WARN]`、`--status` 标「应急降级」、`--json` 记 degraded，
      **绝不静默把直连当常规路径**。`--strict-proxy` 让代理不通时直接 BLOCK，不做降级。
    - **代理端点会漂移**（v2.13 实测：同一天 14126 → 4661 → 7890，端口死了 ≠ 代理不可用）。
      `detect_env.py` 会扫本机常见代理端口，把**实测可通**的写进 `network.proxy_candidates`，
      `sync_now.py` 按候选逐个试。**换端口仍是走代理，只有 `direct` 那段才算绕过** ——
      不要把「换端点」和「绕代理」混为一谈，也不要用换端口来掩饰代理本身的问题。
    - 直连只保留两个用途：① 诊断 `--route direct`（判断「是不是代理把 GitHub 打挂了」）；
      ② 代理彻底不通时的应急兜底。两者都必须显式、可见、不得写成默认。
    - `bypass_hosts` 恒为空；`connect_github.py` 默认强制 `ProxyHandler`，不再对 `api.github.com` 绕代理。
      禁止改 `NO_PROXY` 把 GitHub 排除在代理之外，禁止 `.git/config` 写死代理，禁止强推。
    - 排查顺序：① `--status` 看候选端点与「实际走哪个」（`--json` 的 `endpoint` 字段）；
      ② 都不通查代理进程 / 节点；③ 只有怀疑代理限制 GitHub 时才 `--route direct` 对照；
      ④ 恢复后 `--end` 补推，**不要重新提交**。

62. **用户明确陈述的环境事实，优先于本机单次探测数据**（v2.11）：
    - 起因：v2.10 用「`git ls-remote` 直连 3/3 成功」推翻用户「我设过不让走代理？」的直觉，
      再被用户一句「不可能绕过代理走的，国内环境不允许」全盘纠正。
    - 规矩：涉及网络可达性、代理、白名单、账号权限这类**环境事实**，本机探测只能作为**佐证**，
      不能作为推翻用户陈述的依据；探测结果自相矛盾（同一 host 两次结论相反）时，
      本身就该按「不可靠」处理，而不是挑对自己有利的那次写进规则。
    - 写规则前先自问：这条结论如果错了，会不会让后续每篇论文都踩同一个坑？
      会 → 写成「默认严格 + 显式降级」，不要写成「按探测结果择优」。

63. **远端只保留一个主库；删除仓库是不可逆操作，必须「列清单 → 留底 → 最小权限 → 回读」**（v2.13）：
    - 用户 2026-11-14 要求清空 GitHub 上其他仓库、只留公开主库。这类操作先做四件事，缺一不可：
      ① **列清单让用户逐项确认**（不要从「其他都删」自行推断边界，尤其涉及带历史的归档库）；
      ② 删除前 `git clone --mirror` + `git bundle create <name>.bundle --all` 留底到
      `~/.workbuddy/backups/`，并在回复里报出 bundle 体积与 tag 数；
      ③ 用**最小权限**的 classic PAT（只要 `delete_repo`，7 天过期），token 存**本地文件**读取、
      **绝不出现在聊天/日志/配置**，用完立即 `rm` 并提醒用户去 Settings → Tokens 撤销；
      ④ 删完回读 `GET /user/repos` 核对剩余仓库，并 `ls-remote` 确认主库 HEAD/tag 未受影响。
    - **权限事实**：GCM 的 OAuth token 固定为 `repo, gist, workflow`，**没有 `delete_repo`**，
      且 GCM 2.9 不支持自定义 scope（`github login` 无 `--scopes`）。所以删仓库只能靠
      classic PAT（`/settings/tokens/new?scopes=delete_repo`）或网页操作，**不要试图用连接器或改 OAuth**。
    - 删除后必须同步改文档：凡写着「私有归档库 / archive 库」的段落（`SKILL.md`、`PUBLIC-NOTICE.md`、
      `PUBLIC-RELEASE.md`、`WORKBUDDY-ADAPTER.md`）一律改成「已删除 + 本机 bundle 路径」，
      否则下一个人（或未来的我）会去找一个不存在的仓库。
    - 禁止：批量删除时跳过确认、把 PAT 写进任何文件或命令行历史、未经备份就删带历史的库。

64. **README 与代码同源、同版本；首页过时等同 bug，靠门禁而不是靠自觉**（v2.14）：
    - 起因：公开库首页长期缺失，补写时若把「v2.13 / 14 案例 / 规则 63」写死，几次迭代后就会误导
      下一个人和下一次会话（**撒谎的首页比没有首页更糟**）。
    - 落地：`README.md` 顶部有机器可读状态表（`<!-- readme-state:start -->` 之间），
      `scripts/check_readme.py` 把它与真实来源逐项对齐：版本 ↔ `CHANGELOG.md` 首条、
      规则编号 ↔ `SKILL.md` 最大编号、案例数 ↔ `fixtures/*/expected.json` 计数、
      记忆条数 ↔ `tm/*.json` 长度、文档内路径 ↔ 是否真实存在。
      `iterate_commit.py` 把它作为**提交前门禁**（与公开模式门禁同级），不一致直接 BLOCK。
    - 数字用 `python scripts/check_readme.py --fix` 刷新；**说明文字只能人工同步**——
      凡是让「怎么用」发生变化的改动（新增脚本、改入口、改流程、改依赖、改仓库拓扑），
      必须同一次迭代里改 README 对应章节，否则下次看到的就是半真半假的文档。
    - 禁止：删状态表标记来绕过门禁、只改数字不改文字、把 README 写成第二份 `SKILL.md`
      （README 只讲「是什么 / 怎么装 / 怎么用 / 去哪查」，细节一律指向 `SKILL.md` 与适配层）。

65. **Zotero 桥接不通先查插件兼容门禁，别猜网络**（v2.15）：
    - 起因：`cli bridge` 长期返回 404，被当成「Zotero 没连上 / 网络问题」反复试探。
      真因是 `extensions.json` 里 `dsh-zotero-bridge@local` 的 `active:false, appDisabled:true`——
      它声明 `strict_max_version: 9.*`，而 Zotero 实际是 **10.x**，被 Zotero 主动禁用，端点从未注册。
    - 判据：`extensions.json` 里 `appDisabled:true` 或 `maxVersion` 低于当前大版本 = 插件被禁；
      同机可用的插件（如 Zotero One 写 `10.9.*`）是现成的对照物。
    - Zotero 自带的 `127.0.0.1:23119` 只开放 `/connector/*`（浏览器抓文献用），**没有写库 API**，
      不要在这上面继续探测。写库只有两条路：官方数据层（插件）或 Zotero 关闭时直写 sqlite。
    - 落地：`scripts/zotero_bridge/`（`manifest.json` + `bootstrap.js` + `install_bridge.py`），
      注册 `POST /dsh/bridge`（`ping / attach / note / delete`），走
      `Zotero.Attachments.importFromFile` 与 `new Zotero.Item("note")`；
      `--install` 写入 profile/extensions，`--check` 自检。**装完必须彻底重启 Zotero**。
    - 禁止：在 `--check` 未返回 `ok:true` 时声称「已连上 Zotero」；把 `strict_max_version` 写成
      正好等于当前版本（升级即失效），应覆盖整条版本线（当前 `10.9.*`）。

66. **确定性步骤下沉成本地进程，模型只做翻译与审校**（v2.15）：
    - 起因：流水线原本一步一脚本，每步都要看输出再决定下一步，一篇论文 40+ 次模型往返，
      既慢又费 token——慢的根因是**架构**而不是算力。
    - 判据：这一步需不需要语言判断？不需要（抽取/装配/标记/门禁/合并/索引/排版）就归 `pipeline.py`。
    - 落地：`scripts/pipeline.py` 三个子命令 `prepare / check / finish` + `status`，
      一条命令跑完一整段，失败以非零码退出并打印该步输出尾部，不静默跳过。
      PDF 两条路：`build_pdf.py`（HTML→Edge）优先，产物无效自动回退 `build_pdf_reportlab.py`。
    - 禁止：为「看得清楚」把机械步骤拆回逐步交互；新增脚本必须能被 pipeline 串起来。

67. **机器检查结果解析必须与源计数交叉校验**（v2.15）：
    - 起因：`qa.py` 的 `issues` 是 `[level, msg]` **数组对**而非对象，汇总脚本按 dict 解析，
      结果「终端打印 46 条、审校清单 0 条」——审校漏掉整整一半机器线索，且不报错。
    - 判据：任何「汇总/聚合」环节，都要有一个来自源头的独立计数可以对照。
    - 落地：`pipeline.py` 分别解析 `qa.py`（`results[].issues` 数组对）与
      `rules_check.py`（`findings[]` 对象），并与 `qa.py` 自报的 `fail/warn` 比对，
      不一致立即打印 `[!] qa 计数不一致` 告警。
    - 禁止：以解析结果为准静默通过；删掉交叉校验来「消除告警」。
68. **脚注正文不得泄漏进正文段落**（v2.16）：
    - 起因：Lim & Meer 2018 第 11 页，脚注「因为 −β(n−1) 恒为 1…」被版面提取并进正文段
      （`p11-3` 末尾突兀插入一句），同时又在脚注块 `p11-fn1` 重复出现——读者看到一句
      与前文逻辑无关的数学推导，且与脚注内容重复。
    - 判据：脚注是独立块，其正文若在非脚注正文块里重复出现，就是「泄漏/重复」。
    - 落地：`qa.py` 新增 `check_document()`——抽取每个脚注块中文的「数学公式指纹」
      （含 `= < > ± − β` 等符号的最长连续串，**不含**纯字母数字串以免把作者单位/邮编/邮箱误判），
      若该指纹原样出现在某正文块里即报 FAIL。实测：真案例命中、14 个黄金集无误报。
    - 禁止：把「数学公式指纹」放宽成「任意字母数字串」——合成案例 `p1-affiliations`（作者单位）
      会因此误报一整页。
69. **缩写术语首译给全称，之后一律用缩写；全称重复出现是冗余**（v2.16）：
    - 起因：Lim & Meer 2018 里「身体质量指数（BMI）」已在标题/摘要首译给全称，但后文
      「同伴身体质量指数（BMI）」这个长表达反复出现 5 次，读起来累赘。
    - 判据：glossary 里「中文全称（缩写）」形态的条目，其全称主体在本篇正文出现 >1 次，
      即说明没守住「首译后缩写」约定。
    - 落地：`qa.py` 新增 `check_abbreviation_fullname()`——对每个「全称（缩写）」术语条目，
      统计全称主体（去括注）在全文出现次数，>1 次报 WARN（不 BLOCK，因首次全称+后文
      缩写本就允许首尾各一次，属提示而非硬错）。
    - 禁止：把 WARN 升级成 FAIL 卡门禁——学术写作里「首译全称、结论段再强调一次全称」
      是正当的，只能提示、不能一刀切判错。
70. **人名上标必须渲染成真正的上标，宽表/宽图自动转横向页**（v2.16）：
    - 起因：用户反馈成品「人名上标不对」——reportlab 回退路线的 `norm()` 不处理 `^{x}`，
      作者名 `Flamme^{1}` 被原样打成字面 `^{1}`；宽表用 Image 硬塞 A4 竖版被压小。
    - 落地：`build_pdf_reportlab.py` 新增 `rich()`（先 HTML 转义，再 `^{x}`→`<super>`、`_{x}`→`<sub>`），
      正文/标题/图注/表题全走 `rich()`；宽高比 >1.25 的图/表自动切横向页（reportlab 双
      PageTemplate 竖+横；HTML 路线 `@page landscape` + `figure.landscape` + 纯标准库读 PNG 头）。
    - **关键踩坑（v2.17 修正）**：① reportlab 的 Paragraph 原生上标/下标标签是 `<super>`/`<sub>`，
      **不是** `<sup>`——用 `<sup>` 会被当未知标签忽略、上标数字退化成普通基线数字
      （v2.16 首版 `rich()` 用 `<sup>`，姓名 `Lim1` 数字没 rise）。② 横向切换不能只在
      「方向变化」时设 `NextPageTemplate`（它是一次性的）：连续多个宽表会溢出到下一页，
      溢出的页回落到默认竖版，导致宽表被塞进竖版、左右裁切。必须**每个图/表块都显式
      声明 `NextPageTemplate`**，方向变化时才加 `PageBreak`。
    - 禁止：只修一条路线（HTML/reportlab）——两条都必须支持，否则回退时又露馅。
71. **GPU/CUDA 探测是「发挥机器性能」的第一步，必须可观测**（v2.16）：
    - 起因：用户质疑「没发挥电脑性能」，自查发现 40+ 脚本**零处**探测显卡。
    - 落地：`detect_env.py` 新增 `probe_gpu()`（nvidia-smi → torch.cuda → wmic 三级容错），
      写 `local_config.gpu`；`run_mineru.py` 本就有 `_cuda_ok()` 自动切 device，探测结果供展示+决策。
    - 禁止：把「探测到 CUDA」等同于「已经在用 GPU」——最终要看 run_mineru 实际走的 device。
72. **迭代必须有量化闭环，不能只记「做了什么」不记「改完快多少/省多少」**（v2.16）：
    - 起因：CHANGELOG 一直只记做了什么，导致不知道哪些规则真有用、哪些该删，违背「多快好省」。
    - 落地：`metrics.py`（collect/report/cost 三层，硬指标自动落盘 reports/metrics.json +
      timings.json wall-clock），`pipeline.py` StageTimer 埋点；mtime 反推加 floor=2000-01-01
      过滤 1980 年异常时间戳。
    - 禁止：拿 token 估算冒充账单口径（`usage_from_sessions.py` 在 WorkBuddy 无会话日志不可用），
      实际费用只能由用户提供 `--actual-cost`。
73. **「自动迭代」要做成机制而非靠 Agent 记性：每篇收尾强制产出一份基于数据的迭代待办**（v2.16）：
    - 起因：迭代协议写在文档里但靠自觉执行，用户要「保证在过程中自动迭代和优化」。
    - 落地：`iterate_audit.py` 每篇收尾自动对比本篇 metrics vs 历史基线，产出
      硬指标 / 趋势对比 / 迭代建议三部分，写入 reports/iterate_audit.md；接进 pipeline finish。
      关键口径：只与「完整新结构」篇对比，排除旧 DSH 残缺 metrics（否则把 12 篇 blocks=0
      的旧篇拉低均值，得出「本篇变差」的假象）。
    - 禁止：只在对话里总结「建议加规则」却不改文件——那不算迭代。

## 成本控制（Dimitrov 2026 实测：会话口径 ¥2.44/篇，19 页）

| 项 | 金额 | 占比 |
|---|---|---|
| 编排（主会话 251 次调用，prompt 43.7M，命中率 99.3%） | ¥1.62 | 66% |
| 子代理（11 个翻译 + 12 个审校，共 150 次调用） | ¥0.82 | 34% |
| 其中**思考过程文本** 538k 字符 ≈ 154k tokens | — | 占输出 token 51% |

> 结论先说：**贵的不是翻译，是编排**。翻译+审校的"文本"只值 ¥0.13；剩下 95% 花在
> 读文件、跑命令、返回结果、返工与思考上。所以省钱要动编排，不要动审校标准。

### 六个杠杆（按实测收益排序，均不牺牲质量）

1. **机械步骤关思考**（最大单项，已做 A/B 实测）：同一份 5 页样本（26 条 / 15,947 字符），
   唯一变量是 provider 的 `thinking` / `reasoningEffort`：

   | 指标 | 开思考 | 关思考 | 变化 |
   |---|---|---|---|
   | 调用 / 未命中输入 / 缓存 / 输出 | 12 / 15,375 / 459,904 / 12,277 | 5 / 9,278 / 151,424 / 4,263 | 输出 **−65%** |
   | 费用（空闲价） | ¥0.0737 | ¥0.0294 | **−60%** |
   | 数字缺失 / 上标错 / 长度比异常 | 0 / 0 / 0 | 0(3 处"两种/两个"中文数词，属等价) / 0 / 0 | 未见质量下降 |

   **落地边界与实操（已两轮实测）**：
   - **子代理不能自己切档**：子代理沙箱 `workspace-write` 且审批禁用，连备份都写不进 `~/.dsh`；
     档位在子代理**启动时**由父会话固定，所以只能由**主会话**切。
   - 正确顺序：在 DSH 设置（或 `cordis.patch.yml` 的 `llm-deepseek-account.config.reasoningEffort`）里调到 `off` →
     派完**所有翻译**子代理 → **立刻**调回 `high`。窗口内主会话也被降档，只做"派子代理 / 等结果 / 跑脚本"。
   - 本机 DSH 的档位字段是 `reasoningEffort`（`off/low/high/max`），改完下一次请求即生效；
     `scripts/thinking_mode.sh` 是 macOS 旧配置（`llm-deepseek-account` 旧块 id）的写法，**Windows 上不要用**。
   - **审校与主编排不要关**（审校靠跨段一致性判断；本轮也没测审校关思考）。
   - 实测：5 页/26 条翻译，费用 ¥0.0737 → ¥0.0308（−58%），数字/上标/长度比零异常。
     完整报告：案例内 `work/ab/REPORT.md`。
2. **一次把版式要求说全 + `preflight.py` 兜底**：本篇 8 轮返工（图注落次页、跨页断句、竖版图吐空白页、
   页底拆标题、术语不一、汉字夹数字、URL 断行、脚注表空转）合计约 ¥0.6。
   现在这些全部进了 `preflight.py`，交付前一次跑完；同类问题不再进对话。
3. **上下文纪律**（三条硬规则）：
   - 长输出一律重定向到文件，只看 `tail -5` / `grep -c`（MinerU 进度条尤其致命）；
   - 不把 chunks/译文整段读进主上下文——子代理写文件，主会话只看条数与抽样 1–2 段；
   - 不重复读同一个大 JSON；要断言就写脚本，别靠"再看一眼"。
   本篇主会话平均每次调用 190k prompt（99% 命中缓存），把上下文压一半，编排费约省 25%。
4. **审校不降级、只瘦身**：本篇唯一的 high 级错误（整句从句漏译）是**全量**审校抓到的，
   按 qa 旗标筛选会漏掉——所以"审校覆盖面"不能砍。能砍的是输出形态：只回改动片段 + 理由，
   或一个子代理连做 2 个 chunk 摊薄固定开销。
5. **chunk 与模型档位**：6000 字符/块实测较优（11 块/19 页）；再大容易截断，再小固定开销占比高。
   跑脚本、格式检查、渲染一律用脚本（零 token），不要用模型"看一眼"。
6. **可复算，不猜**：每篇收尾跑 `usage_from_sessions.py`，把 token/费用/推理占比记进笔记与台账；
   下一轮优化用数据说话（哪一轮返工最贵，就去写哪一条断言）。

### 质量红线（省钱不许碰）
- 全量二遍审校（译者与审校者分离）；
- 术语表 + 术语审计；
- 图表裁剪与图注编号核对、脚注/引用交叉核对；
- `preflight.py` 全绿 + `regression_check.py` 全绿 + `regression_suite.py` 全 PASS。


## 费用顺序（先便宜的、后贵的）

```
抽取/装配（0 token）
  → 结构修补（0 token，本篇 fix_layout.py）
  → sanitize（0 token）
  → preflight --stage skeleton   ← 门禁：0 BLOCK 才允许花钱
  → 翻译（子代理，¥0.6 左右）
  → 审校 --mode patch（子代理）
  → 渲染 + preflight --stage full  ← 门禁：0 BLOCK 才交付
  → regression_check / regression_suite
```

**为什么门禁必须在翻译前**：本篇 8 轮返工里有 6 类是"骨架就错了"（图注混进正文、跨页续句没合并、
降部残片、零宽字符、URL 断行、作者单位丢失），如果译完才发现，返工要连译文一起改；
放在翻译前，这些全是 0 token 的脚本修。

---

# 迭代协议（永久，用户 2026-10 明确要求）

> WorkBuddy 会话中的具体落地命令（增量记忆回流、黄金集快照、回归复跑、CHANGELOG 追加、项目日志、
> **git 提交 + tag 收口 + 自动同步**）见 `WORKBUDDY-ADAPTER.md` 第 3 / 6 / 8 节。
> **加载 skill 后第一件事**：`python scripts/sync_now.py --start`（规则 57），确认拿到云端最新版本再开工。
> 协议本身不变：**必须改文件才算迭代**；2026-11-14 起再加一条：**必须留下 git commit hash 才算完成**（规则 55）；
> 已连接 GitHub 时还必须**回读远端 HEAD 与本地一致**，否则只算「本地已提交、云端未更新」。

## 目标函数（四个指标，按优先级）

| # | 指标 | 怎么衡量 | 现状基线 |
|---|---|---|---|
| 1 | **准确 / 学术表达规范** | 审校 high 级错误数；术语审计缺失数；点校抽查的错误率 | PLOS ONE 篇：high 1（漏引用 [51]）/ 术语 0（修表后） |
| 2 | **出错越来越少** | 返工轮数；`preflight`/`regression` 的 BLOCK/WARN 数 | 本篇 0 BLOCK；qa 2 条尺子误报 |
| 3 | **速度** | 墙钟：MinerU / 翻译 / 审校 / 编排 各段耗时 | 本篇 ≈ 75 min（MinerU 9 + 翻译 3 + 审校 8 + 其余为编排） |
| 4 | **token 与费用** | `usage_from_sessions.py`（会话口径，不是产物估算） | 本篇 ¥1.99 / 23 页 = ¥0.086/页，prompt 29.66M（命中 98.8%） |

> 顺序不可颠倒：**先准确，再少错，再快，最后才省**。省钱不许碰「质量红线」那条。

## 三种输入 → 三种落地动作（每次都要走完，不许只在对话里说说）

| 输入 | 落地动作（必须改文件，否则不算迭代） |
|---|---|
| **用户把译文给 ChatGPT/他人审校后反馈**（术语、语气、句式、学术规范） | ① 逐条判定：**接受 / 不接受（写理由）**；② 接受的写进**术语表**（`glossary_meta.json` + `templates/term_exceptions.json`）；③ 属于表达习惯的，改写进**翻译提示词模板**（本文件"翻译规范"块）；④ 记一行 CHANGELOG |
| **新文章遇到的版式/抽取/渲染问题** | ① 先进 `fix_layout.py`（本篇脚本）解决；② 若可泛化 → 变成 `scripts/` 里的规则或新参数；③ 在"工程规则"或"XX 版式特例"加编号条目；④ 为该篇加 fixture |
| **本篇的 FAIL/WARN** | 先判**尺子错 / 译文错 / 原文问题**：<br>尺子错 → 修检查器（改后**必跑黄金集**）；<br>译文错 → 改译文 + 记术语例外；<br>原文问题 → 记 `source_issues.md` |

## 黄金测试集的增长规则（硬性）

- 每处理一篇**新文献**：`snapshot_case.py` 加 **full + extract 两个案例**（本篇即 `williams2015_full` / `williams2015_extract`）；
- 每次**改脚本**：先跑 `regression_suite.py`，只要有一个案例由 PASS 变 FAIL 就不得合并；
- 修了尺子导致断言变化：确认是"预期的正确化"，再 `snapshot_case.py` 更新 `expected.json`，并在 CHANGELOG 写明理由；
- 目标是**案例数单调增长**：案例越厚，后续改动的安全边界越宽。

## 每次收尾必须写的五样

1. `reports/source_issues.md`（原文可疑处，逐条）
2. `reports/usage.md`（会话口径 token/费用，含"编排 vs 子代理"拆分；WorkBuddy 下按适配层第 1 节标注「非账单口径」）
3. `~/.workbuddy/skills/zoterofanyi/CHANGELOG.md` 追加一行：日期 / 篇目 / 四个指标的实测值 / 本轮学到的东西
   （`~/.dsh/` 原版为只读归档，不再写入）
4. **README 同步**（规则 64）：`README.md` 的状态表用 `scripts/check_readme.py --fix` 刷新；
   若本次改动影响「怎么用」（新增脚本、改流程、改依赖、改仓库拓扑），人工改对应章节。
   只改数字、说明文字照旧也算没做完——门禁拦的是数字，文字得自己负责。
5. **一次 git 提交 + 同名版本 tag + 自动同步**：`scripts/iterate_commit.py -v <版本> -m "<说明>"`，
   把本轮规则 / 术语 / 翻译记忆 / 黄金集 / CHANGELOG / README 的改动一起封存；CHANGELOG 版本号 = tag 名，
   **没有 commit hash 不算完成**（规则 55 / 适配层第 6 节）。v2.5 起脚本在提交后默认推送到
   origin 并回读校验远端 HEAD（规则 57 / 适配层第 8 节）；未连接云端时降级为本地提交 + WARNING。

> 1、2 是每篇论文的工作区产物（`zotero-translate/<key>/reports/`），**不进 skill 的 git 仓库**；
> 3、4 改的是 skill 目录内的文档（CHANGELOG / README），第 5 步把它们的改动一起提交。两者边界不要混。

## 本机 Zotero MCP（2026-10 起可用）

- 服务器：`%USERPROFILE%\.dsh\mcp\zotero_mcp.py`（仅标准库）；注册在 `profiles\desktop\cordis.patch.yml`
- 工具：`mcp__zotero__search_items / get_item / list_attachments / attach_file / create_note / append_note / read_note / verify_item`
- 写操作（attach/note）**必须先完全退出 Zotero**；读随时可用
- 交付前跑 `verify_item`，`ok: true` 才算挂好
