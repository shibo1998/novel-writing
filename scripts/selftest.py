#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— kit 自检：用注入故障的方式验证每一项检查真的会触发

为什么需要它：真实书稿往往是「干净」的，跑出来 0 处问题，**分不清是检查通过还是检查没跑**。
本脚本在一本临时假书上注入已知违规，逐项断言对应检查必须报出来。

用法：
    python <插件>/scripts/selftest.py
退出码：0=全部通过  1=有检查项未触发
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

CFG = {
    "_schema": 1,
    "book": {"title": "自检假书", "slug": "selftest", "genre": "测试", "platform": "测试"},
    "paths": {"chapters": "chapters", "outline": "outline/细纲.md",
              "foreshadow": "outline/伏笔.md", "canon": ".soloent/canon.md",
              "ledger": ".soloent/ledger.tsv", "now": ".soloent/memory/now.md", "notes": "notes"},
    "chapter": {"file_regex": "^ch-(\\d+)\\.md$", "title_regex": "^# 第\\d{3}章 \\S",
                "title_hint": "# 第0XX章 标题", "word_max": 3000,
                "brief_canon_sections": ["一"]},
    "state": {"required": ["chapter", "rank", "narrative_verified", "updated"],
              "derivable": ["chapter"], "manual_stamp": "narrative_verified",
              "display": ["rank"]},
    "gate": {"blacklist_total": 60, "blacklist_per_chapter": 6, "batch_warn": 5},
    "checks": {
        "name_bad": {"正名": ["错名"]},
        "role_bad": [["恶霸定位", ["错名打"]]],
        "number_bad": [["成本写错", ["错误成本"]]],
        "intel_bad": {"真相提前写死": ["真相就是这样"]},
        "gated_keywords": [["gated-a", "未解锁词", "悬空设定未解锁就使用", [], None]],
        "unit_conflicts": {"patterns": [["[一二三四五六七八九十]+点", "严重", "口径冲突示例"]],
                           "context": ["振幅"], "window": 8},
        "timeline_bad": [["三年前", "严重", "时间线硬伤示例"]],
        "age_context_bad": [["十岁", ["父亲"], "严重", "年龄×语境示例"]],
        "scale": {"F⁻": [0, 19]},
        "scale_near": {"练气": [10]},
        "scale_exempt": [],
        "locked_details": [["地点", ["药圃"], ["药田"], "唯一写法＝废弃药圃"]],
        "blacklist": ["仿佛", "缓缓"],
        "elevation": {"words": ["从今天起"], "tail_lines": 3},
        "rare_chars": {"贽": "拜师的礼"},
        "rare_soft": [],
        "quote_style": {"forbid": ["\u300c", "\u300d"], "reason": "直角引号禁用示例"},
        "rhythm": {"enabled": True, "narr_avg_min": 22, "narr_long_min_pct": 25,
                   "dialogue_max_pct": 55, "short_ge10_max_pct": 25,
                   "turn_min_per_1000": 7.0, "min_chars": 300, "min_sentences": 20,
                   "tag_names": ["甲"], "turn_words": ["但是", "可是"]},
    },
    "ledger": {
        "columns": ["ch", "day", "nights", "rank", "pts_in", "pts_out", "pts_end", "note"],
        "int_columns": ["ch", "day", "nights", "pts_in", "pts_out", "pts_end"],
        "chapter_column": "ch", "note_column": "note", "min_columns": 7,
        "balance": {"in": "pts_in", "out": "pts_out", "end": "pts_end", "prev_end": "pts_end"},
        "rank_column": "rank", "rank_order": ["凡人", "练气", "筑基"],
        "rank_ranges": None, "range_check": None, "rank_max_jump": 1,
        "rank_costs": [["练气入门", 10, "凡人"]],
        "anchors": {"1": 8}, "anchor_column": "pts_end",
        "jump_check": {"columns": ["pts_end"], "threshold": 40},
        "income": {"cap_column": "pts_in", "rate_by_column": "rank",
                   "rate_table": {"凡人": 1, "练气": 10}, "period_column": "nights",
                   "exempt_note_words": ["灵果"]},
        "day_column": "day", "day_type": "int", "projection": None,
    },
}

# 一段「每句都极短、没有转折词、没有长句」的叙述——用于触发节奏检查
FLAT = "他站着。风停了。他看天。天是灰的。他走了。路很长。他到了。门开着。" * 16

CH1 = f"""# 第001章 自检章

错名站在门口。错名打了他一拳。错误成本写在墙上。真相就是这样。
他使用未解锁词，还说振幅有九十点。
三年前父亲去世时他十岁。他去了药田。他仿佛在缓缓走来。
\u300c你来了\u300d，他说。他认得那个贽字。
他的评级是 F⁻，战力 95。练气要 500 点才能进。

{FLAT}

从今天起，他不再回头。
"""

LEDGER = """# 自检账本
1\t1\t1\t凡人\t5\t0\t7\t首章收入超上限（这一行专门验证「第一行也会被检查」）
2\t3\t1\t筑基\t0\t0\t90\t不平账 + 境界跳级 + 余额突变
"""

NOW = """<!-- SOLOENT-STATE
chapter: 1
rank: 凡人
narrative_verified: 1
updated: 2026-01-01
-->

正文
"""

# §7 故意写成第 5 章（实际只有 1 章）→ 必须被 state_sync 拦下。
# 这是官方文档点名的「最常见陷阱」：now.md 同步了、SOLOENT.md 没同步。
SOLOENT = """# 《自检假书》

## 1. Project DNA（项目基因 · 静态）

- 书名：《自检假书》

## 7. Active Writing State（当前写作状态 · 动态）

- 当前进度：第 5 章完成（这一行故意写错，用来验证滞后检查会不会触发）
- 下一步：第 6 章

## 8. Project Roadmap & Milestones（路线图 · 动态）

- 摘要：测试
"""

OUTLINE = "## 第1章 开局\n\n**核心概括**：测试\n"


def build(tmp):
    os.makedirs(os.path.join(tmp, ".soloent", "memory"), exist_ok=True)
    os.makedirs(os.path.join(tmp, ".soloent"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "chapters"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "outline"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "notes"), exist_ok=True)

    def w(rel, s):
        with open(os.path.join(tmp, rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(s)

    w(".soloent/book.json", json.dumps(CFG, ensure_ascii=False, indent=2))
    w("chapters/ch-01.md", CH1)
    w(".soloent/ledger.tsv", LEDGER)
    w(".soloent/memory/now.md", NOW)
    w("SOLOENT.md", SOLOENT)
    w("outline/细纲.md", OUTLINE)
    w(".soloent/canon.md", "## 一、人物\n\n测试\n")


# 期望触发的规则关键词 -> 说明
EXPECT_CHECKS = {
    "「错名」应为「正名」": "唯一写法",
    "人物定位越界": "人物定位红线",
    "成本写错": "数值口径硬错",
    "真相提前写死": "情报禁令",
    "悬空设定": "未解锁就使用",
    "口径冲突": "单位口径邻近判定",
    "时间线硬伤": "时间线",
    "年龄×语境": "年龄×语境交叉",
    "药田": "锁定细节",
    "仿佛": "AI 黑名单",
    "贽": "生僻字",
    "直角引号": "标点规范",
    "章末升华": "章末禁令",
    "刻度可疑": "评级刻度",
    "成本表": "成本表",
    "句长节奏": "句长节奏分轨",
    "转折词密度": "转折词密度",
}

EXPECT_LEDGER = ["不平账", "超过上限", "境界跳级", "余额突变", "锚点不符"]

# 状态同步层要触发的项（对应 state_sync.py）
EXPECT_STATE = ["SOLOENT.md §7 滞后"]


def main():
    tmp = tempfile.mkdtemp(prefix="kit-selftest-")
    try:
        build(tmp)
        ok, bad = 0, []

        rc, out, _ = kit.run_child("consistency_check.py", tmp)
        for kw, label in EXPECT_CHECKS.items():
            if kw in out:
                ok += 1
            else:
                bad.append(f"对账未触发：{label}（找 {kw!r}）")

        rc2, out2, _ = kit.run_child("ledger.py", tmp, ["--quiet"])
        for kw in EXPECT_LEDGER:
            if kw in out2:
                ok += 1
            else:
                bad.append(f"账本未触发：{kw!r}")

        # 状态同步层（含 SOLOENT.md §7 滞后检查）
        rc4, out4, _ = kit.run_child("state_sync.py", tmp)
        for kw in EXPECT_STATE:
            if kw in out4:
                ok += 1
            else:
                bad.append(f"状态同步未触发：{kw!r}")

        # 闸门必须拦下（因为存在严重问题）
        rc3, out3, _ = kit.run_child("preflight.py", tmp)
        if rc3 != 0:
            ok += 1
        else:
            bad.append("闸门未拦截有明显严重问题的书")

        print(f"自检结果：{ok} 项通过，{len(bad)} 项未触发")
        for b in bad:
            print("  ❌ " + b)
        if not bad:
            print("✅ 全部检查项均已验证会触发。")
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
