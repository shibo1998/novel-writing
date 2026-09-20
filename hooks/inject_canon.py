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
MAX_RULES_BYTES = 26000   # 规则正文注入上限。
# 实测：rules.load 满配 10 个文件合计约 21KB（本书还可能有自写规则），
# 卡在 20KB 会把最后一条**题材文风规则**砍掉——那恰恰是最该注入的一条。
# 真超了也要**报警**，不能静默截断（见 collect_rules）。
MAX_SOLOENT_STATUS_BYTES = 4000   # SOLOENT.md §7/§8 的注入上限（doctor 也用这个数体检）


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


def collect_rules(book, max_bytes=MAX_RULES_BYTES):
    """把 book.json `rules.load` 声明的规则文件**正文**拼起来。

    为什么必须注入正文（2026-09-20 复盘）：规则文件躺在 `assets/rules/` 里
    不会自己进上下文——`AGENTS.md §2` 只列**路径**，读不读全看模型自觉，
    而 inject_canon 此前只注入了正典与流程纪律，**风格规则一条都没进**。
    结果：69 章正文按「无文风约束」写完，作者反馈「读起来像流水账」。

    所以这里补上：`rules.load` 声明谁，就注入谁（`rules.forbid` 天然不在列内）。
    单一来源不变——内容仍只在规则文件里，改规则不用改代码。

    返回 (正文, 已注入文件名列表)。**文件缺失/读不出来时在正文里显式列出**——
    静默少注入一条规则，等于防线悄悄缺一块，而这正是本 hook 存在的理由（见文件头）。
    """
    load = [str(x) for x in ((book.cfg.get("rules") or {}).get("load") or [])]
    vault = book.vault or ""
    base = os.path.join(vault, "rules") if vault else ""
    if not load:
        return "", []
    if not base or not os.path.isdir(base):
        return ("\n\n> ⚠️ **规则目录读不到**：`book.json` 声明要加载 "
                + "、".join(load) + f"，但目录不存在（`{base}`）。"
                "本次未注入任何规则——请检查 `vault` 配置与插件安装完整性。\n"), []
    out, kept, missing, used = [], [], [], 0
    for rel in load:
        # kit 资产优先，kit 里没有就用本书 `.soloent/rules/` 的自写副本
        # （书作者自己写的规则以前会被当成缺失文件跳过 —— 规则写了却没生效）
        hit = book.resolve_rule(rel)
        if not hit:
            missing.append(rel)
            continue
        path = hit[0]
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read().strip()
        except OSError:
            missing.append(rel)
            continue
        if not text:
            missing.append(rel + "（空文件）")
            continue
        blob = f"\n\n---\n\n### 规则文件：{rel}\n\n{text}"
        cost = len(blob.encode("utf-8"))
        if used + cost > max_bytes:
            # 超预算：把剩下的收口成一句提示，而不是静默截断一半规矩
            left = max_bytes - used - 200
            if left > 400:
                out.append(blob.encode("utf-8")[:left].decode("utf-8", "ignore")
                           + "\n\n…（已达注入上限，本文件其余内容请自行打开阅读）")
                kept.append(rel)
            # ⚠️ 原来只把「后面还没处理的」列进提示，**当前这条自己被漏掉**——
            # 于是被挤掉的若是最后一条，就没有任何警告，规则静默消失。
            # 这正是本 hook 文件头明令禁止的情形（2026-09-20 实测命中）。
            rest = [rel] if rel not in kept else []
            rest += [x for x in load[load.index(rel) + 1:] if x not in kept]
            if rest:
                missing.append("未注入（超上限）：" + "、".join(rest))
            break
        out.append(blob)
        kept.append(rel)
        used += cost
    if missing:
        out.append("\n\n> ⚠️ **以下规则文件未能注入**，动笔前请自行打开："
                   + "、".join(missing))
    return "".join(out), kept


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
