# 项目长期约定 —— novel-writing（插件/工具库）

> 只记**跨会话仍然成立的规则**。日常进度记在 `YYYY-MM-DD.md`。

## 定位

- 本仓库是**插件/工具库**（kit），不是小说。小说在 `D:\1-work\novel\<书名>\`。
- 立身原则：**代码通用、配置项目级**。所有随书变化的值都在 `<书>/.soloent/book.json`，
  `scripts/` 下的代码不含任何一本书的内容。改规则先问「这是通用的还是这本书的」。
- `<书>/tools/*.py` 是**薄壳**（由 `sync.py` 生成，勿手改），真身在本仓库 `scripts/`。
  `hooks/` 同理。改完 `book.json` 或模板后必须跑 `scripts/sync.py --root <书>` 重渲染 AGENTS.md。

## 单一来源（改一处就要改的地方）

| 事实 | 唯一来源 | 必须同步的镜像 |
|---|---|---|
| 机检项清单 | `consistency_check.py --list-checks`（代码） | 文档只引用不转述 |
| 本书机检配置 | `<书>/.soloent/book.json` 的 `checks` | `story-style.md`（人读版，值必须一致） |
| 本书红线 | `book.json` 的 `book.extra_discipline` | 自动进 AGENTS.md §4 + SessionStart 注入 |
| 流程留痕格式 | `preflight.py` 的 `FLOW_DETAIL_RE` | SKILL.md / AGENTS.template.md / docs/06 / docs/08 / templates/now.md |
| 状态块字段 | `book.json` 的 `state.required` | `templates/now.md` 的状态块 |

## 流程留痕与「对撞」（2026-09-20 起）

- 格式：`[流程] ch-N 自查X 三连Y 复查Z 回写OK`，X/Y/Z 为**真实**修复处数。
- **闸门不只验格式**：会把该章的机检残留（黑名单/句式七禁/同段连发/比喻堆砌/章末升华/面板条目）
  与「自查已完成」对撞，残留 > 0 → 拦截。把一致性判据复用自 `consistency_check.is_style_finding()`，
  **不要另立一套判据**。
- 确需保留的违规处：行尾加 `；保留：<理由>` → 降为警告，交 `/review` 复核。
- 教训：只卡格式会被伪造（实测 69 章全写 `自查0 三连0 复查0`，正文残留 63 处黑名单词）。

## 三层「不可靠」的结构性教训（2026-09-20）

1. **配置层空着也能开写** → 判据集中在 `kit.style_doc_issues()`（brief 拦死 / doctor 报警）。
   新增任何「本书必须填」的文件，都要挂进那里。
2. **第 7 步（`/review`）由闸门校验上一章产物**（`gate.review_required`）——
   凡「写在文档里的强制步骤」，都必须有一个可机检的产物，否则等于没有。
3. **「配了没人用」比没有更坏**：`gate.batch_warn` 曾被 `--list-checks` 宣称、却没有任何脚本读它。
   `bulk 长跑`判据写死在 `preflight.py`（新增 ≥2 章未逐章校验即拦截）。
   加配置键时先问「谁读它、读了会改变什么行为」。

## 可机检的边界（别把闸门写成噪音源）

- `consistency_check` 的可选机检项（`panel` / `name_roster` / `hook_check` / `foreshadow_check`
  / `rhythm`）**只在 book.json 配了才跑**；类型由 `kit.config_problems` 校验（阈值允许小数）。
- **被判据否决过的做法**：从正文抽人名（`X说/道/问`）查未登记 —— 中文无词边界，
  实测 200+ 条全假。改用确定性三方互校：canon 人物表 ／ `name_roster.registered` ／ `characters/*.md`。
- 教训：**能机检 ≠ 该机检**。判据错就把闸门变成噪音源，宁可换成弱一点但零猜测的。

## 环境与验证

- 一律 `python`（不要 `python3`，Windows 会解析到 Store 占位符）；乱码加 `PYTHONIOENCODING=utf-8`。
- 改完插件必跑四件套：`scripts/regression_test.py`（约 2m45s，用例多、要后台跑）、
  `scripts/selftest.py`、`scripts/audit.py`、`python -m py_compile <改动文件>`。
- 每个功能点先有可复现的失败用例再加实现（红→绿），并且**每个新检查都要有反向边界用例**
  （干净输入不被误伤）——只测「会拦」不测「不该拦」，会把闸门写成噪音源。
- 验证真实书时**复制到临时目录**再跑（`preflight` 会写 `checkpoint.json`）。
- 测试替身要像真身：模块级副作用必须放进 `if __name__ == '__main__'`，
  否则「被 import」会被误记成「被执行」（`counting_child()` 踩过）。

## 待办 / 未决

- `checks.rhythm` 阈值是**临时值**（按书自身实测分布取的 P25 一档，非样板书校准）。
  拆到样板书后必须按实测回调；目标值记在 `story-style.md §3.1`。
- 面板流的 `checks.panel` 只机检「条目行数」与「结论文案词表」；
  「面板是否替读者下结论」本质需要判断，靠 discipline 注入 + `/review`，不要硬塞进正则。
