# Windows 安装状态（zoterofanyi）

> 本机（Windows 11 / DSH 桌面版）已完成配置。**不需要 Git Bash**：`.sh` 只是 macOS 入口，
> Windows 一律用同名 `.py`（`build_pdf.py` / `regression_check.py` / `run_mineru.py`）。

## 0. 当前状态

| 项 | 实际位置 | 状态 |
|---|---|---|
| skill 本体 | `%USERPROFILE%\.dsh\skills\zoterofanyi\` | ✅ 用户级，所有工作区可见 |
| 主工具 Python | `%USERPROFILE%\.dsh\runtimes\zb-translate\venv\Scripts\python.exe` | ✅ Python 3.12 + PyMuPDF 1.28.2 + zstandard |
| UTF-8 修正 | 该 venv 的 `Lib\site-packages\sitecustomize.py` | ✅ 防止 GBK 控制台打印中文/数学符号崩溃 |
| Windows 入口 | `scripts\build_pdf.py`、`regression_check.py`、`run_mineru.py` | ✅ 已替代 bash 脚本 |
| Edge（渲染 PDF） | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` | ✅ 可用，但见下方沙箱限制 |
| MinerU（结构抽取） | `%USERPROFILE%\.dsh\runtimes\mineru\venv310` | ✅ mineru 2.7.0 + torch 2.14.1+cpu；模型缓存在 `...\mineru\modelscope` |
| Git for Windows | — | 不需要 |

## 1. 冒烟测试（不花翻译钱）

```powershell
$PY = "$HOME\.dsh\runtimes\zb-translate\venv\Scripts\python.exe"
$S  = "$HOME\.dsh\skills\zoterofanyi\scripts"

& $PY "$S\preflight.py" --help
& $PY "$S\regression_suite.py" --fixtures "$HOME\.dsh\skills\zoterofanyi\fixtures"
```

预期最后一行：`===== 黄金测试集：8/8 PASS =====`。

渲染链路（用 fixtures 里的双语 JSON，不需要原文 PDF）：

```powershell
& $PY "$S\build_pdf.py" "$HOME\.dsh\skills\zoterofanyi\fixtures\dimitrov2026_full\bilingual.json" `
    "$PWD\_smoke\test.pdf" "Dimitrov 2026"
```

预期最后三行包含 `check: 文本层可检索` 与 `OK -> ...\test.pdf`。

## 2. 已知限制（Windows / DSH 特有）

| 现象 | 原因 | 做法 |
|---|---|---|
| `Edge 未生成 PDF（exit=...）` / STATUS_BREAKPOINT | Edge 无法在 DSH 的 workspace-write 沙箱内初始化自身沙箱 | 用 **danger-full-access** 跑 `build_pdf.py`（每次渲染一次审批）；或在 DSH 外手动跑 |
| 脚本打印 `UnicodeEncodeError: 'gbk' codec ...` | 系统默认 GBK 控制台 | 已由 venv 的 `sitecustomize.py` 统一改成 UTF-8；若换 venv 需重建该文件 |
| 写 `%USERPROFILE%\zotero-translate` 被拒 | 该目录在工作区之外（workspace-write 只允许工作区） | 把 `$W` 设成当前工作区内的 `.\zotero-translate\<key>`，或对该步申请审批 |
| 中文乱码 | 控制台编码/字体 | 用 Windows Terminal + UTF-8；PDF 内文字已确认可检索（`stamp.py --check`） |
| 路径含中文/空格 | 部分子进程对引号处理不同 | 工作目录尽量用纯英文路径 |

## 3. 每篇文献的最短路径

```powershell
$S  = "$HOME\.dsh\skills\zoterofanyi\scripts"
$PY = "$HOME\.dsh\runtimes\zb-translate\venv\Scripts\python.exe"
$W  = "$PWD\zotero-translate\<条目key>"          # 放在工作区内，免审批
New-Item -ItemType Directory -Force "$W\work", "$W\reports", "$W\assets" | Out-Null

& $PY "$S\run_mineru.py" <原文.pdf> "$W\work\mineru" --lang en
$MIDDLE = (Get-ChildItem "$W\work\mineru" -Recurse -Filter "*_middle.json" | Select-Object -First 1).FullName
& $PY "$S\extract_mineru.py" <原文.pdf> $MIDDLE --out-blocks "$W\work\blocks.json" `
    --out-regions "$W\work\regions.json" --assets "$W\work\assets"
```

后续步骤见 `SKILL.md` 的「流水线」一节（已全部改成 PowerShell + `& $PY`）。

## 4. MinerU（已装好）

- 环境：`%USERPROFILE%\.dsh\runtimes\mineru\venv310`（mineru 2.7.0 + torch 2.14.1+cpu）
- 模型缓存：`%USERPROFILE%\.dsh\runtimes\mineru\modelscope`（已下载，pipeline 后端）
- 已验证：2 页测试 PDF 约 2 秒出 `*_middle.json`，**模型缓存后可在沙箱内直接跑**
- 只有**首次下载模型**需要 danger-full-access；`run_mineru.py` 会自动找 `venv310` 并把
  Ultralytics/Matplotlib 配置写进输出目录的 `.mineru-cfg\`（避免写 `%APPDATA%` 被拒）

若换机器重装：

```powershell
$PY0 = "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $PY0 -m venv "$HOME\.dsh\runtimes\mineru\venv310"
$MP = "$HOME\.dsh\runtimes\mineru\venv310\Scripts\python.exe"
& $MP -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv
& "$HOME\.dsh\runtimes\mineru\venv310\Scripts\uv.exe" pip install --python $MP `
    --index-url https://mirrors.aliyun.com/pypi/simple/ "mineru[pipeline]==2.7.0"
```

## 5. 出错先看哪里

- `regression_suite.py` 有 FAIL → 脚本/环境回归，先修，别往下译；
- `preflight.py` 有 BLOCK → 骨架结构未过门禁，不许开始翻译；
- `build_pdf.py` 报"找不到 Edge/Chrome" → 设 `EDGE_BIN`；
- 详细规则与流水线：同目录 `SKILL.md`。
