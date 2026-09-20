# 进度日志

## 会话：2026-09-19

### F00：建立可恢复任务台账
- **状态：** complete
- 执行的操作：
  - 读取 `planning-with-files-zh` 与 `karpathy-guidelines`。
  - 检查项目中不存在旧规划文件或待恢复会话。
  - 建立功能点级任务清单、发现记录和进度日志。
- 创建/修改的文件：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### F01：建立负向回归测试基线
- **状态：** complete
- 执行的操作：
  - 确认测试 seam 为公开 CLI/Hook，不测试私有实现。
  - 决定按功能点逐个执行红→绿；F01 先建立框架和 F02 的首个失败用例。
  - 新增 `scripts/regression_test.py`，所有假书均创建在系统临时目录。
  - 运行首个测试，确认初始化会删除已有 `permissions`、`env` 和自定义 Stop Hook。
- 创建/修改的文件：
  - `scripts/regression_test.py`

### F02：安全初始化与同步
- **状态：** complete
- 执行的操作：
  - 新增 `kit.atomic_write_text()` / `atomic_write_json()`。
  - Harness JSON 改为保留未知字段，并只替换本插件的 SessionStart/PostToolUse 条目。
  - init 在任何写入前验证已有 Harness JSON。
  - `--force` 覆盖前备份本次将改写的现有文件。
  - sync 可安全更新插件 Hook，且 `--check` 保持幂等。
- 创建/修改的文件：
  - `scripts/kit.py`
  - `scripts/sync.py`
  - `scripts/init_book.py`
  - `scripts/regression_test.py`

### F03：闸门真正失败关闭
- **状态：** complete
- 执行的操作：
  - 已完成“未匹配正文文件时拦截”：覆盖没有编号章节、已有编号章节旁夹带异常文件两种场景。
  - 已完成“核心子脚本失败关闭”：覆盖三个脚本缺失、超时、崩溃及伪成功输出。
  - 已完成“批量章节 checkpoint 保护”：默认拦截跨章推进，支持 `--chapter N` 顺序补验并拒绝跳章。
  - F03 整组 7 项回归通过；原有自检、审计和编译检查全部通过。
- 创建/修改的文件：
  - `scripts/preflight.py`
  - `scripts/regression_test.py`

### F04：章节与账本集合级核对
- **状态：** complete
- 执行的操作：
  - 校验正文章号从 1 连续且不重复。
  - 校验账本从第 1 章开始，并与正文做章号集合双向差集。
  - 校验文件名章号、标题章号一致，标题不可解析时失败关闭。
  - F04 全组 6 项回归及基础验证全部通过。
- 创建/修改的文件：
  - `scripts/ledger.py`
  - `scripts/regression_test.py`
  - `assets/book.example.json`

### F05：严格流程凭证与编号统一
- **状态：** complete
- 执行的操作：
  - 流程凭证改为严格完整匹配，跳过类文本硬拦截。
  - checkpoint 记录逐章正文哈希，改写或删除后旧凭证失效。
  - 统一正文步骤 2–5 与 SKILL B3–B6 的映射和文案。
  - 完整回归 22 项及基础验证全部通过。
- 创建/修改的文件：
  - `scripts/preflight.py`
  - `scripts/regression_test.py`
  - `templates/now.md`
  - `docs/00-总览与流程.md`

### F06：写前接地的正式/探索边界
- **状态：** complete
- 执行的操作：
  - 新增 `scripts/draft.py`：探索草稿入口，草稿落 `drafts/` 并强制标记 NON-CANONICAL／非正典，不进 `chapters/`、不推进 `now.md`、不写账本、不产生 checkpoint。
  - `sync.py` 的 TOOLS 清单加入 `draft.py`，各书 `tools/` 薄壳随之生成。
  - 统一 README、SKILL、docs/00、docs/01 对正式/探索边界的表述：删除「直接写第一章」，改为「先写探索草稿」并写明转正清单。
  - 新增 DraftModeTests 与文档契约测试；回归 26 项全绿。
- 创建/修改的文件：
  - `scripts/draft.py`、`scripts/sync.py`、`scripts/regression_test.py`
  - `README.md`、`SKILL.md`、`docs/00-总览与流程.md`、`docs/01-初始化.md`

### F07：多书定位禁止猜测
- **状态：** complete
- 执行的操作：
  - 重写 `hooks/inject_canon.py` 定位逻辑：显式 `--root` > `NOVEL_BOOK_ROOT` > cwd 向上 > 项目根向上 > 下一级唯一候选；任何一步多候选即拒绝注入并提示用户进入具体书目录。
  - 删除按 mtime「猜最近改动的书」的 `find_any_book`。
  - 精确定位时在注入内容中记录定位依据；多书歧义时 doctor 体检改为按 `--lib` 查整个库或跳过，避免「book.json 读不出来」的误导报错。
  - 新增 BookLocationTests 5 项（多候选拒绝、单书注入、精确目录、显式 root、mtime 不起作用）；回归 31 项全绿。
- 创建/修改的文件：
  - `hooks/inject_canon.py`、`scripts/regression_test.py`

### F08：密钥与 Git 安全
- **状态：** complete
- 执行的操作：
  - `sync.py` 新增 `.gitignore` 托管段（`# >>> novel-writing (managed) >>>`）：合并写入保留用户自定义行，历史旧段清理；`.env`、`.env.*`、`!.env.example`、`.soloent/.api.env`、运行时产物全部忽略；进入 sync 计划与 init 备份清单。
  - 新增 `templates/env.example`，init 复制为 `<书>/.env.example`（只含空值）。
  - `doctor.py` 新增 `check_secrets`：.gitignore 缺密钥排除 → 警告；git 可用且密钥文件被跟踪 → 严重错误（附 `git rm --cached` 修复命令）；不在仓库内只记通过项。doctor 清单同时纳入 `draft.py`。
  - 修正 AGENTS 模板 §1.3 与 docs/13：明确「init_book.py 不执行 git init，需手工初始化」，.gitignore 示例改为托管段并说明密钥风险。
  - 新增 SecretSafetyTests 4 项（含 git 跟踪密钥的真实仓库复现）。
- 创建/修改的文件：
  - `scripts/sync.py`、`scripts/init_book.py`、`scripts/doctor.py`、`scripts/regression_test.py`
  - `templates/env.example`、`assets/AGENTS.template.md`、`docs/13-版本控制.md`

### F09：自动入口去重、增量与并发保护
- **状态：** complete
- 执行的操作：
  - `kit.py` 新增 `project_lock`（OS 级文件锁：Windows msvcrt / POSIX flock，进程死亡自动释放，无残留死锁）与 `LockBusy`；`write_report` 原子替换；`append_trend` 读改写全程持 `report` 锁。
  - `preflight.py` 成为唯一编排入口：全程持 `preflight` 锁（`--lock-timeout` 可调）；新增 `--tag`（传播给 consistency 子检查，分入口写报告）、`--since N`（只增量扫正文）、`--json`（结构化结果：ok/blockers/counts/checks/severe_rows）；锁被占用退出码 3（跳过 ≠ 通过）。文本输出保持兼容。
  - `after_chapter_write.py` 只调用一次 preflight（`--no-advance --tag hook --since <改章到最新> --json`），从被改章增量扫描（上限 10 章）；不再自己预跑 consistency。
  - `watch_and_check.py` 同样只调一次 preflight（`--tag watch --since 1 --json`）；rc=3 时跳过本轮并保留基线；watch_state.json 原子写入。
  - `state_sync.py` 的 `save_hashes` 持 `state` 锁 + 原子替换（与编排锁异名，避免自锁）。
  - 新增 AutoEntryTests 5 项：单次保存仅一遍扫描、hook tag 报告落盘、并发 preflight 串行化、锁占用跳过（rc 3）、并发趋势 CSV 写入仍有效且同日一行。
- 创建/修改的文件：
  - `scripts/kit.py`、`scripts/preflight.py`、`scripts/state_sync.py`、`scripts/watch_and_check.py`、`hooks/after_chapter_write.py`、`scripts/regression_test.py`、`docs/08-防漂移工具.md`

### 烟测阶段：小说《高武：开局觉醒记账面板》（2026-09-20）
- **状态：** complete
- 执行的操作：
  - 插件侧修复回归：发现并修复 preflight 补验协议互锁缺陷（旧凭证失效与「从 last+1 补验」互相锁死）——`--chapter N` 的法定起点改为「最早失效章」，校验点只前进不后退、凭证按章刷新；新增回归测试（45 项全绿）。
  - 小说侧：init_book 初始化 → book.json 定制（账本 8 列、灵晶平账等式、12 档境界区间、气血锚点 ch3/6/10/15/21/27=32/51/80/120/170/230、jump_check、name_bad 等 12 类正典机检项）→ 规划文档全套 → 30 章正文 → 每章「账本行→brief 接地→起草→回写留痕→preflight 过闸」。
  - 烟测中闸门真实拦截并修复的问题：name_bad 误配（林澈禁写法含自身）、直角引号、5 处中英混排、账本灵晶漂移 2 轮、正文修正导致的凭证失效重验、状态基线滞后。
  - 字数契约按留档纪律调整：常态 1300–1700（汉字），拍板记录在书内 notes/。
- 终局指标：30/30 过闸（严重 0 · 中等 0 · 轻微 50），账本平账 30 章，6 个锚点全中，全书 44,474 汉字。

## 测试结果

| 功能点 | 测试 | 预期结果 | 实际结果 | 状态 |
|---|---|---|---|---|
| 基线 | `python scripts/selftest.py` | 24 项通过 | 24 项通过 | ✅ |
| 基线 | `python scripts/audit.py` | 无问题 | 无问题 | ✅ |
| 基线 | Python 编译检查 | 通过 | 通过 | ✅ |
| F01/F02 红灯 | init 保留已有 CodeBuddy 设置 | 应保留 | `permissions` 变成空，测试失败 | 🔴 预期失败 |
| F02 | Harness 设置合并、force 备份、非法 JSON 保护 | 5 项通过 | 5 项通过 | ✅ |
| F02 回归 | `selftest.py` / `audit.py` / compileall | 全绿 | 全绿 | ✅ |
| F03 子项 | 未匹配正文文件必须拦截 | 2 项通过 | 2 项通过 | ✅ |
| F03 子项 | 核心子脚本失败关闭 | 7 个子场景通过 | 7 个子场景通过 | ✅ |
| F03 子项 | 批量章节不跨章推进并可顺序补验 | 2 项通过 | 2 项通过 | ✅ |
| F03 完成验证 | F03 整组 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F04 子项 | 正文缺章与重复章号 | 2 项通过 | 2 项通过 | ✅ |
| F04 子项 | 账本必须从第 1 章开始 | 1 项通过 | 1 项通过 | ✅ |
| F04 子项 | 正文与账本章号集合完全一致 | 1 项通过 | 1 项通过 | ✅ |
| F04 子项 | 文件名章号与标题章号一致 | 2 项通过 | 2 项通过 | ✅ |
| F04 完成验证 | F04 整组 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F05 子项 | 严格解析流程凭证 | 1 项通过并回归合法格式 | 通过 | ✅ |
| F05 子项 | 跳过流程改为硬拦截 | 1 项通过 | 1 项通过 | ✅ |
| F05 子项 | 正文变更使旧凭证失效 | 1 项通过并回归顺序补验 | 通过 | ✅ |
| F05 子项 | SKILL/docs/preflight 编号统一 | 1 项文档契约测试通过 | 通过 | ✅ |
| F05 完成验证 | 完整 regression / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F06 子项 | 正式接地资料失败关闭及正向通过 | 2 项通过 | 2 项通过 | ✅ |
| F06 完成验证 | 完整 regression 26 项 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F07 子项 | 多候选拒绝注入 / 精确定位 / mtime 不起作用 | 5 项通过 | 5 项通过 | ✅ |
| F07 完成验证 | 完整 regression 31 项 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F08 子项 | gitignore 托管段 / env.example / doctor 密钥检查 | 4 项通过 | 4 项通过 | ✅ |
| F08 完成验证 | 完整 regression 35 项 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |
| F09 子项 | 单次编排 / tag 报告 / 串行化 / 锁跳过 / 趋势并发 | 5 项通过 | 5 项通过 | ✅ |
| F09 完成验证 | 完整 regression 40 项 / selftest / audit / compileall | 全绿 | 全绿 | ✅ |

## 错误日志

| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|---|---|---:|---|
| — | 暂无 | 0 | — |

## 五问重启检查

| 问题 | 答案 |
|---|---|
| 我在哪里？ | F06：写前接地的正式/探索边界 |
| 我要去哪里？ | 完成 F06 后依次完成 F07–F11 |
| 目标是什么？ | 逐项修复报告问题，并为每项留下完成标记和验证证据 |
| 我学到了什么？ | 见 `findings.md` |
| 我做了什么？ | F00–F05 已完成，F06 进行中 |

---
*每个功能点完成后立即更新本文件和 task_plan.md。*
