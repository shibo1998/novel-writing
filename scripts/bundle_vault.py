#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bundle_vault.py —— 把 writing-vault 的规则与技能打包进插件的 assets/

为什么要打包而不是引用：插件的目标是「装到任何 harness 都能用」。
引用外部绝对路径会让它换一台机器就废掉。规则 64K、技能约 900K，全是 Markdown，打包代价极低。

⚠️ **方向是 vault → assets，会覆盖 assets/ 里的一切。**
`writing-vault/` 是「编辑器时代的只读备份库」，早已不再更新；而 2026-09-19 在 `assets/` 上
做过一批适配修改（`.novelkit` → `.soloent` 等 21 处、golden 恢复等）。
**直接跑本脚本会把这些修改全部回退。** 用之前先 `--check` 看清差异，
确认「vault 才是新的」再执行；否则只在需要从旧备份恢复时才用。

打包规则：
  · `rules/*.md` + `rules/active-plugin-rules/*.md` → `assets/rules/`
  · `plugins/<id>/<ver>/skills/<名字>/`          → `assets/skills/<名字>/`（去掉 plugin.json / icon.png / README.md）
  · `plugins/<id>/<ver>/workflows/<名字>.md`      → `assets/workflows/<名字>.md`
  · （2026-09-19 起不再跳过任何技能；golden-three-chapters-review 已恢复使用）
  · 替换已知失效的脚本（插件原版 count_chinese_words.py 有硬编码作者路径等 5 个问题）

用法：
    python scripts/bundle_vault.py --vault "D:/1-work/writing-vault"
    python scripts/bundle_vault.py --vault ... --check    # 只比对，不写
退出码：0=成功  1=--check 发现差异  2=参数问题
"""
import filecmp
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

SKIP_SKILLS = set()   # 2026-09-19 清空：原先跳过 golden-three-chapters-review（9-14 弃用），
                      # 但该技能已于 9-19 恢复用于「开篇（黄金三章）专项审核」，不再跳过。
SKIP_FILES = {"plugin.json", "icon.png", "README.md", ".DS_Store"}


def arg(argv, name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def collect(vault):
    """-> (plan, notes)。plan = {相对 assets 的路径: 源绝对路径}"""
    plan, notes = {}, []

    # --- rules（含已移出的 _removed/：book.json 的 forbid 清单会引用它们，
    #     打包进来才能让「禁载」这句话指得到一个真实文件）---
    # ⚠️ 注意：古言规则实际躺在**库根**的 `_removed/`，不是 `rules/_removed/`。
    #    两处都扫一遍，避免又出现「渲染出的路径不存在」。
    for sub in ("", "active-plugin-rules"):
        d = os.path.join(vault, "rules", sub) if sub else os.path.join(vault, "rules")
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md") and fn != "README.md":
                plan[os.path.join("rules", sub, fn) if sub else os.path.join("rules", fn)] = \
                    os.path.join(d, fn)
    for d in (os.path.join(vault, "rules", "_removed"), os.path.join(vault, "_removed")):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if os.path.isfile(p) and fn.endswith(".md"):
                plan[os.path.join("rules", "_removed", fn)] = p

    # --- plugins: skills / workflows ---
    pdir = os.path.join(vault, "plugins")
    if not os.path.isdir(pdir):
        notes.append(f"⚠️ 找不到 {pdir}，技能未打包")
        return plan, notes

    for pid in sorted(os.listdir(pdir)):
        base = os.path.join(pdir, pid)
        if not os.path.isdir(base):
            continue
        # 取版本目录（通常只有一个）
        for ver in sorted(os.listdir(base)):
            vdir = os.path.join(base, ver)
            if not os.path.isdir(vdir):
                continue
            for kind in ("skills", "workflows"):
                kdir = os.path.join(vdir, kind)
                if not os.path.isdir(kdir):
                    continue
                for name in sorted(os.listdir(kdir)):
                    src = os.path.join(kdir, name)
                    if kind == "skills":
                        if name in SKIP_SKILLS:
                            notes.append(f"跳过弃用技能：{name}")
                            continue
                        if not os.path.isdir(src):
                            continue
                        for root, dirs, files in os.walk(src):
                            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                            for fn in files:
                                if fn in SKIP_FILES or fn.endswith((".pyc", ".DS_Store")):
                                    continue
                                rel = os.path.relpath(os.path.join(root, fn), kdir)
                                plan[os.path.join("skills", rel)] = os.path.join(root, fn)
                    else:
                        if not name.endswith(".md") or name in SKIP_FILES:
                            continue
                        plan[os.path.join("workflows", name)] = src
            break   # 只取第一个版本目录

    # --- 替换已知失效脚本 ---
    fixed = os.path.join(vault, "_tools", "count_chinese_words.py")
    if os.path.isfile(fixed):
        for rel in list(plan):
            if rel.endswith("count_chinese_words.py"):
                plan[rel] = fixed
                notes.append(f"已替换失效脚本：{rel} → writing-vault/_tools/ 的修复版")

    return plan, notes


def apply(plan, dest, check_only):
    drift, missing = [], []
    for rel, src in plan.items():
        dst = os.path.join(dest, rel)
        if not os.path.isfile(dst):
            missing.append(rel)
        elif not filecmp.cmp(src, dst, shallow=False):
            drift.append(rel)
    if check_only:
        for m in missing:
            print(f"  缺失：{m}")
        for d in drift:
            print(f"  差异：{d}")
        return drift, missing
    for rel, src in plan.items():
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    return drift, missing


def main():
    argv = sys.argv[1:]
    vault = arg(argv, "--vault")
    check_only = "--check" in argv
    if not vault:
        print("用法：python scripts/bundle_vault.py --vault \"D:/1-work/writing-vault\" [--check]")
        return 2
    vault = os.path.abspath(vault)
    if not os.path.isdir(vault):
        print(f"⛔ 不存在：{vault}")
        return 2

    dest = kit.ASSETS
    plan, notes = collect(vault)
    if not plan:
        print("⛔ 没有收集到任何文件")
        return 2

    drift, missing = apply(plan, dest, check_only)
    n_rules = sum(1 for r in plan if r.startswith("rules"))
    n_skills = sum(1 for r in plan if r.startswith("skills"))
    n_wf = sum(1 for r in plan if r.startswith("workflows"))

    # ⚠️ counts 必须记**实盘**，不能只记「计划条目数」。
    # 2026-09-20 事故：`_bundle.json` 记着 skills: 123，而 assets/skills/ 里只有 13 个
    # （后来裁剪过，记录没跟着改），于是「少装了 110 个技能」被当成「本来就这么多」，
    # 直到给 audit 补了账实核对才发现。计划数留在 planned_counts 里做溯源。
    def _live_counts():
        def _dirs(p):
            try:
                return len([d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))])
            except OSError:
                return 0
        return {
            "rules": sum(len(f) for _, _, f in os.walk(os.path.join(dest, "rules"))),
            "skills": _dirs(os.path.join(dest, "skills")),
            "workflows": len([f for f in os.listdir(os.path.join(dest, "workflows"))
                              if f.endswith(".md")])
            if os.path.isdir(os.path.join(dest, "workflows")) else 0,
        }

    planned_counts = {"rules": n_rules, "skills": n_skills, "workflows": n_wf}

    for n in notes:
        print("  " + n)
    if check_only:
        if drift or missing:
            print(f"⚠️  与 {vault} 不一致：{len(missing)} 个缺失、{len(drift)} 个差异")
            print(f"   修复：python scripts/bundle_vault.py --vault \"{vault}\"")
            return 1
        print(f"✅ 已是最新（rules {n_rules} · skills {n_skills} · workflows {n_wf}）")
        return 0

    # 记录来源，便于日后追溯这批资产是从哪一版备份来的
    meta = {
        "source": vault,
        "bundled_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "counts": _live_counts(),          # 实盘（audit E3 会核对它）
        "planned_counts": planned_counts,  # 本次计划条目数（溯源用；与实盘不同说明裁剪过）
        "skipped_skills": sorted(SKIP_SKILLS),
        "note": "本目录由 scripts/bundle_vault.py 从 writing-vault 打包而来，请勿手改。"
                "要更新请改 writing-vault 后重跑打包脚本。",
    }
    with open(os.path.join(dest, "_bundle.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"✅ 已打包：rules {n_rules} · skills {n_skills} · workflows {n_wf}"
          f"（新增 {len(missing)}，更新 {len(drift)}）")
    print(f"   来源：{vault}")
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
