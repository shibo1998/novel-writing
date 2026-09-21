#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探索草稿入口 (draft.py)

用途：正典、细纲、账本还没就绪时，agent 想先把脑子里的开篇/点子落成字，
    用这个入口**试写**。它和正式章节的边界是硬性的：

    · 输出到 drafts/，绝不进 chapters/（闸门与对账都不会把它当正文）；
    · 不推进 now.md 状态块、不写 ledger.tsv、不产生 checkpoint；
    · 文件头强制带 NON-CANONICAL／非正典标记，防止后续被误当正典引用；
    · 转正必须走清单：补齐正典 + 细纲 + 账本行，作者确认后另存为
      chapters/ch-N.md，再按正式流程（brief → 自查 → 三连 → 复查 → 回写）过闸。

为什么需要它：报告 NW-P1-005——「直接写第一章」和强制前置阶段冲突。
    没有这个出口时，试写稿要么被劝退（想法丢了），要么偷偷以正式第 1 章落地
    （正典未定、接地单空转、后续全部反向迁就）。给它一条**不进正史**的路，
    两条问题都消掉。

用法：
    python <kit>/tools/draft.py --name "开篇试写"          # 建一份空草稿（含写作提示）
    python <kit>/tools/draft.py --name "开篇试写" --json   # agent 解析用
退出码：0=已创建  2=参数/配置问题
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

DRAFTS_DIR = "drafts"
MAX_NAME_LEN = 40

TEMPLATE = """<!-- NON-CANONICAL ｜ 非正典草稿 —— 本文件不属于正式章节，闸门与对账都不会检查它 -->

# 草稿：{name}

> 创建：{date}
> 状态：**非正典（NON-CANONICAL）** —— 只是想法的落地，不是事实来源。

## 本草稿的边界（不可越过）

- 写在 `drafts/`，**不进 `chapters/`**；不推进 `now.md`，不写 `ledger.tsv`，不算章号。
- 里面的设定、人名、数值**不得**被正式章节直接引用；想采用，先写进正典。
- 引用本草稿时必须注明「非正典草稿」。

## 转正清单（全部满足后才能移入 chapters/）

1. `canon.md` 已填好且无 ✏️ 占位符；
2. 细纲里已有对应章的条目（节拍 + 章末钩子）；
3. `ledger.tsv` 已追加该章的账本行；
4. **作者确认**采用这份草稿；
5. 把定稿内容另存为 `chapters/ch-<N>.md`（标题按 `chapter.title_hint`），
   本文件删除或移入 `notes/` 归档；
6. 走正式流程：`brief.py` → 起草 → 句法自查 → 技能三连 → 连续性复查 → 回写留痕 →
   `preflight.py` 过闸。**草稿内容跳过这些步骤直接当正式章，等于没有前置资料就开书。**

## 正文（在此之下试写）

（空）
"""


def sanitize_name(name):
    """文件名安全化：去掉路径分隔与非法字符，限制长度。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "-", name or "").strip(" .-")
    if not cleaned:
        cleaned = "未命名草稿"
    return cleaned[:MAX_NAME_LEN]


def unique_path(directory, stem):
    """目录内取不重名的 .md 路径；重名时追加序号，绝不覆盖已有草稿。"""
    base = os.path.join(directory, stem + ".md")
    if not os.path.exists(base):
        return base
    n = 2
    while True:
        candidate = os.path.join(directory, f"{stem}-{n}.md")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def main():
    argv = kit.strip_root_arg(sys.argv[1:])
    book = kit.load_book(sys.argv[1:])

    def arg(name, default=None):
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return default

    name = sanitize_name(arg("--name") or "未命名草稿")
    as_json = "--json" in argv

    # --samples N：一次建 N 份草稿做 A/B 对比（作者 2026-09-14 提出的方法）。
    # 奇数样本=自由口径（只带声音与故事必要项），偶数样本=红线口径（附完整风格禁令）。
    samples_arg = arg("--samples")
    try:
        n_samples = max(1, min(6, int(samples_arg))) if samples_arg and samples_arg.isdigit() else 1
    except ValueError:
        n_samples = 1

    # 红线口径需要完整禁令清单（不受 gate.draft_free 影响——对照组就该带对照组的条件）
    import brief as _brief
    gated_lines = _brief.style_redlines(book, full=True)
    gated_note = "\n".join("- " + x for x in gated_lines) or "-（本书未配置风格机检项）"
    free_note = ("- 动笔时**不看任何风格禁令**：以角色的真实反应和语言的自然流动为主导；\n"
                 "- 声音与故事必要项（正典、账本、钩子、硬口径）照常遵守；\n"
                 "- 完稿后由对账以「提示」报告风格项，不拦闸门。")
    profiles = []
    for k in range(1, n_samples + 1):
        if k % 2 == 1:
            profiles.append((f"样本{k} · 自由口径", free_note))
        else:
            profiles.append((f"样本{k} · 红线口径（对照）", gated_note))

    drafts_dir = os.path.join(book.root, DRAFTS_DIR)
    os.makedirs(drafts_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    created = []
    for label, note in profiles:
        stem = f"{name}-{label.split(' · ')[0]}-{stamp}" if n_samples > 1 else f"{name}-{stamp}"
        path = unique_path(drafts_dir, stem)
        rel = os.path.relpath(path, book.root).replace(os.sep, "/")
        body = TEMPLATE.format(name=f"{name}（{label}）", date=datetime.date.today().isoformat())
        body += f"\n## 本样本的写作口径\n\n**{label}**\n\n{note}\n"
        kit.atomic_write_text(path, body)
        created.append(rel)

    if as_json:
        # 向后兼容：单样本时保留旧键 "draft"，多样本另给 "drafts" 列表
        payload = {"ok": True, "drafts": created, "non_canonical": True,
                   "samples": n_samples}
        if created:
            payload["draft"] = created[0]
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if n_samples == 1:
        print(f"✅ 已创建探索草稿：{created[0]}")
    else:
        print(f"✅ 已创建 {n_samples} 份对比草稿（自由口径=奇数，红线口径=偶数）：")
        for rel in created:
            print(f"   {rel}")
    print("   非正典：不进 chapters/、不推进 now.md、不写账本、不产生章号。")
    print("   转正前必须补齐正典 + 细纲 + 账本行，并经作者确认（清单见草稿文件头）。")
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
