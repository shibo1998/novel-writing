#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_book.py —— 对话式初始化：一条命令把一本书的骨架长出来

它做四件事：
  1. 建目录骨架（chapters / characters / world / outline / notes / 1-边界 / .soloent）
  2. 生成 `.soloent/book.json`（按题材标签自动配好规则加载清单）
  3. 复制骨架模板（canon.md / ledger.tsv / now.md / story-style.md）
  4. 生成 AGENTS.md 与 tools/ 薄壳 —— **这两样不用手写**

跑完即可直接开写；正典机检项与账本结构随写作逐步长出，不需要一开始就填满。

用法：
    python scripts/init_book.py --dir "D:/1-work/novel/我的新书" \\
        --title "我的新书" --genre "都市高武 · 校园学院流" --platform "番茄男频" \\
        --tags urban,system --words 2400 --length "100 万字以上"

    # 只出 JSON 摘要（供 agent 解析）
    python scripts/init_book.py ... --json

退出码：0=成功  2=参数或环境有问题
"""
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

# ---- 规则标签 -> 题材规则文件 ----
TAGS = {
    "urban": ("active-plugin-rules/style-urban.md", "现代都市 / 校园 / 职场等现实背景"),
    "xianxia": ("active-plugin-rules/style-xianxia.md", "修真 / 仙侠 / 东方玄幻"),
    "system": ("active-plugin-rules/style-system.md", "主角有系统 / 面板 / 数值化金手指"),
    "farming": ("active-plugin-rules/style-farming.md", "经营 / 建设 / 种田流"),
    "creative": ("active-plugin-rules/style-creative.md", "设定驱动 / 一文一大脑洞"),
}
# 通用层：所有书都加载
BASE_RULES = [
    "active-plugin-rules/anti-ai-base-constraints.md",
    "active-plugin-rules/anti-ai-character-realism.md",
    "active-plugin-rules/anti-ai-concise-writing.md",
    "active-plugin-rules/rhythm-paragraph-length.md",
    "active-plugin-rules/style-webnovel-base.md",
]
SYSTEM_RULES = ["ai-anti-patterns.md", "prose-directness.md"]
REMOVED = ["_removed/style-historical.md"]

SUBDIRS = ["chapters", "characters", "world", "outline", "notes", "review", "1-边界"]
SOLOENT_SUBDIRS = ["memory", "constitution", "rules"]

# 句长节奏的「启动档」：新书默认就开着，阈值故意放宽——目的是让这一项**可见**，
# 不是卡人。真正的阈值必须来自样板书实测（story-style.md §3.1）；拆完书按实测回调，
# 在那之前 doctor 会一直提醒「仍是启动档」。
# 为什么要有：此前 init 写的是 `"rhythm": null`——等于「节奏/清单体」这一项
# 从第一天就空转，而它正是「读起来像流水账」的主因（2026-09-20 复盘）。
RHYTHM_BOOTSTRAP = {
    "_tier": "bootstrap",
    "_comment": "启动档：只拦「明显成串的短句/清单体」。拆样板书后按实测回调（story-style.md §3.1）。",
    "enabled": True,
    "min_chars": 300,
    "min_sentences": 12,
    "narr_avg_min": 16,
    "narr_long_min_pct": 12,
    "short_ge10_max_pct": 45,
    "dialogue_max_pct": 65,
    "turn_words": list(kit.DEFAULT_TURN_WORDS),   # 表在 kit.py（唯一来源）
    "turn_min_per_1000": 0.5,
    "tag_names": [],
}

# 面板/系统流的专属机检（只在选了 system 标签时写入）
PANEL_CHECK = {
    "_comment": "系统/面板流专属：条目行数上限 + 行内禁用结论文案（面板不得替读者下结论/提前给答案）。",
    "prefix": "【",
    "max_lines": 10,
    "forbid": ["这意味着", "真相是", "原来", "其实", "实质是"],
}

# 书特有红线的「起手三条」：与题材无关，所有书都成立；作者随设定细化。
# 为什么不能留空：AGENTS.md §4「本书特有红线」与 SessionStart 注入都读它——
# 空着就是整节留白，等于没有书级约束（2026-09-20 复盘）。
STARTER_DISCIPLINE = [
    "**金手指/系统条目不得替读者下结论**：条目只给事实与数值；「这意味着…」「真相是…」式的"
    "解释交给人物行为与对话——判断留给读者，也不许提前给答案。",
    "**禁机械时间戳与倒计时**：「第X天」「倒计时」「N天后」用词极省（ai-anti-patterns.md 明令）；"
    "时间推移改用季节、光、身体状态带过。",
    "**每章至少两处「没用」的毛边**：不推情节、不交代信息的私人细节（一个没意义的小动作、"
    "一句答非所问、一个与主线无关的偏好）。细则填进 `.soloent/rules/story-style.md §五`。",
]
# 骨架模板：{模板名: 目标相对路径}
# SOLOENT.md 有 {{占位符}}，要渲染；其余是原样复制。
TEMPLATE_FILES = {
    "SOLOENT.md": "SOLOENT.md",
    "预期.md": os.path.join("1-边界", "预期.md"),
    "canon.md": os.path.join(".soloent", "canon.md"),
    "ledger.tsv": os.path.join(".soloent", "ledger.tsv"),
    "now.md": os.path.join(".soloent", "memory", "now.md"),
    "relations.md": os.path.join(".soloent", "memory", "relations.md"),
    "world-rules.md": os.path.join(".soloent", "memory", "world-rules.md"),
    "roadmap.md": os.path.join(".soloent", "memory", "roadmap.md"),
    "story-style.md": os.path.join(".soloent", "rules", "story-style.md"),
    "MASTER.md": os.path.join(".soloent", "constitution", "MASTER.md"),
    "env.example": ".env.example",
}


def backup_existing(root, relpaths):
    """备份本次将覆盖的现有文件，保持相对路径；无文件可备份时返回 None。"""
    existing = []
    for rel in sorted(set(relpaths)):
        src = os.path.join(root, rel)
        if os.path.isfile(src):
            existing.append((rel, src))
    if not existing:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(root, ".soloent", "backups", stamp)
    dst = base
    n = 2
    while os.path.exists(dst):
        dst = base + "-%d" % n
        n += 1
    for rel, src in existing:
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
    return dst


def arg(argv, name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def build_config(opts):
    """按题材标签组装 book.json。**刻意从简**：正典机检项留空，随写作长出。"""
    tags = [t.strip() for t in (opts["tags"] or "").split(",") if t.strip()]
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise ValueError(f"未知的题材标签：{'、'.join(unknown)}（可选：{'、'.join(TAGS)}）")

    load = SYSTEM_RULES + BASE_RULES + [TAGS[t][0] for t in tags]
    forbid = [TAGS[t][0] for t in TAGS if t not in tags] + REMOVED
    reason = "；".join(
        f"{t}（{TAGS[t][1]}）与本书题材不匹配" for t in TAGS if t not in tags
    ) or "无"

    cfg = {
        "_schema": 1,
        "_readme": [
            "本书唯一配置文件。所有『随书变化』的值都在这里——插件 scripts/ 下的代码不含任何本书内容。",
            "初始化时**刻意从简**：正典机检项与账本结构留空，随写作逐步长出（见 docs/03-世界观与设定.md）。",
            "改本书正典/数值/账本结构 = 改这个文件，不要改插件代码。",
            "改完跑一次：python <插件>/scripts/sync.py --root \"<本书绝对路径>\"",
        ],
        "book": {
            "title": opts["title"],
            "slug": os.path.basename(opts["dir"].rstrip("\\/")),
            "genre": opts["genre"] or "（待定）",
            "platform": opts["platform"] or "（待定）",
            "length_goal": opts["length"] or "（待定）",
            "model_note": "（待填）编辑器原用 OpenAI 兼容端点 `https://api.flashway.ai/v1`，"
                          "模型 `gemini-3.8-flash-medium`，`temperature 0`。"
                          "用其他模型亦可，但风格会漂移，需相应加强规则与技能复核。",
            "extra_discipline": list(STARTER_DISCIPLINE),
            "env_notes": [],
        },
        "vault": "$PLUGIN/assets",
        "paths": {
            "chapters": "chapters",
            "outline": "outline/第一卷-细纲.md",
            "foreshadow": "outline/伏笔回收总表.md",
            "canon": ".soloent/canon.md",
            "ledger": ".soloent/ledger.tsv",
            "now": ".soloent/memory/now.md",
            "notes": "notes",
        },
        "chapter": {
            "file_regex": "^ch-(\\d+)\\.md$",
            "title_regex": "^# 第\\d{3}章 \\S",
            "title_hint": "# 第0XX章 标题",
            "word_max": int(opts["words"]) + 600,
            "default_words": int(opts["words"]),
            "words_range": f"常态 {int(opts['words']) - 200}–{int(opts['words']) + 200}，"
                            f"区间 {int(opts['words']) - 400}–{int(opts['words']) + 600}",
            "outline_format": "auto",
            "brief_canon_sections": [],
        },
        "rules": {
            "system": SYSTEM_RULES,
            "load": load,
            "forbid": forbid,
            "forbid_reason": reason,
        },
        "state": {
            "required": ["chapter", "story_time", "location", "narrative_verified", "updated"],
            "derivable": ["chapter"],
            "manual_stamp": "narrative_verified",
            "display": ["story_time"],
            "display_extra": ["location"],
        },
        "gate": {
            "blacklist_total": 60,
            "blacklist_per_chapter": 6,
            # 注：早期这里还有个 `batch_warn: 5`，但 preflight 的批量长跑判据是**写死的**
            # 「自上次过闸后新增 ≥2 章未逐章校验即拦截」，从不读这个键——
            # 「配了没人用」比没有更坏（改它以为能放宽，其实毫无作用）。
            # 2026-09-20 起不再写出这个键。
            # 第 7 步（/review 质量审查）此前只写在文档里，从没被校验过——
            # 实测某书 69 章的 review 产物数是 0。开着它：出第 N+1 章时，
            # 闸门会要求第 N 章的评审记录已存在（notes/review-chapter-N-*.md）。
            "review_required": True,
        },
        # 写作边界：预期里声明了对标作品却没拆书 → brief 会拦（见 kit.boundary_issues）。
        # 真没样板书时，在这里写明原因即可放行——**要的是拍板留档，不是硬卡**。
        "boundary": {
            "_comment": "opt_out_reason 非空 = 明确决定不做拆书（写明为什么）；"
                        "留空时：预期声明了对标作品而 1-边界/ 又没有拆书成果 → brief 拦截。",
            "opt_out_reason": "",
        },
        # 刻意留空：等世界观/力量体系定下来，再按 docs/03 的清单逐条长出
        "checks": {
            "name_bad": {},
            "role_bad": [],
            "number_bad": [],
            "intel_bad": {},
            "gated_keywords": [],
            "unit_conflicts": None,
            "timeline_bad": [],
            "age_context_bad": [],
            "scale": None,
            "scale_near": None,
            "scale_exempt": [],
            "locked_details": [],
            "blacklist": [
                "一瞬间", "刹那", "这一刻", "那一刻", "仿佛", "似乎", "宛如",
                "缓缓", "微微", "轻轻", "静静", "深吸一口气", "眸子", "凝固",
                "顿时", "瞬间", "瞳孔骤缩", "死一般的寂静", "鸦雀无声",
                "呆若木鸡", "嘴角勾起", "倒吸一口凉气", "全场死寂",
            ],
            "elevation": {
                "words": ["从今天起", "这一刻，他不再", "命运的齿轮", "从此踏上",
                          "终将", "必将", "意义非凡", "注定"],
                "tail_lines": 3,
            },
            "rare_chars": {},
            "rare_soft": [],
            "quote_style": {
                "forbid": ["\u300c", "\u300d"],
                "reason": "正文对话一律用中文双引号“ ”，严禁直角引号「」",
            },
            "rhythm": dict(RHYTHM_BOOTSTRAP),
            # 可选机检项：默认 null = 跳过。显式写出来是为了**看得见这些开关**
            # （配了才生效；类型由 kit.config_problems 校验）。说明见各自文档。
            "panel": None,             # 面板/系统流：条目行数上限 + 行内禁结论词
            "name_roster": None,       # canon／登记册／人物卡 三方互校
            "hook_check": None,        # 细纲章末钩子 vs 正文结尾
            "foreshadow_check": None,  # 伏笔回收总表标的埋设章是否真有线索词
            "_comment_optional_checks": "以上 4 项 + rhythm 都是「配了才生效」；"
                                        "机检项清单以 consistency_check.py --list-checks 为唯一来源。",
        },
        # 账本从最简形态起步（只查「有章必有账」与章号连续）。
        # 等力量体系定了再补 balance / income / rank_order 等（见 docs/03）。
        "ledger": {
            "columns": ["ch", "day", "note"],
            "int_columns": ["ch"],
            "chapter_column": "ch",
            "note_column": "note",
            "min_columns": 2,
            "balance": None,
            "rank_column": None,
            "rank_order": None,
            "rank_ranges": None,
            "range_check": None,
            "rank_costs": None,
            "rank_max_jump": None,
            "anchors": None,
            "anchor_column": None,
            "jump_check": None,
            "income": None,
            "day_column": "day",
            "day_type": "text",
            "projection": None,
        },
    }

    # 面板/系统流专属机检：只在选了 `system` 标签时写入。
    # 别的题材写进去只会变成噪音（【】在某些排版习惯里是强调符号，不是面板条目）。
    if "system" in tags:
        cfg["checks"]["panel"] = dict(PANEL_CHECK)

    return cfg


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    force = "--force" in argv

    opts = {
        "dir": arg(argv, "--dir"),
        "title": arg(argv, "--title"),
        "genre": arg(argv, "--genre"),
        "platform": arg(argv, "--platform"),
        "tags": arg(argv, "--tags"),
        "words": arg(argv, "--words", "2400"),
        "length": arg(argv, "--length"),
    }

    def fail(msg):
        if as_json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print("⛔ " + msg)
        sys.exit(2)

    if not opts["dir"]:
        fail("缺少 --dir（书目录绝对路径）。例：--dir \"D:/1-work/novel/我的新书\"")
    root = os.path.abspath(opts["dir"])
    opts["title"] = opts["title"] or os.path.basename(root)
    try:
        opts["words"] = int(opts["words"])
    except ValueError:
        fail(f"--words 必须是整数，收到 {opts['words']!r}")

    cfg_path = os.path.join(root, kit.CONFIG_REL)
    if os.path.isfile(cfg_path) and not force:
        fail(f"{root} 已经是一本书了（存在 .soloent/book.json）。要重新初始化请加 --force")

    try:
        cfg = build_config(opts)
    except ValueError as e:
        fail(str(e))

    # 生成器自检：init 产出的配置必须能通过 kit 的结构校验（属于生成器的回归）
    problems = kit.config_problems(cfg)
    if problems:
        fail("生成的 book.json 未通过结构校验（插件 bug，请反馈）：\n   - " + "\n   - ".join(problems))

    # 在任何写入前先生成计划并验证已有 Harness JSON，非法配置绝不覆盖。
    sys.path.insert(0, kit.KIT_ROOT)
    import sync as sync_mod  # noqa: E402
    book = kit.Book(root, cfg)
    generated_plan = sync_mod.plan(book, kit.KIT_ROOT)
    try:
        sync_mod.validate_plan(root, generated_plan)
    except ValueError as e:
        fail(str(e))

    backup_dir = None
    if force:
        overwrite = [kit.CONFIG_REL] + list(TEMPLATE_FILES.values()) + list(generated_plan.keys())
        backup_dir = backup_existing(root, overwrite)

    # ---- 1. 目录骨架 ----
    created_dirs = []
    for d in SUBDIRS + [os.path.join(".soloent", s) for s in SOLOENT_SUBDIRS]:
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created_dirs.append(d)

    # ---- 2. book.json ----
    kit.atomic_write_json(cfg_path, cfg)

    # ---- 3. 骨架模板（不覆盖已存在的）----
    copied, skipped = [], []
    for src_name, rel in TEMPLATE_FILES.items():
        src = os.path.join(kit.TEMPLATES, src_name)
        dst = os.path.join(root, rel)
        if not os.path.isfile(src):
            skipped.append(f"{rel}（插件里缺 {src_name}）")
            continue
        if os.path.isfile(dst) and not force:
            skipped.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, encoding="utf-8-sig") as f:
            text = f.read()
        # SOLOENT.md 是唯一带占位符的模板：把它能填的填掉，其余留给商讨阶段
        if src_name == "SOLOENT.md":
            for k, v in {
                "title": cfg["book"]["title"],
                "genre": cfg["book"]["genre"],
                "platform": cfg["book"]["platform"],
                "length_goal": cfg["book"]["length_goal"],
                "default_words": cfg["chapter"]["default_words"],
                "words_range": cfg["chapter"]["words_range"],
                "date": datetime.date.today().isoformat(),
            }.items():
                text = text.replace("{{" + k + "}}", str(v))
        kit.atomic_write_text(dst, text)
        copied.append(rel)

    # ---- 4. AGENTS.md + tools/ 薄壳 ----
    generated = sorted(generated_plan.keys())
    for rel, want in generated_plan.items():
        sync_mod.write_generated(root, rel, want)

    # ---- 5. 部署 Rule / Workflow 到 .soloent/ ----
    # 为什么必须在这里做：`rules.load` 声明的规则要**部署进项目**才算生效
    # （SoloEnt 只从固定目录读 Rule）。此前 init 只写 AGENTS.md/tools，
    # 规则一条都没落地 → 新书的风格规则层从第一天就是空的：
    # hook 注入不到任何规则、doctor 报「声明要加载但未部署的 Rule」要你跑 sync。
    # 2026-09-20 实测：init 出来的探针书 .soloent/rules/ 里只有 story-style.md。
    dep_added, dep_skipped = sync_mod.do_deploy(book)

    result = {
        "ok": True,
        "root": root,
        "title": cfg["book"]["title"],
        "rules_loaded": len(cfg["rules"]["load"]),
        "rules_forbidden": len(cfg["rules"]["forbid"]),
        "rules_deployed": dep_added,
        "created_dirs": created_dirs,
        "templates_copied": copied,
        "templates_skipped": skipped,
        "generated": generated,
        "backup": backup_dir,
        "next": [
            "SOLOENT.md 已生成骨架 —— 它是本书的中央控制面板（8 板块），随创作逐步填充",
            "第一个决策点：先拆样板书（docs/02）还是先做世界观（docs/03）",
            "商讨阶段每定一条设定，落盘到对应文件，并同步进 SOLOENT.md 的索引",
            "每章写完必须跑：python tools/preflight.py",
        ],
    }

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    B = cfg["book"]
    print("=" * 62)
    print(f"✅ 已初始化：《{B['title']}》")
    print(f"   {root}")
    print("=" * 62)
    print(f"  题材：{B['genre']} ｜ 平台：{B['platform']} ｜ 单章 {cfg['chapter']['default_words']} 字")
    print(f"  规则：加载 {len(cfg['rules']['load'])} 条，禁载 {len(cfg['rules']['forbid'])} 条")
    print(f"  目录：新建 {len(created_dirs)} 个")
    print(f"  模板：写入 {len(copied)} 个" + (f"，跳过已存在 {len(skipped)} 个" if skipped else ""))
    print(f"  生成：{len(generated)} 个（AGENTS.md + tools/ 薄壳，**不用手写**）")
    if backup_dir:
        print(f"  备份：{backup_dir}")
    print()
    print("接下来：")
    for n in result["next"]:
        print("  · " + n)
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
