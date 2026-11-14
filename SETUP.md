# SETUP（一次性环境准备）

> **Windows 本机已配置完成**：入口已改为 `scripts\*.py`，不需要 Git Bash；主 venv（含 PyMuPDF、zstandard）已建好。
> 实际状态与验证命令见 `WINDOWS-INSTALL.md`。下面保留 macOS 原始步骤作参考。

本 skill 需要两个独立的 Python 环境，**都不放在任何项目目录里**，删项目不会影响：

| 用途 | 路径 | 说明 |
|---|---|---|
| 主工具（PyMuPDF 等） | `$HOME/.dsh/runtimes/zb-translate/venv` | 抽取、渲染、校验、交叉核对 |
| MinerU 结构抽取 | `$HOME/.dsh/runtimes/mineru/venv310` | 版面分析/表格/公式识别 |
| MinerU 模型缓存 | `$HOME/.dsh/runtimes/mineru/modelscope` | 约 1.9 GB |

## 1. 主工具环境

```bash
mkdir -p "$HOME/.dsh/runtimes/zb-translate"
/usr/local/bin/python3 -m venv "$HOME/.dsh/runtimes/zb-translate/venv"     # 3.10+ 均可
"$HOME/.dsh/runtimes/zb-translate/venv/bin/pip" install -i https://pypi.tuna.tsinghua.edu.cn/simple pymupdf
```

验证：

```bash
"$HOME/.dsh/runtimes/zb-translate/venv/bin/python" -c "import pymupdf; print(pymupdf.__doc__)"
```

## 2. MinerU 环境（可选，但双栏/表格/公式必需）

```bash
mkdir -p "$HOME/.dsh/runtimes/mineru"
# 关键：**不要用 DSH 自带的 Python**。它带硬化运行时签名（flags=0x10000(runtime)），
# 加载 torch 的 adhoc 签名动态库会被 macOS 拒绝：
#   different Team IDs / code signature not valid for use in process
/usr/local/bin/python3 -m venv "$HOME/.dsh/runtimes/mineru/venv310"

export UV_CACHE_DIR="$HOME/.dsh/runtimes/mineru/.uvcache"
"$HOME/.dsh/runtimes/mineru/venv310/bin/pip" install -q -i https://pypi.tuna.tsinghua.edu.cn/simple uv
"$HOME/.dsh/runtimes/mineru/venv310/bin/uv" pip install \
    --python "$HOME/.dsh/runtimes/mineru/venv310/bin/python" \
    --index-url https://mirrors.aliyun.com/pypi/simple/ "mineru[pipeline]==2.7.0"
```

注意：
- 只装 `[pipeline]`，**不要 `[core]`**（会拖进 gradio 与 VLM/llama-cpp，对本用途是负担）；
- 清华源上 `imageio` 可能反复超时 → 换阿里云源；
- `pip` 解析 61 个依赖会停滞数分钟 → 用 `uv`；
- 首次运行会自动下载模型（约 1.9 GB，ModelScope 源），约 12 分钟。

验证：

```bash
MINERU_MODEL_SOURCE=modelscope "$HOME/.dsh/runtimes/mineru/venv310/bin/python" \
  -c "import torch, importlib.metadata as m; print('mineru', m.version('mineru'), 'torch', torch.__version__)"
```

## 3. 路径覆盖

| 变量 | 作用 |
|---|---|
| `ZB_PY` | 覆盖主工具 Python |
| `MINERU_PY` | 覆盖 MinerU Python |
| `MODELSCOPE_CACHE` | 覆盖模型缓存位置 |

## 4. 冒烟测试（不需要真论文）

```bash
$S/../scripts/extract_footnotes.py --help     # 各脚本都应打印 usage
$S/regression_check.sh --help 2>/dev/null || true
```

## 5. Zotero 侧

- Zotero 桌面端运行，且本地 API 可写（DSH 的 zotero MCP 已配置时可用 `zotero_*` 工具）；
- 笔记只能挂在**父条目**下（Zotero 不允许笔记挂附件），API 会报 `must be a regular item`。
