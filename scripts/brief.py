#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
写前接地单 (brief.py)

用途：开工写第 N 章**之前**，一条命令把「该读的东西从文件里捞出来摆到眼前」。

为什么需要它：
    「记忆丢失」不是模型把事忘了，而是它**凭印象写**。印象来自上下文里的残影，
    文件里才是事实。这个脚本把「章前该看的几件事」变成一条命令——上一章末的准确状态、
    本章的细纲节拍、本章该接的伏笔、在场角色是谁。跑一次，事实就重新进入上下文，
    而不是靠回忆。

产出六节：细纲节拍 / 本章数值台账 / 相关伏笔三态 / 当前进度 / 正典速查 / 本章文风红线。

第六节是新加的（2026-09-20 复盘）：此前接地单只打正面事实，**一条文风规则都不打**，
而 9 个风格规则文件只以路径形式躺在 AGENTS.md §2，靠模型自觉打开——
结果 69 章正文里风格规则注入为零，作者反馈「全是流水账」。
文风红线必须和事实一样，在动笔前一刻被摆到眼前；且**内容与闸门同源**
（同一份 book.json 配置），避免「写的规矩」和「查的规矩」打架。

用法：
    python <kit>/tools/brief.py           # 正式模式；默认出「下一章」，前置资料不全即失败
    python <kit>/tools/brief.py 31        # 指定第 31 章
    python <kit>/tools/brief.py --root <书目录>
退出码：0=出单成功  1=参数/文件有问题
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

LINE = "─" * 62


# ---------------------------------------------------------------- 细纲

def outline_texts(book):
    """候选细纲正文，按优先级排序。

    为什么要有多个候选：初始化的默认值是 `paths.outline = "outline/第一卷-细纲.md"`，
    而实际命名习惯是 `第一卷-<卷名>-细纲.md`（带卷名）。忘了改配置时，
    原先只读那一个文件、读不到就**静默返回空**——接地单里只剩一句 ⚠，
    agent 很可能忽略它直接开写，等于**没有细纲就动笔**。
    """
    out = []
    t = book.read_path("outline")
    if t:
        out.append(t)
    d = os.path.join(book.root, "outline")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".md") and "细纲" in f:
                try:
                    with open(os.path.join(d, f), encoding="utf-8-sig") as fh:
                        t2 = fh.read()
                except OSError:
                    continue
                if t2 and t2 not in out:
                    out.append(t2)
    return out


def outline_block(book, n):
    """返回 (title, desc, hook_type, hook_text) 或 None。

    支持两种细纲写法（由 chapter.outline_format 指定，缺省自动探测）：
      inline  —— 一行一章：`31 标题：描述｜钩子·悬念：内容`
      heading —— `## 第31章 标题` + 正文里 `**核心概括**：...`
    """
    fmt = book.get("chapter", "outline_format", "auto")
    for text in outline_texts(book):
        got = _parse_outline(text, n, fmt)
        if got:
            return got
    return None


def _parse_outline(text, n, fmt):
    """在单份细纲正文里找第 n 章。"""
    if fmt in ("auto", "heading") and re.search(r"^##\s+第\s*\d+\s*章", text, re.M):
        pat = re.compile(r"^##\s*第\s*(\d+)\s*章[　\s]*(.*?)$(.*?)(?=^##\s*第\s*\d+\s*章|^# |\Z)", re.M | re.S)
        for m in pat.finditer(text):
            if int(m.group(1)) != n:
                continue
            title, body = m.group(2).strip(), m.group(3)
            g = re.search(r"\*\*核心概括\*\*[：:]\s*(.*?)(?:\n\n|\n\*\*)", body, re.S)
            desc = re.sub(r"\s+", " ", g.group(1)).strip() if g else ""
            h = re.search(r"\*\*(?:章末)?钩子\*\*[：:]\s*(.*?)(?:\n\n|\n\*\*|\Z)", body, re.S)
            htype, htext = "", ""
            if h:
                htext = re.sub(r"\s+", " ", h.group(1)).strip()
                hm = re.match(r"(.+?)[·・:：]\s*(.*)", htext)
                if hm:
                    htype, htext = hm.group(1).strip(), hm.group(2).strip()
            return title, desc, htype, htext
        return None

    # inline
    for line in text.splitlines():
        m = re.match(r"^(\d+)\s+(.*)$", line.strip())
        if not m or int(m.group(1)) != n:
            continue
        rest = m.group(2)
        body, hook = rest.split("｜钩子·", 1) if "｜钩子·" in rest else (rest, "")
        title = body.split("：", 1)[0].strip()
        desc = body.split("：", 1)[1].strip() if "：" in body else ""
        htype, htext = (hook.split("：", 1) + [""])[:2] if "：" in hook else ("", hook)
        return title, desc, htype.strip(), htext.strip()
    return None


# ---------------------------------------------------------------- 正典

def canon_sections(book, want):
    """从 canon.md 抽指定 § 段落。want 如 ['一','二','三']。want 为空则整篇。"""
    text = book.read_path("canon")
    if not text:
        return ""
    if not want:
        return text
    parts = re.split(r"^##\s+", text, flags=re.M)
    out = []
    for p in parts:
        head = p.split("\n", 1)[0].strip()
        for w in want:
            if head.startswith(w) or head.startswith(w + "、"):
                out.append("## " + p.rstrip())
                break
    return "\n\n".join(out)


def canon_ready(book):
    """正式写作要求正典已填写；初始化模板里的 ✏️ 占位符不算事实来源。"""
    text = book.read_path("canon")
    if not text or not text.strip():
        return False, "canon.md 不存在或为空"
    if "✏️" in text:
        return False, "canon.md 仍含 ✏️ 占位符（请填写或删除不用的占位行）"
    return True, ""


def style_blockers(book):
    """风格/红线层是否就绪（判据在 kit.style_doc_issues，doctor 共用同一处）。

    为什么要在接地单里拦：2026-09-20 复盘——此前只拦 canon.md，
    于是 `story-style.md`/`MASTER.md` 与模板一字不差的书也能一路写下去，
    「人味」「口癖」「红线」全是空的。规则没填，写出来的东西自然没有。
    """
    issues = kit.style_doc_issues(book.root, book.cfg)
    return [f"正式模式：{rel} —— {why}" for lvl, rel, why in issues if lvl == "block"]


def boundary_blockers(book):
    """拆书/写作边界是否做过（判据在 kit.boundary_issues）。

    预期里写了对标作品却没拆书 → 拦。这是最上游的一道：文风检查管「写得像不像人」，
    拆书管「这本书凭什么跟别人不一样」。
    """
    return [f"正式模式：{why}" for lvl, why in kit.boundary_issues(book.root, book.cfg)
            if lvl == "block"]


# ---------------------------------------------------------------- 文风红线

def style_redlines(book, full=False):
    """本章文风红线（去 AI 味）。逐条取自机检配置，与闸门同源。

    gate.draft_free=true 且未强制 full 时：返回「起草自由」瘦身版——
    不列任何风格禁令（黑名单/句式/节奏/面板），只保留硬口径（locked_details）
    与模式说明。负向禁令撤出起草上下文，是 2026-09-21 作者拍板的「起草自由、
    验收守底」管线；完稿后对账仍以「提示」报告全部风格项。

    为什么放在接地单里而不是让 agent 自己去读规则文件：规则躺在那儿不会自己进上下文。
    这一节把「本章会被机检查什么」提前说清楚——起草时就知道边界在哪，
    比写完再被闸门拦回来便宜得多。
    """
    C = book.sec("checks")
    out = []

    if not full and bool((book.sec("gate") or {}).get("draft_free")):
        out.append("起草自由模式（gate.draft_free）：动笔时**不看任何风格禁令**——"
                   "以角色的真实反应和语言的自然流动为主导；风格类项目完稿后由对账"
                   "以「提示」报告，不拦闸门。完稿自查按 story-style「节奏软目标」表算"
                   "（过闸线≠目标）。")
        for x in (C.get("locked_details") or []):
            hint = str(x[3])[:46]
            out.append(f"硬口径[{x[0]}]（违者拦，非风格项）：{hint}")
        out.append("保留豁免同旧版：留痕行尾「；保留：<理由>」，/review 逐条复核。")
        return out

    words = [str(w) for w in (C.get("blacklist") or []) if not str(w).startswith("_")]
    if words:
        out.append(f"黑名单词（{len(words)} 个，命中即记残留、过闸对撞）：" + "、".join(words))

    try:
        import consistency_check as _cc
        ids = "／".join(sid for sid, _, _ in _cc.AI_STRUCT_PATTERNS)
        out.append(f"句式禁项（{len(_cc.AI_STRUCT_PATTERNS)} 类，同类一页 ≥2 次即违规）：{ids}")
    except Exception:
        pass

    out.append("章级三限：比喻词（仿佛/似乎/宛如/犹如）一章 ≤2；"
               "「忽然/突然/猛地」同段 ≥2 即违规；章末收在悬念／动作／未尽对话，禁说教升华")

    P = C.get("panel") or {}
    if P:
        seg = f"面板文体：「{P.get('prefix', '【')}」开头的段落每章 ≤{P.get('max_lines')} 行"
        if P.get("forbid"):
            seg += "；行内禁用 " + "、".join(str(x) for x in P["forbid"])
        out.append(seg + "——面板只记账、结算、对账，不替读者下结论、不提前给答案")

    R = C.get("rhythm") or {}
    if R and R.get("enabled", True):
        out.append(f"句长节奏：叙述句均长 ≥{R.get('narr_avg_min')} 字、"
                   f"长句(>25 字) ≥{R.get('narr_long_min_pct')}%、"
                   f"≤10 字短句 ≤{R.get('short_ge10_max_pct')}%、"
                   f"对话 ≤{R.get('dialogue_max_pct')}%")

    out.append("人味：每章至少两处「没用」的东西（不推情节、不交代信息的毛边）——细则见 story-style.md")
    out.append("保留豁免：确需保留的违规处，在留痕行尾加「；保留：<理由>」"
               "——过闸降为警告，但 /review 会逐条复核")
    return out


# ---------------------------------------------------------------- 账本

def ledger_rows(book):
    L = book.sec("ledger")
    cols = L.get("columns") or []
    ch_col = L.get("chapter_column", "ch")
    text = book.read_path("ledger")
    rows = []
    if not text:
        return rows
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if not cells:
            continue
        v = kit.to_int(cells[0])
        if v is None:
            continue
        rows.append(dict(zip(cols, cells)) if cols else {"ch": cells[0]})
    rows.sort(key=lambda r: kit.to_int(r.get(ch_col)) or 0)
    return rows


def ledger_row(book, n):
    ch_col = book.sec("ledger").get("chapter_column", "ch")
    rows = ledger_rows(book)
    for i, r in enumerate(rows):
        if kit.to_int(r.get(ch_col)) == n:
            return r, (rows[i - 1] if i > 0 else None)
    return None, None


# ---------------------------------------------------------------- 伏笔

def foreshadow_hits(book, n):
    text = book.read_path("foreshadow")
    if not text:
        return []
    pat = re.compile(r"(?:^|[（(/／、,，\s\-–])%d(?:[）)/／、,，\s\-–]|$)" % n)
    return [l.strip() for l in text.splitlines()
            if l.strip().startswith("|") and "---" not in l and pat.search(l)]


# ---------------------------------------------------------------- 状态块

def state_block(book):
    text = book.read_path("now")
    if not text:
        return "（读不到 now.md）"
    m = re.search(r"<!--\s*SOLOENT-STATE\s*(.*?)-->", text, re.S)
    return m.group(1).strip() if m else "（now.md 无状态块）"


# ---------------------------------------------------------------- 入口

def main():
    argv = kit.strip_root_arg(sys.argv[1:])
    book = kit.load_book(sys.argv[1:])
    args = [a for a in argv if not a.startswith("--")]
    n = int(args[0]) if args and args[0].isdigit() else book.max_chapter() + 1
    blockers = []

    B = book.sec("book")
    print("=" * 62)
    print(f"写前接地单 ｜ 《{B.get('title', book.cfg.get('book', {}).get('slug', ''))}》第 {n} 章")
    print(f"生成自 brief.py ｜ 项目根 {book.root}")
    print("=" * 62)

    print("\n【一、细纲节拍】")
    blk = outline_block(book, n)
    if blk:
        title, desc, htype, htext = blk
        print(f"  标题：第{n:03d}章 {title}")
        if desc:
            print(f"  内容：{desc}")
        print(f"  章末钩子·{htype or '（未标）'}：{htext}")
    else:
        print(f"  ⚠ 细纲中未找到第 {n} 章条目。已查：{book.cfg.get('paths', {}).get('outline')}"
              f" ＋ outline/ 下所有 *细纲*.md —— 请在其中补齐第 {n} 章。")
        blockers.append(f"正式模式：第 {n} 章细纲未就绪")

    print("\n【二、本章数值台账（ledger.tsv）】")
    L = book.sec("ledger")
    cols = L.get("columns") or []
    note_col = L.get("note_column", "note")
    bal = L.get("balance") or {}
    cur, prev = ledger_row(book, n)
    if cur:
        show = [c for c in cols if c not in (note_col,)]
        print("  " + " ｜ ".join(f"{c} {cur.get(c) or '-'}" for c in show))
        if prev and bal.get("end") and bal.get("prev_end"):
            print(f"  上一章余额：{bal['prev_end']} {prev.get(bal['prev_end']) or '-'}")
            if bal.get("in") and bal.get("out"):
                print(f"  ★ 本章应满足：{prev.get(bal['prev_end'])} + 收入 - 支出 = 章末余额"
                      f"（写时留痕，写完补账本）")
        print(f"  备注：{cur.get(note_col) or ''}")
    else:
        print(f"  ⚠ 账本中没有第 {n} 章行——开写前先在账本里追加一行。")
        blockers.append(f"正式模式：账本未准备第 {n} 章")

    print("\n【三、相关伏笔三态（伏笔回收总表）】")
    hits = foreshadow_hits(book, n)
    if hits:
        for h in hits:
            print("  " + h)
    else:
        print("  （本章无直接标注的伏笔埋设/引爆/回收节点）")

    print("\n【四、当前进度（now.md 状态块）】")
    for l in state_block(book).splitlines():
        print("  " + l)

    print("\n【五、正典速查（canon.md 摘录——写作时以此为准）】")
    want = book.get("chapter", "brief_canon_sections") or []
    sec = canon_sections(book, want)
    ready, canon_problem = canon_ready(book)
    if sec:
        for l in sec.splitlines():
            print("  " + l)
    else:
        print("  ⚠ 读不到 canon.md")
    if not ready:
        blockers.append("正式模式：正典未就绪（" + canon_problem + "）")
    blockers.extend(style_blockers(book))
    blockers.extend(boundary_blockers(book))

    print("\n【前置体检 · 写作边界与风格层（判据与 doctor 同源）】")
    _issues = ([("style", lvl, why) for lvl, _rel, why in kit.style_doc_issues(book.root, book.cfg)]
               + [("boundary", lvl, why) for lvl, why in kit.boundary_issues(book.root, book.cfg)])
    if not _issues:
        print("  ✅ 拆书成果齐备，三份风格/红线文件均已填写")
    for _kind, _lvl, _why in _issues:
        print(("  ❌ " if _lvl == "block" else "  ⚠️ ") + _why)

    print("\n【六、本章文风红线（去 AI 味 · 起草时逐条遵守 · 与闸门同源）】")
    for l in style_redlines(book):
        print("  " + l)

    print("\n" + LINE)
    if blockers:
        for problem in blockers:
            print("拦截：" + problem)
        print("结果：❌ 写前接地失败。补齐正式资料后重跑；仅试想法请使用 tools/draft.py。")
        return 1
    print("写完本章后：跑 consistency_check.py → preflight.py；")
    print("并把本章数值补进账本、把叙述性字段回写 now.md 并把 narrative_verified 改成本章号。")
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
