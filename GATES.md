# 门禁总纲（GATES.md）

> 本文件是 **zoterofanyi 全部门禁的单一地图**：一类门禁有哪些关、谁负责、怎么自检、
> 换机时怎么一键体检。日常只需记住一条命令：**`python scripts/gate.py`**。
>
> 规则 75（v2.21）把「门禁散落四处、靠记忆维系」收敛成「一张地图 + 一个入口」。

---

## 一、一句话定位

- **门禁 = 在错误变贵之前拦住它**。翻译前骨架错 1 处，返工要连译文一起改；交付后发现漏译，
  等于白做一轮审校。门禁就是把「花的钱/时间」和「可回退的代价」对好账的关卡。
- **统一入口**：`python scripts/gate.py`（默认跑全部四类；`resources` / `pipeline` / `quality` /
  `meta` 只跑一类；`--offline` 跳过联网；`--json` 供脚本消费）。
- **退出码**：`0` = 资源就绪；`1` = 有必需项缺失（BLOCK）；`2` = 用法错。

---

## 二、四类门禁地图

| 类别 | 回答的问题 | 统一入口 | 换机最关键？ |
|---|---|---|---|
| **① 资源门禁** | 这台机器能不能跑（缺什么软件/配置/连通） | `gate.py resources` | ✅ 是 |
| **② 流程门禁** | 该跑哪些流程、顺序、必走不可跳 | `gate.py pipeline` + `preflight.py` | 否（版本资产随 git） |
| **③ 质量门禁** | 怎么确保质量、提速、省 token | `gate.py quality` + `regression_suite.py` | 否 |
| **④ 元门禁** | 门禁自己会不会坏（守门人自检） | `gate.py meta` | 否 |

---

## 三、① 资源门禁（软件 / 配置 / 连通）

**目标**：换机后一条命令告诉你「还差哪些」，而不是一个个手动跑脚本。

| 关 | 谁负责 | 判据 | 缺失时 |
|---|---|---|---|
| 主工具 Python（含 PyMuPDF） | `paths.py --check` | `python` 路径存在 + `python_has_fitz=yes` | 装 PyMuPDF，或 `detect_env.py` 重指 |
| MinerU Python | `paths.py` | `mineru_python` 存在 | 表格/公式抽取不可用（可先跳过） |
| Edge/Chromium | `paths.py` | `browser` 存在 | PDF 渲染走 reportlab 回退 |
| Zotero 挂回 | `zotero_api.py status` | `local_api.ok=true`（server_id 非空） | 挂回改 sqlite 直写（须退 Zotero） |
| git 命令 | `gate.py` | `git --version` 可用 | 版本控制/同步全断 |
| 远端 origin | `gate.py` | `git remote get-url origin` 有值 | `connect_github.py --remote <url>` |
| 代理连通 | `sync_now.py --status` | 候选端点 `ls-remote` 真通（走代理） | 见规则 61；国内必须走代理 |
| GPU/CUDA | `detect_env.py probe_gpu` | `gpu.cuda_available=true` | MinerU 回退 CPU（慢，但非必需） |

**换机标准动作**：

```bash
python scripts/bootstrap.py --remote <url>   # 或 bundle 解压
python scripts/detect_env.py                  # 探测生成 local_config.json
python scripts/gate.py resources             # 一键体检，缺啥补啥
```

> 关键：`local_config.json` 是**本机专属、绝不入库**。所以资源门禁的结论**每台机器不同**，
> 这正是换机要重新体检的原因——版本资产（规则/脚本/黄金集）随 git 走，本机差异（路径/GPU/代理）不随。

---

## 四、② 流程门禁（跑哪些流程、必走不可跳）

**目标**：确定性步骤下沉本地进程，模型只做翻译与审校；两道硬门禁卡在花钱前后。

| 关 | 谁负责 | 判据 | 时机 |
|---|---|---|---|
| Gate 1（骨架） | `preflight.py --stage skeleton` | 0 BLOCK | **翻译前**，否则等于花钱译残渣 |
| 一键编排不跳步 | `pipeline.py prepare/check/finish` | 三段跑完，失败非零退出 | 全程 |
| 机器检查计数交叉校验 | 规则 67 | qa 计数与汇总一致 | 审校前 |
| Gate 2（全文） | `preflight.py --stage full` | 0 BLOCK | 交付前 |
| 全量二遍审校 | 译者 ≠ 审校者 | 不抽样 | 翻译后 |
| 收尾反馈时间/token | `metrics.py` + `iterate_audit.py` | 落盘 reports/ | 每篇收尾 |
| **git 收口** | `iterate_commit.py -v vX.Y` | commit + tag + 推送 + 回读 | **每篇收尾，缺此不算完成** |

**流程门禁的硬约束**（来自迭代协议）：门禁 BLOCK 时**先修到通过**，不许靠 `--skip-regression`
绕过质量红线；`iterate_commit.py` 的门禁（版本号 / CHANGELOG 条目 / 实际改动 / 黄金集全绿 /
tag 不重复 / README 同源 / 公开模式隐私）一项不过就不提交。

---

## 五、③ 质量门禁（质量 / 提速 / 省 token）

**目标**：目标函数优先级固定 **准确 → 少错 → 速度 → 成本**，省钱不许碰质量红线。

| 关 | 谁负责 | 判据 |
|---|---|---|
| 黄金集回归 | `regression_suite.py` | 15 案例全 PASS；有 PASS 变 FAIL 不得合并 |
| 术语统一 | `term_audit.py` + `term_exceptions.json` | 缺失术语为 0 |
| 脚注/引用交叉核对 | `qa.py` + `build_fn_index.py` | 数值只对纯数字标记 |
| 翻译记忆回流 | `build_memory.py` | 审校纠正 → 下篇提示词（81 词级 + 9 整句） |
| 隐私门禁 | `public_sanitize.py --gate` | 公开模式每次提交强制，混入正文 BLOCK |
| README 同源 | `check_readme.py` | 状态表与真实来源一致 |

**省钱四杠杆**（均不牺牲质量，见 SKILL.md「成本控制」）：① 机械步骤关思考/用 lite 子代理；
② 版式要求一次说全 + preflight 兜底；③ 上下文纪律（长输出只 tail、不整读大 JSON）；
④ 审校不降级只瘦身（只回改动片段）。

**质量红线（不许碰）**：全量二遍审校、术语表 + 术语审计、图表/脚注交叉核对、三道门禁全绿。

---

## 六、④ 元门禁（门禁的自检，你没想到的那一类）

**目标**：门禁脚本本身是「守门人」，守门人坏了没人拦。这一层防「门禁静默失效」。

| 关 | 谁负责 | 判据 |
|---|---|---|
| 门禁脚本可 import | `gate.py meta` | 12 个门禁脚本 import 成功（语法有效、依赖可解析） |
| 黄金集护栏 | 迭代协议 | 改脚本后必跑回归，PASS→FAIL 不得合并 |
| 降级路径质量 | `preflight --stage full` + 读成品 | 渲染/挂回回退后，成品仍 0 BLOCK（见下） |

> **降级路径门禁**（v2.16 踩过的坑）：PDF 渲染有 HTML→reportlab 两条路，挂回有
> Local API→sqlite→bridge 三条路。回退后质量是否仍达标，**只看回归计数不够**——
> v2.16 上标/横向两个 bug 就是「回归绿了、成品没验」漏到用户面前的。规则 70 已定：
> **渲染类 bug 必须读到成品 PDF 的字形坐标/页面方向才算验证通过**。

---

## 七、门禁与「换机运行」的关系（本文件的核心目的）

换机时，四类门禁的「可移植性」不同：

| 门禁 | 随 git 走？ | 换机要做什么 |
|---|---|---|
| ② 流程门禁 | ✅ 完全随版本资产 | 无需额外动作，`pipeline.py` 原样可用 |
| ③ 质量门禁 | ✅ 完全随版本资产（黄金集/tm/规则） | 只需确认 `fixtures` 克隆完整 |
| ④ 元门禁 | ✅ 门禁脚本本身随 git | 无需额外动作 |
| ① 资源门禁 | ❌ **不随**（本机路径/GPU/代理） | **每台机器重新 `detect_env.py` + `gate.py`** |

所以「不同电脑间后续运行」的唯一真正变量是**资源门禁**，其余三层由 git 保证一致。
换机口诀：**`bootstrap.py --remote <url>` → `detect_env.py` → `gate.py` → 补齐 BLOCK → 开跑。**

---

## 八、自检清单（每次迭代收尾，5 秒对账）

- [ ] `python scripts/gate.py` 总判定「就绪」或「部分就绪（可选项）」
- [ ] `python scripts/regression_suite.py --fixtures fixtures` 15/15 PASS
- [ ] `python scripts/check_readme.py` 通过（README 与真实来源一致）
- [ ] `python scripts/iterate_commit.py -v vX.Y` 门禁全过、有 commit hash、云端回读一致
- [ ] 若改了门禁脚本（含本 gate.py），回归 + 元门禁 `gate.py meta` 复跑确认

---

> 本文件与 `README.md`（是什么/怎么用）、`SKILL.md`（全量规则）、`WORKBUDDY-ADAPTER.md`
> （环境映射）分工：**本文件只讲「门禁」这一件事的全景**，细节一律指回那三份。
