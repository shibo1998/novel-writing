#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
出章闸门 (preflight.py)

作用：每写完 1 章必跑一次；不通过就停下，不许继续写下一章。
这是把「每章固定流程」从"建议"变成"闸门"的执行层——**与 agent 是否守流程无关**。

它汇总五件事：
  1. 单章对账（consistency_check.py）——严重问题数
  2. 状态同步（state_sync.py）——进度是否已回写
  3. 章节账本（ledger.py --quiet）——跨章的账是否平
  4. 批量长跑检测——自上次过闸后新增多章却未逐章校验（历史漂移事故即由此产生）
  5. **流程留痕**——正文步骤 2–5（SKILL B3–B6）：句法自查／技能三连／连续性复查／回写
     （缺失或格式伪造即拦截；**且与本章机检残留对撞**——宣称自查完成却仍有
     黑名单词／句式违规等机检可见的 AI 味残留，同样拦截。见 flow_trace()）
  6. **上一章质量评审**——第 7 步（`/review`）是否真的跑过：出第 N 章时要求
     第 N-1 章的 `notes/review-chapter-<N-1>-*.md` 已存在（`gate.review_required` 开启时）。
     此前这一步**连代理都没有**，实测某书 69 章评审产物数为 0，没人发现。

判定纪律（不可放宽）：
  · **失败关闭**：抓不到机检摘要行（脚本报错 / 被中断 / 输出格式变了）一律判「未通过」，
    不默认放行。
  · **校验点只前进不后退**：章节目录为空或缺失时不会把 checkpoint 写回小值。

用法：
    python <kit>/scripts/preflight.py                 # 校验并推进校验点
    python <kit>/scripts/preflight.py --chapter N     # 批量积压时按顺序补验第 N 章
    python <kit>/scripts/preflight.py --no-advance    # 只校验，**不推进校验点**
    python <kit>/scripts/preflight.py --root <书目录>
    python <kit>/scripts/preflight.py --json          # 结构化输出（程序契约；钩子/watcher 用）
    python <kit>/scripts/preflight.py --tag hook --since 1   # 自动入口：tag 区分报告，since 增量扫描
退出码：0=通过  1=拦截  3=跳过（项目锁被其他入口占用，不等于通过）

`--json` 只决定**输出格式**，不改变「是否推进校验点」——要只校验不推进请显式传
`--no-advance`（自动入口都是这么传的）。2026-09-20 前 --json 会隐式不推进，
同一个动作两种结果，已修。

编排约定（报告 NW-P1-008）：
  · **preflight 是唯一编排入口**——Hook / Watcher 只调用它，不得自己再跑
    consistency_check（否则一次保存触发两遍全量扫描，且共享报告互相覆盖）。
  · 全程持 `.soloent/locks/preflight.lock` 项目锁：多个入口同时触发时串行执行，
    不会踩写同一份报告/状态文件。
  · `--tag` 传播给子检查（hook/watch 各写各的当日报告）；`--since N` 只扫最近 N 章，
    账本与状态检查仍然全量——增量只用于正文扫描。

⚠️ `--no-advance` 是给**自动入口**（后台 watcher、落盘钩子）用的：
   「批量长跑」拦截靠 `当前章 - 上次校验章`，如果自动入口每次都推进校验点，
   这个差值永远是 1，**19 章连发这类事故特征就再也报不出来了**。
   所以只有「人/AI 显式过闸」才推进校验点。
"""
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402


def ckpt_path(book):
    return os.path.join(book.soloent, "checkpoint.json")


def load_ckpt(book):
    p = ckpt_path(book)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"last_verified_chapter": 0, "verified_at": None}


CHECK_VERSION = 2


def chapter_digest(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def checkpoint_issues(book, ckpt, latest):
    """已通过章节的正文一旦变化或消失，旧 checkpoint 立即失效。

    返回 (问题列表, 最早失效章号 or None)。最早失效章是「重新补验」的法定起点：
    不修它，后面的章一律不许过闸。
    """
    issues = []
    invalid = []
    try:
        last = int(ckpt.get("last_verified_chapter", 0) or 0)
    except (TypeError, ValueError):
        return ["checkpoint.json 的 last_verified_chapter 不是有效整数"], None
    if last > latest:
        issues.append(f"校验点在第 {last} 章，但当前正文只到第 {latest} 章；已通过章节可能被删除")
    records = ckpt.get("chapters")
    if not isinstance(records, dict):
        return issues, None
    actual = {}
    for chapter, filename, text in book.chapter_files():
        actual.setdefault(chapter, (filename, chapter_digest(text)))
    for raw_ch, record in records.items():
        try:
            chapter = int(raw_ch)
        except (TypeError, ValueError):
            issues.append(f"checkpoint.json 含无效章号：{raw_ch!r}")
            continue
        if chapter > last:
            continue
        if not isinstance(record, dict) or not record.get("sha256"):
            issues.append(f"checkpoint.json 第 {chapter} 章缺少正文哈希")
            invalid.append(chapter)
            continue
        current = actual.get(chapter)
        if current is None:
            issues.append(f"已通过校验的第 {chapter} 章正文已删除或不再匹配命名规则")
            invalid.append(chapter)
        elif current[1] != record["sha256"]:
            issues.append(f"已通过校验的第 {chapter} 章正文已变化；旧流程凭证失效，请重新核验")
            invalid.append(chapter)
    return issues, (min(invalid) if invalid else None)


def save_ckpt(book, cur, previous=None):
    p = ckpt_path(book)
    verified_at = datetime.datetime.now().isoformat(timespec="seconds")
    records = dict((previous or {}).get("chapters") or {})
    prev_last = int((previous or {}).get("last_verified_chapter", 0) or 0)
    for chapter, filename, text in book.chapter_files():
        if chapter > cur:
            continue
        records[str(chapter)] = {
            "file": filename,
            "sha256": chapter_digest(text),
            "verified_at": verified_at,
            "check_version": CHECK_VERSION,
        }
    kit.atomic_write_json(
        p,
        {
            # 校验点只前进不后退；cur < prev_last 是「补验旧章」——
            # 只刷新该章的哈希凭证，不回退校验点。
            "last_verified_chapter": max(cur, prev_last),
            "verified_at": verified_at,
            "check_version": CHECK_VERSION,
            "chapters": records,
        },
    )


def pick(out, prefix):
    return [l[len(prefix):].strip() for l in (out or "").splitlines() if l.startswith(prefix)]


def tail_lines(out, n=6):
    lines = [l for l in (out or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def requested_chapter(argv):
    """读取 `--chapter N` / `--chapter=N`；未提供返回 None，非法值抛 ValueError。"""
    raw = None
    for i, arg in enumerate(argv):
        if arg == "--chapter":
            if i + 1 >= len(argv):
                raise ValueError("--chapter 缺少章号")
            raw = argv[i + 1]
        elif arg.startswith("--chapter="):
            raw = arg.split("=", 1)[1]
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"无效章号：{raw}")
    if value < 1:
        raise ValueError(f"章号必须大于 0：{raw}")
    return value


def blacklist_over_limit(book):
    """单章黑名单词计数超阈值的章。词表复用 book.json，保持单一来源。"""
    words = book.sec("checks").get("blacklist") or []
    limit = book.get("gate", "blacklist_per_chapter", 6)
    over = []
    for _, fn, text in book.chapter_files():
        n = sum(text.count(w) for w in words)
        if n > limit:
            over.append((fn, n))
    return over


def review_artifact(book, chapter):
    """第 N 章的质量评审产物（`/review` 第 7 步）是否存在。返回文件名或 None。

    约定路径：`notes/review-chapter-<N>-<日期>.md`（见 SKILL.md 场景 C 与 `/review` 一节）。
    """
    notes = book.notes_dir
    if not notes or not os.path.isdir(notes):
        return None
    pat = re.compile(r"^review-chapter-0*%d(?!\d)" % chapter)
    for fn in sorted(os.listdir(notes)):
        if fn.endswith(".md") and pat.match(fn):
            return fn
    return None


def review_blocker(book, cur, requested):
    """第 7 步（质量审查）的落地校验。

    为什么需要（2026-09-20 复盘）：闸门此前只校验第 2–5 步的留痕，**第 7 步连代理都没有**。
    实测某书 69 章的 `notes/` 里 review 产物数为 **0**——SKILL 写着「跳过 = 正文只过了
    一半的关」，而那一半从没跑过，且没有任何检查发现。

    规则（与「每章一停」同向）：**出第 N 章时，第 N-1 章的评审记录必须已经存在**。
    这样任何一章都不会在没被评审的情况下被下一章覆盖掉注意力。
    `--chapter N`（批量补验历史）不适用——补验是恢复旧的校验点，不是发布新章。
    """
    if not book.sec("gate").get("review_required"):
        return None
    if requested is not None or cur < 2:
        return None
    prev = cur - 1
    if review_artifact(book, prev) is None:
        return (f"第 {prev} 章还没有质量评审记录——第 7 步（`/review`，五层审查）没跑。"
                f"闸门只管算得出来的硬伤；追读性／钩子质量／角色声音／AI 味只能靠这一步。"
                f"请在 notes/ 下产出 `review-chapter-{prev}-<日期>.md` 再出下一章"
                f"（做法见 assets/workflows/chapter-review.md）。")
    return None


FLOW_RE = re.compile(r"^\s*\[流程\]\s*ch-?0*(\d+)\b(.*)$", re.M)
# 尾部允许两种形态：
#   · 干净章：`自查0 三连0 复查0 回写OK`
#   · 声明保留：`…回写OK；保留：<理由>`（确有语境例外时用，过闸降为警告并交 /review 复核）
# 除这两种之外任何文本都不算凭证——F05 起就是这么卡的。
FLOW_DETAIL_RE = re.compile(
    r"自查(\d+)\s+三连(\d+)\s+复查(\d+)\s+回写OK"
    r"(?:\s*[；;]\s*保留[：:]\s*\S[^\n]{0,99})?"
)
FLOW_KEEP_RE = re.compile(r"[；;]\s*保留[：:]")


def style_residual(book, cur):
    """第 cur 章当前文本里「机检可见的去 AI 味残留」清单。

    为什么需要它（2026-09-20 复盘）：F05 只把凭证的**格式**卡严了，
    `自查0 三连0 复查0 回写OK` 依旧合法，于是 69 章留痕几乎全报 0/1，
    正文却残留 63 处黑名单词（35 章命中）。**格式合法 ≠ 真跑过。**
    这一层把「留痕宣称的清零」与「机检算出的残留」对撞：真跑过 B3 句法自查的章，
    机检能看见的那几类应当已经清零（确需保留的必须写明理由）。

    数据来源就是 consistency_check 已算出的结果，不做二次扫描、不另立一套判据。

    取不到机检模块时返回空表（**不是**放行整个闸门）：本层是「格式层」之上的加固，
    而机检模块缺失/崩溃本身会让第 1 项单章对账失败关闭——两道防线不互相顶替。
    关键是不让这一层把闸门炸掉：它对一致性负责，不对可用性负责。
    """
    try:
        import consistency_check  # 同目录，按需导入
        checker = getattr(consistency_check, "check", None)
        is_style = getattr(consistency_check, "is_style_finding", None)
    except Exception:
        return []
    if checker is None or is_style is None:
        return []
    for chno, fn, text in book.chapter_files():
        if chno != cur:
            continue
        try:
            findings = checker(book, [(chno, fn, text)])
        except Exception:
            return []
        return [f for f in findings if is_style(f[3])]
    return []


def flow_trace(book, cur):
    """正文步骤 2–5（SKILL B3–B6）执行痕迹校验。

    为什么需要它：闸门原来只查「算得出来的东西」（人名／刻度／字数／账本），
    而 B3 句法自查、B4 技能三连、B5 连续性复查**没有任何机器能验证**——
    2026-09-18 的 ch-31、ch-32 两次都漏在这三步上（作者反馈「AI 味重、没人味」）。

    做法分两层：
      ① **格式层**：每章在 now.md 留一行 `[流程] ch-N 自查X 三连Y 复查Z 回写OK`；
      ② **对撞层**：把该章的机检残留与「自查已完成」这个声明对撞——留有残留
         且没有写明保留理由，就说明自查没真跑（或跑了一半），一律拦截。
    只校验**本次目标章节**——正常出章是最新章，批量恢复时是 `--chapter N` 指定章。

    返回状态：ok / warn（声明保留，降级为警告）/ missing / invalid / stale。
    """
    p = os.path.join(book.soloent, "memory", "now.md")
    if not os.path.exists(p):
        return "missing", "now.md 不存在，无法校验流程留痕"
    try:
        with open(p, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return "missing", "now.md 读取失败，无法校验流程留痕"
    hits = {int(m.group(1)): m.group(2).strip() for m in FLOW_RE.finditer(text)}
    if cur not in hits:
        return "missing", (
            f"第 {cur} 章没有流程留痕——正文步骤 2–5（SKILL B3–B6）到底跑没跑，"
            f"机器无从判断，只能靠这一行。请在 now.md 追加："
            f"「[流程] ch-{cur} 自查X 三连Y 复查Z 回写OK」（X/Y/Z ＝ 各步发现并修复的处数）"
        )
    tail = hits[cur]
    if re.search(r"未执行|跳过|历史章|未跑", tail):
        return "stale", (f"第 {cur} 章留痕标注为未执行／跳过（{tail}）"
                         f"——正式章节必须逐条执行。")
    if not FLOW_DETAIL_RE.fullmatch(tail):
        return "invalid", (
            f"第 {cur} 章流程凭证格式无效（{tail or '空'}）。必须严格写成："
            f"[流程] ch-{cur} 自查N 三连N 复查N 回写OK；N 为非整数即无效；"
            f"确需保留违规处时在行尾另加「；保留：<理由>」。"
        )
    # --- 对撞层：留痕说自查完成了，机检却说还有残留 ---
    residual = style_residual(book, cur)
    if residual:
        sample = "；".join(f"{r[1]} 第{r[2]}行 {r[3]}" for r in residual[:3])
        more = f" 等 {len(residual)} 处" if len(residual) > 3 else ""
        if FLOW_KEEP_RE.search(tail):
            return "warn", (
                f"第 {cur} 章声明保留 {len(residual)} 处机检可见的去 AI 味残留"
                f"（{sample}{more}）——过闸放行，但 /review 时必须逐条复核保留理由。"
            )
        return "invalid", (
            f"第 {cur} 章留痕宣称自查已完成，但机检仍可见 {len(residual)} 处去 AI 味残留"
            f"（{sample}{more}）。要么自查没真跑（数字是编的），要么留着没改也没说明。"
            f"修复：清掉残留后把留痕数字改成**真实**处数；确有语境例外要保留的，"
            f"在留痕行尾加「；保留：<理由>」（降为警告，交 /review 复核）。"
        )
    return "ok", ""


def opt_value(argv, name):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def extract_severe_rows(out, limit=15):
    """从 consistency_check 的输出里摘「严重」段表格行（给 hook 注入用）。"""
    rows, in_sev = [], False
    for l in (out or "").splitlines():
        if l.startswith("## 严重"):
            in_sev = True
            continue
        if in_sev and l.startswith("## "):
            break
        if in_sev and l.startswith("| ") and not l.startswith("| 章 |") and "---" not in l:
            rows.append(l.strip())
            if len(rows) >= limit:
                break
    return rows


def extract_report_path(out):
    for l in (out or "").splitlines():
        if l.startswith("[已写出]"):
            return l[len("[已写出]"):].strip()
    return None


def run_main():
    argv = kit.strip_root_arg(sys.argv[1:])
    as_json = "--json" in argv
    lock_timeout = 120
    raw_lt = opt_value(argv, "--lock-timeout")
    if raw_lt and raw_lt.isdigit():
        lock_timeout = int(raw_lt)
    tag = opt_value(argv, "--tag") or ""
    since = opt_value(argv, "--since")
    since_n = int(since) if since and since.isdigit() and int(since) > 0 else None

    try:
        root = book_root_of(sys.argv[1:])
        if root is None:
            return orchestrate(argv, as_json=as_json, tag=tag, since=since_n)
        with kit.project_lock(root, "preflight", timeout=lock_timeout):
            return orchestrate(argv, as_json=as_json, tag=tag, since=since_n)
    except kit.LockBusy as e:
        # 锁被其他入口占用：排队等没有意义（自动入口有自己的超时预算），
        # 用独立退出码 3 表达「跳过」，与「拦截(1)」「通过(0)」区分——
        # **跳过绝不等于通过**，watcher 会留下基线等下轮重查。
        if as_json:
            print(json.dumps({"ok": False, "skipped": True, "reason": str(e),
                              "blockers": [], "warnings": [], "checks": {}},
                             ensure_ascii=False))
        else:
            print("跳过：" + str(e))
            print("结果：⏭ 本次检查跳过（另一个检查正在进行）。这不等于通过；稍后会自动重查。")
        return 3


def book_root_of(argv):
    return kit.find_book_root(explicit=kit.root_arg(argv))


def orchestrate(argv, as_json=False, tag="", since=None):
    book = kit.load_book(sys.argv[1:])
    advance = "--no-advance" not in argv
    try:
        requested = requested_chapter(argv)
    except ValueError as e:
        blockers = [str(e)]
        if as_json:
            print(json.dumps({"ok": False, "blockers": blockers, "warnings": [], "checks": {}},
                             ensure_ascii=False))
            return 1
        print("拦截：" + blockers[0])
        return 1
    result = {"ok": False, "skipped": False, "chapter": None, "blockers": [], "warnings": [],
              "counts": None, "checks": {}}
    G = book.sec("gate")
    total_limit = G.get("blacklist_total", 60)
    latest = book.max_chapter()
    cur = latest
    ckpt = load_ckpt(book)
    last = ckpt.get("last_verified_chapter", 0)
    stray = book.stray_files()
    ckpt_blockers, earliest_invalid = checkpoint_issues(book, ckpt, latest)
    result["chapter"] = requested if requested is not None else latest

    if requested is not None:
        chapter_numbers = {n for n, _, _ in book.chapter_files()}
        # 补验的法定起点：旧凭证失效时从「最早失效章」重新核验；
        # 否则从 last+1 顺序推进。两条规则共用同一把号，互不锁死。
        expected = earliest_invalid if earliest_invalid is not None else last + 1
        if requested != expected:
            msg = (f"逐章补验不能跳号；当前校验点为第 {last} 章，"
                   f"请先补验第 {expected} 章。")
            if as_json:
                result["blockers"].append(msg)
                print(json.dumps(result, ensure_ascii=False))
                return 1
            print(f"拦截：{msg}")
            return 1
        if requested == earliest_invalid:
            # 本轮就是对「最早失效章」的重新核验：其余失效章的问题不拦本轮，
            # 它们会在后续逐章补验中依次修复。
            ckpt_blockers = []
        if requested not in chapter_numbers:
            msg = f"找不到符合 chapter.file_regex 的第 {requested} 章正文。"
            if as_json:
                result["blockers"].append(msg)
                print(json.dumps(result, ensure_ascii=False))
                return 1
            print(f"拦截：{msg}")
            return 1
        cur = requested

    # 新书（或章节被清空）：没有章节可校验，直接放行但把两种情况都说清楚，
    # 免得「误删章节」被静默当成「新书」。
    if latest == 0:
        if stray:
            msg = "发现未纳入检查的正文文件：" + "、".join(stray)
            if as_json:
                result["blockers"].append(msg)
                print(json.dumps(result, ensure_ascii=False))
                return 1
            print("=" * 56)
            print(f"出章闸门 ｜ 《{book.sec('book').get('title', '')}》")
            print("=" * 56)
            print("\n拦截：" + msg)
            print("结果：❌ 未通过。请按 chapter.file_regex 重命名，或移出正式 chapters/ 目录。")
            return 1
        if ckpt_blockers:
            if as_json:
                result["blockers"].extend(ckpt_blockers)
                print(json.dumps(result, ensure_ascii=False))
                return 1
            print("=" * 56)
            print(f"出章闸门 ｜ 《{book.sec('book').get('title', '')}》")
            print("=" * 56)
            for problem in ckpt_blockers:
                print("\n拦截：" + problem)
            print("结果：❌ 未通过。旧校验点与当前正文不一致。")
            return 1
        if as_json:
            result["ok"] = True
            print(json.dumps(result, ensure_ascii=False))
            return 0
        print("=" * 56)
        print(f"出章闸门 ｜ 《{book.sec('book').get('title', '')}》")
        print("=" * 56)
        print("chapters/ 里还没有章节。")
        print("  · 若这是新书 → 正常，可以开始写第一章。")
        print("  · 若原本有章节 → 请检查 chapters/ 是否被误删或命名不符。")
        print(f"    （判定规则来自 book.json 的 chapter.file_regex = {book.get('chapter', 'file_regex')}）")
        print("\n结果：✅ 通过（无章节可校验）。")
        return 0

    blockers, warns = list(ckpt_blockers), []
    result["blockers"], result["warnings"] = blockers, warns
    if stray:
        blockers.append("发现未纳入检查的正文文件：" + "、".join(stray))

    # --- 1. 单章对账（tag/since 由编排入口传播：自动入口与手工入口各写各的报告）---
    child_extra = []
    if tag:
        child_extra += ["--tag", tag]
    if since:
        child_extra += ["--since", str(since)]
    rc, out, present = kit.run_child("consistency_check.py", book.root, child_extra)
    sev = mid = low = 0
    summary_ok = False
    if not present:
        blockers.append("consistency_check.py 未接入（kit 目录不完整）")
    else:
        m = re.search(r"严重 (\d+) · 中等 (\d+) · 轻微 (\d+)", out)
        if rc != 0 or not m:
            msg = (f"consistency_check.py 退出码 {rc}，未解析出摘要行。"
                   f"可能原因：检查脚本报错 / 被中断 / 输出格式已变。")
            if as_json:
                result["checks"]["consistency"] = {"ok": False, "error": msg}
                print(json.dumps(result, ensure_ascii=False))
                print(msg, file=sys.stderr)
                return 1
            print("=" * 56)
            print("出章闸门 ｜ 机检未正常完成 → 判未通过（失败关闭）")
            print("=" * 56)
            print(msg)
            print("排查：单独运行 consistency_check.py 看报错。")
            print("\n结果：❌ 未通过。（抓不到摘要不默认放行）")
            return 1
        sev, mid, low = int(m.group(1)), int(m.group(2)), int(m.group(3))
        summary_ok = True
        result["counts"] = {"严重": sev, "中等": mid, "轻微": low}
        result["checks"]["consistency"] = {"ok": True, "report": extract_report_path(out)}
        if sev > 0:
            blockers.append(f"存在 {sev} 处「严重」问题（硬伤/口径冲突/悬空设定），禁止继续写。")

    # --- 2. 状态同步 ---
    st_rc, st_out, st_present = kit.run_child("state_sync.py", book.root)
    st_problems = pick(st_out, "问题：")
    st_warns = pick(st_out, "警告：")
    if not st_present:
        st_problems = ["state_sync.py 未接入（kit 目录不完整）"]
    elif st_rc == 0 and "结果：✅ 状态一致。" not in st_out:
        st_problems = ["state_sync.py 输出格式异常，无法确认状态校验是否完成"]
    elif st_rc != 0 and not st_problems:
        st_problems = ["状态同步未正常完成，请单独运行 state_sync.py 排查"]
    blockers.extend(st_problems)
    warns.extend(st_warns)
    result["checks"]["state"] = {"ok": bool(st_present and st_rc == 0 and not st_problems)}

    # --- 3. 章节账本 ---
    lg_rc, lg_out, lg_present = kit.run_child("ledger.py", book.root, ["--quiet"])
    lg_blocks = pick(lg_out, "拦截：")
    lg_warns = pick(lg_out, "警告：")
    if not lg_present:
        lg_blocks = ["ledger.py 未接入（kit 目录不完整）"]
    elif lg_rc == 0 and "结果：✅ 平账。" not in lg_out:
        lg_blocks = ["ledger.py 输出格式异常，无法确认账本校验是否完成"]
    elif lg_rc != 0 and not lg_blocks:
        lg_blocks = ["账本校验未正常完成（脚本报错或被中断），请单独运行 ledger.py 排查"]
    blockers.extend(lg_blocks)
    warns.extend(lg_warns)
    result["checks"]["ledger"] = {"ok": bool(lg_present and lg_rc == 0 and not lg_blocks)}

    # --- 4. 批量长跑与黑名单 ---
    if low > total_limit:
        warns.append(f"黑名单/AI句式词累计 {low} 处，建议先清再写（阈值 {total_limit}）。")
    over = blacklist_over_limit(book)
    if over:
        shown = "、".join(f"{fn.replace('.md', '')}({n})" for fn, n in over[:6])
        tail = f" 等 {len(over)} 章" if len(over) > 6 else ""
        warns.append(f"单章黑名单词超阈值：{shown}{tail}（警告项，不拦截）。")
    if requested is None and latest - last > 1:
        blockers.append(
            f"自上次校验(第 {last} 章)后有 {latest - last} 章尚未逐章校验，"
            f"不能把校验点直接推进到第 {latest} 章。"
            f"请运行 `preflight.py --chapter {last + 1}`，从第 {last + 1} 章开始逐章补验。"
        )

    # --- 5. 流程留痕（正文步骤 2–5 / SKILL B3–B6 是否真的跑过）---
    ft_state, ft_msg = flow_trace(book, cur)
    if ft_state in ("missing", "invalid", "stale"):
        blockers.append(ft_msg)
    elif ft_state == "warn":
        # 声明保留：留痕是诚实的（写明了保留理由），不拦闸门，但必须被 /review 看见。
        warns.append(ft_msg)
    result["checks"]["flow"] = {"ok": ft_state in ("ok", "warn"), "state": ft_state}
    ft_label = {"ok": "✅", "warn": "⚠️", "missing": "❌",
                "invalid": "❌", "stale": "❌"}.get(ft_state, "—")

    # --- 6. 上一章的质量评审（第 7 步）是否落地 ---
    rv_msg = review_blocker(book, cur, requested)
    if rv_msg:
        blockers.append(rv_msg)
    result["checks"]["review"] = {"ok": rv_msg is None, "required": bool(G.get("review_required"))}

    result["ok"] = not blockers
    # ⚠️ 不能在这里提前 return：`--json` 只是**输出格式**，不是 `--no-advance` 的同义词。
    # 此前 --json 在落校验点之前就返回了，于是「人手动跑 preflight --json 看一眼」
    # 会静默不推进校验点，而普通跑法会——同一个动作两种结果（2026-09-20 写用例时发现）。
    # 自动入口（hook / watcher）本来就显式传 --no-advance，不依赖这个隐式行为。

    st_label = "未接入" if not st_present else ("✅" if st_rc == 0 else "❌")
    lg_label = "未接入" if not lg_present else ("✅" if lg_rc == 0 else "❌")
    target = f" ｜ 本次补验第 {cur} 章" if requested is not None else ""
    tag_note = f" ｜ 入口 tag={tag}" if tag else ""
    _rv = result["checks"].get("review", {})
    rv_label = ("—" if not _rv.get("required")
                else ("✅" if _rv.get("ok") else "❌"))

    def json_out(msg=""):
        result["severe_rows"] = extract_severe_rows(out) if summary_ok else []
        if msg:
            result["result"] = msg
        print(json.dumps(result, ensure_ascii=False))

    if blockers:
        if as_json:
            json_out()
            return 1
        print("=" * 56)
        print(f"出章闸门 ｜ 《{book.sec('book').get('title', '')}》当前至第 {latest} 章{target} ｜ 上次校验至第 {last} 章{tag_note}")
        print(f"严重 {sev} ｜ 中等 {mid} ｜ 轻微 {low} ｜ 状态同步 {st_label} ｜ 账本 {lg_label}"
              f" ｜ 流程留痕 {ft_label} ｜ 上章评审 {rv_label}")
        if st_present and st_rc != 0:
            print(tail_lines(st_out))
        if lg_present and lg_rc != 0:
            print(tail_lines(lg_out))
        print("=" * 56)
        for w in warns:
            print("警告：" + w)
        for b in blockers:
            print("拦截：" + b)
        print("\n结果：❌ 未通过。请修复后重跑。")
        print("提示：完整清单见 notes/ 下当日对账文件（含行号与清除条件）")
        return 1

    # --- 通过：先落凭证，再报结果（json/text 两种输出共用同一套推进逻辑）---
    if advance and cur >= last:
        save_ckpt(book, cur, ckpt)
        msg = f"✅ 通过。校验点：第 {max(cur, last)} 章。"
    elif advance:
        save_ckpt(book, cur, ckpt)
        msg = f"✅ 通过。第 {cur} 章凭证已刷新，校验点仍为第 {last} 章。"
    elif cur >= last:
        msg = f"✅ 通过。校验点仍为第 {last} 章（--no-advance，自动入口不推进）。"
    else:
        msg = f"✅ 通过。校验点仍为第 {last} 章。"

    if as_json:
        json_out(msg)
        return 0
    print("=" * 56)
    print(f"出章闸门 ｜ 《{book.sec('book').get('title', '')}》当前至第 {latest} 章{target} ｜ 上次校验至第 {last} 章{tag_note}")
    print(f"严重 {sev} ｜ 中等 {mid} ｜ 轻微 {low} ｜ 状态同步 {st_label} ｜ 账本 {lg_label}"
          f" ｜ 流程留痕 {ft_label} ｜ 上章评审 {rv_label}")
    print("=" * 56)
    for w in warns:
        print("警告：" + w)
    print("\n结果：" + msg)
    return 0


def main():
    return run_main()


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
