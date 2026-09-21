#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节账本校验 (ledger.py) —— 跨章对账

用途：把「前后不连贯」里**唯一能算的那一半**交给脚本算。

它管什么（可推导）：
  1. 有章必有账 —— chapters/ 有的章，账本必须有对应行（这一条最要命）
  2. 重号与缺口
  3. 余额平账   —— 上章余额 + 本章收入 - 本章支出 == 本章余额
  4. 收入合法   —— 收入不得超过「期数 × 速率」
  5. 境界单调   —— 不许倒退，一次只许跨一大境
  6. 突破成本   —— 升境界那一章的支出必须够得上正典成本表
  7. 故事日单调 —— 不许倒退；跨日幅度不得小于期数
  8. 时间空档   —— 跨了 N 天却没记够收入（额度不累计、漏掉不补）
  9. 破境日投影 —— 从账本推「哪天攒够」，与细纲写死的那天比对

它不管什么（不可推导）：这一章**实际写了什么**。那一半交给
  · brief.py（写前把该读的摆到眼前）
  · 每章一停（作者过目）

每一项都由 <书>/.soloent/book.json 的 ledger 区块开关；未配置的项自动跳过。

用法：
    python <kit>/tools/ledger.py            # 全量对账 + 投影
    python <kit>/tools/ledger.py --quiet    # 只输出结论/警告/拦截行（供闸门调用）
    python <kit>/tools/ledger.py --root <书目录>
退出码：0=平账  1=有问题
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402


def load_ledger(book):
    """-> (rows, errors)。rows 为 dict 列表，按章号升序。"""
    L = book.sec("ledger")
    path = book.path("ledger")
    cols = L.get("columns") or []
    int_cols = set(L.get("int_columns") or [])
    ch_col = L.get("chapter_column", "ch")
    note_col = L.get("note_column", "note")
    min_cols = int(L.get("min_columns", max(2, len(cols) - 1)) or 2)

    if not path or not os.path.isfile(path):
        return [], [f"账本不存在：{path}"]
    rows, errs = [], []
    with open(path, encoding="utf-8-sig") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < min_cols:
                errs.append(f"账本第 {ln} 行字段不足（需 ≥{min_cols} 列，实际 {len(parts)}）：{line[:70]}")
                continue
            d = {"_line": ln, note_col: parts[len(cols) - 1] if len(parts) >= len(cols) else ""}
            for k, v in zip(cols, parts):
                d[k] = v
            for k in int_cols:
                if k in d:
                    raw = d[k]
                    d[k] = kit.to_num(raw)
                    if d[k] is None and str(raw).strip() not in ("", "—", "-", "?"):
                        errs.append(f"账本第 {ln} 行「{k}」不是数字：{raw!r}")
            rows.append(d)
    rows.sort(key=lambda r: (r.get(ch_col) if isinstance(r.get(ch_col), int) else 10 ** 9))
    return rows, errs


def parse_outline(book):
    """细纲 -> ({章号: 故事日}, {章号: (标题, 核心概括)})。只认 projection.day_regex。"""
    P = book.sec("ledger").get("projection") or {}
    days, blocks = {}, {}
    text = book.read_path("outline")
    if not text or not P:
        return days, blocks
    pat = re.compile(r"^## 第\s*(\d+)\s*章[　\s]*(.*?)$(.*?)(?=^## 第\s*\d+\s*章|^# |\Z)", re.M | re.S)
    day_re = P.get("day_regex")
    for m in pat.finditer(text):
        n = int(m.group(1))
        title, body = m.group(2).strip(), m.group(3)
        g = re.search(r"\*\*核心概括\*\*[：:]\s*(.*?)(?:\n\n|\n\*\*)", body, re.S)
        summary = re.sub(r"\s+", " ", g.group(1)).strip() if g else ""
        blocks[n] = (title, summary)
        if day_re:
            hit = re.search(day_re, title) or re.search(day_re, summary)
            if hit:
                v = kit.cn2int(hit.group(1)) if not hit.group(1).isdigit() else int(hit.group(1))
                if v:
                    days[n] = v
    return days, blocks


def next_goal(costs, pts, rank, rank_col_value):
    """余额 -> 下一个买得起的阶段 (名称, 成本)。costs = [[名称, 成本, 前置境界], ...]"""
    for name, cost, src in costs:
        if src and src != rank_col_value:
            continue
        if pts < cost:
            return name, cost
    return None, None


def day_key(s):
    """把自由文本故事日解析成可比较的键。

    支持两种写法：
      · month_day —— '9/2'→(9,2)；'10/中'→(10,15)；'12/底'→(12,27)；'?'→None
      · int       —— 由 to_int 直接处理
    解析不出来返回 None（不判、不误杀）。
    """
    s = (s or "").strip()
    if not s or s == "?":
        return None
    m = re.match(r"^(\d{1,2})\s*[/／]\s*(初|中|底|上|下|\d{1,2})?$", s)
    if not m:
        return None
    mon = int(m.group(1))
    tail = m.group(2) or ""
    dmap = {"初": 3, "上": 3, "中": 15, "下": 25, "底": 27, "": 1}
    dd = dmap.get(tail)
    if dd is None and tail.isdigit():
        dd = int(tail)
    return (mon, dd if dd is not None else 1)


def project(book, row):
    """从最后一行往前推：按「每日修炼不辍」，哪天攒够。"""
    L = book.sec("ledger")
    P = L.get("projection") or {}
    inc = L.get("income") or {}
    costs = L.get("rank_costs") or []
    if not P or not costs or not inc.get("rate_table"):
        return None
    day_col = L.get("day_column")
    rank_col = L.get("rank_column")
    end_col = (L.get("balance") or {}).get("end") or (L.get("balance") or {}).get("prev_end")
    period_col = inc.get("period_column")
    if not (day_col and rank_col and end_col and period_col):
        return None

    day, period = row.get(day_col), row.get(period_col) or 0
    pts, rank = row.get(end_col), row.get(rank_col)
    if not isinstance(day, int) or not isinstance(pts, int):
        return None
    name, cost = next_goal(costs, pts, rank, rank)
    daily = (inc.get("rate_table") or {}).get(rank, 0)
    if cost is None or not daily:
        return None
    base_day = day - (0 if period >= 1 else 1)
    need = cost - pts
    return {"goal": name, "cost": cost, "daily": daily, "need": need,
            "days": math.ceil(need / daily), "day": base_day + math.ceil(need / daily)}


def check(book, rows, errs):
    L = book.sec("ledger")
    ch_col = L.get("chapter_column", "ch")
    note_col = L.get("note_column", "note")
    bal = L.get("balance") or {}
    rank_col = L.get("rank_column")
    rank_order = L.get("rank_order") or []
    costs = L.get("rank_costs") or []
    inc = L.get("income") or {}
    day_col = L.get("day_column")
    day_type = L.get("day_type", "text")
    warn, block = list(errs), []

    def note_of(r):
        return str(r.get(note_col) or "")

    # --- 0. 正文章号集合自身必须完整且唯一 ---
    chapter_seen = {}
    for ch, fn, _ in book.chapter_files():
        chapter_seen.setdefault(ch, []).append(fn)
    for ch, files in sorted(chapter_seen.items()):
        if len(files) > 1:
            block.append(f"正文第 {ch} 章对应 {len(files)} 个文件（重号）：" + "、".join(files))
    chapter_got = sorted(chapter_seen)
    if chapter_got:
        chapter_missing = [n for n in range(1, chapter_got[-1] + 1) if n not in chapter_seen]
        if chapter_missing:
            block.append("正文章号有缺口：" + "、".join(str(n) for n in chapter_missing))

    # 文件名决定工具识别出的章号；标题也必须声明同一章号，否则读者与工具看到的是两本账。
    title_number_pattern = book.get(
        "chapter", "title_number_regex", r"^\s*#\s*第\s*0*(\d+)\s*章(?:\s|$)"
    )
    try:
        title_number_re = re.compile(title_number_pattern)
    except re.error as e:
        block.append(f"chapter.title_number_regex 无效：{e}")
        title_number_re = None
    # 中文数字章号兜底（2026-09-21）：仙侠类书的标题是「第一章　药圃」（中文数字+全角空格），
    # 默认正则只认阿拉伯数字——这类书的 preflight 从装机起就没通过过（实测发现）。
    # 书级 title_number_regex 仍优先；这里只做**识别不出时的兜底**，不改变书级配置语义。
    _CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9}

    def _cn_to_int(s):
        """中文数字 → int（支持 一~九百九十九；转换不了返回 None）。"""
        if s.isdigit():
            return int(s)
        total, num = 0, 0
        for chn in s:
            if chn in _CN_DIGITS:
                num = _CN_DIGITS[chn]
            elif chn == "十":
                total += (num or 1) * 10
                num = 0
            elif chn == "百":
                total += (num or 1) * 100
                num = 0
            else:
                return None
        return total + num if (total or num) else None

    _cn_title_re = re.compile(r"^\s*#\s*第\s*([0-9一二三四五六七八九十百零两]+)\s*章")

    if title_number_re is not None:
        for file_ch, fn, text in book.chapter_files():
            first_line = text.splitlines()[0] if text.splitlines() else ""
            hit = title_number_re.search(first_line)
            title_ch = None
            if hit:
                try:
                    title_ch = int(hit.group(1))
                except (IndexError, TypeError, ValueError):
                    title_ch = None
            if title_ch is None:
                cn_hit = _cn_title_re.search(first_line)
                if cn_hit:
                    title_ch = _cn_to_int(cn_hit.group(1))
            if title_ch is None:
                block.append(f"无法从标题识别章号：{fn}")
                continue
            if title_ch != file_ch:
                block.append(f"{fn} 文件名章号 {file_ch} 与标题章号 {title_ch} 不一致")

    # --- 1. 账本自身的重号与缺口 ---
    seen = {}
    for r in rows:
        seen.setdefault(r.get(ch_col), []).append(r)
    for ch, dup in seen.items():
        if isinstance(ch, int) and len(dup) > 1:
            block.append(f"账本第 {ch} 章出现 {len(dup)} 次（重号）")
    got = sorted(k for k in seen if isinstance(k, int))
    if got and got[0] != 1:
        block.append(f"账本必须从第 1 章开始，当前首章为 {got[0]}")
    if got and got != list(range(got[0], got[-1] + 1)):
        miss = [n for n in range(got[0], got[-1] + 1) if n not in seen]
        if miss:
            block.append("账本章号有缺口：" + "、".join(str(n) for n in miss))

    # --- 2. 正文与账本必须是同一个章号集合（不能只比较最大章号）---
    chapter_set = set(chapter_seen)
    ledger_set = set(got)
    missing_in_ledger = sorted(chapter_set - ledger_set)
    extra_in_ledger = sorted(ledger_set - chapter_set)
    if missing_in_ledger:
        block.append("★ 正文有、账本缺：" + "、".join(str(n) for n in missing_in_ledger))
    if extra_in_ledger:
        block.append("★ 账本有、正文缺：" + "、".join(str(n) for n in extra_in_ledger))

    # --- 4. 收入合法（独立一轮，覆盖全部行）---
    # 为什么独立成轮：原来写在「逐行对比」的循环里，于是**第一行永远不被检查**——
    # 首章的收入可以无限大而无人发现。收入上限只需要「本行 + 上一行的境界」，不需要配对比较。
    if inc.get("cap_column") and inc.get("rate_table"):
        for i, r in enumerate(rows):
            prev = rows[i - 1] if i > 0 else None
            rn = r.get(inc.get("period_column")) or 0 if inc.get("period_column") else 0
            src_rank = (prev or r).get(inc.get("rate_by_column"))
            daily = (inc["rate_table"] or {}).get(src_rank)
            if not daily or not isinstance(r.get(inc["cap_column"]), (int, float)):
                continue
            exempt = any(w in str(r.get(note_col) or "") for w in (inc.get("exempt_note_words") or []))
            cap = rn * daily
            if r[inc["cap_column"]] > cap and not exempt:
                warn.append(f"第 {r.get(ch_col)} 章收入 {r[inc['cap_column']]} 超过上限："
                            f"{rn} × {daily} = {cap}（若来自额外来源，请在账本备注里写明）")
            if r[inc["cap_column"]] > 0 and rn == 0 and not exempt:
                warn.append(f"第 {r.get(ch_col)} 章没有期数却有 {r[inc['cap_column']]} 收入"
                            f"——额度不累计，不修就没有")

    # --- 逐行 ---
    for i in range(1, len(rows)):
        p, r = rows[i - 1], rows[i]
        rn = r.get(inc.get("period_column")) or 0 if inc.get("period_column") else 0
        daily = (inc.get("rate_table") or {}).get(p.get(rank_col)) if inc.get("rate_table") else None
        exempt = any(w in note_of(r) for w in (inc.get("exempt_note_words") or []))

        # 3. 平账
        if bal.get("end") and bal.get("prev_end") and bal.get("in") and bal.get("out"):
            if all(isinstance(r.get(k), int) for k in (bal["in"], bal["out"], bal["end"])) \
               and isinstance(p.get(bal["prev_end"]), int):
                expect = p[bal["prev_end"]] + r[bal["in"]] - r[bal["out"]]
                if expect != r[bal["end"]]:
                    block.append(f"第 {r.get(ch_col)} 章不平账：上章余额 {p[bal['prev_end']]} "
                                 f"+ 收 {r[bal['in']]} - 支 {r[bal['out']]} = {expect}，"
                                 f"账本却写 {r[bal['end']]}")

        # 5. 境界单调 / 6. 突破成本
        if rank_col and rank_order:
            rp, rr = p.get(rank_col), r.get(rank_col)
            ip = rank_order.index(rp) if rp in rank_order else None
            ir = rank_order.index(rr) if rr in rank_order else None
            if ip is None or ir is None:
                warn.append(f"第 {r.get(ch_col)} 章境界写法不在阶梯里：{rp!r} → {rr!r}"
                            f"（正典：{'/'.join(rank_order)}）")
            elif ir < ip:
                block.append(f"第 {r.get(ch_col)} 章境界倒退：{rp} → {rr}")
            elif ir - ip > 1:
                max_jump = L.get("rank_max_jump", 1)
                if max_jump is not None and ir - ip > max_jump:
                    warn.append(f"第 {r.get(ch_col)} 章境界跳级：{rp} → {rr}"
                                f"（正典：一次最多跨 {max_jump} 档）")
            elif ir > ip:
                need = next((c for n, c, _ in costs if n and str(n).startswith(str(rr))), None)
                if need is not None and bal.get("out") and isinstance(r.get(bal["out"]), int) \
                        and r[bal["out"]] < need:
                    block.append(f"第 {r.get(ch_col)} 章由 {rp} 升 {rr}，支出 {r[bal['out']]}，"
                                 f"低于正典成本 {need}")

        # 6b. 数值是否落在该行评级的区间内（如「战力 12」必须落在 F⁻(0–19)）
        rc = L.get("range_check") or {}
        if not rc.get("ranges") and L.get("rank_ranges"):
            # 回退：`rank_ranges` 是初始化生成的简化版（只有「档位 → 区间」）。
            # 补上缺的两个键就能当 `range_check` 用 —— 否则这个字段配了等于没配：
            # 高武填了 20 档，而实际生效的 `range_check` 只有 15 档，后 5 档静默不检查。
            rc = {"value_column": L.get("power_column") or "power",
                  "range_by_column": L.get("rank_column") or "rank",
                  "ranges": L.get("rank_ranges")}
        if rc.get("value_column") and rc.get("range_by_column") and rc.get("ranges"):
            v = r.get(rc["value_column"])
            g = r.get(rc["range_by_column"])
            rng = (rc["ranges"] or {}).get(g)
            if isinstance(v, (int, float)) and rng:
                lo, hi = rng[0], rng[1]
                if not (lo <= v <= hi):
                    warn.append(f"第 {r.get(ch_col)} 章 {rc['range_by_column']}={g}，"
                                f"但 {rc['value_column']}={v} 不在该档区间 {lo}–{hi} 内——请核对")

        # 7. 故事日单调 / 8. 时间空档
        if day_col:
            if day_type == "int":
                pd, rd = p.get(day_col), r.get(day_col)
            else:
                # month_day 等自由文本：解析成可比较的键；解析不出的行跳过（不误杀）
                pd, rd = day_key(p.get(day_col)), day_key(r.get(day_col))
            if isinstance(pd, int) and isinstance(rd, int) and day_type == "int":
                span = rd - pd
                if span < 0:
                    block.append(f"第 {r.get(ch_col)} 章故事日倒退：第 {pd} 日 → 第 {rd} 日")
                elif inc.get("period_column") and span < rn:
                    warn.append(f"第 {r.get(ch_col)} 章跨了 {span} 日却记了 {rn} 期——期数不可能多于所跨天数")
                elif span > 0 and daily and inc.get("cap_column"):
                    practice = span - (0 if rn >= 1 else 1)
                    expect_in = practice * daily
                    told = any(w in note_of(r) for w in
                               ("未修炼", "没修", "断了", "停", "病", "伤", "没练", "跳"))
                    if expect_in > 0 and isinstance(r.get(inc["cap_column"]), int) \
                            and r[inc["cap_column"]] < expect_in and not told:
                        warn.append(
                            f"第 {p.get(ch_col)}→{r.get(ch_col)} 章之间空了 {span} 天"
                            f"（第 {pd} → 第 {rd} 日），按「每日不辍」应有 {expect_in}，"
                            f"账上只有 {r[inc['cap_column']]}。额度不累计、漏掉不补——"
                            f"是有意为之（在账本备注写明），还是漏记了？")
            elif pd is not None and rd is not None and rd < pd:
                block.append(f"★ 故事日回退：第 {p.get(ch_col)} 章 → 第 {r.get(ch_col)} 章"
                             f"（{r.get(day_col)}）")
            if not str(r.get(day_col) or "").strip():
                warn.append(f"第 {r.get(ch_col)} 章「{day_col}」为空")

        # 7b. 突破锚点：见下方独立循环（同样要覆盖第一行）

        # 7c. 余额突变：见下方独立循环（需跨行追踪「上一个有效值」，不能只看相邻行）

    # --- 8a. 突破锚点：指定章号的指定列必须等于正典值（独立一轮，覆盖全部行）---
    anchors = L.get("anchors") or {}
    anchor_col = L.get("anchor_column")
    if anchors and anchor_col:
        want_by_ch = {int(k): v for k, v in anchors.items()}
        for r in rows:
            ch_v = r.get(ch_col)
            if ch_v not in want_by_ch:
                continue
            want, got = want_by_ch[ch_v], r.get(anchor_col)
            if got is None or str(got).strip() == "":
                warn.append(f"锚点章第 {ch_v} 章 {anchor_col} 未填（正典锚点 {want}）")
            elif isinstance(got, (int, float)) and abs(got - want) > 1e-6:
                block.append(f"★ 锚点不符：第 {ch_v} 章 {anchor_col} 账本 {got}，正典要求 {want}")

    # --- 8b. 余额突变（警告不拦截）---
    # 关键：要跟**上一个有效值**比，不能只看相邻行——账本里大量行写「—」或「?」
    # （表示该章无面板数值记载），只看相邻行会把真正的突变整段漏掉。
    jc = L.get("jump_check") or {}
    if jc.get("columns") and jc.get("threshold"):
        last_valid = {}
        for r in rows:
            for col in jc["columns"]:
                v = r.get(col)
                if not isinstance(v, (int, float)):
                    continue
                if col in last_valid and abs(v - last_valid[col]) > jc["threshold"]:
                    warn.append(f"第 {r.get(ch_col)} 章 {col} 余额突变 "
                                f"{last_valid[col]:g} → {v:g}（> {jc['threshold']:g}），请核对来源")
                last_valid[col] = v

    # --- 9. 破境日投影 ---
    proj = None
    if rows:
        proj = project(book, rows[-1])
        if proj:
            o_days, o_blocks = parse_outline(book)
            P = (L.get("projection") or {})
            kws = P.get("goal_words") or ["破境", "突破"]
            target = None
            for n in sorted(o_blocks):
                if n <= rows[-1].get(ch_col, 0):
                    continue
                title, summary = o_blocks[n]
                if any(k in title or k in summary for k in kws):
                    target = n
                    break
            if target and target in o_days and o_days[target] != proj["day"]:
                warn.append(f"★ 破境日对不上：按账本估算落在第 {proj['day']} 日"
                            f"（第 {rows[-1][ch_col]} 章末 {rows[-1][(L.get('balance') or {}).get('end')]}，"
                            f"还需 {proj['need']}，{proj['daily']}/天 → {proj['days']} 天），"
                            f"但细纲第 {target} 章写的是第 {o_days[target]} 天。"
                            f"两处必须有一处改，别拖到写那一章时才发现")
    return warn, block, proj


def main():
    argv = kit.strip_root_arg(sys.argv[1:])
    book = kit.load_book(sys.argv[1:])
    quiet = "--quiet" in argv
    L = book.sec("ledger")
    ch_col = L.get("chapter_column", "ch")
    bal = L.get("balance") or {}

    rows, errs = load_ledger(book)
    warn, block, proj = check(book, rows, errs)
    if not rows:
        block.append("账本没有有效数据行——这一层记忆是空的，无法对账")

    if not quiet:
        cur = book.max_chapter()
        print("=" * 66)
        tail = f"（至第 {rows[-1][ch_col]} 章）" if rows else ""
        print(f"章节账本 ｜ chapters/ 至第 {cur} 章 ｜ 账本 {len(rows)} 行{tail}")
        print("=" * 66)
        cols = L.get("columns") or []
        for r in rows:
            cells = " ｜ ".join(f"{c}={r.get(c)}" for c in cols if c != L.get("note_column"))
            print(f"  {cells} ｜ {str(r.get(L.get('note_column')) or '')[:32]}")
        if proj:
            print("-" * 66)
            print(f"  投影：还需 {proj['need']}（{proj['daily']}/天 × {proj['days']} 天）"
                  f"→【{proj['goal']}】落在第 {proj['day']} 日")
    for w in warn:
        print("警告：" + w)
    if block:
        for b in block:
            print("拦截：" + b)
        print("\n结果：❌ 未平账。")
        return 1
    print(f"\n结果：✅ 平账。（{len(rows)} 章" + (f"，投影第 {proj['day']} 日）" if proj else "）"))
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
