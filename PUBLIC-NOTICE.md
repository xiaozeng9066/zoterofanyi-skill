# 公开版说明

本仓库是 `zoterofanyi` skill 的**脱敏公开分发版**。

- `fixtures/` 内的英文/中文文本为自造合成句，不含任何已发表论文的正文；
  仅保留结构、计数与少量短断言串，黄金集 `14/14 PASS` 可复现。
- `tm/error_patterns.json*` 的整句对照已截断为短摘要。
- 作者邮箱统一替换为 `author@example.org`。
- 生成方式：私有主库跑 `python scripts/public_sanitize.py --all --out dist/public`。
- 完整历史与全强度 fixture 保留在私有主库，**公开库不含其 git 历史**（历史里曾有论文正文）。

## 本仓库拓扑（v2.8 起）

- **本库 = 公开主库**：`https://github.com/xiaozeng9066/zoterofanyi-skill.git`（public），从脱敏副本建**干净初始提交** tag `v2.8`，不含任何历史里的论文正文。
- 旧私有库已改名 `xiaozeng9066/zoterofanyi-skill-archive`（private），保留 v2.2–v2.8 完整历史与全强度真实 fixture；文档里提到「私有库」的段落是迁移前的历史记录。
- `fixtures_full/` 是本机全强度回归用的真实快照，**gitignored、不入库**；`fixtures/` 只含合成案例。
- `PUBLIC-MODE.json` 开启每次提交的隐私门禁（`public_sanitize.py --gate`，规则 60），禁止删除或绕过。
