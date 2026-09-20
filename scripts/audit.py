#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件自审计（audit.py）—— 检查**插件自身**是否自洽。

与 `doctor.py` 的分工（两个不同的「体检」）：
  · `doctor.py`  检查**某一本书**的防护是否在生效 —— 要先有书
  · `audit.py`   检查**插件本身**是否完整自洽 —— 不需要书

为什么需要它：插件的正确性依赖「模板 / 技能 / 文档 / 脚本 / 生成物」五者互相对得上。
这类不一致**不会报错**，只会让 agent 读到过时路径、找不到文件、或按错的结构干活——
2026-09-19 实测抓到两个真 bug（见下 D2 / E2），都是这一类。

检查项：
  A1 模板缺失（init_book 要复制但文件不在）
  A2 孤儿模板（文件在但 init_book 不引用）
  A3 模板占位符无对应渲染值（会原样漏进生成物）
  B1 技能缺 SKILL.md
  B2 技能内引用断链
  C1 docs 引用断链
  D1 脚本语法错误
  D2 **生成物含未渲染占位符**（真建一本临时书跑一遍再检查）★
  D3 **流程冒烟**（建书 → 造章 → 回写 → 过闸，验证主干路走得通）★
  E1 孤儿技能（从未被主流程或 docs 引用）
  E2 **过时路径**（如 `.novelkit` —— 上一代目录名，读到会让 agent 找错地方）
  F1 **死字段**（book.json 的 schema 里有、但脚本没人读）

用法：
    python scripts/audit.py            # 全量
    python scripts/audit.py --no-render  # 跳过 D2（不建临时书，更快）
退出码：0=无问题  1=有问题
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

ROOT = kit.PLUGIN_ROOT          # 插件根（scripts/ 的上一级）
ASSETS = os.path.join(ROOT, "assets")
SKILLS = os.path.join(ROOT, "assets", "skills")
DOCS = os.path.join(ROOT, "docs")
TEMPLATES = os.path.join(ROOT, "templates")

# 与 init_book.py 的 SOLOENT.md 渲染字典保持同步（那边加了字段这里也要加）
TEMPLATE_KEYS = {"title", "genre", "platform", "length_goal",
                 "default_words", "words_range", "date"}

# 这些是「每本书里的文件/目录」，插件目录下不存在是正常的
BOOK_FILES = {
    "SOLOENT.md", "AGENTS.md", "book.json", "canon.md", "ledger.tsv", "now.md",
    "relations.md", "world-rules.md", "roadmap.md", "story-style.md", "MASTER.md",
    "预期.md", "checkpoint.json", "state_hashes.json", "对账日志.md",
    "伏笔回收总表.md", "watcher.pid",
    # 纯生成物（插件里没有同名文件，由 sync.py 写进每本书的 tools/）
    "start_watcher.ps1", "stop_watcher.ps1",
}
BOOK_PREFIX = ("outline/", "world/", "characters/", "chapters/", "notes/", "1-边界/",
               ".soloent/", "review/", "summary_cache/", "_tools/", "tools/",
               ".novelkit/", "2-设定/", "3-大纲/", "4-正文/", "5-审查/", ".zcode/",
               "novel/")     # 文档里举的「作品库/书名/…」示例路径

# 文档里的「占位符式引用」，不是真路径
PLACEHOLDER_REF = re.compile(r"(^|/)X\.|\*")
# 拆书产物命名（1.2_文风.md / 1.4_框架.md …）—— 生成在每本书的 1-边界/ 下
BREAKDOWN_REF = re.compile(r"^\d+\.\d+_")

# 已废弃的路径：出现即报（读到会让 agent 找错地方）
# `plugins-manifest.md` / `assets/plugins` 是 2026-09-20 抓到的：AGENTS.template.md 的
# 第 3 步「技能三连」让 agent 去那里查路径，而目录其实叫 skills/、清单文件根本不存在。
STALE_PATHS = [".novelkit", "character_state.md", "foreshadowing.md",
               "plugins-manifest.md", "assets/plugins", "_agent/"]


def is_book_ref(ref):
    if os.path.basename(ref) in BOOK_FILES:
        return True
    return any(ref.startswith(p) for p in BOOK_PREFIX)


def build_index():
    """插件内所有文件的 basename 索引 —— 短名引用只要能搜到就不算断链。"""
    idx = {}
    for dp, dn, fn in os.walk(ROOT):
        if any(x in dp for x in (".git", ".workbuddy-ai", "__pycache__")):
            continue
        for f in fn:
            idx.setdefault(f, []).append(os.path.join(dp, f))
    return idx


def _smoke(tmp, add):
    """流程冒烟：造一章 → 回写状态／账本／留痕 → 过闸。

    验证的是**链路有没有断**，不是文笔。D2 只检查「生成物干不干净」，
    本项再往前一步：真走一遍「写章 → 回写 → 过闸」，确保这条主干路走得通。
    （2026-09-19 的 `stop_watcher.ps1` 损坏、`brief.py` 细纲探测失效，
    都是「静态看没问题、真跑才暴露」那一类。）
    """
    # 1) 造一章（标题要匹配 title_regex）
    # ⚠️ 正文必须是「真能过闸的干净章」的样子，不能是同一句复读 24 遍：
    #    2026-09-20 init 默认开启句长节奏机检（启动档）后，那种复读文本会因
    #    「无长句／无转折词」被判 2 处严重——冒烟测的是**链路通不通**，
    #    不是「闸门会不会误伤假文本」。所以这里写一段句长有变化、
    #    带几个转折词的正常叙述，且不含黑名单词／比喻词／章末升华词／直角引号。
    ch = os.path.join(tmp, "chapters", "ch-01.md")
    os.makedirs(os.path.dirname(ch), exist_ok=True)
    body = (
        "夜里十一点半，林澈把最后一单送进城西老巷，回来时车轮碾过积水，"
        "车架上的反光条在路灯下一明一暗。\n\n"
        "他坐在出租屋的床沿上，把今天的账从头到尾捋了一遍：房租两千六，"
        "助学贷款月供八百九，现金余额只剩三十七块五。\n\n"
        "但是他没有慌。七个月跑单教会他一件事——任何东西都要留第二本账，"
        "账目清楚，日子才不至于被一笔意外的支出压垮。\n\n"
        "不过今天多了一笔谁都没想到的收入：跨区配送费十九块五。"
        "他把这笔钱单独记了一行，又在旁边画了个小圈，提醒自己明天去问问平台。\n\n"
        "窗外传来水管的声音。楼道里有人上楼，脚步声一慢一快。"
        "林澈合上本子，把手机屏幕按灭，屋里重新暗下来，"
        "只剩窗帘缝里那一道路灯的光斜斜地铺在地板上。\n\n"
        "他忽然想起下午那个小姑娘塞给他的水果糖，"
        "就从胸袋里把它摸出来，摆在账本旁边。\n"
    )
    with open(ch, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 第001章 冒烟测试\n\n" + body)
    # 2) 回写 now.md（章号／人工戳／留痕行）
    nowp = os.path.join(tmp, ".soloent", "memory", "now.md")
    try:
        with open(nowp, encoding="utf-8") as f:
            t = f.read()
        t = (t.replace("chapter: 0", "chapter: 1")
              .replace("narrative_verified: 0", "narrative_verified: 1")
              .replace("updated: YYYY-MM-DD", "updated: 2026-09-19"))
        # 模板里 story_time / location 是空值 —— 第一次写章必须填，否则状态同步会拦（设计如此）
        t = re.sub(r"^story_time:.*$", "story_time: 冒烟日", t, flags=re.M)
        t = re.sub(r"^location:.*$", "location: 冒烟地", t, flags=re.M)
        t = re.sub(r"`\[流程\] ch-1[^`]*`", "[流程] ch-1 自查0 三连0 复查0 回写OK", t)
        with open(nowp, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    except OSError as e:
        add("D3", "流程冒烟失败（now.md 写不了）", str(e)[:100])
        return
    # 2b) 同步 SOLOENT.md §7 的「第 N 章」（否则状态同步会拦——这是设计如此）
    try:
        sp = os.path.join(tmp, "SOLOENT.md")
        with open(sp, encoding="utf-8") as f:
            t = f.read()
        t = re.sub(r"- 当前进度：第 \d+ 章[^\n]*",
                   "- 当前进度：第 1 章《冒烟测试》完成", t)
        with open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    except OSError:
        pass
    # 3) 追加账本行
    try:
        with open(os.path.join(tmp, ".soloent", "ledger.tsv"), "a",
                  encoding="utf-8", newline="\n") as f:
            f.write("1\t1\t冒烟\n")
    except OSError:
        pass
    # 4) 过闸
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(tmp, "tools", "preflight.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=tmp, timeout=120)
    except Exception as e:  # noqa: BLE001
        add("D3", "流程冒烟失败（闸门跑不起来）", str(e)[:100])
        return
    if r.returncode != 0:
        tail = [l for l in (r.stdout or r.stderr or "").splitlines() if l.strip()][-6:]
        add("D3", "流程冒烟失败（造章后过不了闸）", " ｜ ".join(tail)[:420])


def main():
    quiet = "--quiet" in sys.argv
    do_render = "--no-render" not in sys.argv
    issues = []

    def add(code, kind, detail):
        issues.append((code, kind, detail))

    idx = build_index()

    # ---------- A. 模板层 ----------
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import init_book  # noqa: E402
    tpl_actual = {f for f in os.listdir(TEMPLATES)
                  if os.path.isfile(os.path.join(TEMPLATES, f))}
    tpl_needed = set(init_book.TEMPLATE_FILES.keys())
    for m in sorted(tpl_needed - tpl_actual):
        add("A1", "模板缺失（init_book 要复制但文件不在）", m)
    for o in sorted(tpl_actual - tpl_needed):
        add("A2", "孤儿模板（文件在但 init_book 不引用）", o)
    for name in sorted(tpl_needed & tpl_actual):
        txt = open(os.path.join(TEMPLATES, name), encoding="utf-8", errors="replace").read()
        for ph in sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", txt))):
            if ph not in TEMPLATE_KEYS:
                add("A3", "模板占位符无对应渲染值（会原样漏进生成物）",
                    "%s -> {{%s}}" % (name, ph))

    # ---------- A4. AGENTS.template.md 的渲染契约 ----------
    # 为什么单列：A3 只覆盖 templates/（init_book 复制的骨架），**不覆盖
    # assets/AGENTS.template.md**——而它是每本书 AGENTS.md 的唯一来源。
    # 占位符写错、或新增了 sync.py 替换表里没有的键，会原样变成书里的
    # `{{skills_dir}}` 这种字面量；而 audit 此前一直是绿的（2026-09-20 发现）。
    # 做法：拿 assets/book.example.json 真渲染一遍，看有没有没被替换掉的占位符——
    # 这样不必维护一份「键名清单」，替换表改了这里自动跟上。
    try:
        import json  # noqa: PLC0415
        import sync  # noqa: PLC0415
        with open(os.path.join(ASSETS, "book.example.json"), encoding="utf-8") as f:
            probe_cfg = json.load(f)
        rendered = sync.render_agents(kit.Book(ROOT, probe_cfg), ROOT)
        if not rendered:
            add("A4", "AGENTS.template.md 渲染不出来", "render_agents 返回空")
        else:
            for ph in sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", rendered))):
                add("A4", "AGENTS 模板占位符未被替换（会漏进每本书的 AGENTS.md）", "{{%s}}" % ph)
    except Exception as e:  # 渲染逻辑本身炸了也是问题，不能静默
        add("A4", "AGENTS 模板渲染异常", "%s: %s" % (type(e).__name__, e))

    # ---------- B0. {{vault}} 路径必须真实存在 ----------
    # 为什么单列：B2 遇到 `{{vault}}/...` 会因 startswith("{{") 直接跳过，
    # 于是模板里长期写着不存在的路径也没人报。而这些路径**是给写作 agent 照着找的**——
    # 第 3 步「技能三连」就靠它定位 SKILL.md，走空一次就等于这一步没跑（2026-09-20）。
    for f in [os.path.join(ASSETS, "AGENTS.template.md"), os.path.join(ROOT, "SKILL.md")] + \
             [os.path.join(SKILLS, d, "SKILL.md") for d in sorted(os.listdir(SKILLS))
              if os.path.isdir(os.path.join(SKILLS, d))]:
        if not os.path.isfile(f):
            continue
        txt = open(f, encoding="utf-8", errors="replace").read()
        for ref in sorted(set(re.findall(r"\{\{vault\}\}[/\\]([^\s`)\"'\u201c\u201d，。；]*)", txt))):
            rel = ref.strip("\\/").replace("\\", "/")
            if not rel or "<" in rel or "{{" in rel or PLACEHOLDER_REF.search(rel):
                continue
            if not os.path.exists(os.path.join(ASSETS, rel)):
                add("B0", "{{vault}} 路径不存在（agent 会照它去找）",
                    "%s -> assets/%s" % (os.path.relpath(f, ROOT).replace("\\", "/"), rel))

    # ---------- B. 技能层 ----------
    for d in sorted(os.listdir(SKILLS)):
        base = os.path.join(SKILLS, d)
        if not os.path.isdir(base):
            continue
        p = os.path.join(base, "SKILL.md")
        if not os.path.isfile(p):
            add("B1", "技能缺 SKILL.md", d)
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        for ref in sorted(set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|ps1|md|tsv|json))`", txt))):
            if ref.startswith(("http", "{{")) or is_book_ref(ref):
                continue
            if PLACEHOLDER_REF.search(ref):        # Dialogue/X.md 这类占位符
                continue
            if os.path.basename(ref) in idx:
                continue
            if os.path.exists(os.path.join(base, ref)):
                continue
            add("B2", "技能内引用断链", "%s -> %s" % (d, ref))

    # ---------- C. 文档层 ----------
    for f in sorted(os.listdir(DOCS)):
        if not f.endswith(".md"):
            continue
        txt = open(os.path.join(DOCS, f), encoding="utf-8", errors="replace").read()
        for ref in sorted(set(re.findall(r"`([^`\n]+\.(?:md|py|tsv|json|ps1))`", txt))):
            ref = ref.strip()
            if ref.startswith(("http", "{{")) or " " in ref or is_book_ref(ref):
                continue
            if PLACEHOLDER_REF.search(ref):        # 通配符 / 占位符式引用
                continue
            if "<" in ref or ref.startswith(("~", "$")):   # <书>/.codebuddy/…、~/.codebuddy/… 这类示例路径
                continue
            if BREAKDOWN_REF.match(ref):           # 拆书产物（生成在书里）
                continue
            if os.path.basename(ref) in idx:
                continue
            if os.path.exists(os.path.join(ROOT, ref)):
                continue
            add("C1", "docs 引用断链", "%s -> %s" % (f, ref))

    # ---------- D1. 脚本语法 ----------
    for f in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if not f.endswith(".py"):
            continue
        try:
            compile(open(os.path.join(ROOT, "scripts", f), encoding="utf-8").read(),
                    f, "exec")
        except SyntaxError as e:
            add("D1", "脚本语法错误", "%s: %s" % (f, e))

    # ---------- D2. 生成物残留占位符（真建一本临时书）----------
    if do_render:
        tmp = tempfile.mkdtemp(prefix="nw-audit-")
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "init_book.py"),
                 "--dir", tmp, "--title", "审计临时书", "--genre", "都市",
                 "--platform", "番茄男频", "--tags", "urban",
                 "--words", "2400", "--length", "测试"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                add("D2", "初始化失败（临时书建不出来）",
                    (r.stderr or r.stdout or "")[-160:].replace("\n", " "))
            else:
                for dp, dn, fn in os.walk(tmp):
                    for f in fn:
                        p = os.path.join(dp, f)
                        try:
                            t = open(p, encoding="utf-8", errors="replace").read()
                        except OSError:
                            continue
                        if "{{" in t:
                            add("D2", "生成物含未渲染占位符（语法会坏）",
                                "%s（%d 处）" % (os.path.relpath(p, tmp), t.count("{{")))
                # 钩子配置必须两份都在：本 harness 读 .codebuddy，ZCode 读 .zcode
                for rel in (os.path.join(".codebuddy", "settings.json"),
                            os.path.join(".zcode", "config.json")):
                    if not os.path.isfile(os.path.join(tmp, rel)):
                        add("D2", "生成物缺失（钩子配置）", rel)
                _smoke(tmp, add)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- E1. 孤儿技能 ----------
    all_sk = {d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d))}
    sk_txt = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8", errors="replace").read()
    doc_txt = "".join(open(os.path.join(DOCS, f), encoding="utf-8", errors="replace").read()
                      for f in os.listdir(DOCS) if f.endswith(".md"))
    for name in sorted(all_sk):
        if name not in sk_txt and name not in doc_txt:
            add("E1", "孤儿技能（从未被主流程或 docs 引用）", name)

    # ---------- E3. 打包账实相符 ----------
    # 为什么单列：`assets/_bundle.json` 记着「从 writing-vault 打包了什么、各多少个」，
    # 而它是**唯一**声称资产规模的凭据——实测记着 skills: 123、磁盘上只有 13 个，
    # 没有任何检查发现（2026-09-20）。账实不符会让「缺技能」被当成「本来就没有」。
    try:
        import json as _json
        with open(os.path.join(ASSETS, "_bundle.json"), encoding="utf-8") as f:
            bundle = _json.load(f)
        counts = bundle.get("counts") or {}
        actual = {
            "rules": sum(len(fn) for _, _, fn in os.walk(os.path.join(ASSETS, "rules"))),
            "skills": len([d for d in os.listdir(SKILLS)
                           if os.path.isdir(os.path.join(SKILLS, d))]),
            "workflows": len([f for f in os.listdir(os.path.join(ASSETS, "workflows"))
                              if f.endswith(".md")]),
        }
        for key, real in actual.items():
            if key in counts and counts[key] != real:
                add("E3", "打包账与实盘不一致（_bundle.json）",
                    "%s: 记 %s，实盘 %s——「缺资产」会被当成「本来就没有」"
                    % (key, counts[key], real))
    except FileNotFoundError:
        pass
    except Exception as e:
        add("E3", "_bundle.json 读不了", repr(e))

    # ---------- E2. 过时路径 ----------
    # 扫描范围：assets/ ＋ scripts/ ＋ hooks/ ＋ docs/（2026-09-20 扩）。
    # 原先只走 assets/，于是 `sync.py` / `selftest.py` 的用法示例里躺着 `_agent/sync.py`
    # （该目录早已不存在）却一直查不到——那正是 agent 会照着敲的命令。
    # 排除「说明性引用」：写清楚它已废弃/不存在/不要用的行不算问题（否则改不动历史说明）。
    EXPLAIN_OK = re.compile(
        r"已废弃|已不存在|早已|不再|作废|移除|上一代|前身|改名|迁移|适配|修正|踩过|历史|"
        r"→|不要用|不存在|旧版|以前|曾")
    scan_roots = [os.path.join(ROOT, "assets"), os.path.join(ROOT, "scripts"),
                  os.path.join(ROOT, "hooks"), os.path.join(ROOT, "docs")]
    self_path = os.path.join(ROOT, "scripts", "audit.py")   # 这份清单就是它定义的，别自报
    for scan_root in scan_roots:
        for dp, dn, fn in os.walk(scan_root):
            if "__pycache__" in dp:
                continue
            for f in fn:
                if not f.endswith((".md", ".py", ".json", ".ps1")):
                    continue
                p = os.path.join(dp, f)
                if os.path.abspath(p) == os.path.abspath(self_path):
                    continue
                try:
                    t = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for stale in STALE_PATHS:
                    if stale not in t:
                        continue
                    hits = [ln for ln in t.splitlines()
                            if stale in ln and not EXPLAIN_OK.search(ln)]
                    if hits:
                        add("E2", "过时路径（上一代目录/文件名）",
                            "%s -> %s（%d 处）"
                            % (os.path.relpath(p, ROOT), stale, len(hits)))

    # ---------- F2. 两份基线必须一致（只比结构，不比内容） ----------
    # `book.example.json`（文档/模板/A4 的渲染探针）与 `init_book.build_config`
    # （新书实际拿到的、doctor 报「缺基线键」用的）是同一概念的两份拷贝。
    # 它们漂开时：migrate 补的键和示例说的不是一回事，照着示例填会漏键。
    # ⚠️ 只比**顶层段**与**骨架键清单**：示例里 name_bad/intel_bad 等是示范内容，
    #    全量递归比对会把「示例有、新书没有」的内容键全报出来（纯噪音）。
    SKELETON = [
        "gate.review_required", "gate.blacklist_total", "gate.blacklist_per_chapter",
        "checks.rhythm", "checks.panel", "checks.name_roster", "checks.hook_check",
        "checks.foreshadow_check", "checks.blacklist", "checks.elevation",
        "checks.quote_style", "checks.locked_details", "checks.gated_keywords",
        "state.required", "state.derivable", "state.manual_stamp",
        "chapter.file_regex", "chapter.title_regex", "chapter.default_words",
        "chapter.word_max", "paths.chapters", "paths.canon", "paths.now", "paths.ledger",
        "boundary.opt_out_reason", "rules.load", "rules.forbid",
    ]
    try:
        import json as _j
        import init_book as _ib
        with open(os.path.join(ASSETS, "book.example.json"), encoding="utf-8") as f:
            ex = _j.load(f)
        ini = _ib.build_config({"dir": "x", "title": "x", "genre": "x", "platform": "x",
                                "tags": "urban", "words": 2400, "length": "x"})

        def has(d, dotted):
            cur = d
            for part in dotted.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return False
                cur = cur[part]
            return True

        for label, skip in (("两份基线漂开（示例缺）", None), ("两份基线漂开（新书缺）", None)):
            pass
        miss_in_ex = [k for k in SKELETON if has(ini, k) and not has(ex, k)]
        miss_in_ini = [k for k in SKELETON if has(ex, k) and not has(ini, k)]
        sec_ex = {k for k in ex if not str(k).startswith("_")}
        sec_ini = {k for k in ini if not str(k).startswith("_")}
        miss_sec = (sec_ex ^ sec_ini)
        if miss_in_ex:
            add("F2", "两份配置基线漂开（示例缺这些键）",
                "、".join(miss_in_ex[:6]) + ("…" if len(miss_in_ex) > 6 else ""))
        if miss_in_ini:
            add("F2", "两份配置基线漂开（新书缺这些键）",
                "、".join(miss_in_ini[:6]) + ("…" if len(miss_in_ini) > 6 else ""))
        if miss_sec:
            add("F2", "两份配置基线漂开（顶层段不一致）", "、".join(sorted(miss_sec)))
    except Exception as e:
        add("F2", "基线一致性检查失败", repr(e)[:80])

    # ---------- F1. book.json 死字段 ----------
    # schema 里有、但没有任何脚本读它 —— 「配了没人用」。
    # 2026-09-19 实测抓到两个：`state.display_extra`、`ledger.rank_ranges`。
    try:
        base = init_book.build_config({
            "dir": "x", "title": "x", "genre": "x", "platform": "x",
            "tags": "urban", "words": 2400, "length": "x",
        })
        allkeys = set()

        def walk(d):
            for k, v in d.items():
                if k.startswith("_"):
                    continue
                allkeys.add(k)
                if isinstance(v, dict):
                    walk(v)

        walk(base)
        sc2 = os.path.join(ROOT, "scripts")
        src = "".join(open(os.path.join(sc2, f), encoding="utf-8", errors="replace").read()
                      for f in os.listdir(sc2) if f.endswith(".py"))
        for k in sorted(allkeys):
            if ('"%s"' % k) not in src and ("'%s'" % k) not in src:
                add("F1", "死字段（schema 里有、脚本没人读）", k)
    except Exception as e:  # noqa: BLE001
        add("F1", "schema 检查失败", str(e)[:80])

    # ---------- 输出 ----------
    print("=" * 62)
    print("插件自审计 ｜ %s" % ROOT)
    print("=" * 62)
    if not issues:
        print("结果：✅ 未发现问题。")
        return 0
    groups = {}
    for code, kind, detail in issues:
        groups.setdefault((code, kind), []).append(detail)
    for (code, kind), items in sorted(groups.items()):
        print("[%s] %s（%d）" % (code, kind, len(items)))
        for d in items[:12]:
            print("      %s" % d)
        if len(items) > 12:
            print("      … 另有 %d 项" % (len(items) - 12))
    print("-" * 62)
    print("结果：❌ 共 %d 个问题。" % len(issues))
    return 1


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
