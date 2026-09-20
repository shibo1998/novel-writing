#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
状态同步校验 (state_sync.py)

用途：解决「章节写完了，但进度/状态没回写进 now.md」——把 now.md 的状态块与
      chapters/ 的真实情况做机检，不一致就报错（可被 preflight.py 拦下）。

原理：now.md 里维护一个结构化状态块（HTML 注释，不干扰阅读）：

    <!-- SOLOENT-STATE
    chapter: 30
    rank: E⁻
    power: 63.2
    story_time: 高二上 · 集训终期考核后
    location: 临江 · 青峰山集训基地
    narrative_verified: 30
    updated: 2026-09-18
    -->

  · **可机检的是「可推导字段」**（如 chapter）——由本脚本自动对齐。
  · **叙述性字段**（rank/power/…）由作者或 agent 填写，本脚本只保证它们**存在**且**章节号对得上**。
  · `narrative_verified` 是「叙述性字段已回写」的**人工确认戳**，`--fix` 不会碰它——
    因此跑 `--fix` 无法绕过这道（防止「章节号修好了、状态其实是旧的」）。
  · **状态块含重复键一律报错**：同一键出现多行时，本脚本取末值、doctor 取首值，
    两个工具会读出不同章号（2026-09-20 事故）。`--fix` 可去重为单份。

字段契约由 <书>/.soloent/book.json 的 state 区块定义。

用法：
    python <kit>/tools/state_sync.py           # 校验；不一致退出码 1
    python <kit>/tools/state_sync.py --fix     # 自动修可推导字段并刷新基线
    python <kit>/tools/state_sync.py --root <书目录>
退出码：0=一致  1=不一致
"""
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

BLOCK_RE = re.compile(r"<!--\s*SOLOENT-STATE\s*(.*?)-->", re.S)


def chapter_hashes(book):
    """{文件名: 内容 sha256}。用于区分「正文被改了」与「只是 mtime 被碰了」。"""
    out = {}
    d = book.chapters_dir
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if not os.path.isfile(p) or not book.file_regex.match(fn):
            continue
        with open(p, "rb") as f:
            out[fn] = hashlib.sha256(f.read()).hexdigest()
    return out


def hash_path(book):
    return os.path.join(book.soloent, "state_hashes.json")


def load_hashes(book):
    p = hash_path(book)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def save_hashes(book, h):
    """基线哈希落盘。持 `state` 项目锁 + 原子替换（报告 NW-P1-008）：
    state_hashes.json 曾是无锁直写——并发入口同时保存会互相覆盖或留下半截 JSON。
    锁名与 preflight 的编排锁不同，子检查在编排锁内运行时不会自锁。"""
    p = hash_path(book)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with kit.project_lock(book.root, "state", timeout=30):
        kit.atomic_write_json(p, h)


def parse_block(text):
    """解析状态块。返回 (字段字典, 块区间, 重复键列表)。

    重复键为什么要单独报（2026-09-20 事故）：状态块被**逐章追加**而不是就地更新时，
    同一个键会出现多行（本书 now.md 里 `chapter:` 出现了 10 次）。
    dict 赋值是「后值覆盖前值」，本脚本读到的是最后一章；
    而 doctor.py 用的是 `re.search`，读到的是**第一个**匹配 —— 两个工具对同一份文件
    得出不同结论（doctor 报"now.md 写第 60 章，正文已到第 69 章"，实际是 69）。
    同一事实两个口径 = 机制失效。所以重复键一律报错，不许悄悄覆盖。
    """
    m = BLOCK_RE.search(text)
    if not m:
        return None, None, []
    data, dup = {}, []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k in data:
            dup.append(k)
        data[k] = v.strip()
    return data, (m.start(), m.end()), dup


def render_block(fields, d):
    return "<!-- SOLOENT-STATE\n" + "\n".join(f"{k}: {d.get(k, '')}" for k in fields) + "\n-->"


def soloent_chapter(book):
    """从 SOLOENT.md 的「活跃写作状态」板块抽章节号。

    为什么查这个：官方文档把「让 §7 过时」列为最常见的陷阱——写了 3 章却不更新状态，
    AI 就会在你实际写到第 8 章时写得像还在第 2 章。
    而 now.md 同步了、SOLOENT.md 没同步，是很容易发生的事（两处都要写）。

    返回 (章节号 or None, 无法解析的原因)。
    """
    p = os.path.join(book.root, "SOLOENT.md")
    if not os.path.isfile(p):
        return None, "SOLOENT.md 不存在"
    try:
        with open(p, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as e:
        return None, f"读不了（{e}）"
    # 抓「## 7. xxx」到下一个二级标题之间
    m = re.search(r"^##\s*7[\.、]?\s*(.+?)$(.*?)(?=^##\s*\d|^---\s*$|\Z)", text, re.M | re.S)
    if not m:
        return None, "找不到 §7 板块"
    body = m.group(2)

    # 逐行找「第 N 章」。要同时容忍两种写法——
    #   · markdown 加粗：`第 **31** 章`
    #   · 中文数字：`第二章`
    # 早期版本只认阿拉伯数字，结果跳过了「当前进度」那行（写的是「第二章」），
    # 误取了「下一步」里的章号，报出错误的滞后结论。
    NUM = re.compile(r"第\s*\**\s*(\d+)\s*\**\s*章")
    CN = re.compile(r"第\s*([一二三四五六七八九十百]+)\s*章")

    def nums_in(line):
        out = [int(x) for x in NUM.findall(line)]          # 阿拉伯优先
        out += [v for v in (kit.cn2int(x) for x in CN.findall(line)) if v]
        return out

    cands = [(line, nums_in(line)) for line in body.splitlines()]
    cands = [(l, n) for l, n in cands if n]
    if not cands:
        return None, "§7 里没有可识别的「第 N 章」"

    # 优先「当前进度 / 当前章节 / 已完成」这类行；否则取第一处
    KEY = ("当前进度", "当前章节", "最新进度", "已完成")
    for line, nums in cands:
        if any(k in line for k in KEY):
            return nums[0], ""
    return cands[0][1][0], ""


def main():
    argv = kit.strip_root_arg(sys.argv[1:])
    book = kit.load_book(sys.argv[1:])
    fix = "--fix" in argv
    S = book.sec("state")
    fields = S.get("required") or ["chapter", "narrative_verified", "updated"]
    derivable = S.get("derivable") or ["chapter"]
    stamp = S.get("manual_stamp", "narrative_verified")
    # display_extra 是「次要显示字段」（如 stats／assets）——原先只读 display，
    # 这个字段配了没人用，作者填了却在摘要里看不到。
    display = (S.get("display") or []) + (S.get("display_extra") or [])

    now_path = book.path("now")
    cur = book.max_chapter()
    problems, notes, warns = [], [], []

    if not now_path or not os.path.isfile(now_path):
        print(f"⛔ 找不到 {now_path}")
        return 1
    with open(now_path, encoding="utf-8-sig") as f:
        text = f.read()

    data, span, dup_keys = parse_block(text)

    # --- 1. 状态块是否存在 ---
    if data is None:
        problems.append("now.md 缺少 SOLOENT-STATE 状态块（本机制无法校验进度同步）")
        if fix:
            data = {k: "" for k in fields}
            for k in derivable:
                data[k] = str(cur)
            data[stamp] = "0" if cur == 0 else ""
            data["updated"] = datetime.date.today().isoformat()
            blk = render_block(fields, data)
            text = blk + "\n\n" + text
            with open(now_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            notes.append("已插入空状态块（叙述性字段待填；填好后把 "
                         f"{stamp} 改成章节号）")
            span = (0, len(blk))
            dup_keys = []
        else:
            print("⛔ " + problems[0])
            print("   运行 `--fix` 可插入状态块。")
            return 1

    # --- 1b. 重复键（后值覆盖前值；doctor 取首个、本校验取末个，口径必须统一）---
    if dup_keys:
        names = "、".join(sorted(set(dup_keys)))
        if fix:
            # 去重 = 用（后值胜出的）data 按首次出现顺序重渲染整块
            blk = render_block(list(data.keys()), data)
            text = text[:span[0]] + blk + text[span[1]:]
            with open(now_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            span = (span[0], span[0] + len(blk))
            notes.append(f"状态块重复键已去重（{names}）——保留最后一次写入的值")
        else:
            problems.append(
                f"状态块含重复键：{names}——后值覆盖前值，"
                f"且 doctor（取首个）与本校验（取末个）读出不同章号。"
                f"请把状态块去重为单份（每键一行）；跑 `--fix` 可自动去重")

    # --- 2. 缺字段 ---
    missing = [k for k in fields if not data.get(k)]
    if missing:
        problems.append("状态块缺字段：" + "、".join(missing))

    # --- 3. ★ 可推导字段与实际章节数是否一致 ---
    for k in derivable:
        try:
            declared_n = int(str(data.get(k, "")).strip())
        except (TypeError, ValueError):
            declared_n = -1
        if declared_n != cur:
            if fix:
                data[k] = str(cur)
                data["updated"] = datetime.date.today().isoformat()
                text = text[:span[0]] + render_block(fields, data) + text[span[1]:]
                with open(now_path, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                notes.append(f"已把 {k} 从「{declared_n if declared_n >= 0 else '空'}」修正为「{cur}」")
                declared_n = cur
            else:
                problems.append(f"★ 进度未同步：`chapters/` 已有 {cur} 章，但 now.md 写着 {k}: "
                                f"{data.get(k) or '空'}")

    # --- 4. ★ 叙述性字段是否已回写确认 ---
    try:
        nv = int(str(data.get(stamp, "")).strip())
    except (TypeError, ValueError):
        nv = -1
    expect = cur
    if nv != expect:
        problems.append(
            f"★ 第 {expect} 章的叙述性字段未回写确认（{stamp}={data.get(stamp) or '空'}）——"
            f"请把 {'、'.join(f for f in fields if f not in derivable and f not in (stamp, 'updated'))} "
            f"补成当前状态，并把 {stamp} 改成 {expect}")

    # --- 5. 正文在上次核对之后是否又被改过 ---
    h_now = chapter_hashes(book)
    baseline = load_hashes(book)
    if baseline is None:
        notes.append(f"已建立正文内容基线（{len(h_now)} 章）——本次不判定「记忆是否滞后」")
    else:
        stale = []
        for fn, h in h_now.items():
            if baseline.get(fn) == h:
                continue
            p = os.path.join(book.chapters_dir, fn)
            try:
                if os.path.getmtime(p) > os.path.getmtime(now_path) + 2:
                    stale.append(fn)
            except OSError:
                pass
        if stale:
            problems.append("★ 以下章的正文明细在上次核对之后被改过，而 now.md 未再更新："
                            + "、".join(sorted(stale))
                            + f"——请把状态补成当前值，再把 {stamp} 改成最新章号")

    # --- 6. ★ SOLOENT.md §7 是否同步（中央面板过时是最常见的陷阱）---
    s7, why = soloent_chapter(book)
    if s7 is None:
        warns.append(f"SOLOENT.md §7 无法校验：{why}——建议在该节按「第 N 章」的格式写当前进度")
    elif s7 != cur:
        problems.append(
            f"★ SOLOENT.md §7 滞后：中央面板写的是第 {s7} 章，实际已到第 {cur} 章"
            f"——面板过时会让 AI 按旧进度写。请把 §7 的当前进度改成第 {cur} 章")

    # --- 输出 ---
    print("=" * 58)
    print(f"状态同步 ｜ chapters/ 至第 {cur} 章 ｜ now.md 声明第 {data.get(derivable[0]) if derivable else '—'} 章")
    if display:
        print(" ｜ ".join(f"{f} {data.get(f) or '—'}" for f in display))
    print("=" * 58)
    for n in notes:
        print("已修：" + n)
    for w in warns:
        print("警告：" + w)
    if problems:
        for p in problems:
            print("问题：" + p)
        print("\n结果：❌ 状态未同步。")
        print("提示：`--fix` 只能修可推导字段；叙述性字段需回写 now.md；"
              "SOLOENT.md §7 需手工同步摘要。")
        return 1
    print("\n结果：✅ 状态一致。")
    save_hashes(book, h_now)
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
