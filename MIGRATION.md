# 迁移到另一台机器（含 Windows）

> **本机现状（2026-10）**：已按 `WINDOWS-INSTALL.md` 配置完成，**不需要 Git Bash**（Windows 用 `.py` 入口，
> 主 venv 在 `%USERPROFILE%\.dsh\runtimes\zb-translate\venv`）。以下流程适用于再迁到第三台机器。

skill 本身与两套 Python 环境**都不在项目目录里**，因此迁移只需搬 skill 目录 + 重建 venv。

## 一、在源机器打包

```bash
bash "$HOME/.dsh/skills/zoterofanyi/scripts/export_skill.sh" ~/Desktop
# → ~/Desktop/zoterofanyi-skill-YYYYMMDD.tar.gz（约 1 MB，含 fixtures 黄金测试集）
```

## 二、Windows 前置

| 项 | 说明 |
|---|---|
| **Git for Windows** | **必需**。脚本是 bash，必须在 **Git Bash** 里运行（PowerShell 不行） |
| Python 3.10+ | 建 venv 用；建议 3.12 |
| Microsoft Edge | Windows 自带，脚本会自动探测路径 |
| MinerU（可选） | 双栏/表格/公式需要；纯单栏文本论文可先跳过 |

## 三、Windows 安装步骤（Git Bash）

```bash
# 1) 解压到全局 skill 目录
mkdir -p "$HOME/.dsh/skills"
tar -xzf /c/Users/<你>/Desktop/zoterofanyi-skill-YYYYMMDD.tar.gz -C "$HOME/.dsh/skills"
ls "$HOME/.dsh/skills/zoterofanyi"      # 应看到 SKILL.md SETUP.md scripts templates fixtures

# 2) 建主工具环境（注意：Windows 的 venv 在 Scripts/python.exe，脚本已适配）
python -m venv "$HOME/.dsh/runtimes/zb-translate/venv"
"$HOME/.dsh/runtimes/zb-translate/venv/Scripts/python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pymupdf

# 3) 冒烟测试
PY="$HOME/.dsh/runtimes/zb-translate/venv/Scripts/python.exe"
"$PY" "$HOME/.dsh/skills/zoterofanyi/scripts/qa.py" --help

# 4) 跑黄金测试集（2 个案例都应 PASS）
"$PY" "$HOME/.dsh/skills/zoterofanyi/scripts/regression_suite.py" \
      --fixtures "$HOME/.dsh/skills/zoterofanyi/fixtures" --verbose
```

## 四、Windows 上已适配 / 需注意

| 项 | 状态 |
|---|---|
| venv 可执行文件路径（`Scripts/python.exe`） | ✅ 已由 `scripts/env.sh` 统一解析 |
| Edge 打印 PDF | ✅ 自动探测 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` 等三处；也可 `EDGE_BIN` 覆盖 |
| 中文字体（宋体/微软雅黑） | ✅ `render.py` 字体栈已加 `SimSun` / `Microsoft YaHei` |
| 页脚页码（PyMuPDF 内置 CJK 字体 `china-s`） | ✅ 跨平台可用 |
| 路径含空格/中文 | ⚠️ 脚本都加了引号，但**建议工作目录用纯英文路径**（`C:\zotero-translate\`） |
| `caffeinate`（macOS 防睡眠） | ❌ Windows 用「电源选项」设成不睡眠，或 `powercfg /change standby-timeout-ac 0` |
| MinerU | ⚠️ 用**独立 venv**装，别装到系统 Python；模型走 ModelScope |

## 五、MinerU（Windows，可选）

```bash
python -m venv "$HOME/.dsh/runtimes/mineru/venv310"
"$HOME/.dsh/runtimes/mineru/venv310/Scripts/python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv
"$HOME/.dsh/runtimes/mineru/venv310/Scripts/uv.exe" pip install \
  --python "$HOME/.dsh/runtimes/mineru/venv310/Scripts/python.exe" \
  --index-url https://mirrors.aliyun.com/pypi/simple/ "mineru[pipeline]==2.7.0"
export MINERU_MODEL_SOURCE=modelscope
```

> Windows 没有 macOS 的"硬化运行时签名"问题，但仍建议单独 venv，避免与其它 torch 项目冲突。

## 六、迁移后验证清单

- [ ] `regression_suite.py` 两个案例都 PASS
- [ ] `build_pdf.sh` 能调起 Edge（拿一个 fixture 的 bilingual.json 试渲染）
- [ ] 中文在 PDF 里**可检索**（`stamp.py --check` 会打印中文样例，若为乱码说明字体/文本层有问题）
- [ ] `run_mineru.sh` 能跑（若装了 MinerU）
- [ ] Zotero 侧：`zotero_*` 工具可用，且笔记挂在**父条目**下

## 七、备选：WSL2

若不想用 Git Bash，可在 WSL2 里跑（脚本原生兼容），但要经过 `/mnt/c/...` 访问 Windows 文件，
**MinerU 的模型与临时文件会变慢**；且 DSH 若跑在 Windows 侧，调用 WSL 需要额外封装。
**结论：优先 Git Bash，其次 WSL。**
