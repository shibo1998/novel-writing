#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
doctor.py —— 一次性全身体检（**只读**）。

设计前提：**这个脚本永远不会自动运行**。它不接进 `preflight.py` 闸门，
不挂在 SessionStart / PostToolUse 钩子上，不被 watcher 调用。
只有人手动敲它才会跑，所以**它不可能影响写作流程**。

它回答的问题只有一个：**"防护是不是真的在生效？"**
—— 这类"配置漏配"平时毫无症状，等出事时才发现钩子早就静默失效了。

检查范围
--------
A. 插件本体    scripts/ 是否齐、hooks/ 是否齐、SKILL.md 在不在
B. 已装副本     与源是否一致（复用 install.py --check）
C. 每本书
   1. 五件套      book.json / canon.md / SOLOENT.md / now.md / tools 薄壳
   2. 章节        chapters/ 存在、章号连续无缺号、有无游离 .md
   3. 进度一致    checkpoint 章号、now.md 状态块章号、实际章数 三者对齐
   4. 内容漂移    正文 hash 与 state_hashes.json 基线是否一致
   5. 钩子        .zcode/config.json 是否存在、路径是否绝对路径、指向文件是否存在
   6. watcher     pid 文件里的进程是否还活着
   7. 生成物      是否漂移（复用 sync.py --check）
   8. 资产部署    rules.load 声明的 Rule 是否真的部署进 .soloent/rules/
   9. 账本        ledger.tsv 是否存在、数据行是否覆盖各章

用法
----
    python doctor.py                          # 体检当前书（自动向上找 book.json）
    python doctor.py --root "<书目录>"
    python doctor.py --lib "D:/1-work/novel"  # 体检作品库里每一本
    python doctor.py --json                   # 机器可读输出
    python doctor.py --quiet                  # 只报问题，不报通过项
    python doctor.py --fix                    # 只做绝对安全的清理（见下）

`--fix` 只做一件事：**删掉已经死掉的 `watcher.pid`**（它的存在会让启动脚本
误判"已在运行"）。其余问题一律只打印修复命令，不动手 —— 正典、正文、
配置一律不碰，避免体检工具反过来破坏书稿。

退出码
------
    0  全部通过
    1  有警告（⚠️），但没有错误
    2  有错误（❌）
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

# 注入预算是 SessionStart hook 定死的——从那边取，不在体检里重写一份
# （两份预算各自演化，最后一定是「医生说没事、hook 已经在截断」）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
try:
    import inject_canon as _inject  # noqa: E402
    MAX_CANON_BYTES = _inject.MAX_CANON_BYTES
    MAX_RULES_BYTES = _inject.MAX_RULES_BYTES
except Exception:  # hook 缺失/语法错时给同样的默认值，别让体检自己炸掉
    _inject = None
    MAX_CANON_BYTES = 24000
    MAX_RULES_BYTES = 20000

KIT_ROOT = kit.KIT_ROOT
PLUGIN_ROOT = kit.PLUGIN_ROOT
CONFIG_REL = kit.CONFIG_REL

OK, WARN, ERR = "ok", "warn", "err"
ICON = {OK: "✅", WARN: "⚠️ ", ERR: "❌"}

# 插件 scripts/ 下必须存在的脚本（doctor 自己可选列出）
REQUIRED_SCRIPTS = ["kit.py", "brief.py", "draft.py", "consistency_check.py",
                    "state_sync.py", "ledger.py", "preflight.py", "watch_and_check.py",
                    "sync.py", "install.py", "init_book.py", "selftest.py", "doctor.py",
                    "migrate_config.py"]
REQUIRED_HOOKS = ["inject_canon.py", "after_chapter_write.py"]
# 每本书 tools/ 下应有的薄壳
TOOL_SHELLS = ["brief.py", "draft.py", "consistency_check.py", "state_sync.py",
               "ledger.py", "preflight.py", "watch_and_check.py", "doctor.py"]
# 密钥文件：出现在 Git 历史里就等于泄漏（NW-P1-007）
SECRET_FILES = [".env", ".soloent/.api.env"]


class Report(object):
    """收集 [(级别, 归属, 说明, 建议)]。"""

    def __init__(self, quiet=False):
        self.rows = []
        self.quiet = quiet

    def add(self, level, where, what, how=""):
        self.rows.append((level, where, what, how))

    def ok(self, where, what):
        if not self.quiet:
            self.add(OK, where, what)

    def warn(self, where, what, how=""):
        self.add(WARN, where, what, how)

    def err(self, where, what, how=""):
        self.add(ERR, where, what, how)

    def worst(self):
        if any(r[0] == ERR for r in self.rows):
            return 2
        if any(r[0] == WARN for r in self.rows):
            return 1
        return 0

    def counts(self):
        c = {OK: 0, WARN: 0, ERR: 0}
        for r in self.rows:
            c[r[0]] += 1
        return c


# ---------------------------------------------------------------- A 插件本体

def check_plugin(rep):
    where = "插件"
    missing = [s for s in REQUIRED_SCRIPTS
               if not os.path.isfile(os.path.join(KIT_ROOT, s))]
    if missing:
        rep.err(where, "scripts/ 缺少：" + "、".join(missing),
                "插件安装不完整，重新跑 install.py")
    else:
        rep.ok(where, "scripts/ %d 个脚本齐全" % len(REQUIRED_SCRIPTS))

    hd = os.path.join(PLUGIN_ROOT, "hooks")
    mh = [h for h in REQUIRED_HOOKS if not os.path.isfile(os.path.join(hd, h))]
    if mh:
        rep.err(where, "hooks/ 缺少：" + "、".join(mh))
    else:
        rep.ok(where, "hooks/ %d 个钩子齐全" % len(REQUIRED_HOOKS))

    if os.path.isfile(os.path.join(PLUGIN_ROOT, "SKILL.md")):
        rep.ok(where, "SKILL.md 存在")
    else:
        rep.err(where, "SKILL.md 不存在（agent 找不到入口）")


# ------------------------------------------------------------ B 已装副本

def check_installed(rep):
    """复用 install.py --check：它已经知道各 harness 的落点。

    ⚠️ install.py **必须带 --target**，不带会直接退出码 2（参数错误）——
    早期版本这里漏传 target，结果体检永远报"未通过"，是个假警报。
    """
    where = "已装副本"
    ip = os.path.join(KIT_ROOT, "install.py")
    if not os.path.isfile(ip):
        rep.warn(where, "找不到 install.py，跳过")
        return
    try:
        sys.path.insert(0, KIT_ROOT)
        import install as inst
        targets = list(getattr(inst, "USER_TARGETS", {}).keys())
    except Exception:
        targets = ["workbuddy"]
    if not targets:
        targets = ["workbuddy"]

    checked = 0
    for t in targets:
        try:
            r = subprocess.run([sys.executable, ip, "--target", t, "--check"],
                               capture_output=True, text=True,
                               encoding="utf-8", timeout=120)
        except Exception:
            continue
        out = (r.stdout or "") + (r.stderr or "")
        if "不存在" in out or "未安装" in out or "尚未安装" in out:
            continue  # 这个 harness 根本没装，不报
        # 输出第一行就是落点目录（如 "⚠️  C:\...\skills\novel-writing"）。
        # 只体检**真的装过**的目标：这些 harness 的目录可能是空壳残留，
        # 给从没装过的目标报"缺失"纯属噪音。
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        dest = ""
        if lines:
            cand = re.sub(r"^[^\w]*", "", lines[0]).strip()
            if re.match(r"^[A-Za-z]:[\\/]", cand):
                dest = cand
        if dest and not os.path.isfile(os.path.join(dest, "SKILL.md")):
            continue  # 从来没装过（目录是空的）→ 不算问题
        if not dest:
            continue

        checked += 1
        diffs = [l.strip() for l in out.splitlines() if l.strip().startswith("差异：")]
        if diffs:
            rep.warn("%s（%s）" % (where, t), "与源不一致（%d 个文件）" % len(diffs),
                     '重跑：python "%s" --target %s' % (ip, t))
        elif r.returncode == 0:
            rep.ok("%s（%s）" % (where, t), "与源一致")
        else:
            rep.warn("%s（%s）" % (where, t),
                     "检查未通过（退出码 %d）" % r.returncode)
    if not checked:
        rep.ok(where, "没有检测到已安装的副本（可跳过）")


# ---------------------------------------------------------------- C 每本书

def _read(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def check_five_pieces(rep, book, name):
    where = name
    # book.json 必然存在（能构造出 Book 就说明读到了）
    rep.ok(where, ".soloent/book.json 可解析")

    canon = os.path.join(book.soloent, "canon.md")
    if os.path.isfile(canon):
        txt = _read(canon) or ""
        # 审计 D3：canon 里残留「待核对」说明值还没定稿
        pending = re.findall(r"待(?:作者)?核对|待确认|TODO|TBD", txt)
        if pending:
            rep.warn(where, "canon.md 里还有 %d 处「待核对」注记" % len(pending),
                     "值没定稿的话，机检等于在跟一个占位符比对")
        else:
            rep.ok(where, "canon.md 存在且无待核对注记")
    else:
        rep.err(where, "缺 .soloent/canon.md（正典速查表）",
                "机检项会从 book.json 退化为全空")

    soloent_md = os.path.join(book.root, "SOLOENT.md")
    if os.path.isfile(soloent_md):
        rep.ok(where, "SOLOENT.md 存在")
    else:
        rep.err(where, "缺 SOLOENT.md（中央控制面板）")

    now = book.path("now")
    if now and os.path.isfile(now):
        rep.ok(where, "now.md 状态块存在")
    else:
        rep.err(where, "now.md 状态块不存在",
                "「正文改了但记忆没更新」这类问题就检测不出来")

    miss = [t for t in TOOL_SHELLS
            if not os.path.isfile(os.path.join(book.root, "tools", t))]
    if miss:
        rep.err(where, "tools/ 缺薄壳：" + "、".join(miss),
                '跑 sync.py --root "<书>"')
    else:
        rep.ok(where, "tools/ %d 个薄壳齐全" % len(TOOL_SHELLS))


def _missing_keys(base, have, prefix=""):
    """递归比对键集。缺「键」与键的值为空是两回事——前者意味着这段代码根本没被长出来。"""
    out = []
    for k, v in base.items():
        if k not in have:
            out.append(prefix + k)
        elif isinstance(v, dict) and isinstance(have.get(k), dict):
            out += _missing_keys(v, have[k], prefix + k + ".")
    return out


def check_writing_readiness(rep, book, name):
    """写作就绪度：风格/红线层填了没、机检项有没有空转、注入会不会被截断、薄壳能不能跑。

    为什么单列（2026-09-20 复盘）：这套体检此前只查「文件在不在、生成物新不新」，
    从不查「内容填了没、检查有没有开」。于是出现这样的事：某书
    `story-style.md`/`MASTER.md` 与模板 **byte 级一致**、`checks.rhythm` 还是 `null`，
    照样 24 通过 0 警告地写完 69 章——**四道内容防线全是空转，体检一句没提**。
    """
    where = name

    # 1) 风格/红线层（判据在 kit.style_doc_issues，与 brief.py 同源）
    issues = kit.style_doc_issues(book.root, book.cfg)
    if not issues:
        rep.ok(where, "风格/红线层已填写（story-style ／ MASTER ／ 预期）")
    for level, rel, why in issues:
        rep.warn(where, "写作前置未就绪：%s" % why,
                 "补齐 %s（模板在插件 templates/ 下）；占位符没填时 brief.py 会拦下每一章"
                 % rel)

    # 1b) 写作边界（拆书）——最上游的一层：管「这本书凭什么跟别人不一样」
    for level, why in kit.boundary_issues(book.root, book.cfg):
        if level == "block":
            rep.warn(where, "写作边界未就绪：%s" % why,
                     "按 docs/02 走一遍拆书（产出 1-边界/1.1_…），"
                     "或在 book.json 的 boundary.opt_out_reason 写明不拆的理由")
        else:
            rep.ok(where, "写作边界：没有对标作品也没拆书（允许，但缺参照系）")

    # 2) 机检项有没有「配了等于没配」
    C = book.sec("checks")
    R = C.get("rhythm")
    if not R or not R.get("enabled", True):
        rep.warn(where, "句长节奏机检未启用（checks.rhythm 为空 = 该项空转）",
                 "节奏/清单体是「读起来像流水账」的主因；按 story-style.md §3.1 填阈值")
    elif str(R.get("_tier") or "").lower() == "bootstrap":
        rep.warn(where, "句长节奏仍是「启动档」阈值（尚未按样板书校准）",
                 "硬门槛会拦错人也会放错人；拆书后按实测回调 story-style.md §3.1")

    # 3) 注入体积：超了就会被 hook 截断，而截断只写在注入正文里（人看不见）
    canon = book.path("canon")
    if canon and os.path.isfile(canon):
        size = kit.str_bytes(_read(canon) or "")
        if size > MAX_CANON_BYTES:
            rep.warn(where, "正典速查表 %d KB 超过注入上限 %d KB——**正在被截断**"
                     % (size // 1024, MAX_CANON_BYTES // 1024),
                     "精简 canon.md；被截断的部分每次会话都进不了上下文")
        elif size * 100 // MAX_CANON_BYTES >= 80:
            rep.warn(where, "正典速查表已用掉注入预算的 %d%%（%d KB / %d KB）"
                     % (size * 100 // MAX_CANON_BYTES, size // 1024, MAX_CANON_BYTES // 1024),
                     "长篇后期会继续长；趁早按「摘要留面板、细节存分档」瘦身")
    if _inject is not None:
        try:
            st = _inject.soloent_status(book)
            if "已截断" in st:
                rep.warn(where, "SOLOENT.md §7/§8 超过注入上限（%d 字节）——**正在被截断**"
                         % _inject.MAX_SOLOENT_STATUS_BYTES,
                         "面板只放摘要；细节搬去 .soloent/memory/now.md")
        except Exception:
            pass
    rule_bytes = 0
    vault = book.vault or ""
    for rel in ((book.cfg.get("rules") or {}).get("load") or []):
        p = os.path.join(vault, "rules", str(rel).replace("/", os.sep))
        if os.path.isfile(p):
            rule_bytes += kit.str_bytes(_read(p) or "")
    if rule_bytes > MAX_RULES_BYTES:
        rep.warn(where, "文风规则合计 %d KB 超过注入上限 %d KB——**正在被截断**"
                 % (rule_bytes // 1024, MAX_RULES_BYTES // 1024),
                 "规则文件瘦身，或减少 rules.load 条目（注入上限在 hooks/inject_canon.py）")

    # 4) 薄壳能不能跑：tools/*.py 里写死了插件绝对路径，插件挪了位置就全部失效
    stale = []
    for shell in TOOL_SHELLS:
        p = os.path.join(book.root, "tools", shell)
        if not os.path.isfile(p):
            continue
        m = re.search(r'KIT_TOOLS\s*=\s*r?"([^"]+)"', _read(p) or "")
        if m and os.path.normcase(m.group(1)) != os.path.normcase(kit.KIT_ROOT):
            stale.append(shell)
    if stale:
        rep.err(where, "tools/ 薄壳指向的插件路径已失效：%s" % "、".join(stale[:5]),
                "插件挪过位置。跑 `python <插件>/scripts/sync.py --root \"%s\"` 重写薄壳"
                % book.root)
    elif TOOL_SHELLS:
        rep.ok(where, "tools/ 薄壳指向当前插件位置")


def check_skeleton(rep, book, name):
    """骨架完整性：新书初始化该有的东西是否齐全。

    存量书从旧体系迁移过来时最容易漏这两样——漏了不报任何错，
    但防跑偏的锚点和后续机检会长不出来，属于「体检全绿其实有洞」的那类问题。
    """
    where = name

    # 1) 1-边界/预期.md —— 铁律二（每阶段开始前回看）依赖的防跑偏锚点
    yq = os.path.join(book.root, "1-边界", "预期.md")
    if os.path.isfile(yq):
        rep.ok(where, "1-边界/预期.md 存在（防跑偏锚点）")
    else:
        rep.warn(where, "缺 1-边界/预期.md",
                 "铁律二要求每阶段开始前回看它；模板在插件 templates/预期.md，"
                 "事实部分可从创作宪法/全局架构回抄，主观项留给作者")

    # 2) book.json 结构：与 init_book 的基线逐键比对
    try:
        import init_book
        base = init_book.build_config({
            "dir": book.root, "title": "x", "genre": "x", "platform": "x",
            "tags": "urban", "words": 2400, "length": "x",
        })
        miss = _missing_keys(base, book.cfg)
    except Exception:
        return
    if miss:
        shown = "、".join(miss[:6]) + ("…" if len(miss) > 6 else "")
        rep.warn(where, "book.json 缺 %d 个基线键：%s" % (len(miss), shown),
                 "补成 null 即可（工具遇 null 跳过，行为不变）；"
                 "真值按 docs/03 等设定定案后再填")
    else:
        rep.ok(where, "book.json 结构与基线一致")

    # 3) now.md 的「流程留痕」小节 —— preflight 的 flow_trace 依赖它
    nowp = os.path.join(book.soloent, "memory", "now.md")
    if not os.path.isfile(nowp):
        rep.warn(where, "缺 .soloent/memory/now.md", "闸门的状态同步与流程留痕都读它")
        return
    try:
        with open(nowp, encoding="utf-8-sig") as f:
            txt = f.read()
    except OSError:
        rep.warn(where, "now.md 读取失败", "闸门的流程留痕校验会因此拦下每一章")
        return
    if "流程留痕" in txt:
        rep.ok(where, "now.md 含「流程留痕」小节（闸门校验用）")
    else:
        rep.warn(where, "now.md 缺「流程留痕」小节",
                 "preflight.py 每章都校验 now.md 里的 [流程] 行；没有这一小节，"
                 "每章过闸都会被拦。模板在插件 templates/now.md")


def check_chapters(rep, book, name):
    where = name
    if not os.path.isdir(book.chapters_dir):
        rep.err(where, "chapters/ 目录不存在")
        return None
    chaps = book.chapter_files()
    if not chaps:
        rep.err(where, "chapters/ 下没有符合命名规则的章")
        return None
    nums = [n for n, _, _ in chaps]
    rep.ok(where, "章节 %d 章（第 %d–%d 章）" % (len(nums), min(nums), max(nums)))

    gaps = [i for i in range(min(nums), max(nums) + 1) if i not in set(nums)]
    if gaps:
        rep.err(where, "章号缺号：" + "、".join("第 %d 章" % g for g in gaps[:10]))
    else:
        rep.ok(where, "章号连续无缺号")

    stray = book.stray_files()
    if stray:
        rep.warn(where, "chapters/ 下 %d 个 .md 未被纳入检查：%s"
                 % (len(stray), "、".join(stray[:5])),
                 "命名不符 file_regex，这些章等于没被对账")
    return chaps


def check_progress(rep, book, name, chaps):
    """checkpoint / now.md 状态块 / 实际章数 三者是否对齐。"""
    where = name
    if not chaps:
        return
    actual = max(n for n, _, _ in chaps)

    ck = os.path.join(book.soloent, "checkpoint.json")
    ck_n = None
    if os.path.isfile(ck):
        try:
            ck_n = int((json.loads(_read(ck) or "{}")
                        or {}).get("last_verified_chapter") or 0)
        except (ValueError, TypeError):
            ck_n = None
        if ck_n is None:
            rep.warn(where, "checkpoint.json 读不出章号")
        elif ck_n < actual:
            rep.warn(where, "校验点停在第 %d 章，正文已到第 %d 章（%d 章未过闸）"
                     % (ck_n, actual, actual - ck_n),
                     "跑 tools/preflight.py 推进")
        else:
            rep.ok(where, "校验点第 %d 章 ≥ 正文第 %d 章" % (ck_n, actual))
    else:
        rep.warn(where, "还没有 checkpoint.json（跑一次 preflight.py 就会生成）")

    # now.md 状态块里的章号
    now = book.path("now")
    if now and os.path.isfile(now):
        txt = _read(now) or ""
        m = re.search(r"(?:narrative_verified|已核对至|当前章节)\s*[:：]?\s*第?\s*(\d+)", txt)
        if m:
            nn = int(m.group(1))
            if nn < actual:
                rep.warn(where, "now.md 写第 %d 章，正文已到第 %d 章" % (nn, actual),
                         "把状态补成当前值，再改 narrative_verified")
            else:
                rep.ok(where, "now.md 章号（第 %d 章）已跟上正文" % nn)
        else:
            rep.warn(where, "now.md 里没找到章号字段（narrative_verified）")


def check_drift(rep, book, name):
    """正文 hash vs state_hashes.json 基线。

    这是「改了正文但没有重新过闸」的信号。它平时会被 mtime 序掩盖 ——
    一旦有人碰过文件（备份还原、git checkout、误存盘），就会突然冒出来。
    """
    where = name
    try:
        import state_sync
    except Exception:
        return
    base = state_sync.load_hashes(book)
    if base is None:
        rep.ok(where, "正文基线尚未建立（下次过闸时自动建立）")
        return
    cur = state_sync.chapter_hashes(book)
    diff = sorted(fn for fn, h in cur.items() if base.get(fn) != h)
    if not diff:
        rep.ok(where, "正文 %d 章与基线一致" % len(cur))
    else:
        rep.warn(where, "%d 章内容与基线不一致：%s"
                 % (len(diff), "、".join(diff[:6])),
                 "若这些改动是已核对过的，重跑 preflight.py 重建基线；"
                 "否则说明改了正文没过闸")


def _zcode_is_live(book):
    """当前 harness 是否真的会加载 `.zcode/config.json`。

    判据：本机存在 CodeBuddy 系配置（`~/.codebuddy/` 或项目 `.codebuddy/`）时，
    说明跑的是 CodeBuddy／WorkBuddy harness —— 它的钩子来源只有
    `~/.codebuddy/settings.json`、`<项目根>/.codebuddy/settings.json`、插件 `hooks/hooks.json`，
    **不读 `.zcode/`**。此时按 `.zcode/` 部署的钩子永不触发，体检**不能报绿**。
    """
    if os.path.isdir(os.path.expanduser("~/.codebuddy")):
        return False
    if os.path.isdir(os.path.join(book.root, ".codebuddy")):
        return False
    return True


def _watcher_procs(book):
    """扫进程，返回正在监视本书的 watch_and_check.py 进程 PID。

    必须覆盖两种命令行形态：
      A) `<kit>/scripts/watch_and_check.py --root "<书>"`  —— 当前 start_watcher.ps1 生成
      B) `<书>/tools/watch_and_check.py`                   —— 早期薄壳，无 --root（本机在跑的就是这种）

    为什么不只看 pid 文件：进程被强杀、或插件更新后重启，pid 文件会丢，
    而 watcher 还在跑 —— 只看文件会把「正在生效的防护」报成「没开」。
    """
    out = []
    root = os.path.abspath(book.root).lower()
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                  "ForEach-Object { $_.ProcessId.ToString() + '||' + $_.CommandLine }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=25)
            for line in (r.stdout or "").splitlines():
                if "watch_and_check.py" not in line:
                    continue
                pid, _, cl = line.partition("||")
                if root in cl.lower():
                    out.append(pid.strip())
        else:
            r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
            for line in (r.stdout or "").splitlines():
                if "watch_and_check.py" in line and root in line.lower():
                    out.append(line.split(None, 1)[0].strip())
    except Exception:  # noqa: BLE001  探测失败按「没扫到」处理，不误报
        pass
    return out


def check_hooks(rep, book, name):
    where = name

    # ① 本 harness（CodeBuddy / WorkBuddy）真正读的是这份 —— 优先检查它
    cb = os.path.join(book.root, ".codebuddy", "settings.json")
    if os.path.isfile(cb):
        try:
            d = json.loads(_read(cb) or "{}")
        except ValueError:
            rep.err(where, ".codebuddy/settings.json 不是合法 JSON")
            return
        cmds = []
        for grps in (d.get("hooks") or {}).values():
            if not isinstance(grps, list):
                continue
            for g in grps:
                for h in (g.get("hooks") or []):
                    if isinstance(h.get("command"), str):
                        cmds.append(h["command"])
        if not cmds:
            rep.warn(where, ".codebuddy/settings.json 里没有可用的 command 钩子",
                     '跑 sync.py --root "<书>" 重新生成')
            return
        miss = []
        for c in cmds:
            for p in re.findall(r'"([^"]+\.py)"', c):
                if not os.path.isfile(p.replace("/", os.sep)):
                    miss.append(p)
        if miss:
            rep.err(where, "钩子指向的脚本不存在：%s" % miss[0],
                    "插件挪过位置？重跑 sync.py 会重写成当前绝对路径")
        else:
            rep.ok(where, ".codebuddy/settings.json 已就位（%d 个钩子，本 harness 生效）" % len(cmds))
        return

    # ② 退回到 ZCode 那份配置（schema 不同，只在 ZCode 环境生效）
    cfg = os.path.join(book.root, ".zcode", "config.json")
    if not os.path.isfile(cfg):
        rep.warn(where, "没有钩子配置（.codebuddy/settings.json 与 .zcode/config.json 都不在）"
                 " —— 打开这本书时钩子不会生效",
                 '跑 sync.py --root "<书>"')
        return
    try:
        d = json.loads(_read(cfg) or "{}")
    except ValueError:
        rep.err(where, ".zcode/config.json 不是合法 JSON")
        return

    paths = []
    for ev in (d.get("hooks") or {}).get("events", {}).values():
        for grp in ev:
            for h in grp.get("hooks", []):
                paths.extend(h.get("args") or [])
    py = [p for p in paths if p.endswith(".py")]
    if not py:
        rep.err(where, ".zcode/config.json 里没有 .py 钩子路径")
        return

    rel = [p for p in py if "${" in p]
    if rel:
        rep.err(where, "钩子用了相对路径变量（%d 处）：%s" % (len(rel), rel[0]),
                "相对路径只在「工作区根目录 = novel/」时成立；"
                "打开书文件夹时会静默失效。改成绝对路径。")
    else:
        rep.ok(where, "钩子路径均为绝对路径")

    dead = [p for p in py if not os.path.isfile(p.replace("/", os.sep))]
    if dead:
        rep.err(where, "钩子指向的文件不存在（%d 处）：%s" % (len(dead), dead[0]),
                "插件挪过位置？重跑 sync.py 会重写成当前绝对路径")
    else:
        rep.ok(where, "%d 个钩子文件均存在" % len(py))

    # 配置本身没问题 ≠ 钩子会触发 —— 还要看当前 harness 认不认这个路径。
    # 少了这一步，就是「用插件自己的 schema 自证」：本 harness 下报全绿，而钩子从不触发。
    if not _zcode_is_live(book):
        rep.warn(where,
                 "钩子配置写在 .zcode/config.json，但当前 harness 不读它 —— 钩子不会触发",
                 "本 harness 的钩子来源是 <项目根>/.codebuddy/settings.json 或插件 hooks/hooks.json；"
                 "插件目前只产 .zcode 格式。详见 docs/10。")


def check_watcher(rep, book, name, do_fix, quick=False):
    """watcher 是否在跑。

    **按进程判断，pid 文件只作辅助。** 原实现只看 pid 文件：
    文件不在就报「未在运行」——而本机两本书的 watcher 一直在跑（PID 45096／21156），
    pid 文件却是空的（插件更新／强杀会丢），于是把**正在生效的防护报成「没开」**。
    """
    where = name
    pidf = os.path.join(book.soloent, "watcher.pid")
    raw = (_read(pidf) or "").strip() if os.path.isfile(pidf) else ""

    if quick:
        # 会话启动时跑，不能起 PowerShell 扫进程（拖慢开场）。
        # 措辞必须诚实：不能说成「未在运行」——那正是本检查原先的反向假绿。
        if raw:
            rep.ok(where, "watcher.pid 存在（PID %s）；快速模式未扫进程" % raw)
        else:
            rep.ok(where, "无 watcher.pid；快速模式未扫进程（全量体检可确认）")
        return

    procs = _watcher_procs(book)
    if procs:
        extra = "" if raw else "（pid 文件缺失——插件更新或强杀会丢，不影响防护）"
        rep.ok(where, "watcher 正在运行（PID %s）%s" % ("、".join(procs), extra))
        return
    if raw:
        if do_fix:
            try:
                os.remove(pidf)
                rep.ok(where, "已清理死掉的 watcher.pid（PID %s）" % raw)
            except OSError as e:
                rep.warn(where, "想删 watcher.pid 但失败了：%s" % e)
        else:
            rep.warn(where, "watcher.pid 里的进程已死（PID %s）——"
                     "会让 start_watcher.ps1 误判「已在运行」" % raw,
                     "加 --fix 自动清理，或手动删除 .soloent/watcher.pid")
        return
    rep.ok(where, "watcher 未在运行（已扫进程确认）")


def check_generated(rep, book, name, quick=False):
    """生成物是否漂移（复用 sync.py --check，只读）。

    `--quick` 下跳过——它要起子进程，会话启动时跑会拖慢开场。
    慢检查留给手动全量体检。
    """
    where = name
    if quick:
        return
    sp = os.path.join(KIT_ROOT, "sync.py")
    if not os.path.isfile(sp):
        return
    try:
        r = subprocess.run([sys.executable, sp, "--root", book.root, "--check"],
                           capture_output=True, text=True,
                           encoding="utf-8", timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        rep.warn(where, "跑 sync.py --check 失败：%s" % e)
        return
    miss = [l.strip() for l in out.splitlines() if l.strip().startswith("缺失：")]
    drift = [l.strip() for l in out.splitlines() if l.strip().startswith("漂移：")]
    if miss or drift:
        rep.warn(where, "生成物有 %d 缺失 / %d 漂移" % (len(miss), len(drift)),
                 '跑：python "%s" --root "%s"' % (sp, book.root))
    else:
        rep.ok(where, "生成物已是最新")


def check_assets(rep, book, name):
    """rules.load 声明的 Rule 是否真的部署进 .soloent/rules/。"""
    where = name
    load = book.sec("rules").get("load") or []
    if not load:
        return
    rd = os.path.join(book.soloent, "rules")
    if not os.path.isdir(rd):
        rep.err(where, "book.json 声明加载 %d 条 Rule，但 .soloent/rules/ 不存在" % len(load),
                "跑 sync.py 部署")
        return
    have = set()
    for cur, _dirs, files in os.walk(rd):
        for f in files:
            have.add(os.path.relpath(os.path.join(cur, f), rd).replace("\\", "/"))
    miss = []
    for want in load:
        w = str(want)
        if not any(w in h or h.endswith(w) or h.endswith(w + ".md") for h in have):
            miss.append(w)
    if miss:
        rep.err(where, "声明要加载但未部署的 Rule：%s" % "、".join(miss[:6]),
                "跑 sync.py 部署")
    else:
        rep.ok(where, "Rule 已部署 %d 条（与 rules.load 一致）" % len(load))


def check_ledger(rep, book, name, chaps):
    """账本是否覆盖每一章。

    ⚠️ 别数行数——账本里既有 `#` 注释块，表头本身也可能以 `#` 开头。
    早期版本先过滤掉注释、再减 1 当表头，**表头被减了两次**，
    于是每本书都凭空少一行，报出「有章没记账」的假警报。
    正确做法是**直接解析第一列的章号**去比对，不数行。
    """
    where = name
    lp = book.path("ledger") or os.path.join(book.soloent, "ledger.tsv")
    if not os.path.isfile(lp):
        rep.warn(where, "账本 ledger.tsv 不存在（跨章数值对账会退化）")
        return

    have = set()
    for line in (_read(lp) or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cell = re.split(r"\t|\s{2,}|\s", s, 1)[0].strip()
        if not re.match(r"^\d+$", cell):
            continue  # 表头（"ch"）或说明行
        have.add(int(cell))

    if not have:
        rep.warn(where, "账本里没有解析出任何章号")
        return

    if not chaps:
        rep.ok(where, "账本 %d 章" % len(have))
        return

    want = set(n for n, _, _ in chaps)
    miss = sorted(want - have)
    extra = sorted(have - want)
    if miss:
        rep.warn(where, "%d 章没记账：%s"
                 % (len(miss), "、".join("第 %d 章" % m for m in miss[:10])),
                 "跨章数值对账对这几章是瞎的")
    else:
        rep.ok(where, "账本覆盖全部 %d 章" % len(want))
    if extra:
        rep.warn(where, "账本里有 %d 个章号在 chapters/ 中不存在：%s"
                 % (len(extra), "、".join("第 %d 章" % e for e in extra[:10])))


def _git_available():
    import shutil
    return shutil.which("git") is not None


def check_secrets(rep, book, name):
    """密钥安全（NW-P1-007）：.env / .soloent/.api.env 绝不能被 Git 跟踪。

    只读：不 git add、不改 .gitignore、不碰密钥内容。
    Git 不可用或不在仓库内时只记通过项，不报错——没仓库就没有「入库」这回事。
    """
    where = name + " · 密钥"
    gi = _read(os.path.join(book.root, ".gitignore")) or ""
    missing = [p for p in SECRET_FILES if p not in gi]
    if missing:
        rep.warn(where, ".gitignore 未忽略：" + "、".join(missing),
                 '跑 sync.py --root "<书>" 会安全合并托管段，不会动你的自定义行')
    else:
        rep.ok(where, ".gitignore 已忽略密钥文件")
    if not _git_available():
        return
    try:
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                cwd=book.root, capture_output=True, text=True,
                                errors="replace", timeout=10)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            rep.ok(where, "不在 Git 仓库内（密钥无入库风险）")
            return
        for rel in SECRET_FILES:
            if not os.path.isfile(os.path.join(book.root, rel)):
                continue
            tracked = subprocess.run(["git", "ls-files", "--", rel],
                                     cwd=book.root, capture_output=True, text=True,
                                     errors="replace", timeout=10)
            if (tracked.stdout or "").strip():
                rep.err(where, f"{rel} 已被 Git 跟踪——密钥可能已进提交历史",
                        f'git rm --cached "{rel}" 后提交；历史里已有过的密钥应直接轮换')
            else:
                rep.ok(where, f"{rel} 未被 Git 跟踪")
    except Exception as e:
        rep.warn(where, "Git 跟踪状态检查未完成：%r" % e)


def check_book(root, rep, do_fix, quick=False):
    book = kit.load_book(["--root", root], required=False)
    if book is None:
        rep.err(os.path.basename(root) or root, ".soloent/book.json 读不出来")
        return
    name = book.sec("book").get("title") or os.path.basename(root.rstrip("\\/"))
    check_five_pieces(rep, book, name)
    check_skeleton(rep, book, name)
    check_writing_readiness(rep, book, name)
    chaps = check_chapters(rep, book, name)
    check_progress(rep, book, name, chaps)
    check_drift(rep, book, name)
    check_hooks(rep, book, name)
    check_watcher(rep, book, name, do_fix, quick=quick)
    check_generated(rep, book, name, quick)
    check_assets(rep, book, name)
    check_ledger(rep, book, name, chaps)
    check_secrets(rep, book, name)


# ---------------------------------------------------------------- 输出

def render(rep, books_n):
    c = rep.counts()
    print("=" * 58)
    print("系统体检 ｜ 插件：%s" % PLUGIN_ROOT)
    print("检查范围：插件本体 · 已装副本 · %d 本书" % books_n)
    print("=" * 58)
    cur = None
    for level, where, what, how in rep.rows:
        if where != cur:
            print("\n── %s ──" % where)
            cur = where
        print("  %s %s" % (ICON[level], what))
        if how and level != OK:
            print("      → %s" % how)
    print("\n" + "=" * 58)
    print("通过 %d ｜ 警告 %d ｜ 错误 %d" % (c[OK], c[WARN], c[ERR]))
    if c[ERR]:
        print("结果：❌ 有错误，防护存在缺口。")
    elif c[WARN]:
        print("结果：⚠️  能跑，但有隐患。")
    else:
        print("结果：✅ 全部通过，防护都在生效。")
    print("=" * 58)


def main():
    argv = sys.argv[1:]
    do_fix = "--fix" in argv
    as_json = "--json" in argv
    quiet = "--quiet" in argv

    quick = "--quick" in argv

    rep = Report(quiet=quiet)
    if quick:
        # 快速模式：只查"会不会让防护失效"的硬项，跳过所有起子进程的检查，
        # 保证会话启动时跑完 <1 秒。慢项留给手动全量体检。
        check_plugin(rep)
    else:
        check_plugin(rep)
        check_installed(rep)

    roots = []
    lib = None
    for i, a in enumerate(argv):
        if a == "--lib" and i + 1 < len(argv):
            lib = argv[i + 1]
        if a.startswith("--lib="):
            lib = a.split("=", 1)[1]
    if lib:
        lib = os.path.abspath(lib)
        if not os.path.isdir(lib):
            print("⛔ 作品库目录不存在：%s" % lib)
            return 2
        roots = [os.path.join(lib, n) for n in sorted(os.listdir(lib))
                 if os.path.isfile(os.path.join(lib, n, CONFIG_REL))]
        if not roots:
            print("⛔ %s 下没有找到任何书（缺 %s）" % (lib, CONFIG_REL))
            return 2
    else:
        r = kit.root_arg(argv) or kit.find_book_root()
        if not r:
            me = os.path.abspath(__file__)
            print("⛔ 没定位到书。用法：")
            print('  python "%s" --root "<书目录>"' % me)
            print('  python "%s" --lib "D:/1-work/novel"' % me)
            return 2
        roots = [r]

    for root in roots:
        name = os.path.basename(root.rstrip("\\/"))
        # 先逐本自检配置，再进体检。
        # 为什么：`kit.load_book()` 遇到坏配置是 `sys.exit(2)`，而 `SystemExit` 属于
        # BaseException，下面的 `except Exception` **接不住**——于是库级体检遇到
        # 第一本坏书就整体中止，剩下的书一本都不报（2026-09-20 写用例时实测）。
        # 「一次看全库」正是 --lib 的唯一价值，不能在第一本上断掉。
        try:
            with open(os.path.join(root, CONFIG_REL), encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            rep.err(name, "book.json 读不了：%s（%s）" % (type(e).__name__, e),
                    "修好它，或跑 scripts/migrate_config.py --root \"%s\"；其余书会继续体检" % root)
            continue
        problems = kit.config_problems(cfg)
        if problems:
            shown = "；".join(problems[:3]) + ("…" if len(problems) > 3 else "")
            rep.err(name, "book.json 结构校验未通过：%s" % shown,
                    "跑 scripts/migrate_config.py --root \"%s\" 或按提示逐项修" % root)
            continue
        try:
            check_book(root, rep, do_fix, quick)
        except (Exception, SystemExit) as e:  # 一本书炸了不能连累其余的
            rep.err(name, "体检过程异常：%r" % e)

    if as_json:
        print(json.dumps(
            [{"level": lv, "where": w, "what": x, "how": h} for lv, w, x, h in rep.rows],
            ensure_ascii=False, indent=2))
    else:
        render(rep, len(roots))
    return rep.worst()


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
