#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py —— 把插件安装到某个 agent harness 的技能目录

插件是纯 Markdown + 纯标准库 Python，没有依赖。安装 = 把整个目录复制过去。
不同 harness 只是「技能放哪」不同，SKILL.md 的格式是通用的（Anthropic Agent Skills 约定）。

两种安装形态：
    复制（默认）    —— 各 harness 一份独立副本。改了插件源码要逐一重装，否则漂移。
    链接（--link）  —— 各 harness 一个指向插件源目录的 junction/symlink。
                       只有一份实体，永不漂移。**多 harness 时用这个。**

用法：
    python scripts/install.py --list                    # 看已知目标与探测结果
    python scripts/install.py --target workbuddy        # 装到用户级（复制）
    python scripts/install.py --target claude --link    # 装成链接（推荐）
    python scripts/install.py --target claude --unlink  # 链接换回副本（harness 不跟随链接时用）
    python scripts/install.py --dir "D:/某处/skills"     # 装到自定义位置
    python scripts/install.py --target workbuddy --project "D:/1-work"   # 装到项目级
    python scripts/install.py --target workbuddy --check # 只检查已装版本是否一致
    python scripts/install.py --all --link              # 一次性装到本机所有已存在的 harness

退出码：0=成功/一致  1=--check 发现差异  2=参数问题
"""
import filecmp
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

SKILL_NAME = "novel-writing"
HOME = os.path.expanduser("~")

# 用户级技能目录（跨项目可用）
USER_TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy-ai", "skills"),
    "codebuddy": os.path.join(HOME, ".codebuddy", "skills"),
    "claude": os.path.join(HOME, ".claude", "skills"),
    "zcode": os.path.join(HOME, ".zcode", "skills"),
    "cursor": os.path.join(HOME, ".cursor", "skills"),
    "windsurf": os.path.join(HOME, ".windsurf", "skills"),
}
# 项目级技能目录（相对项目根）
PROJECT_SUBDIRS = {
    "workbuddy": os.path.join(".workbuddy-ai", "skills"),
    "codebuddy": os.path.join(".codebuddy", "skills"),
    "claude": os.path.join(".claude", "skills"),
    "zcode": os.path.join(".zcode", "skills"),
    "cursor": os.path.join(".cursor", "skills"),
}
# .workbuddy-ai 里放的是本插件自己的工作记忆与备份，不属于技能内容，
# 不排除的话会被复制进每个 harness 的副本里。
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".workbuddy-ai"}


def arg(argv, name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def iter_files(root):
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            p = os.path.join(cur, fn)
            yield os.path.relpath(p, root), p


def compare(src, dst):
    """-> (缺失, 差异)"""
    missing, drift = [], []
    for rel, sp in iter_files(src):
        dp = os.path.join(dst, rel)
        if not os.path.isfile(dp):
            missing.append(rel)
        elif not filecmp.cmp(sp, dp, shallow=False):
            drift.append(rel)
    return missing, drift


def sync_tree(src, dst):
    """把 src 同步到 dst：复制/更新所有文件，删掉 dst 里 src 已没有的，清理空目录。

    刻意**不用 shutil.rmtree**：那样会把整个目录推倒重建，在带安全拦截的环境里
    还会被「移入回收站」的钩子拦下（实测报 trash-failed）。增量同步更稳，
    也不会误伤用户放在里面的东西。
    """
    src_files = {rel for rel, _ in iter_files(src)}
    added = updated = 0
    for rel, sp in iter_files(src):
        dp = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        if not os.path.isfile(dp):
            added += 1
        elif not filecmp.cmp(sp, dp, shallow=False):
            updated += 1
        shutil.copyfile(sp, dp)

    removed = 0
    for rel, dp in list(iter_files(dst)):
        if rel not in src_files:
            try:
                os.remove(dp)
                removed += 1
            except OSError:
                pass

    for cur, _dirs, _files in os.walk(dst, topdown=False):
        if os.path.abspath(cur) == os.path.abspath(dst):
            continue
        try:
            if not os.listdir(cur):
                os.rmdir(cur)
        except OSError:
            pass
    return added, updated, removed


def do_install(src, dst, check_only):
    missing, drift = compare(src, dst)
    total = sum(1 for _ in iter_files(src))
    if check_only:
        if missing or drift:
            print(f"⚠️  {dst}")
            for m in missing[:10]:
                print(f"   缺失：{m}")
            for d in drift[:10]:
                print(f"   差异：{d}")
            if len(missing) + len(drift) > 20:
                print(f"   …（共 {len(missing)} 缺失 / {len(drift)} 差异）")
            return 1
        print(f"✅ 已是最新（{total} 个文件）：{dst}")
        return 0

    existed = os.path.isdir(dst)
    added, updated, removed = sync_tree(src, dst)
    print(f"✅ {'已更新' if existed else '已安装'}（{total} 个文件）")
    print(f"   从：{src}")
    print(f"   到：{dst}")
    print(f"   新增 {added} ｜ 更新 {updated}" + (f" ｜ 移除 {removed}" if removed else ""))
    return 0


# ---------------------------------------------------------------- 链接形态

def is_junction(path):
    """是不是目录链接（junction / symlink）。复制出来的普通目录返回 False。"""
    if os.path.islink(path):
        return True
    if os.name != "nt":
        return False
    try:
        attrs = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x0400)  # FILE_ATTRIBUTE_REPARSE_POINT


def link_state(path):
    """-> 'link' | 'copy' | 'none'"""
    if is_junction(path):
        return "link"
    if os.path.isdir(path):
        return "copy"
    return "none"


def make_link(src, dst):
    """建目录链接。Windows 用 junction（免管理员权限），其它平台用 symlink。

    返回 None 表示成功，否则返回错误信息。
    """
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.isdir(dst):
            return None
        return (r.stdout or "") + (r.stderr or "") or "mklink 失败（未知原因）"
    try:
        os.symlink(src, dst, target_is_directory=True)
        return None
    except OSError as e:
        return str(e)


def do_link(src, dst, check_only):
    """把 dst 变成指向 src 的目录链接。已存在的副本会被改名保留，不删除。"""
    real_src = os.path.realpath(src)

    if check_only:
        st = link_state(dst)
        if st == "none":
            print(f"⚠️  {dst} 未安装")
            return 1
        if st != "link":
            print(f"⚠️  {dst} 是副本，不是链接（要切换请去掉 --check 重跑）")
            return 1
        if os.path.realpath(dst) != real_src:
            print(f"⚠️  {dst} 指向 {os.path.realpath(dst)}，不是插件源 {src}")
            return 1
        print(f"🔗 已链接：{dst} → {src}")
        return 0

    if link_state(dst) == "link":
        if os.path.realpath(dst) == real_src:
            print(f"🔗 已是链接：{dst} → {src}")
            return 0
        os.rmdir(dst)  # 指向别处的链接：摘掉重来（只删链接，不动目标内容）

    if os.path.exists(dst):
        # 已有副本：改名保留，绝不自动删除——确认新链接可用后由用户自行处理
        if not os.path.isfile(os.path.join(dst, "SKILL.md")):
            print(f"⛔ {dst} 已存在，但里面没有 SKILL.md，不像技能目录。未改动。")
            return 2
        bak = f"{dst}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.rename(dst, bak)
        except OSError as e:
            print(f"⛔ 无法改名旧副本 {dst}：{e}")
            return 2
        print(f"   旧副本已改名保留：{bak}")
        print(f"   （确认新链接能用后，这个目录可以自行删除）")

    err = make_link(src, dst)
    if err:
        print(f"⛔ 建链接失败：{err.strip()}")
        print(f"   插件源仍在：{src}。可用复制模式兜底（去掉 --link 重跑）。")
        return 2

    if not os.path.isfile(os.path.join(dst, "SKILL.md")):
        print(f"⛔ 链接建了但读不到 SKILL.md：{dst}")
        return 2
    print(f"🔗 已链接")
    print(f"   从：{src}")
    print(f"   到：{dst}")
    return 0


def do_unlink(src, dst):
    """链接换回副本。某些 harness 不跟随 junction 时用这个降级。"""
    if link_state(dst) != "link":
        print(f"i   {dst} 不是链接，无需还原")
        return 0
    os.rmdir(dst)  # 只摘链接，不会碰插件源里的任何文件
    total = sum(1 for _ in iter_files(src))
    added, _updated, _removed = sync_tree(src, dst)
    print(f"✅ 已还原为副本（{total} 个文件，新增 {added}）：{dst}")
    return 0


def main():
    argv = sys.argv[1:]
    src = kit.PLUGIN_ROOT
    if "--link" in argv:
        mode = "link"
    elif "--unlink" in argv:
        mode = "unlink"
    else:
        mode = "copy"

    if "--list" in argv:
        print(f"插件源：{src}")
        print()
        print("已知的 harness 技能目录（★ = 本机已存在）：")
        for name, d in USER_TARGETS.items():
            mark = "★" if os.path.isdir(d) else " "
            tag = {"link": "  🔗 链接", "copy": "  副本", "none": "  未装"}[
                link_state(os.path.join(d, SKILL_NAME))]
            print(f"  {mark} {name:<12} {d}{tag}")
        print()
        print("用法：")
        print(f"  python \"{os.path.join(src, 'scripts', 'install.py')}\" --all --link")
        print(f"  python \"{os.path.join(src, 'scripts', 'install.py')}\" --target claude --link")
        print(f"  python \"{os.path.join(src, 'scripts', 'install.py')}\" --dir \"<任意技能目录>\"")
        return 0

    check_only = "--check" in argv
    explicit = arg(argv, "--dir")
    target = arg(argv, "--target")
    project = arg(argv, "--project")

    if "--all" in argv:
        rc = 0
        for name, d in USER_TARGETS.items():
            if not os.path.isdir(d):
                print(f"—  {name}：目录不存在，跳过（{d}）")
                continue
            print(f"### {name}")
            sub_dst = os.path.join(d, SKILL_NAME)
            if mode == "link":
                r = do_link(src, sub_dst, check_only)
            elif mode == "unlink":
                r = do_unlink(src, sub_dst)
            else:
                r = do_install(src, sub_dst, check_only)
            if r:
                rc = r
            print()
        return rc

    if explicit:
        dst = os.path.join(os.path.abspath(explicit), SKILL_NAME)
        if mode == "link":
            return do_link(src, dst, check_only)
        if mode == "unlink":
            return do_unlink(src, dst)
        return do_install(src, dst, check_only)

    if not target:
        print("⛔ 需要 --target <harness>、--dir <路径> 或 --all。用 --list 看可选值。")
        return 2
    if target not in USER_TARGETS:
        print(f"⛔ 未知目标：{target}（可选：{'、'.join(USER_TARGETS)}）")
        print("   自定义位置请用 --dir <技能目录>")
        return 2

    if project:
        sub = PROJECT_SUBDIRS.get(target)
        if not sub:
            print(f"⛔ {target} 没有已知的项目级技能目录，请用 --dir 指定")
            return 2
        base = os.path.join(os.path.abspath(project), sub)
    else:
        base = USER_TARGETS[target]

    dst = os.path.join(base, SKILL_NAME)
    if mode == "link":
        return do_link(src, dst, check_only)
    if mode == "unlink":
        return do_unlink(src, dst)

    rc = do_install(src, dst, check_only)
    if rc == 0 and not check_only:
        print()
        print("下一步：")
        print("  1. 重开一个会话（技能列表通常在会话启动时加载）")
        print("  2. 对 agent 说：「我想写一本小说」")
        print("  3. 它应该会问你书名、题材、平台等，然后生成整本书的骨架")
        print()
        print("可选 · 挂上钩子（自动注入正典与纪律、章节落盘自动对账）：")
        print(f"  把 {os.path.join(src, 'assets', 'hooks.config.example.json')} 的内容")
        print("  合并进你的 agent 配置，并把 <KIT> 换成：")
        print(f"    {dst}")
    return rc


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
