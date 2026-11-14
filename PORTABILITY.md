# 跨机同步与本机本地化 · zoterofanyi

> 用户 2026-11-14 第三个要求：换一台电脑上的 WorkBuddy，也能拿到最新的 skill，
> 并且能按那台电脑的配置做本地化修改。
> 结论：**能**。做法是把「与机器无关的版本」和「与机器有关的配置」彻底分开。
>
> **v2.5 更新（用户第四个要求）**：同步源改为 **GitHub 私有仓库**，并且从「显式才同步」
> 升级为「每次使用前后自动交互」——开工 `sync_now.py --start`，收尾 `iterate_commit.py`
> 自动 push + 回读远端 HEAD 校验。`connect_github.py` 负责一次性连接与建仓。
> 隐私审计结论见文末：**无凭证/无隐私数据，但含公开论文正文摘录 → 建议私有仓库**。
> 全局安装（用户级 skill）也在本文最后一节。

## 一句话架构

| 层 | 放什么 | 同步吗 | 落到哪 |
|---|---|---|---|
| 版本层 | SKILL.md / CHANGELOG / scripts / fixtures / templates / tm | 同步 | git 仓库（规则 55） |
| 配置层 | 本机 Python / MinerU / 浏览器 / Zotero MCP / 工作区路径 | **不同步** | `local_config.json`（`.gitignore` 排除） |
| 引导层 | 探测、变量生成、clone/pull、校验 | 同步且零第三方依赖 | `paths.py` / `detect_env.py` / `bootstrap.py` |

**关键事实**：原有 40+ 个流水线脚本里**没有任何硬编码的本机绝对路径**（v2.4 实测 grep 命中 0），
所有路径都由调用方 `$PY / $S / $W` 注入。所以跨机不需要改脚本，只要让这几个变量
在新机上被正确生成——这正是 `local_config.json` + `paths.py` 做的事。
（新增 `detect_env.py` 里 `C:/Program Files/.../msedge.exe` 一类字符串是**跨平台候选探测常量**，
探测不到只会给 warning 并可用环境变量覆盖，不构成对某台机器的依赖。）

## 三种同步通道（选一种即可）

| 通道 | 怎么开 | 自动程度 | 适用 / 风险 |
|---|---|---|---|
| **A. GitHub 私有仓库（本机已接通，首选）** | 本机：`xiaozeng9066/zoterofanyi-skill`（private）。一次性动作 = GCM **用户级**授权 → 建私有空仓 → `connect_github.py --remote <url>`；新机 `bootstrap.py --remote <url>`。**GitHub 连接器是 App**：建仓 403、新建私有仓 404，不能替代本地 git 凭据（规则 58） | **开工 `sync_now.py --start` 自动拉；收尾 `iterate_commit.py` 自动推 + 回读校验**（`--no-push` 可临时关闭） | 版本与冲突最清楚；默认 private。`tm/`、`fixtures/` 含公开论文正文摘录，见文末审计 |
| **B. 云同步盘 + bare 仓库** | `bootstrap.py --init-sync "%USERPROFILE%"`（或坚果云/Dropbox 目录）→ 云盘自动同步 `zoterofanyi-sync.git` → 其他机 `--remote <云盘上的该路径>` | 云盘客户端自动同步 bare 文件，各机仍需 push/pull | 本机已检测到 **OneDrive** 与 **坚果云**。**不要直接同步含 `.git` 的 skill 工作目录**（多机并发写会损坏仓库）；bare 仓库也要错峰 push |
| **C. 离线包 / WorkBuddy 资料库** | `bootstrap.py --bundle /tmp/zoterofanyi-v2.4.zip` → 上传到资料库或网盘 → 新机下载解压 | 半自动，人工触发 | 没有 git 远端时的兜底；无冲突检测，靠包内 `BOOTSTRAP.txt` 记版本与 commit |

> 三种通道可以混用（例如 A 为主、C 做冷备份）。**不要**把 skill 目录整个丢进云盘同步：
> `.git` 的 refs/index 在多机同时写入时会竞态损坏；也不要把 `local_config.json` 放进任何同步空间。

### 同步节奏（v2.5 起默认自动，规则 57）

1. **加载 skill 后第一件事**：`python scripts/sync_now.py --start`
   —— fetch + ff-only；分叉 BLOCK，离线 WARN（本地照用），未连接 GitHub 也照用。
2. **每次迭代收尾**：`python scripts/iterate_commit.py -v <版本> -m "<说明>"`
   —— commit + annotated tag 后**自动** push `origin/main --follow-tags`，
   再用 `ls-remote` 回读远端 HEAD 与本地比对；失败明确报「云端未更新」，不假装成功。
   `--no-push` 临时纯本地，`--push` 强制要求已连接（无 origin 直接 BLOCK）。
3. 手工补推 / 只看状态：`sync_now.py --end` / `sync_now.py --status`。
4. 换机第一次：`bootstrap.py --remote <url>` → 自动 clone → 到目标目录再跑一次 `bootstrap.py`。
5. 多机都改了导致 ff-only 失败：人工合并文本冲突后提交，**禁止** `reset --hard` / 强推。

> 「云端永远最新」的准确边界：**每次成功收尾之后**一致；离线改动在恢复网络后由
> `--end` 补推（`--start` 会提示「本地有 N 个未推送提交」）。脚本不后台常驻，
> 多机并发写造成的分叉一律交人工裁决，这是故意的安全设计。

## 新电脑的一次性引导（5 步）

```bash
# 0) 前提：装好 WorkBuddy、git、任意 Python 3.9+（系统自带的即可，引导脚本零第三方依赖）
# 1) 取版本：从远端 clone，或下载 bundle zip 解压到临时目录按 BOOTSTRAP.txt 放置
python <临时目录>/scripts/bootstrap.py --remote https://.../zoterofanyi.git
# 2) 到 ~/.workbuddy/skills/zoterofanyi/ 跑引导（探测 + 生成配置 + 黄金集）
python scripts/bootstrap.py
# 3) 看自检、按提示补齐缺失组件（PyMuPDF venv / MinerU / Edge / Zotero MCP）
python scripts/paths.py --check
# 4) 取本机变量，之后 SKILL.md / ADAPTER 里的命令原样可用
eval "$(python scripts/paths.py --shell bash)"     # Git Bash
python scripts/paths.py --shell powershell          # PowerShell 用户复制输出
# 5) 确认黄金集 14/14 PASS，再开始翻译
```

`bootstrap.py` 输出会明确区分「组件齐全」与「待处理」；**缺组件不假装就绪**，
并给出对应环节（MinerU 抽取 / PDF 渲染 / Zotero 挂回）的补齐建议。

## 本机配置 `local_config.json`

由 `detect_env.py` 生成，**每台机器一份，绝不入库、绝不跨机复用**：

| 字段 | 含义 | 覆盖它的环境变量 |
|---|---|---|
| `python` / `python_has_fitz` | 主解释器（含 PyMuPDF 与 zstandard） | `ZF_PYTHON` / `ZB_PY` |
| `mineru_python` | MinerU venv 解释器 | `ZF_MINERU_PY` / `MINERU_PY` |
| `browser` | Edge 或 Chrome，用于渲染 PDF | `ZF_BROWSER` |
| `zotero_mcp` | Zotero CLI 路径 | `ZF_ZOTERO_MCP` |
| `workspace` / `runs_dir` | 工作区与 `zotero-translate/` | `ZF_WORKSPACE` / `ZF_RUNS_DIR` |
| `sync` | 同步模式、origin、云盘 bare 路径 | `bootstrap.py --remote` 或 `--init-sync` 写回 |

- **增量更新**：重跑 `detect_env.py` 时保留 `sync.remote_url`、`sync.cloud_bare`、
  `sync.mode`、`overrides` 等人工字段（沿用规则 54 的增量精神），不会把同步配置冲掉。
- `paths.py` 统一读取，优先级：环境变量 > `local_config.json` > 模板兜底。
- `iterate_commit.py` 会二次拦截 `local_config.json`，即使误改 `.gitignore` 也不会把它提交。

## 与迭代协议的关系（规则 55 / 56 / 57）

- 同步的是**版本**：每次迭代仍必须 CHANGELOG 同版本条目 + 黄金集全绿 + commit + tag；
- 自动推送只是把已完成的版本**复制**到云端，不是新的迭代动作，也不会替你写 CHANGELOG；
  **没有 commit hash 仍不算迭代完成**；云端未对齐时只算「本地已提交」。
- 开工 `sync_now.py --start` 只做 ff-only 拉取，不改本机配置、不覆盖本地改动；
- 版本号与 tag 是唯一坐标：其他电脑上 `git describe --tags` 就能确认拿到的是哪一版，
  `sync_now.py --status` 能进一步确认本地 HEAD 与远端 HEAD 是否一致。

## 隐私与安全 · 2026-11-14 全量审计结论

用户问「反正也没什么敏感数据，查一下，如果都是公开论文我觉得没啥」。
对 **100 个入库文件**（3.4 MB，全部文本扫描）实测：

| 检查项 | 结果 |
|---|---|
| GitHub PAT / `sk-` / AWS key / 私钥 / Bearer / 明文口令 | **0 处** |
| 邮箱 | 40 处 / 12 文件，均为论文里**公开的作者通讯邮箱** + 文档示例 `author@example.org` |
| 本机绝对路径 / 用户名 `<USER>` | 清理前 12 + 16 处；v2.5 已把 21 个 fixture 的 `source_pdf` 改为 `<WORKSPACE>/zotero-translate/<KEY>/source.pdf`，文档示例改 `%USERPROFILE%`；黄金集复跑仍 **14/14 PASS** |
| 论文原文 PDF / 二进制 | **0**（`.gitignore` 排除） |
| 论文正文摘录 | 7 篇 `bilingual.json` 共 **363,695 英文字符 ≈ 6 万词**，另有 `tm/` 的整句译文模式 |

**判断**：确实没有机密、没有个人凭证、没有私人笔记——用户的直觉基本正确。
但这不是「元数据/摘要」，而是**公开论文的大段正文再分发**：公开仓库会被搜索引擎抓取、
被 fork，也可能触及出版社的版权与 text-mining 条款。因此 `connect_github.py` 默认
**private**，`--public` 需要用户显式选择。理由不是「怕泄密」，是「不替你决定再分发范围」。

> **v2.7 更新（用户第五个要求）**：若确实要公开分享，正确做法不是「把私有库改成 public」，
> 而是 **`scripts/public_sanitize.py` 数据最小化 → 从 `dist/public` 新建干净历史的公开仓库**。
> 脱敏副本把论文正文换成自造合成句、短标题换成合成锚点、邮箱与本机路径占位化，
> 实测 **14/14 PASS + 残留 0 处**、体积 −39.4%；私有主库仍保留全强度 fixtures，
> 本机回归质量不降级。**加密不是答案**（读者无密钥解不开、CI/网页失效、
> 且擦不掉 v2.2–v2.6 历史里的明文），完整论证与发布 SOP 见
> **`PUBLIC-RELEASE.md`** 与适配层第 9 节。

其余边界：

- `local_config.json` 含本机用户名与绝对路径，**不入库、不上传**；`iterate_commit.py`
  会二次拦截，即使误改 `.gitignore` 也提交不上去。
- **v2.5 起日常同步自动联网**（用户明确要求）：`--start` 与收尾自动 push。
  仍然显式的是：`connect_github.py`（连接/建仓）、`--init-sync` / `--remote` / `--bundle`
  （写云盘或导出）、以及任何改仓库可见性的动作。脚本**绝不擅自创建远端或改 public/private**。
- token 只从 **GCM 凭据库 / 环境变量**读取，**绝不写进 URL / `.git/config` / 日志 / 入库文件**；
  本机实际用的是 GCM 设备码授权得到的用户级 token（scopes `repo, workflow, gist`）。
  设备码在无终端环境可能静默完成、stdout 为空，凭据是否就绪以 `git credential fill` 实测为准。
- 云盘通道的已知边界：云客户端同步 bare 仓库期间两台机器同时 push 可能产生 refs 竞态；
  单用户错峰使用没问题，真出问题就从远端重新 clone（先备份本机配置），不要手工改 `.git`。

## 全局安装（本机已核实，用户第四个要求）

skill 与 git 仓库都在 **`~/.workbuddy/skills/zoterofanyi/`** —— WorkBuddy **用户级**目录，
对本机**所有项目、所有会话**生效，不限于 `%USERPROFILE%>/Desktop/zoterofanyi/` 这个文件夹。

- 证据：`~/.workbuddy/skills/` 下只有这一个 skill；项目级 `.workbuddy/skills/` **不存在**
  （不会被同名项目级 skill 覆盖）；技能注册表「已启用」列表可见 `zoterofanyi`；
  用户级 `settings.json` 把 `~/.workbuddy/skills/` 列为可写目录。
- 那个文件夹里只有 `.workbuddy/memory/`（日志）和 `zotero-translate/`（每篇论文产物），
  **不是 skill 本体**；换工作区、删产物都不影响 skill。
- 禁止：把 skill 复制进项目目录、在项目级建同名 skill、用项目级配置覆盖用户级触发词。
- 其他电脑同样全局：`bootstrap.py --remote <url>` clone 到各机自己的用户级目录，
  再 `detect_env.py` 生成本机配置 —— **同一份版本，每台机器各自全局**。

## 版本坐标

v2.4 提供跨机骨架，**v2.5** 接入 GitHub 云端同步与全局安装（规则 57），
**v2.6** 真正接通仓库并记下连接器权限边界（规则 58），
**v2.7** 提供公开库的数据最小化与发布 SOP（规则 59），
**v2.8** 迁移到公开主库并加每次提交的隐私门禁（规则 60），
**v2.10** 加入 GitHub 直连优先 + 自动回落的网络策略（规则 61）；下一版从 **v2.11** 起。
详见 `CHANGELOG.md`、`PUBLIC-RELEASE.md` 与 `WORKBUDDY-ADAPTER.md` 第 7 / 8 / 9 节。

> **换机时的网络注意（规则 61）**：新机跑 `detect_env.py` 会把「直连 / 代理哪条通」
> 写进 `local_config.json` 的 `network` 段，`sync_now.py` 首选失败会自动回落另一条。
> 若新机有代理但 GitHub 常失败，把该 host 加入 `NO_PROXY` 或设 `network.force`，
> **不要**全局关代理，也不要写死进 `.git/config`。
