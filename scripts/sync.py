#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync.py —— 给书目录生成/校验「薄壳」，终结 tools/ 跨书漂移

问题背景：历史上两本书各自复制了一份完整 tools/，结果 6 个脚本全部漂移
（同一个 ledger.py，一本 225 行、另一本 330 行，改进互不回流）。

本方案：
  · **真身**只有一份，在本插件的 `scripts/`（与书无关，代码通用）。
  · 每本书的 `tools/` 只放**薄壳**——内容固定、不含任何本书逻辑，
    唯一作用是让 `python tools/preflight.py` 这种命令继续可用。
  · 薄壳由本脚本生成，**手改会在下次 sync 时被覆盖**。

用法：
    python <插件>/scripts/sync.py --root "D:/1-work/novel/某本书"   # 生成/刷新薄壳+AGENTS.md
    python <插件>/scripts/sync.py --root ... --check               # 只检查是否与真身一致（不写）
    python <插件>/scripts/sync.py --all                            # 作品库下所有书一起处理
退出码：0=一致或已写入  1=--check 模式下发现漂移

（原文档字符串写的是 `_agent/sync.py`——那是上一代 SoloEnt 的目录，早已不存在；
  照它敲命令只会找不到文件。2026-09-20 修正。）
"""
import filecmp
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

KIT = kit.KIT_ROOT
TOOLS = ["brief.py", "draft.py", "consistency_check.py", "state_sync.py",
         "ledger.py", "preflight.py", "watch_and_check.py", "doctor.py"]
HOOK_CONFIGS = {
    os.path.normcase(os.path.normpath(os.path.join(".codebuddy", "settings.json"))),
    os.path.normcase(os.path.normpath(os.path.join(".zcode", "config.json"))),
}
HOOK_MARKERS = {
    "SessionStart": "inject_canon.py",
    "PostToolUse": "after_chapter_write.py",
}
GITIGNORE_KEY = os.path.normcase(".gitignore")

# .gitignore 的托管段（NW-P1-007）：密钥文件绝不能进 Git。
# 用户自己的忽略规则写在托管段之外，sync 只替换段内内容，绝不整文件覆盖。
GITIGNORE_START = "# >>> novel-writing (managed) >>>"
GITIGNORE_END = "# <<< novel-writing (managed) <<<"
GITIGNORE_BLOCK = "\n".join([
    GITIGNORE_START,
    "# 密钥与本地环境（拆书脚本读取 API Key 的位置）——改动前先读 docs/13-版本控制.md",
    ".env",
    ".env.*",
    "!.env.example",
    ".soloent/.api.env",
    "# 运行时产物",
    ".soloent/watcher.pid",
    ".soloent/watch_state.json",
    "notes/watcher.*.log",
    "notes/对账日志.md",
    "__pycache__/",
    "*.pyc",
    GITIGNORE_END,
]) + "\n"


def merge_gitignore(existing, block):
    """保留用户行，替换托管段；历史遗留的旧托管段一并清掉。"""
    lines, in_block = [], False
    for line in existing.replace("\r\n", "\n").split("\n"):
        marker = line.strip()
        if marker == GITIGNORE_START:
            in_block = True
            continue
        if marker == GITIGNORE_END:
            in_block = False
            continue
        if not in_block:
            lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        return "\n".join(lines) + "\n\n" + block
    return block

# 需要部署到 <书>/.soloent/ 下的打包资产：assets 下的子目录 -> 书内目标目录
#
# 为什么必须部署：SoloEnt 只从**固定目录**读取 Rule / Workflow / Skill——
#   · Rule     → Rules 目录（项目级是 .soloent/rules/），装进去才「自动生效」
#   · Workflow → .soloent/workflows/，装进去才会出现在 `/` 命令菜单里
#   · Skill    → .soloent/skills/，装进去才会出现在 Skills 面板
# 放在 assets/ 里的库**不会被识别**。早期版本漏了这一步，结果是
# 「工作流明明打包进来了，用户却打不出 /chapter-review」。
DEPLOY = [
    ("rules", os.path.join(".soloent", "rules")),
    ("workflows", os.path.join(".soloent", "workflows")),
]
# 技能体积大（100+ 文件），默认不部署；需要时用 --with-skills 打开
DEPLOY_OPTIONAL = [
    ("skills", os.path.join(".soloent", "skills")),
]

SHELL_TMPL = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""{name} —— 薄壳。真身在 {kit}\\{name}

本文件由 novel-writing 插件的 sync.py 生成，**请勿手改**（改动会在下次 sync 时被覆盖）。
它不含任何本书逻辑：所有随书变化的值都在 .soloent/book.json。

（文档字符串用 raw 前缀是必须的：路径里的反斜杠会被当成转义序列并抛 SyntaxWarning。）
"""
import os
import sys

KIT_TOOLS = r"{kit_tools}"
BOOK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, KIT_TOOLS)
os.environ.setdefault("NOVEL_BOOK_ROOT", BOOK_ROOT)

import importlib  # noqa: E402
sys.exit(importlib.import_module("{mod}").main())
'''

WATCH_START = '''# 启动后台对账监视器（薄壳，由 novel-writing 插件的 sync.py 生成）
# 用法：powershell -ExecutionPolicy Bypass -File tools\\start_watcher.ps1
$root = Split-Path -Parent $PSScriptRoot
$kit  = "{kit}"
$pidFile = Join-Path $root ".soloent\\watcher.pid"
if (Test-Path $pidFile) {{
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {{
        Write-Host "watcher 已在运行（PID $old）"; exit 0
    }}
}}
$log = Join-Path $root "notes\\watcher.out.log"
$p = Start-Process -FilePath "python" `
     -ArgumentList "`"$kit\\watch_and_check.py`" --root `"$root`"" `
     -WorkingDirectory $root -WindowStyle Hidden -PassThru `
     -RedirectStandardOutput $log -RedirectStandardError ($log -replace "\\.out\\.", ".err.")
Write-Host "watcher 已启动（PID $($p.Id)），日志：$log"
'''

WATCH_STOP = '''# 停止后台对账监视器（薄壳，由 novel-writing 插件的 sync.py 生成）
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".soloent\\watcher.pid"
if (Test-Path $pidFile) {{
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old) {{
        Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
        Write-Host "已停止 watcher（PID $old）"
    }}
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}} else {{
    Write-Host "没有找到 watcher.pid，watcher 未在运行"
}}
'''

AGENTS_HEADER = "# AGENTS.md — 本书写作代理运行守则"


def _bullets(items, indent="   "):
    """列表 -> 带缩进的 bullet 文本。空则给出显式提示（而不是留白）。"""
    if not items:
        return f"{indent}（未配置）"
    return "\n".join(f"{indent}- `{x}`" if "/" in str(x) or str(x).endswith(".md") else f"{indent}- {x}"
                     for x in items)


def _subdirs(path):
    """目录下的子目录名（升序）。路径不存在返回空表——调用方负责把「没有」说清楚。"""
    try:
        return sorted(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))
    except OSError:
        return []


def _files_with_ext(path, ext):
    try:
        return [f for f in os.listdir(path) if f.endswith(ext)]
    except OSError:
        return []


def render_agents(book, kit_dir):
    """把 AGENTS.template.md + book.json 渲染成这本书的 AGENTS.md。

    模板顶部（`# AGENTS.md — 写作代理运行守则（通用层 · 模板）` 到第一个正标题之间）
    是给人看的使用说明，不进入渲染产物。
    """
    tpl_path = os.path.join(kit.ASSETS, "AGENTS.template.md")
    if not os.path.isfile(tpl_path):
        return None
    with open(tpl_path, encoding="utf-8-sig") as f:
        tpl = f.read()
    idx = tpl.find(AGENTS_HEADER)
    tpl = tpl[idx:] if idx >= 0 else tpl

    B = book.sec("book")
    C = book.sec("chapter")
    S = book.sec("state")
    R = book.sec("rules")
    L = book.sec("ledger")
    vault = book.vault

    def vpath(rel):
        """规则文件 -> 绝对路径。统一反斜杠，避免同一份清单里混着 / 和 \\。"""
        p = str(rel).replace("/", "\\")
        return f"{vault}\\rules\\{p}" if vault else p

    system = R.get("system") or ["ai-anti-patterns.md", "prose-directness.md"]
    system_set = {str(x).replace("\\", "/") for x in system}
    # rules.load 里可能顺手把系统级也写进去了；渲染时剔掉，避免同一份清单列两遍
    load_only = [x for x in (R.get("load") or []) if str(x).replace("\\", "/") not in system_set]

    derivable = set(S.get("derivable") or [])
    stamp = S.get("manual_stamp", "narrative_verified")
    state_fields = [f for f in (S.get("required") or []) if f not in derivable and f != stamp]

    subs = {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": B.get("title", ""),
        "slug": B.get("slug", ""),
        "genre": B.get("genre", ""),
        "platform": B.get("platform", ""),
        "length_goal": B.get("length_goal") or "（未配置）",
        "vault": vault,
        "kit": kit.PLUGIN_ROOT,
        "rules_system": "\n".join(f"   - `{vpath(x)}`" for x in system),
        "rules_load": _bullets([vpath(x) for x in load_only]),
        "rules_forbid": _bullets([vpath(x) for x in (R.get("forbid") or [])]),
        "forbid_reason": R.get("forbid_reason") or "（未配置）",
        "default_words": C.get("default_words") or "2400",
        "words_range": C.get("words_range") or "常态 2200–2600，区间 2000–3000",
        "state_fields": "、".join(f"`{f}`" for f in state_fields) or "（未配置）",
        "ledger_columns": " / ".join(L.get("columns") or []),
        "extra_discipline": "\n".join(f"  - {x}" for x in (B.get("extra_discipline") or [])) or "  （无）",
        "env_notes": "\n".join(f"{i}. {x}" for i, x in enumerate(B.get("env_notes") or [], 4)) or "",
        "model_note": B.get("model_note") or "（未配置）",
        # 技能/工作流的真实路径与数量——由脚本数出来，不写死在模板里。
        # 起因：模板曾写「plugins/ 内 30 个插件包，逐条路径见 plugins-manifest.md」，
        # 而磁盘上旧版既没装 plugins/ 也没装那个清单文件（目录叫 skills/），
        # 实际技能数是 13。写作 agent 照 §3 去找三连技能的路径 → 直接走进死路，
        # 而 audit 当时完全没查过 {{vault}} 路径（2026-09-20 发现）。
        "skills_dir": os.path.join(vault, "skills") if vault else "skills",
        "skills_count": str(len(_subdirs(os.path.join(vault, "skills")))) if vault else "?",
        "skills_list": "、".join(_subdirs(os.path.join(vault, "skills"))) or "（未打包）",
        "workflows_dir": os.path.join(vault, "workflows") if vault else "workflows",
        "workflows_count": str(len(_files_with_ext(os.path.join(vault, "workflows"), ".md"))) if vault else "?",
    }
    out = tpl
    for k, v in subs.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def shell_text(name, kit_dir):
    # 注意：插件里所有脚本与 kit.py **同级**（都在 scripts/），没有 tools/ 子目录。
    # 早期版本这里写成 os.path.join(kit_dir, "tools")，指向一个不存在的目录，
    # 结果 import 回退到书自己的 tools/ 并加载了薄壳自身 → AttributeError: no attribute 'main'。
    return SHELL_TMPL.format(name=name, kit=kit_dir, kit_tools=kit_dir, mod=name[:-3])


def deploy_map(book, with_skills=False):
    """-> {书内相对路径: 源文件绝对路径}。把打包的 Rule / Workflow（可选 Skill）部署进项目。

    ⚠️ **Rule 只部署本书 `rules.load` 声明的那几条**，不是全量。
    为什么：SoloEnt 的 Rule 是「安装后自动生效」的——把全部 14 条都放进去，
    一本都市高武的书会自动加载 `style-xianxia`（要求禁现代口语、对话须有古韵），
    与主角人设直接打架。**「禁载」在 SoloEnt 里靠"不部署"实现，在别的 harness 里靠 forbid 清单。**

    ⚠️ 已存在且内容不同的文件**不覆盖**——那可能是用户针对本书改过的定制版。
    """
    out = {}
    for sub, dest_dir in list(DEPLOY) + (DEPLOY_OPTIONAL if with_skills else []):
        src_dir = os.path.join(kit.ASSETS, sub)
        if not os.path.isdir(src_dir):
            continue
        if sub == "rules":
            # 只收 rules.load 里列出的（路径相对 rules/）
            allow = {str(x).replace("/", os.sep) for x in (book.sec("rules").get("load") or [])}
            for rel in sorted(allow):
                sp = os.path.join(src_dir, rel)
                if os.path.isfile(sp):
                    out[os.path.join(dest_dir, rel)] = sp
            continue
        for cur, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for fn in files:
                if fn.endswith((".pyc", ".DS_Store")):
                    continue
                sp = os.path.join(cur, fn)
                out[os.path.join(dest_dir, os.path.relpath(sp, src_dir))] = sp
    return out


def deploy_status(book, with_skills=False):
    """-> (待新增, 待更新, 已定制)。已定制的不会被覆盖。"""
    added, updated, customized = [], [], []
    for rel, sp in deploy_map(book, with_skills).items():
        dp = os.path.join(book.root, rel)
        if not os.path.isfile(dp):
            added.append(rel)
        elif not filecmp.cmp(sp, dp, shallow=False):
            # 区分「源更新了」与「用户改过」——无法完全区分，保守起见都不覆盖，
            # 只在内容差异时报告，让用户判断。
            customized.append(rel)
    return added, updated, customized


def do_deploy(book, with_skills=False):
    """执行部署。返回 (新增数, 跳过数)。"""
    added, _, customized = deploy_status(book, with_skills)
    for rel in added:
        sp = deploy_map(book, with_skills)[rel]
        dp = os.path.join(book.root, rel)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        shutil.copyfile(sp, dp)
    return len(added), len(customized)


def hook_config_codebuddy(kit_dir, lib_dir=""):
    """`<工作区根>/.codebuddy/settings.json` 的内容 —— 本 harness 真正会读的那份。

    为什么必须单独产一份：`.zcode/config.json` 用的是另一套 schema
    （`hooks.events.<Event>` ＋ `type:"process"` ＋ `command`/`args`），
    而本 harness 要求 `hooks.<Event>` ＋ `type:"command"` ＋ **单条 shell 字符串**，
    且只从 `~/.codebuddy/settings.json`、`<项目根>/.codebuddy/settings.json`、
    `<项目根>/.codebuddy/settings.local.json`、插件 `hooks/hooks.json` 四处读取。
    → 只产 `.zcode` 时，钩子在本 harness 下**永不触发**（2026-09-19 实测确认）。

    钩子脚本自己从「被写文件的路径」反推书根（见 `find_book()`），所以**不需要** `--root`，
    同一份配置对各书通用；路径仍写绝对路径，避免工作区打开位置影响解析。
    """
    hooks = os.path.join(os.path.dirname(kit_dir), "hooks").replace("\\", "/")
    # inject_canon 需要知道「作品库根」：工作区是插件目录（novel-writing）时，
    # 它在前三级范围内扫不到任何书，必须靠这个参数才能找到库下的书并注入正典。
    inject = 'python "%s/inject_canon.py"' % hooks
    if lib_dir:
        inject += ' --lib "%s"' % lib_dir.replace("\\", "/")
    return json.dumps({
        "hooks": {
            "SessionStart": [{
                "hooks": [{
                    "type": "command",
                    "command": inject,
                    "timeout": 15,
                }],
            }],
            "PostToolUse": [{
                "matcher": "Write|Edit",
                "hooks": [{
                    "type": "command",
                    "command": 'python "%s/after_chapter_write.py"' % hooks,
                    "timeout": 30,
                }],
            }],
        }
    }, ensure_ascii=False, indent=2) + "\n"


def hook_config(kit_dir, lib_dir=""):
    """`.zcode/config.json` 的内容 —— 给真正的 ZCode harness 用。

    ⚠️ **本 harness（CodeBuddy / WorkBuddy）不读这个文件**，只认 `.codebuddy/settings.json`
    （见 `hook_config_codebuddy()`）。保留它只是为了覆盖 ZCode 环境，两边 schema 不同。

    ⚠️ 路径**必须写绝对路径**到插件的 hooks/。
    曾经用 `${ZCODE_PROJECT_DIR}/../novel-writing/hooks/...` —— 那个相对路径只有在
    「工作区根目录 = novel/」时才解析得出来；打开某本书时它指向 `<书>/../novel-writing/`（不存在）
    → 钩子静默失效，且毫无报错。
    """
    hooks = os.path.join(os.path.dirname(kit_dir), "hooks")
    hooks = hooks.replace("\\", "/")
    inject_args = [f"{hooks}/inject_canon.py"]
    if lib_dir:
        inject_args += ["--lib", lib_dir.replace("\\", "/")]
    return json.dumps({
        "hooks": {
            "enabled": True,
            "timeoutMs": 30000,
            "events": {
                "SessionStart": [{
                    "hooks": [{
                        "type": "process",
                        "command": "python",
                        "args": inject_args,
                        "timeoutMs": 15000,
                        "statusMessage": "注入本书正典与写作纪律…",
                    }],
                }],
                "PostToolUse": [{
                    "matcher": "Write|Edit",
                    "hooks": [{
                        "type": "process",
                        "command": "python",
                        "args": [f"{hooks}/after_chapter_write.py"],
                        "timeoutMs": 30000,
                        "statusMessage": "章节对账…",
                    }],
                }],
            },
        }
    }, ensure_ascii=False, indent=2) + "\n"


def plan(book, kit_dir):
    """-> {相对路径: 期望内容}。全部是「生成物」，不含任何需要手写的东西。"""
    out = {}
    for n in TOOLS:
        out[os.path.join("tools", n)] = shell_text(n, kit_dir)
    out[os.path.join("tools", "start_watcher.ps1")] = WATCH_START.format(kit=kit_dir)
    out[os.path.join("tools", "stop_watcher.ps1")] = WATCH_STOP.format()
    rendered = render_agents(book, kit_dir)
    if rendered is not None:
        out["AGENTS.md"] = rendered
    # 每本书各带一份钩子配置 → 「打开具体作品的文件夹」也能生效（修 V4）
    # 作品库根 = 书的上一级。传给 inject_canon，让它在「工作区 = 插件目录」时
    # 也能扫到库下的书（否则 SessionStart 注入不了任何正典）。
    lib = os.path.dirname(os.path.abspath(book.root))
    out[os.path.join(".zcode", "config.json")] = hook_config(kit_dir, lib)
    # 本 harness（CodeBuddy / WorkBuddy）真正读的是这份 —— 少了它钩子不会触发
    out[os.path.join(".codebuddy", "settings.json")] = hook_config_codebuddy(kit_dir, lib)
    # .gitignore 托管段：只承诺段内的密钥/运行时排除，段外用户内容原样保留
    out[".gitignore"] = GITIGNORE_BLOCK
    return out


def _norm(s):
    """比较用归一化：统一换行、剔除「生成时间」行（否则 --check 每次都报漂移）。"""
    out = []
    for line in s.replace("\r\n", "\n").split("\n"):
        if line.startswith("> 生成时间："):
            continue
        out.append(line.rstrip())
    return "\n".join(out)


def _contains_marker(value, marker):
    return marker in json.dumps(value, ensure_ascii=False, sort_keys=True)


def _merge_hook_tree(existing, desired, path=()):
    """合并 Harness 配置：保留未知字段，只替换本插件拥有的 Hook 条目。"""
    if isinstance(desired, dict):
        out = dict(existing) if isinstance(existing, dict) else {}
        for key, value in desired.items():
            out[key] = _merge_hook_tree(out.get(key), value, path + (key,))
        return out
    if isinstance(desired, list) and path and path[-1] in HOOK_MARKERS:
        marker = HOOK_MARKERS[path[-1]]
        kept = [x for x in (existing if isinstance(existing, list) else [])
                if not _contains_marker(x, marker)]
        return kept + desired
    return desired


def merged_generated_content(root, rel, want):
    """返回写入内容；Harness JSON 与 .gitignore 安全合并，其他生成物原样返回。"""
    key = os.path.normcase(os.path.normpath(rel))
    path = os.path.join(root, rel)
    if key == GITIGNORE_KEY:
        if not os.path.isfile(path):
            return want
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                existing = f.read()
        except OSError as e:
            raise ValueError(f"已有 .gitignore 读不出来，拒绝覆盖：{path}（{e}）")
        return merge_gitignore(existing, want)
    if key not in HOOK_CONFIGS or not os.path.isfile(path):
        return want
    try:
        with open(path, encoding="utf-8-sig") as f:
            existing = json.load(f)
        desired = json.loads(want)
    except (OSError, ValueError) as e:
        raise ValueError(f"已有 Harness 配置不是有效 JSON，拒绝覆盖：{path}（{e}）")
    if not isinstance(existing, dict):
        raise ValueError(f"已有 Harness 配置根节点不是对象，拒绝覆盖：{path}")
    merged = _merge_hook_tree(existing, desired)
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def validate_plan(root, planned):
    """在任何写入前验证需要合并的已有生成物。"""
    for rel, want in planned.items():
        merged_generated_content(root, rel, want)


def write_generated(root, rel, want):
    content = merged_generated_content(root, rel, want)
    kit.atomic_write_text(os.path.join(root, rel), content)


def diff(book, kit_dir):
    """-> (漂移列表, 缺失列表)"""
    drift, missing = [], []
    for rel, want in plan(book, kit_dir).items():
        p = os.path.join(book.root, rel)
        if not os.path.isfile(p):
            missing.append(rel)
            continue
        with open(p, encoding="utf-8-sig") as f:
            have = f.read()
        effective = merged_generated_content(book.root, rel, want)
        if _norm(have) != _norm(effective):
            drift.append(rel)
    return drift, missing


def do_book(root, kit_dir, check_only, with_skills=False):
    book = kit.load_book(["--root", root], required=False)
    if book is None:
        print(f"⛔ {root} 的 .soloent/book.json 读不出来，跳过")
        return 2
    try:
        generated = plan(book, kit_dir)
        drift, missing = diff(book, kit_dir)
    except ValueError as e:
        print(f"⛔ {e}")
        return 2
    dep_added, _, dep_custom = deploy_status(book, with_skills)
    total = len(generated)

    if not drift and not missing and not dep_added:
        msg = f"生成物已是最新（{total} 个文件）"
        if dep_custom:
            msg += f"；另有 {len(dep_custom)} 个资产是本书定制版，未动"
        print(f"✅ {root}\n   {msg}")
        return 0

    if check_only:
        print(f"⚠️  {root}")
        for m in missing:
            print(f"   缺失：{m}")
        for d in drift:
            print(f"   漂移：{d}")
        for a in dep_added[:10]:
            print(f"   待部署：{a}")
        if len(dep_added) > 10:
            print(f"   …（共 {len(dep_added)} 个待部署）")
        if dep_custom:
            print(f"   已定制（不覆盖）：{len(dep_custom)} 个")
        print(f"   修复：python \"{os.path.join(KIT, 'sync.py')}\" --root \"{root}\"")
        return 1

    for rel, want in generated.items():
        write_generated(root, rel, want)
    n_added, n_custom = do_deploy(book, with_skills)

    print(f"✅ {root}")
    print(f"   生成物：写入 {len(missing)} 个缺失 + 刷新 {len(drift)} 个漂移（共 {total} 个）")
    if n_added or n_custom:
        print(f"   资产部署：新增 {n_added} 个"
              + (f"，{n_custom} 个已存在且内容不同，**未覆盖**（那是本书的定制版）" if n_custom else ""))
        if n_custom:
            print("     若要强制同步这些文件，先备份再手工替换——工具不会替你覆盖定制内容。")
    return 0


def do_lib(lib, kit_dir, check_only):
    """给「作品库根」写钩子配置。

    为什么需要：钩子读的是**工作区根**的 `.codebuddy/settings.json`。
    用户常在「作品库根」打开工作区（而不是某一本书），此时若只有书级配置，钩子不会触发。
    钩子脚本自己会从被写文件反推书根（`hooks/after_chapter_write.py` 的 `find_book()`），
    所以库根这一份能覆盖库下所有书 —— **包括将来新建的**，不必每加一本书就补一次配置。
    """
    want = {
        os.path.join(".codebuddy", "settings.json"): hook_config_codebuddy(kit_dir, lib),
        os.path.join(".zcode", "config.json"): hook_config(kit_dir, lib),
    }
    # 两处都要：① 作品库根（从库根打开工作区）② 插件目录（从 novel-writing 打开工作区）
    # —— 用户完全可能直接在插件目录里干活（写小说时就在那儿跟 agent 对话），
    # 少了插件目录这一份，那种情形下钩子不触发。
    targets = [lib, os.path.dirname(os.path.abspath(kit_dir))]
    rc = 0
    for tgt in targets:
        missing, drift, effective = [], [], {}
        for rel, content in want.items():
            p = os.path.join(tgt, rel)
            try:
                effective[rel] = merged_generated_content(tgt, rel, content)
            except ValueError as e:
                print(f"⛔ {e}")
                rc = 2
                effective = None
                break
            if not os.path.isfile(p):
                missing.append(rel)
                continue
            try:
                if _norm(open(p, encoding="utf-8-sig").read()) != _norm(effective[rel]):
                    drift.append(rel)
            except OSError:
                drift.append(rel)
        if effective is None:
            continue
        label = "作品库根" if tgt == lib else "插件目录"
        if not missing and not drift:
            print(f"✅ {label} {tgt}\n   钩子配置已是最新")
            continue
        if check_only:
            print(f"⚠️  {label} {tgt}")
            for m in missing:
                print(f"   缺失：{m}")
            for d in drift:
                print(f"   漂移：{d}")
            rc = 1
            continue
        for rel in missing + drift:
            kit.atomic_write_text(os.path.join(tgt, rel), effective[rel])
        tail = f" + 安全合并更新 {len(drift)} 个" if drift else ""
        print(f"✅ {label} {tgt}\n   写入 {len(missing)} 个钩子配置{tail}")
    return rc


def _lib_arg(argv):
    for i, a in enumerate(argv):
        if a == "--lib" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lib="):
            return a.split("=", 1)[1]
    return None


def main():
    argv = sys.argv[1:]
    kit_dir = KIT
    check_only = "--check" in argv
    with_skills = "--with-skills" in argv
    root = kit.root_arg(argv)
    me = os.path.join(KIT, "sync.py")

    if "--all" in argv:
        lib = _lib_arg(argv)
        if not lib:
            print("⛔ --all 需要指定作品库根目录：--lib \"D:/1-work/novel\"")
            print(f"   例：python \"{me}\" --all --lib \"D:/1-work/novel\"")
            return 2
        lib = os.path.abspath(lib)
        if not os.path.isdir(lib):
            print(f"⛔ 作品库目录不存在：{lib}")
            return 2
        books = [os.path.join(lib, n) for n in sorted(os.listdir(lib))
                 if os.path.isfile(os.path.join(lib, n, kit.CONFIG_REL))]
        rc = 0
        # 先给库根写钩子配置：这样「在作品库根打开工作区」时钩子也能触发，
        # 且一份覆盖库下所有书（含将来新建的），不必每加一本书补一次。
        rc |= do_lib(lib, kit_dir, check_only)
        if not books:
            print(f"⚠️  {lib} 下没有找到任何带 .soloent/book.json 的书（库根钩子配置已处理）")
            return rc
        for b in books:
            rc |= do_book(b, kit_dir, check_only, with_skills)
        return rc

    if not root:
        print("novel-writing · 生成物同步")
        print(f"  python \"{me}\" --root \"D:/1-work/novel/某本书\"      # 生成/刷新该书")
        print(f"  python \"{me}\" --all --lib \"D:/1-work/novel\"         # 刷新全部")
        print("  加 --check 只检查不写（可用于定期核对）")
        print("  加 --with-skills 连技能库一起部署进项目（默认只部署 Rule 与 Workflow）")
        return 2
    root = os.path.abspath(root)
    if not os.path.isfile(os.path.join(root, kit.CONFIG_REL)):
        print(f"⛔ {root} 下没有 .soloent/book.json —— 不是一本书的目录")
        return 2
    return do_book(root, kit_dir, check_only, with_skills)


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
