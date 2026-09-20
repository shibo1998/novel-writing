#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置迁移 (migrate_config.py)

用途：把旧版 `.soloent/book.json` 升级到当前 `_schema`。kit 从 1.x 起对配置做
最小结构校验（`kit.config_problems`），不合法的配置会被所有工具拒绝——
本命令是唯一的合法升级通道。

做法：
  1. 读 book.json（不是经 load_book，否则校验会先拒绝）；
  2. 以 `assets/book.example.json` 为基线，**递归补齐缺失的键**（只增不改），
     用户已有值与未知键一律保留；
  3. 置 `_schema` 为当前版本，跑结构校验——仍不通过则**不写任何东西**；
  4. 原文件先备份到 `.soloent/backups/<时间戳>-migrate/`，再原子写入。

用法：
    python <kit>/scripts/migrate_config.py --root <书目录>
退出码：0=已升级或本就是当前版本  2=配置坏到补不齐（未写入）
"""
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402

EXAMPLE = os.path.join(kit.ASSETS, "book.example.json")


def deep_fill(target, baseline, prefix=""):
    """用 baseline 补齐 target 缺失的键（递归 dict）。返回新增键的列表（展示用）。"""
    added = []
    for k, v in baseline.items():
        if k.startswith("_"):
            continue
        path = prefix + k
        if k not in target:
            target[k] = v
            added.append(path)
        elif isinstance(v, dict) and isinstance(target.get(k), dict):
            added += deep_fill(target[k], v, path + ".")
    return added


def main():
    root = kit.find_book_root(explicit=kit.root_arg(sys.argv[1:]))
    if not root:
        print("⛔ 找不到书：请用 --root 指定书目录")
        return 2
    p = os.path.join(root, kit.CONFIG_REL)
    try:
        with open(p, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        print(f"⛔ 配置读不了：{p}\n   {e}\n   JSON 坏了没法自动修，请从 book.example.json 重建后再迁。")
        return 2

    before = kit.config_problems(cfg)
    if not before:
        print(f"✅ {p} 已是当前版本（_schema={kit.SCHEMA}），无需迁移。")
        return 0

    try:
        with open(EXAMPLE, encoding="utf-8-sig") as f:
            baseline = json.load(f)
    except (OSError, ValueError) as e:
        print(f"⛔ 读不了插件基线配置 {EXAMPLE}：{e}")
        return 2
    added = deep_fill(cfg, baseline)
    cfg["_schema"] = kit.SCHEMA

    problems = kit.config_problems(cfg)
    if problems:
        print("⛔ 迁移后仍未通过结构校验，**未写入任何文件**：")
        for pr in problems:
            print("   - " + pr)
        return 2

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(root, ".soloent", "backups", stamp + "-migrate")
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(p, os.path.join(backup_dir, "book.json"))
    kit.atomic_write_json(p, cfg)

    print(f"✅ 已升级到 _schema={kit.SCHEMA}：{p}")
    print(f"   备份：{backup_dir}")
    if added:
        print(f"   补齐 {len(added)} 个缺失键：{'、'.join(added[:10])}"
              + (f" …等共 {len(added)} 个" if len(added) > 10 else ""))
    else:
        print("   结构未缺键，仅版本号需要更新。")
    return 0


if __name__ == "__main__":
    kit.force_utf8()
    sys.exit(main())
