# 发现与决策

## 需求

- 开始修复审查报告确认的问题。
- 每完成一个功能点立即标记，避免任务中断后丢失进度。
- 修复必须有可重复的验证结果。

## 已确认事实

- 当前目录不是 Git 仓库，不能依赖 Git diff 恢复本次任务进度。
- `selftest.py` 当前覆盖 24 个“违规会触发”用例，但缺少防绕过负向测试。
- `preflight.py` 的流程留痕正则只检查行首和章号，任意尾部文本可以通过。
- 批量新增达到阈值时当前只警告，成功后仍推进 checkpoint。
- `ledger.py` 只检查账本自身首尾区间，账本仅有第 5 章时不会发现第 1–4 章缺失。
- `preflight.py` 在最大章号为 0 时提前成功，未匹配命名的正文可能漏检。
- init/sync 会整体重写编辑器配置；`--force` 没有自动备份。
- `append_trend()` 已有同日覆盖和日期去重，但读改写过程无锁、无原子替换。
- `consistency_check.py` 已有 `--since N`，自动入口尚未接入。
- Watcher 的直接检查有 `watch` tag，但它调用的 preflight 会再次执行无 tag 检查。
- `preflight.py` 的 B 编号与 `SKILL.md` 错位；实际凭证对应 SKILL B3–B6 或正文步骤 2–5。
- `sync.plan()` 同时生成普通薄壳文本和两种 Harness JSON；当前 `diff()`、`do_book()` 对它们一视同仁整体比较和整体覆盖。
- `do_lib()` 当前只写缺失的 Hook 配置；发现内容不同时一律视为定制并跳过，导致插件 Hook 本身也无法安全升级。
- `init_book.py` 在生成计划前已经写入 `book.json` 和模板，无法在已有 Harness JSON 非法时做到“写前拒绝”。

## 技术决策

| 决策 | 理由 |
|---|---|
| 新建独立负向回归脚本，而不是把全部内容塞进现有 selftest | 现有 selftest 职责是规则触发；防绕过测试属于闸门/事务层 |
| 测试项目一律放系统临时目录 | 不触碰真实小说和真实 checkpoint |
| 回归测试按功能点逐个加入并立刻修绿 | 遵守红→绿垂直切片，避免批量测试想象中的接口 |
| 先修数据损失和假通过，再做增强功能 | 风险优先级最高 |
| 共享写入能力集中到 `kit.py` | 避免各脚本重复实现不一致的安全写入 |
| Harness JSON 使用“保留未知字段 + 替换本插件 Hook 条目”的专用合并 | 既保留用户 Stop/权限/env，又能更新本插件自己的 SessionStart/PostToolUse |
| init 在任何写入前构造生成计划并验证已有 JSON | 非法配置应在项目发生变化前失败 |
| `--force` 只备份本次会覆盖的已存在文件，并保持相对路径 | 避免把备份目录递归复制进自身，同时满足可恢复性 |
| Harness Hook 合并通过脚本文件名识别本插件条目 | 能更新本插件条目，同时保留用户在相同事件下添加的其他 Hook |
| 批量积压通过显式 `--chapter N` 顺序恢复 | 既禁止跨章推进 checkpoint，又给已经生成多章的项目保留可执行的恢复路径 |

## 遇到的问题

| 问题 | 解决方案 |
|---|---|
| 烟测发现 preflight 协议互锁：旧章修正后凭证失效拦住 --chapter last+1，跳号规则又拦住 --chapter 旧章 | 补验法定起点改为「最早失效章」；校验点用 max(cur, last) 不回退；+1 回归测试锁定 |
| 账本灵晶流水跨章漂移（正文中半价药等未入账） | ledger 平账检查自动拦截；逐行修正后重验 |
| writeback 流程留痕被小节替换吞掉 / 模板示例行骗过防重复 | 留痕行插入到「流程留痕」小节头部；防重复用行首锚定 |
| Windows 并发 os.replace 共享冲突 | atomic_write_text 加 PermissionError 重试；报告+趋势合并一次持 report 锁 |
| 项目不是 Git 仓库 | 用规划文件和回归测试记录进度，不执行破坏性 Git 操作 |
| F02 红灯：初始化后 `permissions`、`env`、自定义 Stop Hook 消失 | 需要对编辑器 JSON 做所有者范围内的深度合并，不能整体覆盖 |

## 已完成修复

- F02：新增共享原子写入；init/sync 安全合并两种 Harness JSON；非法 JSON 写前拒绝；`--force` 自动备份。
- F03（子项）：preflight 在空书和已有正常章节两种场景下，都会拦截 `chapters/` 中未被 `chapter.file_regex` 纳入检查的 Markdown 正文。
- F03（子项）：三个核心子脚本缺失、超时、崩溃或返回无法确认成功的输出时，preflight 均失败关闭；状态同步与账本不再以“未接入”放过。
- F03（子项）：一次积压多章时默认过闸失败且 checkpoint 不变；新增 `preflight.py --chapter N`，只允许从 `last_verified_chapter + 1` 开始顺序补验，拒绝跳章。
- F03：闸门失败关闭完整回归 7 项通过；`selftest.py` 24 项、`audit.py` 和编译检查同时通过。
- F04（子项）：ledger 现在直接校验正文章号集合，拦截正文缺章，以及 `ch-1.md` / `ch-01.md` 这类多个文件映射同一章号的重号。
- F04（子项）：账本有数据但首个章号不是 1 时独立拦截，不再因自身区间连续而把“仅第 5 章”判为平账。
- F04（子项）：正文与账本改为章号集合双向差集核对，最大章号相同但中间错位时也会分别列出“正文有/账本缺”和“账本有/正文缺”。
- F04（子项）：ledger 使用 `chapter.title_number_regex` 提取标题章号；标题不可解析或与文件名章号不一致时失败关闭，新书模板已提供默认表达式。
- F04：集合级核对完整回归 6 项通过；`selftest.py` 24 项、`audit.py` 和编译检查同时通过。
- F05（子项）：流程凭证尾部改为完整匹配 `自查N 三连N 复查N 回写OK`，N 必须是非负整数；任意文本不能再冒充凭证。
- F05（子项）：凭证包含“跳过/未执行/未跑/历史章”时不再仅警告，而是作为流程未执行的硬拦截。
- F05（子项）：checkpoint 升级为逐章记录文件名、SHA-256、通过时间和检查版本；已通过章节改写、删除或命名失配后旧凭证失效，写入使用原子替换。
- F05（子项）：统一表述为“正文步骤 2–5（SKILL B3–B6）”；修正 preflight、now 模板和总览中原先错位的 B2/B0 编号。
- F05：完整回归累计 22 项通过；`selftest.py` 24 项、`audit.py` 和编译检查同时通过。
- F06：新增 `draft.py` 探索草稿入口（drafts/ + NON-CANONICAL 标记 + 转正清单）；README/SKILL/docs/00/docs/01 统一为「先写探索草稿」，删除「直接写第一章」旁门；draft.py 进入 sync 薄壳清单。
- F07：inject_canon 定位改为六级优先（显式 root > env > cwd 向上 > 项目根向上 > 唯一候选 > --lib 唯一候选），多候选一律拒绝注入并提示；定位依据写进注入头；mtime 猜书逻辑删除。
- F08：.gitignore 托管段安全合并（保留用户行）；.env/.soloent/.api.env 默认忽略 + .env.example 模板；doctor 检查密钥是否被 Git 跟踪（跟踪即严重错误）；文档改为「git init 需手工执行」。
- F09：preflight 成为唯一编排入口并全程持项目锁；hook/watcher 只调一次 preflight，tag/since 传播、--json 结构化输出、锁占用退出码 3；报告/趋势/哈希全部锁 + 原子写入；OS 级文件锁（msvcrt/flock）避免残留死锁，子检查用异名锁防止自锁。
- F06（子项）：`brief.py` 默认明确为正式模式；正典仍含占位符、目标章无细纲或无预置账本行时汇总拦截并返回 1，三项齐全时返回 0。

## 资源

- `docs/14-项目流程与漏洞审查报告.md`
- `scripts/selftest.py`
- `scripts/audit.py`
- `scripts/preflight.py`
- `scripts/ledger.py`
- `scripts/sync.py`
- `scripts/init_book.py`

---
*每获得新的实现事实或做出关键技术决策后更新本文件。*
