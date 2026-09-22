#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SessionStart hook —— 把「本书正典速查表」「写作纪律」「文风规则正文」自动注入上下文。

存在意义：md 方案的死穴是「AI 愿不愿意读」。这个脚本由 agent 在会话开始时自动执行，
输出会被注入对话——**不依赖 AI 主动读取**。

注入四段（按序）：① SOLOENT.md §7/§8 ② 正典速查表 ③ 写作纪律 ④ 文风规则正文。
④ 是 2026-09-20 补上的：此前风格规则只以路径形式存在于 AGENTS.md §2，
69 章正文的风格规则注入为零，作者反馈「读起来像流水账」。

事件：SessionStart（startup / resume / clear / compact）
输出 schema（严格，多一个键就会校验失败被丢弃）：
    {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import kit  # noqa: E402

MAX_CANON_BYTES = 24000   # 防止正典表膨胀到吃掉半个上下文
MAX_DISCIPLINE = 16       # 纪律条目上限（默认 9 条 + 本书 extra_discipline 的余量）
MAX_RULES_BYTES = 40000   # 规则正文注入上限。
# 实测演进：
#   · 2026-09-20 满配 10 个文件约 22.5KB，卡在 20KB 会把**题材文风规则**砍掉，
#     那恰恰是最该注入的一条，于是定 26KB。
#   · 2026-09-21 复盘发现 26KB 仍然不够：把 `story-style.md`（每本书风格第一手文件，
#     高武 6.4KB／仙侠 16KB）补进 `rules.load` 后，高武 12 个文件在第 7 个处耗尽——
#     **被挤掉的正是 style-urban 这类题材规则**；仙侠更极端，只注入了 3/11。
#   · 同日再调：底座实测最大 30.4KB（仙侠），定 40KB 让「底座 + 题材」都能装下。
#   同时 collect_rules 改为**分层配额 + 逐条报告**，不再按列表顺序静默砍尾部。
# ⚠️ 调大上限是权衡：注入越多，写手的可支配上下文越少。若某本书超限，
#    正确做法是**压缩规则文件**（把复盘/定案记录移出规则文件，见
#    `notes/story-style-定案记录.md` 的做法），不是无脑调大这个数。
MAX_SOLOENT_STATUS_BYTES = 4000   # SOLOENT.md §7/§8 的注入上限（doctor 也用这个数体检）

# 底座层拿总预算的多大比例（2026-09-21）。
# 底座 = `rules.base_rules` 声明的文件（story-style / 通用反 AI 味 / 直白纪律）。
# 实测仙侠的 story-style 单独就有 16KB，若与题材层共用一个池子，
# 它会按顺序挤掉 style-xianxia——所以给底座一个固定份额，剩下的留给题材层。
# 0.78 ≈ 40KB 里给底座 31.2KB（实测最大 30.4KB，装得下）、题材层 8.8KB（实测 10.6KB 略紧，
# 超出的部分会被逐条报告，作者据此压缩——**报告比静默丢失强**）。
BASE_RULES_SHARE = 0.78


def find_book_candidates(start):
    """从 start 逐级向上找所有含 .soloent/book.json 的书目录。

    返回列表而不是单个结果：向上走可能撞到嵌套的多本书（病理情况），
    这类多候选必须交给上层拒绝注入，而不是悄悄取最近的一个。
    """
    d = os.path.abspath(start)
    out = []
    for _ in range(8):
        if os.path.isfile(os.path.join(d, kit.CONFIG_REL)):
            out.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return out


def find_books_below(root):
    """在 root **下一级**找所有书目录（不猜、不排序、不改名）。"""
    out = []
    try:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name, kit.CONFIG_REL)
            if os.path.isfile(p):
                out.append(os.path.join(root, name))
    except OSError:
        pass
    return out


def locate_book(explicit_root, lib):
    """定位当前书。返回 (书根 or None, 定位依据 or 拒绝原因, 候选列表)。

    定位优先级（报告 NW-P1-006）：
      ① 显式 --root；② 环境变量 NOVEL_BOOK_ROOT；③ cwd 向上；④ 项目根向上；
      ⑤ 项目根下一级**唯一候选**；⑥ 显式 --lib 下一级**唯一候选**。
    任何一步出现多个候选 → **不注入任何一本书的正典**，把决定权还给用户。
    「最近修改的 book.json」不等于「当前要写的书」——按 mtime 猜书曾把另一部
    作品的人物、数值与纪律整包注入上下文，跨书污染无从挽回。
    """
    if explicit_root:
        d = os.path.abspath(explicit_root)
        if os.path.isfile(os.path.join(d, kit.CONFIG_REL)):
            return d, "显式 --root", []
        return None, f"--root 指向的目录不是一本书：{d}", []
    env = os.environ.get("NOVEL_BOOK_ROOT")
    if env and os.path.isfile(os.path.join(os.path.abspath(env), kit.CONFIG_REL)):
        return os.path.abspath(env), "环境变量 NOVEL_BOOK_ROOT", []

    proj = (os.environ.get("ZCODE_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd())
    steps = [("当前工作目录向上查找", os.getcwd())]
    if os.path.abspath(proj) != os.path.abspath(os.getcwd()):
        steps.append(("项目根向上查找", proj))
    for basis, start in steps:
        hits = find_book_candidates(start)
        if len(hits) == 1:
            return hits[0], basis, []
        if len(hits) > 1:
            return None, "ambiguous", hits
    for basis, base in (("项目根下一级唯一候选", proj), ("--lib 下一级唯一候选", lib)):
        if not base:
            continue
        hits = find_books_below(base)
        if len(hits) == 1:
            return hits[0], basis, []
        if len(hits) > 1:
            return None, "ambiguous", hits
    return None, "", []


def _read_rule(book, rel):
    """读一条规则。返回 (正文, 错误说明)。正文为空串表示失败。"""
    hit = book.resolve_rule(rel)
    if not hit:
        return "", "找不到文件"
    try:
        with open(hit[0], encoding="utf-8-sig") as f:
            text = f.read().strip()
    except OSError as e:
        return "", f"读不出来（{e.__class__.__name__}）"
    if not text:
        return "", "空文件"
    return text, ""


# 注入时剥离「给作者看的定案记录」——2026-09-21 新增。
#
# 为什么：各书 `story-style.md` 是按「定案档案」写的——既有**写作指令**（写手必须看到），
# 也有**复盘**（"我一开始搞错了…""为什么补这一条…"、参照数据、"错在哪"）。
# 后者是给作者看的办案记录，写手读了没用，却吃掉大量注入预算。
# 实测：仙侠 story-style 18.5KB 里约 6KB 是这类内容，它一个人吃掉一半预算，
# 把 `ai-anti-patterns`、`prose-directness` 全挤了出去。
#
# 剥离规则（只删「明确以复盘口吻开头」的块，不碰写作指令）：
#   · 引用块里以「为什么/我一开始/我拿/错在哪/实测/参照/我搞错」等开头的段落
#   · `> **为什么…**` / `> **我…**` 形式的整块
# 安全原则：**剥多了会丢掉写作指令，剥少了只是浪费预算**——所以只删高置信度的，
# 拿不准的一律保留。剥离后若某节变成空壳，整节保留（宁多勿少）。
_INJECT_STRIP_RE = re.compile(
    r"^>\s*\*\*(?:为什么|我|错在哪|实测|参照|背景|起因|定案)", re.M)

# 明确的「整块复盘」起始标记：出现即从此处删到该引用块结束
_BLOCK_STRIP_HEADS = (
    "> **为什么补", "> **为什么单列", "> **为什么要", "> **我一开始",
    "> **错在哪", "> **我拿",
)


def strip_review_noise(text):
    """剥离规则文件里「给作者看的复盘」，只留写作指令。

    返回 (瘦身后正文, 省下的字节数)。省下 0 表示没有可剥的内容。
    """
    lines = text.splitlines()
    keep, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if any(ln.startswith(h) for h in _BLOCK_STRIP_HEADS) or _INJECT_STRIP_RE.match(ln):
            # 跳过这一整个引用块（连续的 > 行 + 紧邻的空行）
            while i < len(lines) and (lines[i].lstrip().startswith(">") or not lines[i].strip()):
                i += 1
            continue
        keep.append(ln)
        i += 1
    out = "\n".join(keep)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    # 安全阀：剥掉超过一半说明规则误判，宁可原样返回
    if len(out.encode("utf-8")) < len(text.encode("utf-8")) * 0.5:
        return text, 0
    saved = len(text.encode("utf-8")) - len(out.encode("utf-8"))
    return out, saved


def collect_rules(book, max_bytes=MAX_RULES_BYTES):
    """把 book.json `rules.load` 声明的规则文件**正文**拼起来。

    为什么必须注入正文（2026-09-20 复盘）：规则文件躺在 `assets/rules/` 里
    不会自己进上下文——`AGENTS.md §2` 只列**路径**，读不读全看模型自觉，
    而 inject_canon 此前只注入了正典与流程纪律，**风格规则一条都没进**。
    结果：69 章正文按「无文风约束」写完，作者反馈「读起来像流水账」。

    所以这里补上：`rules.load` 声明谁，就注入谁（`rules.forbid` 天然不在列内）。
    单一来源不变——内容仍只在规则文件里，改规则不用改代码。

    ## 分层配额（2026-09-21 新增，修一个真实缺陷）

    实测：高武 `rules.load` 满配 12 个文件，其中**前 4 个**（story-style 6.4KB、
    consistency-gate 2.6KB、ai-anti-patterns 3.9KB、prose-directness 3.6KB）就吃掉
    16.5KB，26KB 预算在**第 7 个文件**处耗尽——被挤掉的恰恰是 `style-urban.md`
    这种**题材文风规则**，也就是最该进上下文的那些。仙侠更极端：只注入了 3/11。

    根因：`rules.load` 的顺序是「作者填写顺序」，不是「重要性顺序」，
    而旧的超限处理是**按列表顺序砍尾部 + 只发一句警告**——等于防线悄悄缺一块。

    现口径：**通用底座必进（保底配额），题材/专属规则吃剩余预算，超限逐条报告。**
    保底不是靠顺序，是靠显式分层——`base_rules` 声明的文件优先分配。

    返回 (正文, 已注入文件名列表)。
    """
    R = book.cfg.get("rules") or {}
    load = [str(x) for x in (R.get("load") or [])]
    if not load:
        return "", []
    base_set = {str(x) for x in (R.get("base_rules") or [])}
    vault = book.vault or ""
    base_dir = os.path.join(vault, "rules") if vault else ""
    if not base_dir or not os.path.isdir(base_dir):
        return ("\n\n> ⚠️ **规则目录读不到**：`book.json` 声明要加载 "
                + "、".join(load) + f"，但目录不存在（`{base_dir}`）。"
                "本次未注入任何规则——请检查 `vault` 配置与插件安装完整性。\n"), []

    # 排单：声明的顺序就是注入顺序（作者可用它控制优先级）；
    # 未在 base_rules 里声明的，默认全部当「通用底座」——
    # 向后兼容：没写 base_rules 的书，行为与旧版一致（按声明顺序全量尝试）。
    if base_set:
        ordered = [x for x in load if x in base_set] + [x for x in load if x not in base_set]
    else:
        ordered = list(load)

    # ---- 分层配额（2026-09-21）----
    # 两段预算：底座层与题材层**各花各的**，互不挤占。
    #
    # 为什么必须分开算：底座里装着 story-style（每本书风格第一手文件，实测高武 6.4KB、
    # 仙侠 18.5KB）、节奏目标、通用反 AI 味——缺一条，写手就在没有风格约束的状态下动笔。
    # 而题材层（style-urban / style-xianxia / style-system）决定「像不像这类书」。
    # 用一个大池子按顺序分配时，仙侠的 story-style 一个人就吃掉一半，
    # 结果 style-xianxia 与 anti-ai-character-realism 双双被砍（实测 6/11）。
    # 分开算之后：底座层保底吃满自己的份额，题材层用剩下的，互不伤害。
    if base_set:
        base_pool = int(max_bytes * BASE_RULES_SHARE)
    else:
        base_pool = max_bytes
    pools = {"base": [base_pool, 0], "genre": [max_bytes, 0]}   # [上限, 已用]

    def _pool_of(rel):
        return "base" if (base_set and rel in base_set) else "genre"

    out, kept = [], []
    missing, dropped = [], []
    for rel in ordered:
        text, err = _read_rule(book, rel)
        if err:
            missing.append(f"{rel}（{err}）")
            continue
        blob = f"\n\n---\n\n### 规则文件：{rel}\n\n{text}"
        cost = len(blob.encode("utf-8"))
        key = _pool_of(rel)
        cap = pools[key][0] if key == "base" else min(pools["genre"][0], max_bytes)
        # 底座层用自己的份额；题材层可以用「总上限 － 底座已用」的剩余
        if key == "genre":
            cap = max_bytes - pools["base"][1]
            cap = min(cap, pools["genre"][0])
        if pools[key][1] + cost > cap:
            dropped.append((key, rel))
            continue
        out.append(blob)
        kept.append(rel)
        pools[key][1] += cost

    base_dropped = [r for k, r in dropped if k == "base"]
    genre_dropped = [r for k, r in dropped if k == "genre"]
    warn = []
    if missing:
        warn.append("\n\n> ⚠️ **以下规则文件未能注入**，动笔前请自行打开："
                    + "、".join(missing))
    if base_dropped:
        warn.append("\n\n> ⚠️ **底座规则文件因注入预算（底座层 %d B）不足被跳过**：%s\n"
                    "> 底座缺失 = 写手在无风格约束状态下动笔，**属配置错误，请立即处理**："
                    "压缩该文件正文，或减少 `rules.load` 的底座条目。"
                    % (base_pool, "、".join(base_dropped)))
    if genre_dropped:
        # 逐条列出被挤掉的，而不是只报一句「已达上限」——
        # 被挤掉的每一条都要能被看见，否则等于静默缺防线。
        warn.append("\n\n> ⚠️ **以下题材/补充规则因注入预算（%d B，底座已占 %d B）不足被跳过**，"
                    "动笔前请自行打开：%s\n> 处理建议：压缩该文件，或删掉本书用不上的条目，"
                    "或减少 `rules.load` 条目——**不要让题材文风规则长期缺席。**"
                    % (max_bytes, pools["base"][1], "、".join(genre_dropped)))
    return "".join(out) + "".join(warn), kept


def rhythm_target_block(book):
    """把「节奏目标」注入到写手上下文（2026-09-21 新增）。

    为什么必须单独注入：实测发现写手上下文里 `平均句长`/`均长`/`40%`/`3.5`
    **出现 0 次** —— 目标档只写在本书 `.soloent/rules/story-style.md`，
    而那个文件不在 `rules.load` 里，**从未进过注入**。写手只拿到 36 条负向禁令，
    没有可操作的正向目标，于是照着闸门下限写 = 「及格的碎」（ch-33 实测：
    句均 21.27 / 长句 32.0% / 转折 2.55，三项全低于 ch-01）。

    数值来源与闸门同源：优先 `checks.rhythm._target`（对标实测档），
    没有就用过渡档阈值本身。**不硬编码数字**，改 book.json 即改注入。
    返回注入正文；本书没配 rhythm 时返回空串。
    """
    R = book.sec("checks").get("rhythm") or {}
    if not R:
        return ""

    def pick(key, default=None):
        tgt = R.get("_target") or {}
        for v in (tgt.get(key), R.get(key), default):
            if v is not None:
                return v
        return None

    avg = pick("narr_avg_min")
    lng = pick("narr_long_min_pct")
    sht = pick("short_ge10_max_pct")
    twn = pick("turn_min_per_1000")
    dial = pick("dialogue_max_pct")
    if avg is None and lng is None:
        return ""
    is_target = bool(R.get("_target"))

    rows = []
    if avg is not None:
        rows.append(f"叙述句均长 ≥ {avg} 字（低于它说明句子碎、密度不足）")
    if lng is not None:
        rows.append(f"长句(>25字)占比 ≥ {lng}%（没有长句 = 没有铺陈 = 读者读不到重量）")
    if sht is not None:
        rows.append(f"≤10 字短句占比 ≤ {sht}%（短句是重锤，连着敲就是清单）")
    if twn is not None:
        rows.append(f"转折/关联词密度 ≥ {twn} 个/千字（句与句之间的关节）")
    if dial is not None:
        rows.append(f"对话占比 ≤ {dial}%")

    src = ("对标实测目标档 `checks.rhythm._target`" if is_target
           else "本书 `checks.rhythm` 阈值（尚未填 `_target` 目标档）")
    lines = [
        "\n\n---\n\n# 【自动注入 · 节奏目标（写作方向，不是闸门下限）】\n",
        f"> 来源：`{os.path.join(book.root, kit.CONFIG_REL)}` 的 {src} —— 与闸门同源，改配置即改此处。\n",
        "> **过闸线 ≠ 目标**：硬闸门只拦最差的下限，照着下限写就是「及格的碎」。",
        "> 写作一律朝下面这组数走；完稿自查也按这组算，不按闸门算。\n",
    ]
    lines += [f"- {r}" for r in rows]
    lines.append(
        "\n**长句怎么写出来**（禁令给不了这个，必须正向给）：\n"
        "1. **动作链不切断**：一个连续动作用「，」串起来，最后落一个结果。"
        "❌「他把碗放下。他站起来。他走到门口。」→ ✅「他把碗往桌上一推站起来，"
        "绕过两张凳子走到门口，一把拉开了那扇被油烟熏得发黏的木门。」\n"
        "2. **因果/让步挂上去**：❌「他不想问。他还是问了。」→ ✅「他本来不想问，"
        "可那句话在舌尖上转了两圈，到底还是溜了出来。」\n"
        "3. **长句带细节，短句砸落点**：一段的标准配比是「长—长—短」，"
        "前面铺够，最后一句用短句收，力道才出得来。\n"
        "\n**落笔前三问**：① 这段主句是不是又短又平？② 这段有没有一个长句扛住信息？"
        "③ 该有转折词的地方有没有？（细则见 `assets/rules/prose-directness.md` §六）\n"
        "\n> ⚠️ 倒过来同样成立：**长 ≠ 好**。长句是承载信息的，不是把句子灌水拉长；"
        "该短的地方就要短。目标是「长短错落」，不是「全都变长」。")
    return "\n".join(lines)


def default_discipline(kit_dir):
    """本书未在 book.json 里自定义 discipline 时用的通用纪律。

    命令一律写成「书目录下的薄壳」——agent 无论在哪台机器、插件装在哪，
    只要在书目录里工作，这个相对路径就成立。
    """
    return [
        "**每章写完后必须过闸**：`python tools/preflight.py` —— 退出码 0 才许写下一章。",
        "**过闸之后再跑 `/review`**：五层质量审查（一致性／风格／钩子／结尾陷阱／AI 写作模式）。"
        "闸门查硬伤，`/review` 查质量——**顺序不能反**。",
        "**每章完成后停下**，向作者汇报并等待确认。**禁止连续生成多章。**"
        "（历史事故：19 章在 69 分钟内连发，从第 15 章起设定全线漂移。）",
        "**整章重写禁止**。重设计 = 在旧稿上做外科手术，不是推倒重来；动笔前先列"
        "「保留 / 迁移 / 新增 / 删除」四列清单呈作者确认；已验证的文本资产只迁移不删除。",
        "**单一来源**：新增人物或改动数值口径时，先改 `.soloent/canon.md`，再改 "
        "`.soloent/book.json` 里对应的机检项。两处不一致 = 机制失效。",
        "**前后台双层防线**：`tools/watch_and_check.py` 若在运行，`chapters/` 一有改动会自动"
        "对账＋过闸并写进 `notes/对账日志.md`——那一层与 AI 是否守流程无关。",
        "**文风红线就是上面那节「文风规则」**：起草时逐条遵守，不是写完再说。"
        "**自查的含义是「黑名单词与句式禁项清零」**，不是「我觉得没问题」——"
        "闸门会把留痕数字与本章机检残留对撞，编数字过不了闸（见 preflight 的流程留痕检查）。",
        "**确需保留的违规处必须在留痕行尾写明理由**：`[流程] ch-N 自查X 三连Y 复查Z 回写OK；保留：<理由>`"
        "——无理由的保留一律拦截。",
        "**上下文到 100–150K 就该考虑开新窗口**。开新窗口前先落盘（`now.md` + `SOLOENT.md §7/§8` + 账本），"
        "`/compact` 不能替代落盘。",
    ]


def soloent_status(book, max_bytes=MAX_SOLOENT_STATUS_BYTES):
    """从 SOLOENT.md 抽出「当前写作状态」与「路线图」两节。

    为什么只抽这两节：SOLOENT.md 是中央控制面板，但整篇注入会吃掉太多上下文。
    会话开始时最需要的是「写到哪了、下一步做什么」——那就是 §7 与 §8。
    其余板块（DNA/世界/角色/大纲/风格/约束）该由 agent 按会话恢复协议自己读。
    """
    text = book.read(os.path.join(book.root, "SOLOENT.md"))
    if not text:
        return ""
    out = []
    for n in ("7", "8"):
        m = re.search(rf"^##\s*{n}[\.、]?\s*(.+?)$(.*?)(?=^##\s*\d|^---|\Z)",
                      text, re.M | re.S)
        if m:
            out.append(f"## {n}. {m.group(1).strip()}\n{m.group(2).rstrip()}")
    if not out:
        return ""
    s = "\n\n".join(out)
    if len(s.encode("utf-8")) > max_bytes:
        s = s.encode("utf-8")[:max_bytes].decode("utf-8", "ignore") + \
            "\n\n…（已截断，完整内容见 SOLOENT.md）"
    return s


def arg(argv, name, default=""):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def main():
    kit_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proj = (os.environ.get("ZCODE_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd())
    lib = arg(sys.argv[1:], "--lib")

    root, basis, candidates = locate_book(arg(sys.argv[1:], "--root"), lib)

    parts = []
    if basis == "ambiguous":
        # 多候选 = 无法确认「当前要写的是哪本」。宁可什么都不注入，
        # 也不能把另一部作品的正典当成事实塞进上下文。
        listing = "\n".join(f"- {c}" for c in candidates[:6])
        tail = f"\n- …（共 {len(candidates)} 本）" if len(candidates) > 6 else ""
        parts.append("# 【自动注入 · 未注入正典】\n\n"
                     "检测到多本书，无法确认当前项目。**本次未注入任何一本书的正典。**\n\n"
                     "候选：\n" + listing + tail +
                     "\n\n请进入具体书目录再开会话，或用 `--root <书目录>` 显式指定。"
                     "在确认之前，**不要引用任何一本书的设定**。\n")
    elif root:
        try:
            book = kit.load_book(["--root", root], required=False)
        except SystemExit:
            book = None
        if book:
            B = book.sec("book")
            # ① SOLOENT.md 的当前状态（中央控制面板里最该先看到的两节）
            st = soloent_status(book)
            if st:
                parts.append(
                    f"# 【自动注入 · SOLOENT.md 当前状态】\n\n"
                    f"> 作品：**{B.get('title', '')}**（{B.get('genre', '')}）\n"
                    f"> 来源：`{os.path.join(book.root, 'SOLOENT.md')}` —— **中央控制面板**，"
                    f"由 SessionStart hook 自动注入这两节。\n"
                    f"> 定位依据：{basis}。\n"
                    f"> 完整面板（8 板块：DNA／世界／角色／大纲／风格／约束／状态／路线图）"
                    f"请自己打开读，它是所有文件指针的入口。\n\n" + st)
            # ② 正典速查表
            canon = book.read_path("canon") or ""
            if len(canon.encode("utf-8")) > MAX_CANON_BYTES:
                canon = (canon.encode("utf-8")[:MAX_CANON_BYTES].decode("utf-8", "ignore")
                         + "\n\n…（正典表过长已截断，请直接打开文件读全）")
            parts.append(
                f"# 【自动注入 · 本书正典速查表】\n\n"
                f"> 来源：`{book.path('canon')}` —— 由 SessionStart hook 自动注入，**不是你自己读的**。\n"
                f"> 定位依据：{basis} → `{book.root}`。\n"
                f"> **关键值一律以此为准，不要凭印象引用。** 需要完整设定时再读 `world/` 与 `characters/`。\n\n"
                + canon)
            # ③ 写作纪律（本书可用 book.json 的 discipline 覆写；extra_discipline 一律追加，
            #    后者与 AGENTS.md §4「本书特有红线」同源，改一处两处都变）
            disc = list(book.cfg.get("discipline") or default_discipline(kit_dir))
            disc += [str(x) for x in ((book.cfg.get("book") or {}).get("extra_discipline") or [])
                     if str(x).strip()]
            parts.append("\n---\n\n# 【自动注入 · 写作纪律】不可跳过\n\n"
                         + "\n".join(f"{i}. {d}" for i, d in enumerate(disc[:MAX_DISCIPLINE], 1)))
            # ④ 文风规则正文（rules.load 声明谁就注入谁）
            rules_text, rule_names = collect_rules(book)
            if rules_text:
                parts.append(
                    "\n---\n\n# 【自动注入 · 文风规则（去 AI 味）】\n\n"
                    f"> 来源：本书 `book.json` 的 `rules.load` 声明的 {len(rule_names)} 个文件"
                    f"（{'、'.join(rule_names)}）。\n"
                    f"> 由 SessionStart hook 自动注入——**不依赖你去读**。"
                    f"AGENTS.md §2 只列路径，光靠自觉打开是不够的（2026-09-20 复盘结论）。\n"
                    f"> **起草时逐条遵守；写完的自查以这些规则为判据。**\n"
                    + rules_text)
            # ⑤ 节奏目标（2026-09-21 新增）
            #
            # 为什么要单独一节：实测写手上下文里「平均句长/均长/40%/3.5」出现 0 次——
            # 目标档只躺在 story-style.md（不在 rules.load 内），从未进过注入。
            # 于是写手只看到 36 条负向禁令，没有正向目标，照着闸门下限写 = 及格的碎。
            # 本节的数字**从 book.json 现取**，与闸门同源，不硬编码。
            rt = rhythm_target_block(book)
            if rt:
                parts.append(rt)
        else:
            parts.append("# 【自动注入 · 作品库环境】\n\n"
                         "检测到书目录，但 `.soloent/book.json` 读不出来——请先修复配置。\n")
    else:
        parts.append("# 【自动注入 · 作品库环境】\n\n"
                     "当前不在某本书的项目目录内。进入具体作品目录工作前，"
                     "先读该书的 `SOLOENT.md`、`.soloent/canon.md` 与 `AGENTS.md`。\n")

    # ④ 系统体检（自动，只在有问题时才注入）
    #
    # 为什么要自动：体检的价值恰恰在「你还不知道自己需要它」的时候——
    # 钩子失效、薄壳缺失这类配置漏配平时毫无症状，等发现时防护早就不在了。
    # 指望人手动跑 = 永远不会跑。
    #
    # 安全保障：
    #   · 用 --quick（跳过所有子进程检查，毫秒级）
    #   · 只读，绝不写任何文件
    #   · 全部 try/except —— 体检自己出任何问题都静默跳过，绝不影响开场
    #   · **全部通过时不注入任何东西**，避免每会话刷屏
    #
    # 多书歧义时：给了 --lib 就按库体检（逐本书查），没给就跳过书籍体检——
    # 把体检指向「不是书的目录」只会得到一句误导性的「book.json 读不出来」。
    doctor_root, doctor_extra = (root or proj), ["--quick", "--json"]
    if basis == "ambiguous":
        if lib:
            doctor_extra += ["--lib", lib]
        else:
            doctor_root = None
    if doctor_root:
        try:
            rc, out, present = kit.run_child("doctor.py", doctor_root, doctor_extra, timeout=20)
            if present and rc in (0, 1, 2) and out.strip().startswith("["):
                import json as _json
                rows = _json.loads(out)
                bad = [r for r in rows if r.get("level") in ("warn", "err")]
                if bad:
                    n_err = sum(1 for r in bad if r["level"] == "err")
                    n_warn = len(bad) - n_err
                    lines = ["# 【自动体检 · 发现 %d 个问题】\n" % len(bad),
                             "> 每次会话开始自动跑（只读，不改任何文件）。"
                             "**全绿时不会显示这一段。**\n"]
                    for r in bad[:8]:
                        icon = "❌" if r["level"] == "err" else "⚠️ "
                        lines.append("- %s **%s**：%s" % (icon, r.get("where", ""),
                                                         r.get("what", "")))
                        if r.get("how"):
                            lines.append("    - → %s" % r["how"])
                    if len(bad) > 8:
                        lines.append("- … 另有 %d 项，跑 `python tools/doctor.py` 看全部"
                                     % (len(bad) - 8))
                    lines.append("\n严重 %d · 警告 %d —— "
                                 "这些不会自动修复，需要你决定。" % (n_err, n_warn))
                    parts.append("\n".join(lines))
        except Exception:
            pass  # 体检失败绝不能影响正典注入

    kit.emit_hook("SessionStart", "\n".join(parts))
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
