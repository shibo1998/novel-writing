#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PostToolUse hook —— 每当 Write/Edit 落盘的是一个章节文件，就自动过闸并把结果注入上下文。

存在意义：让「写完了」和「知道有没有写错」之间的间隔变成零。AI 不必记得去跑脚本，
结果会直接出现在它眼前。

编排约定（报告 NW-P1-008）：本钩子**只调用 preflight 一次**，不再自己先跑一遍
consistency_check——那会让每次保存触发两遍全书扫描，且共享当日报告互相覆盖。
正文扫描用 `--since` 增量（从被改的章扫到最新章，上限 10 章），账本与状态检查
仍然全量；`--no-advance`：自动入口不推进校验点（只有显式过闸才推进）。

事件：PostToolUse（matcher: Write|Edit）
输入：stdin 的 JSON，含工具名与入参（字段名做容错）
输出：{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "..."}}
      出问题时静默退出（退出码 0、无输出），绝不影响正常写作。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import kit  # noqa: E402

MAX_SINCE = 10          # 增量扫描章数上限；更早的章节交给手工/定期全量检查
LOCK_TIMEOUT = 25       # 宿主给钩子 30s；锁等待必须留出子检查的时间


def extract_path(payload):
    """从 hook 输入里抠出被写入的文件路径，字段名做容错。"""
    cands = []
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(ti, dict):
        for k in ("file_path", "filePath", "path", "notebook_path"):
            if isinstance(ti.get(k), str):
                cands.append(ti[k])
    for k in ("file_path", "filePath", "path"):
        if isinstance(payload.get(k), str):
            cands.append(payload[k])
    return cands[0] if cands else ""


def find_book(p):
    d = os.path.dirname(os.path.abspath(p))
    for _ in range(8):
        if os.path.isfile(os.path.join(d, kit.CONFIG_REL)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def main():
    payload = kit.read_stdin_json()
    path = extract_path(payload)
    if not path:
        return 0
    root = find_book(path)
    if not root:
        return 0
    book = kit.load_book(["--root", root], required=False)
    if not book:
        return 0

    # 只关心「正文目录下的章节文件」——用 book.json 的规则判定，不硬编码路径
    ch_dir = os.path.abspath(book.chapters_dir)
    if not os.path.abspath(path).startswith(ch_dir + os.sep):
        return 0
    m = book.file_regex.match(os.path.basename(path))
    if not m:
        return 0

    # 增量范围：从被改的章扫到最新章（上限 MAX_SINCE）。改旧章也能被覆盖到；
    # 更早的改动交给手工/定期全量检查。
    try:
        changed = int(m.group(1))
    except (IndexError, ValueError):
        changed = book.max_chapter()
    since = max(1, min(MAX_SINCE, book.max_chapter() - changed + 1))

    rc, out, _ = kit.run_child(
        "preflight.py", root,
        ["--no-advance", "--tag", "hook", "--since", str(since),
         "--json", "--lock-timeout", str(LOCK_TIMEOUT)],
    )
    try:
        result = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = None

    if result is None:
        kit.emit_hook("PostToolUse",
                      "# 【自动注入 · 章节对账结果】\n\n"
                      f"闸门未返回结构化结果（退出码 {rc}）——**按失败处理**，"
                      "请手动运行 `python tools/preflight.py` 排查。\n")
        return 0

    if result.get("skipped"):
        kit.emit_hook("PostToolUse",
                      "# 【自动注入 · 章节对账结果】\n\n"
                      "⏭ **本次自动对账跳过**：另一个检查正在进行（串行保护，不重复扫描）。\n\n"
                      "这不等于通过——后台会重查；落笔前记得自己跑 `python tools/preflight.py`。\n")
        return 0

    counts = result.get("counts") or {}
    sev = counts.get("严重", 0)
    blockers = result.get("blockers") or []
    ok = result.get("ok") and rc == 0
    checks = result.get("checks") or {}
    report = (checks.get("consistency") or {}).get("report")

    ctx = (f"# 【自动注入 · 章节对账结果】\n\n"
           f"文件：`{os.path.relpath(path, root)}`（增量扫描最近 {since} 章）\n\n"
           f"**{'✅ 本章通过闸门' if ok else '⛔ 本章未通过闸门 —— 必须先修再往下写'}**\n\n"
           f"- 对账：严重 {sev} · 中等 {counts.get('中等', 0)} · 轻微 {counts.get('轻微', 0)}\n"
           f"- 闸门拦截项：{len(blockers)}\n")
    for b in blockers[:5]:
        ctx += f"- ⛔ {b}\n"
    if report:
        ctx += f"- 完整报告：`{report}`\n"

    if not ok:
        rows = result.get("severe_rows") or []
        if rows:
            ctx += ("\n**严重项**：\n\n" + "\n".join(rows) + "\n\n")
        ctx += "\n**按纪律：不通过就停下修，禁止「先写着后面再改」。**\n"
        ctx += "\n确认修完后记得显式过闸：`python tools/preflight.py`（自动检查不推进校验点）。\n"

    kit.emit_hook("PostToolUse", ctx)
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
