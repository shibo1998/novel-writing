#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关键流程回归测试。

测试只通过项目公开 CLI/Hook 观察行为，并在系统临时目录中创建假书；
不会读取、修改或推进任何真实小说项目的状态。

用法：
    python scripts/regression_test.py
    python scripts/regression_test.py InitSyncSafetyTests.test_init_preserves_existing_codebuddy_settings
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def run_cli(script, *args, cwd=None):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *map(str, args)],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="novel-regression-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class InitSyncSafetyTests(TempDirTest):
    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "回归测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_preserves_existing_codebuddy_settings(self):
        settings_dir = os.path.join(self.tmp, ".codebuddy")
        os.makedirs(settings_dir, exist_ok=True)
        settings_path = os.path.join(settings_dir, "settings.json")
        original = {
            "permissions": {"allow": ["Read"]},
            "env": {"USER_SETTING": "keep"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "custom-stop"}]}]},
        }
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(original, f, ensure_ascii=False, indent=2)

        self.init_book()
        with open(settings_path, encoding="utf-8-sig") as f:
            actual = json.load(f)
        self.assertEqual(actual.get("permissions"), original["permissions"])
        self.assertEqual(actual.get("env"), original["env"])
        self.assertEqual(actual.get("hooks", {}).get("Stop"), original["hooks"]["Stop"])
        self.assertIn("SessionStart", actual.get("hooks", {}))
        self.assertIn("PostToolUse", actual.get("hooks", {}))

    def test_sync_updates_plugin_hook_without_deleting_custom_settings(self):
        self.init_book()
        settings_path = os.path.join(self.tmp, ".codebuddy", "settings.json")
        with open(settings_path, encoding="utf-8-sig") as f:
            settings = json.load(f)
        settings["env"] = {"USER_SETTING": "keep"}
        settings["hooks"]["Stop"] = [
            {"hooks": [{"type": "command", "command": "custom-stop"}]}
        ]
        settings["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 1
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

        result = run_cli("sync.py", "--root", self.tmp)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(settings_path, encoding="utf-8-sig") as f:
            actual = json.load(f)
        self.assertEqual(actual.get("env"), {"USER_SETTING": "keep"})
        self.assertEqual(actual["hooks"].get("Stop"), settings["hooks"]["Stop"])
        plugin_hooks = [
            entry for entry in actual["hooks"]["SessionStart"]
            if "inject_canon.py" in json.dumps(entry, ensure_ascii=False)
        ]
        self.assertEqual(len(plugin_hooks), 1)
        self.assertEqual(plugin_hooks[0]["hooks"][0]["timeout"], 15)
        check = run_cli("sync.py", "--root", self.tmp, "--check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_force_reinitialize_backs_up_existing_story_files(self):
        self.init_book()
        canon_path = os.path.join(self.tmp, ".soloent", "canon.md")
        marker = "不可丢失的旧正典"
        with open(canon_path, "a", encoding="utf-8") as f:
            f.write("\n" + marker + "\n")

        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "回归测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--force",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        backup = payload.get("backup")
        self.assertTrue(backup and os.path.isdir(backup), payload)
        backed_canon = os.path.join(backup, ".soloent", "canon.md")
        with open(backed_canon, encoding="utf-8-sig") as f:
            self.assertIn(marker, f.read())

    def test_init_rejects_invalid_existing_settings_before_writing_book(self):
        settings_dir = os.path.join(self.tmp, ".codebuddy")
        os.makedirs(settings_dir, exist_ok=True)
        settings_path = os.path.join(settings_dir, "settings.json")
        invalid = "{ this is not valid json"
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(invalid)

        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "回归测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".soloent", "book.json")))
        with open(settings_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), invalid)

    def test_init_preserves_existing_zcode_events_and_fields(self):
        config_dir = os.path.join(self.tmp, ".zcode")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.json")
        original_stop = [{"hooks": [{"type": "process", "command": "custom-stop"}]}]
        original = {
            "custom": {"keep": True},
            "hooks": {"events": {"Stop": original_stop}},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(original, f, ensure_ascii=False, indent=2)

        self.init_book()

        with open(config_path, encoding="utf-8-sig") as f:
            actual = json.load(f)
        self.assertEqual(actual.get("custom"), original["custom"])
        self.assertEqual(actual["hooks"]["events"].get("Stop"), original_stop)
        self.assertIn("SessionStart", actual["hooks"]["events"])
        self.assertIn("PostToolUse", actual["hooks"]["events"])


class PreflightFailClosedTests(TempDirTest):
    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "闸门回归测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_isolated_preflight(
        self,
        missing=None,
        replacements=None,
        child_timeout=None,
        isolated_name="isolated-scripts",
        preflight_args=None,
    ):
        isolated_scripts = os.path.join(self.tmp, isolated_name)
        shutil.copytree(
            SCRIPTS,
            isolated_scripts,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for script in missing or []:
            os.remove(os.path.join(isolated_scripts, script))
        for script, source in (replacements or {}).items():
            with open(os.path.join(isolated_scripts, script), "w", encoding="utf-8") as f:
                f.write(source)
        if child_timeout is not None:
            kit_path = os.path.join(isolated_scripts, "kit.py")
            with open(kit_path, encoding="utf-8") as f:
                source = f.read()
            source = source.replace(
                "def run_child(script, book_root, extra=None, timeout=180):",
                f"def run_child(script, book_root, extra=None, timeout={child_timeout}):",
            )
            with open(kit_path, "w", encoding="utf-8") as f:
                f.write(source)
        return subprocess.run(
            [
                sys.executable,
                os.path.join(isolated_scripts, "preflight.py"),
                "--root",
                self.tmp,
                *(preflight_args or []),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def test_unmatched_chapter_file_is_blocked_even_when_no_numbered_chapters(self):
        self.init_book()
        path = os.path.join(self.tmp, "chapters", "chapter-1.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 第001章 未纳入检查\n\n正文。\n")

        result = run_cli("preflight.py", "--root", self.tmp)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未纳入检查", result.stdout + result.stderr)

    def test_unmatched_chapter_file_is_reported_alongside_numbered_chapters(self):
        self.init_book()
        chapters = os.path.join(self.tmp, "chapters")
        with open(os.path.join(chapters, "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 正常正文\n\n正文。\n")
        with open(os.path.join(chapters, "chapter-2.md"), "w", encoding="utf-8") as f:
            f.write("# 第002章 未纳入检查\n\n正文。\n")

        result = run_cli("preflight.py", "--root", self.tmp)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("发现未纳入检查的正文文件：chapter-2.md", result.stdout + result.stderr)

    def test_missing_core_children_are_explicit_blockers(self):
        for script in ("consistency_check.py", "state_sync.py", "ledger.py"):
            with self.subTest(script=script):
                case_root = os.path.join(self.tmp, script.replace(".py", ""))
                os.makedirs(case_root, exist_ok=True)
                original_tmp = self.tmp
                self.tmp = case_root
                try:
                    self.init_book()
                    with open(os.path.join(case_root, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
                        f.write("# 第001章 测试\n\n正文。\n")

                    result = self.run_isolated_preflight(missing=[script])
                finally:
                    self.tmp = original_tmp

                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("拦截：" + script + " 未接入", output)

    def test_core_child_crash_or_malformed_success_is_blocked(self):
        cases = (
            ("consistency_check.py", "raise RuntimeError('boom')\n", "consistency_check.py 退出码"),
            ("state_sync.py", "print('looks fine')\n", "state_sync.py 输出格式异常"),
            ("ledger.py", "print('looks fine')\n", "ledger.py 输出格式异常"),
        )
        for index, (script, source, expected) in enumerate(cases):
            with self.subTest(script=script):
                case_root = os.path.join(self.tmp, "bad-child-" + str(index))
                os.makedirs(case_root, exist_ok=True)
                original_tmp = self.tmp
                self.tmp = case_root
                try:
                    self.init_book()
                    with open(os.path.join(case_root, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
                        f.write("# 第001章 测试\n\n正文。\n")
                    result = self.run_isolated_preflight(replacements={script: source})
                finally:
                    self.tmp = original_tmp

                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(expected, output)

    def test_core_child_timeout_is_blocked(self):
        for index, script in enumerate(("consistency_check.py", "state_sync.py", "ledger.py")):
            with self.subTest(script=script):
                case_root = os.path.join(self.tmp, "timeout-" + str(index))
                os.makedirs(case_root, exist_ok=True)
                original_tmp = self.tmp
                self.tmp = case_root
                try:
                    self.init_book()
                    with open(os.path.join(case_root, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
                        f.write("# 第001章 测试\n\n正文。\n")
                    result = self.run_isolated_preflight(
                        replacements={script: "import time\ntime.sleep(1)\n"},
                        child_timeout=0.05,
                    )
                finally:
                    self.tmp = original_tmp

                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertRegex(output, r"(退出码 -3|未正常完成)")

    def test_batch_chapters_do_not_advance_checkpoint(self):
        self.init_book()
        chapters = os.path.join(self.tmp, "chapters")
        for chapter in range(1, 6):
            with open(os.path.join(chapters, f"ch-{chapter}.md"), "w", encoding="utf-8") as f:
                f.write(f"# 第{chapter:03d}章 测试\n\n第 {chapter} 章正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write("\n[流程] ch-5 自查0 三连0 复查0 回写OK\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（5 章）')\n",
        }

        result = self.run_isolated_preflight(replacements=children)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("逐章", output)
        checkpoint = os.path.join(self.tmp, ".soloent", "checkpoint.json")
        if os.path.exists(checkpoint):
            with open(checkpoint, encoding="utf-8-sig") as f:
                self.assertEqual(json.load(f).get("last_verified_chapter"), 0)

    def test_batch_chapters_can_only_be_recovered_in_order(self):
        self.init_book()
        chapters = os.path.join(self.tmp, "chapters")
        for chapter in range(1, 4):
            with open(os.path.join(chapters, f"ch-{chapter}.md"), "w", encoding="utf-8") as f:
                f.write(f"# 第{chapter:03d}章 测试\n\n第 {chapter} 章正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            for chapter in range(1, 4):
                f.write(f"\n[流程] ch-{chapter} 自查0 三连0 复查0 回写OK\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（3 章）')\n",
        }

        first = self.run_isolated_preflight(
            replacements=children,
            isolated_name="verify-1",
            preflight_args=["--chapter", "1"],
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        checkpoint = os.path.join(self.tmp, ".soloent", "checkpoint.json")
        with open(checkpoint, encoding="utf-8-sig") as f:
            self.assertEqual(json.load(f).get("last_verified_chapter"), 1)

        skipped = self.run_isolated_preflight(
            replacements=children,
            isolated_name="verify-3-too-soon",
            preflight_args=["--chapter", "3"],
        )
        self.assertEqual(skipped.returncode, 1, skipped.stdout + skipped.stderr)
        self.assertIn("第 2 章", skipped.stdout + skipped.stderr)
        with open(checkpoint, encoding="utf-8-sig") as f:
            self.assertEqual(json.load(f).get("last_verified_chapter"), 1)

        second = self.run_isolated_preflight(
            replacements=children,
            isolated_name="verify-2",
            preflight_args=["--chapter", "2"],
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        with open(checkpoint, encoding="utf-8-sig") as f:
            self.assertEqual(json.load(f).get("last_verified_chapter"), 2)

    def test_edited_verified_chapters_can_be_reverified_in_order(self):
        """补验协议：旧凭证失效后，从最早失效章逐章重验，不与跳号规则互锁。"""
        self.init_book()
        chapters = os.path.join(self.tmp, "chapters")
        for chapter in (1, 2):
            with open(os.path.join(chapters, f"ch-{chapter}.md"), "w", encoding="utf-8") as f:
                f.write(f"# 第{chapter:03d}章 测试\n\n第 {chapter} 章正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            for chapter in (1, 2):
                f.write(f"\n[流程] ch-{chapter} 自查0 三连0 复查0 回写OK\n")
        # 新书默认开启 gate.review_required：普通出闸第 2 章要求第 1 章的评审产物在
        notes = os.path.join(self.tmp, "notes")
        os.makedirs(notes, exist_ok=True)
        with open(os.path.join(notes, "review-chapter-1-2026-01-01.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 第 1 章评审\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（2 章）')\n",
        }
        first = self.run_isolated_preflight(
            replacements=children, isolated_name="reverify-pass1", preflight_args=["--chapter", "1"])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.run_isolated_preflight(
            replacements=children, isolated_name="reverify-pass1b", preflight_args=["--chapter", "2"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        checkpoint = os.path.join(self.tmp, ".soloent", "checkpoint.json")
        with open(checkpoint, encoding="utf-8-sig") as f:
            self.assertEqual(json.load(f).get("last_verified_chapter"), 2)

        # 修改第 1 章（已通过校验的正文）→ 旧凭证失效
        with open(os.path.join(chapters, "ch-1.md"), "a", encoding="utf-8") as f:
            f.write("过闸后补写的一句。\n")
        blocked = self.run_isolated_preflight(replacements=children, isolated_name="reverify-blocked")
        output = blocked.stdout + blocked.stderr
        self.assertEqual(blocked.returncode, 1, output)
        self.assertIn("已变化", output)

        # --chapter 2（跳号）：必须被拦，且提示从第 1 章开始
        skip = self.run_isolated_preflight(
            replacements=children, isolated_name="reverify-skip", preflight_args=["--chapter", "2"])
        self.assertEqual(skip.returncode, 1, skip.stdout + skip.stderr)
        self.assertIn("请先补验第 1 章", skip.stdout + skip.stderr)

        # 从第 1 章逐章重验：校验点不回退，凭证刷新
        re1 = self.run_isolated_preflight(
            replacements=children, isolated_name="reverify-1", preflight_args=["--chapter", "1"])
        self.assertEqual(re1.returncode, 0, re1.stdout + re1.stderr)
        self.assertIn("校验点仍为第 2 章", re1.stdout + re1.stderr)
        # 重验通过后，凭证失效问题清零，普通闸门恢复通过
        final = self.run_isolated_preflight(replacements=children, isolated_name="reverify-pass2")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)

    def test_arbitrary_flow_text_does_not_count_as_credential(self):
        self.init_book()
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write("\n[流程] ch-1 任意文本\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }

        result = self.run_isolated_preflight(
            replacements=children,
            isolated_name="invalid-flow-text",
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("流程凭证格式无效", output)

    def test_skipped_flow_steps_are_blockers_not_warnings(self):
        self.init_book()
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write("\n[流程] ch-1 跳过\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }

        result = self.run_isolated_preflight(
            replacements=children,
            isolated_name="skipped-flow",
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("留痕标注为未执行／跳过", output)

    def test_editing_verified_chapter_invalidates_checkpoint(self):
        self.init_book()
        chapter_path = os.path.join(self.tmp, "chapters", "ch-1.md")
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n第一版正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write("\n[流程] ch-1 自查0 三连0 复查0 回写OK\n")
        children = {
            "consistency_check.py": "print('严重 0 · 中等 0 · 轻微 0')\n",
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }
        first = self.run_isolated_preflight(
            replacements=children,
            isolated_name="hash-first-pass",
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        checkpoint = os.path.join(self.tmp, ".soloent", "checkpoint.json")
        with open(checkpoint, encoding="utf-8-sig") as f:
            before = json.load(f)
        self.assertIn("sha256", before.get("chapters", {}).get("1", {}))

        with open(chapter_path, "a", encoding="utf-8") as f:
            f.write("过闸后新增的一句。\n")
        second = self.run_isolated_preflight(
            replacements=children,
            isolated_name="hash-after-edit",
        )

        output = second.stdout + second.stderr
        self.assertEqual(second.returncode, 1, output)
        self.assertIn("已通过校验的第 1 章正文已变化", output)
        with open(checkpoint, encoding="utf-8-sig") as f:
            after = json.load(f)
        self.assertEqual(after, before)


class StyleEnforcementTests(TempDirTest):
    """2026-09-20 复盘新增：留痕对撞 + 面板文体机检。

    为什么需要这些：F05 只把流程凭证的**格式**卡严了，`自查0 三连0 复查0 回写OK`
    依旧合法——实测某书 69 章留痕几乎全报 0/1，正文却残留 63 处黑名单词（35 章命中）。
    本组用例把「格式合法但没真跑」这条绕过路径钉死，同时守住反向边界：
    干净章必须照常放行、写明保留理由的章降级为警告而不是拦截。
    """

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "文风对撞测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def configure_checks(self, **updates):
        cfg_path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        cfg.setdefault("checks", {}).update(updates)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def write_chapter(self, body, number=1):
        chapters = os.path.join(self.tmp, "chapters")
        os.makedirs(chapters, exist_ok=True)
        with open(os.path.join(chapters, f"ch-{number}.md"), "w", encoding="utf-8") as f:
            f.write(f"# 第{number:03d}章 测试\n\n{body}\n")

    def write_flow_line(self, tail, number=1):
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write(f"\n[流程] ch-{number} {tail}\n")

    def run_isolated_preflight(self, isolated_name, preflight_args=None):
        """state_sync / ledger 用桩（状态与账本另有专项用例），consistency_check 保持真身。

        对撞层的判据必须来自**真实机检**——把 consistency_check 也换成桩，
        就变成了在测试自己编的结论上做断言。
        """
        isolated_scripts = os.path.join(self.tmp, isolated_name)
        shutil.copytree(
            SCRIPTS,
            isolated_scripts,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for script, source in {
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }.items():
            with open(os.path.join(isolated_scripts, script), "w", encoding="utf-8") as f:
                f.write(source)
        return subprocess.run(
            [
                sys.executable,
                os.path.join(isolated_scripts, "preflight.py"),
                "--root", self.tmp,
                "--json",
                *(preflight_args or []),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def preflight_json(self, result):
        self.assertTrue(result.stdout.strip().startswith("{"),
                        "preflight 未输出 JSON：" + result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_clean_chapter_passes_collision_layer(self):
        """反向边界：没有残留的章不能因为对撞层而被误伤。"""
        self.init_book()
        self.write_chapter("他抬起头，看着那本账。这笔账他记了三年，一天没落。")
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("residual-clean")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["checks"]["flow"]["state"], "ok")

    def test_machine_residual_blocks_fabricated_trace(self):
        """核心用例：留痕写「自查0」，但本章仍有黑名单词 → 拦截。"""
        self.init_book()
        self.write_chapter("他缓缓抬起头，看着那本账。")
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("residual-block")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(data["ok"])
        self.assertEqual(data["checks"]["flow"]["state"], "invalid")
        self.assertTrue(any("去 AI 味残留" in b for b in data["blockers"]), data["blockers"])

    def test_residual_count_is_reported_not_just_flagged(self):
        """对撞层要说清「残留几处、在第几行」——否则作者只知道被拦，不知道改哪儿。"""
        self.init_book()
        self.write_chapter("他缓缓抬起头。\n\n账本上静静躺着一行字。\n\n他又轻轻合上。")
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("residual-detail")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        blocker = next(b for b in data["blockers"] if "去 AI 味残留" in b)
        self.assertIn("ch-1.md 第", blocker)

    def test_declared_retention_downgrades_residual_to_warning(self):
        """确需保留的违规处：写明理由 → 放行但留警告（交 /review 复核），不是无条件拦截。"""
        self.init_book()
        self.write_chapter("他缓缓抬起头，看着那本账。")
        self.write_flow_line("自查0 三连0 复查0 回写OK；保留：角色专属节奏")

        result = self.run_isolated_preflight("residual-keep")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["checks"]["flow"]["state"], "warn")
        self.assertTrue(any("声明保留" in w for w in data["warnings"]), data["warnings"])

    def test_panel_line_cap_is_enforced(self):
        self.init_book()
        self.configure_checks(panel={"prefix": "【", "max_lines": 2, "forbid": []})
        self.write_chapter(
            "【入账：气血 +2】\n\n【支出：灵晶 3】\n\n【结算：账目完整】\n\n他把单子收进兜里。"
        )
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("panel-cap")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(any("面板条目" in b for b in data["blockers"]), data["blockers"])

    def test_panel_conclusion_line_is_severe(self):
        """面板行内出现结论文案（替读者下结论）→ 严重，且被对撞层拦下。"""
        self.init_book()
        self.configure_checks(panel={"prefix": "【", "max_lines": 10, "forbid": ["这意味着"]})
        self.write_chapter("【这意味着，水厂下面埋着主脉。】\n\n他把单子收进兜里。")
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("panel-forbid")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(data["counts"]["严重"], 1)

    def test_panel_check_is_inert_without_config(self):
        """没配 panel 的书不该被面板规则误伤（机检项按书配置开关）。"""
        self.init_book()
        self.write_chapter("【入账：气血 +2】\n\n【支出：灵晶 3】\n\n【结算：账目完整】\n\n他把单子收进兜里。")
        self.write_flow_line("自查0 三连0 复查0 回写OK")

        result = self.run_isolated_preflight("panel-off")

        data = self.preflight_json(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data["checks"]["flow"]["state"], "ok")


class StateBlockHygieneTests(TempDirTest):
    """状态块重复键：doctor 取**首个** chapter、state_sync 取**末个**，
    同一份文件两个口径 = 机制失效（2026-09-20 实测某书 now.md 里 chapter 键出现 10 次，
    doctor 报「写第 60 章」而实际已到第 69 章）。这里钉住「报错 + 可修」。
    """

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "状态块测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def pin_soloent_progress(self, chapter):
        """把 SOLOENT.md §7 的当前进度对齐到第 N 章，避免 §7 滞后混进本用例的断言。"""
        path = os.path.join(self.tmp, "SOLOENT.md")
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        for index, line in enumerate(lines):
            if line.startswith("## 7"):
                lines.insert(index + 1, f"当前进度：第 {chapter} 章")
                break
        else:
            self.fail("SOLOENT.md 里找不到 §7 板块")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def write_duplicated_block(self):
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        block = (
            "<!-- SOLOENT-STATE\n"
            "chapter: 1\nstory_time: 第一天\nlocation: 测试地\n"
            "narrative_verified: 1\nupdated: 2026-01-01\n"
            "chapter: 1\nstory_time: 第二天\nlocation: 另一处\n"
            "narrative_verified: 1\nupdated: 2026-01-02\n"
            "-->\n"
        )
        with open(now_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(block + "\n正文随便写点。\n")
        return now_path

    def test_duplicate_state_keys_are_blocked_then_fixed(self):
        self.init_book()
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n正文。\n")
        now_path = self.write_duplicated_block()
        self.pin_soloent_progress(1)

        blocked = run_cli("state_sync.py", "--root", self.tmp)
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertIn("重复键", blocked.stdout + blocked.stderr)

        fixed = run_cli("state_sync.py", "--root", self.tmp, "--fix")
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        self.assertIn("已去重", fixed.stdout + fixed.stderr)

        with open(now_path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("chapter:"), 1, text)
        # 去重取「后值胜出」——与 parse_block 的覆盖语义一致
        self.assertIn("location: 另一处", text)

    def test_single_block_is_not_touched(self):
        """反向边界：干净的单份状态块不该被 --fix 改写，也不该报重复键。"""
        self.init_book()
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n正文。\n")
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        block = (
            "<!-- SOLOENT-STATE\n"
            "chapter: 1\nstory_time: 第一天\nlocation: 测试地\n"
            "narrative_verified: 1\nupdated: 2026-01-01\n"
            "-->\n"
        )
        with open(now_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(block + "\n正文随便写点。\n")
        self.pin_soloent_progress(1)

        result = run_cli("state_sync.py", "--root", self.tmp, "--fix")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("重复键", result.stdout + result.stderr)
        with open(now_path, encoding="utf-8") as f:
            self.assertTrue(f.read().startswith(block), "干净状态块被改写了")


class ProjectDefaultsTests(TempDirTest):
    """新书默认值 + 规则部署 + 写前接地拦空配置 + 批量同步。

    2026-09-20 复盘：某书是「init 出来的」——`checks.rhythm` 为 null、
    `extra_discipline` 为空、两份风格模板原封未动、`rules.load` 声明的规则**一条都没部署**，
    仍然一路写完 69 章。这组用例把「初始化后的默认状态」钉死。
    """

    def init_book(self, tags="urban,system", dirname=None):
        target = dirname or self.tmp
        result = run_cli(
            "init_book.py",
            "--dir", target,
            "--title", "默认值测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", tags,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return target

    def read_cfg(self, root):
        with open(os.path.join(root, ".soloent", "book.json"), encoding="utf-8-sig") as f:
            return json.load(f)

    def test_init_writes_usable_style_defaults(self):
        """默认值不能是「全关」：节奏机检要开着（启动档）、红线要非空、评审要开启。"""
        self.init_book(tags="urban,system")
        cfg = self.read_cfg(self.tmp)
        rhythm = cfg["checks"].get("rhythm")
        self.assertTrue(rhythm and rhythm.get("enabled"), "checks.rhythm 必须开箱启用")
        self.assertEqual(rhythm.get("_tier"), "bootstrap")
        self.assertTrue(cfg["checks"].get("panel"), "system 标签的书应带面板文体机检")
        self.assertTrue(cfg["gate"].get("review_required"), "第 7 步评审应默认开启")
        self.assertGreaterEqual(len(cfg["book"].get("extra_discipline") or []), 1,
                                "书特有红线不能留空（AGENTS.md §4 会整节留白）")

    def test_init_omits_panel_check_for_non_system_genre(self):
        """反向边界：不是面板流就不该塞面板规则（【】在别的排版习惯里是强调符号）。"""
        self.init_book(tags="xianxia")
        cfg = self.read_cfg(self.tmp)
        self.assertIsNone(cfg["checks"].get("panel"))
        self.assertTrue(cfg["checks"]["rhythm"].get("enabled"))

    def test_init_deploys_declared_rules(self):
        """init 必须把 rules.load 声明的规则**落到 .soloent/rules/**，否则风格层第一天就是空的。"""
        self.init_book()
        cfg = self.read_cfg(self.tmp)
        rules_dir = os.path.join(self.tmp, ".soloent", "rules")
        missing = [rel for rel in cfg["rules"]["load"]
                   if not os.path.isfile(os.path.join(rules_dir, rel.replace("/", os.sep)))]
        self.assertEqual(missing, [], "未部署的规则：%s" % missing)
        self.assertTrue(os.path.isfile(os.path.join(rules_dir, "ai-anti-patterns.md")))
        self.assertTrue(os.path.isfile(os.path.join(rules_dir, "story-style.md")))

    def test_brief_blocks_while_style_layer_is_unfilled(self):
        """风格层还是模板（✏️）时，接地单必须拦在动笔之前。"""
        self.init_book()
        result = run_cli("brief.py", "--root", self.tmp, "1")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("story-style.md", out)
        self.assertIn("MASTER.md", out)

    def test_brief_passes_style_layer_once_filled(self):
        """反向边界：把风格层填实后，接地单不再因它拦截。"""
        self.init_book()
        with open(os.path.join(self.tmp, ".soloent", "rules", "story-style.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 本书风格规则\n\n已填写。\n")
        with open(os.path.join(self.tmp, ".soloent", "constitution", "MASTER.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 创作宪法\n\n已填写。\n")
        with open(os.path.join(self.tmp, "1-边界", "预期.md"), "w", encoding="utf-8") as f:
            f.write("# 预期\n\n已填写。\n")

        result = run_cli("brief.py", "--root", self.tmp, "1")

        out = result.stdout + result.stderr
        # 注意不能断言「输出里没有 story-style.md」——第六节的正文就引用了这个文件名。
        # 要判的是「风格层不再报未就绪」。边界层是**另一层**：这个夹具没有拆书成果、
        # 预期也没声明对标，所以只该出现 ⚠️（提醒），不该出现 ❌/拦截。
        self.assertNotIn("仍含占位符", out)
        self.assertNotIn("与插件模板一字不差", out)
        self.assertIn("⚠️ 没有拆书成果", out)


class SyncAllTests(TempDirTest):
    """`sync.py --all`：一次写作品库下所有书——破坏面最大的操作，此前零用例。"""

    def test_sync_all_processes_every_book_in_library(self):
        lib = os.path.join(self.tmp, "lib")
        os.makedirs(lib, exist_ok=True)
        for name in ("书甲", "书乙"):
            root = os.path.join(lib, name)
            result = run_cli("init_book.py", "--dir", root, "--title", name,
                             "--genre", "测试", "--platform", "测试", "--tags", "urban",
                             "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        result = run_cli("sync.py", "--all", "--lib", lib)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("书甲", "书乙"):
            agents = os.path.join(lib, name, "AGENTS.md")
            self.assertTrue(os.path.isfile(agents), "缺 AGENTS.md：%s" % name)
            with open(agents, encoding="utf-8") as f:
                self.assertIn(name, f.read())


class ReviewGateTests(TempDirTest):
    """第 7 步（`/review`）的落地校验：此前连代理都没有，实测某书 69 章评审产物数为 0。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "评审闸门测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_chapter(self, n):
        with open(os.path.join(self.tmp, "chapters", "ch-%d.md" % n),
                  "w", encoding="utf-8") as f:
            f.write("# 第%03d章 测试\n\n他抬起头，看着那本账。这笔账他记了三年，一天没落。\n" % n)

    def write_flow(self, n):
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write("\n[流程] ch-%d 自查0 三连0 复查0 回写OK\n" % n)

    def run_isolated_preflight(self, isolated_name, preflight_args=None):
        isolated_scripts = os.path.join(self.tmp, isolated_name)
        shutil.copytree(
            SCRIPTS,
            isolated_scripts,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for script, source in {
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（2 章）')\n",
        }.items():
            with open(os.path.join(isolated_scripts, script), "w", encoding="utf-8") as f:
                f.write(source)
        return subprocess.run(
            [sys.executable, os.path.join(isolated_scripts, "preflight.py"),
             "--root", self.tmp, "--json", *(preflight_args or [])],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )

    def test_gate_requires_previous_chapter_review(self):
        self.init_book()
        self.write_chapter(1)
        self.write_chapter(2)
        self.write_flow(1)
        self.write_flow(2)

        # 先把第 1 章补验掉（--chapter 1 → 校验点推到 1，否则会被批量长跑规则拦下）
        first = self.run_isolated_preflight("review-first", ["--chapter", "1"])
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        # 现在正常出闸第 2 章：第 1 章没有评审记录 → 拦
        blocked = self.run_isolated_preflight("review-missing")
        data = json.loads(blocked.stdout)
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertTrue(any("质量评审" in b for b in data["blockers"]), data["blockers"])
        self.assertFalse(data["checks"]["review"]["ok"])

        # 补上评审产物 → 放行
        notes = os.path.join(self.tmp, "notes")
        os.makedirs(notes, exist_ok=True)
        with open(os.path.join(notes, "review-chapter-1-2026-01-01.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 第 1 章评审\n")
        passed = self.run_isolated_preflight("review-present")
        data2 = json.loads(passed.stdout)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertTrue(data2["checks"]["review"]["ok"], data2)

    def test_recovery_mode_does_not_require_reviews(self):
        """反向边界：`--chapter N` 是补验历史，不该被评审要求卡住。"""
        self.init_book()
        self.write_chapter(1)
        self.write_flow(1)

        result = self.run_isolated_preflight("review-recovery", ["--chapter", "1"])

        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(data["checks"]["review"]["ok"], data)


class RosterCompletenessTests(TempDirTest):
    """登记完整性：canon ／ 登记册 ／ 人物卡 三方互校（零 NLP，全是确定性比对）。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "登记测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_canon(self, names):
        rows = "\n".join("| %s | %s | — | 测试 |" % (n, n) for n in names)
        with open(os.path.join(self.tmp, ".soloent", "canon.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 正典\n\n## 一、人物\n\n"
                    "| 角色 | 唯一写法 | 常见错写 | 定位 |\n|---|---|---|---|\n"
                    + rows + "\n")

    def configure_roster(self, names):
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        cfg["checks"]["name_roster"] = {"enabled": True, "registered": names}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def run_check(self):
        return run_cli("consistency_check.py", "--root", self.tmp)

    def test_missing_cards_and_unregistered_names_are_reported(self):
        """形如本次事故：canon 登记了 A/B，人物卡只有 A；登记册里还有个 canon 没登记的 C。"""
        self.init_book()
        self.write_canon(["林澈", "陈砚秋"])
        self.configure_roster(["林澈", "陈砚秋", "簿房"])
        os.makedirs(os.path.join(self.tmp, "characters"), exist_ok=True)
        with open(os.path.join(self.tmp, "characters", "人物卡.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 人物卡\n\n林澈。\n")

        out = self.run_check().stdout + self.run_check().stderr

        self.assertIn("人物卡", out)
        self.assertIn("陈砚秋", out)        # canon 登记了，但没有卡
        self.assertIn("簿房", out)          # 登记册有、canon 没有
        self.assertIn("（正典）", out)

    def test_no_findings_when_three_sides_agree(self):
        """反向边界：三方一致时不该报任何东西。"""
        self.init_book()
        self.write_canon(["林澈"])
        self.configure_roster(["林澈"])
        os.makedirs(os.path.join(self.tmp, "characters"), exist_ok=True)
        with open(os.path.join(self.tmp, "characters", "人物卡.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 人物卡\n\n林澈：主角。\n")

        out = self.run_check().stdout + self.run_check().stderr

        self.assertNotIn("找不到卡", out)
        self.assertNotIn("却没登记", out)


class WatcherTests(TempDirTest):
    """`watch_and_check.py`：唯一的无人值守入口，此前三处检查全无引用。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "watcher 测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_watcher_detects_change_and_leaves_evidence(self):
        self.init_book()
        ch = os.path.join(self.tmp, "chapters", "ch-1.md")
        with open(ch, "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n第一版正文。\n")

        proc = subprocess.Popen(
            [sys.executable, os.path.join(SCRIPTS, "watch_and_check.py"),
             "--root", self.tmp, "--interval", "1"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            time.sleep(2.5)                       # 让它建立基线
            with open(ch, "a", encoding="utf-8") as f:
                f.write("改动一行。\n")            # 触发一轮
            deadline = time.time() + 20
            seen = ""
            report = None
            while time.time() < deadline:
                time.sleep(0.5)
                notes = os.path.join(self.tmp, "notes")
                if os.path.isdir(notes):
                    hits = [f for f in os.listdir(notes)
                            if f.startswith("对账-watch-") and f.endswith(".md")]
                    if hits:
                        report = hits[0]
                        break
            self.assertIsNotNone(report, "watcher 未在改动后留下带 watch tag 的报告")
        finally:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()

        self.assertIn("watcher 启动", out)
        self.assertIn("检测到变化", out)
        self.assertNotIn("Traceback", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".soloent", "watcher.pid")),
                        "watcher 应留下 pid 文件（stop_watcher.ps1 靠它收工）")


class ConfigTypeSafetyTests(TempDirTest):
    """可选机检项的类型校验：写错类型要给**指明键名**的报错，而不是 int() 抛栈。

    2026-09-20 实测：`checks.panel.max_lines` 写成「十」时，consistency_check 崩在
    `invalid literal for int() with base 10: '十'`——作者完全看不出是哪个键写错了。
    """

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "配置类型测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban,system",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def mutate_checks(self, mutate):
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        mutate(cfg["checks"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def test_bad_option_types_are_reported_with_key_names(self):
        cases = (
            (lambda c: c.__setitem__("panel", {"prefix": "【", "max_lines": "十"}),
             "checks.panel.max_lines"),
            (lambda c: c.__setitem__("hook_check", {"enabled": True, "tail_lines": "abc"}),
             "checks.hook_check.tail_lines"),
            (lambda c: c["rhythm"].__setitem__("narr_avg_min", "高"),
             "checks.rhythm.narr_avg_min"),
            (lambda c: c.__setitem__("name_roster", {"enabled": True, "registered": "林澈"}),
             "checks.name_roster.registered"),
        )
        for index, (mutate, expected) in enumerate(cases):
            with self.subTest(key=expected):
                root = os.path.join(self.tmp, "case-%d" % index)
                original_tmp = self.tmp
                self.tmp = root
                try:
                    self.init_book()
                    self.mutate_checks(mutate)
                    result = run_cli("consistency_check.py", "--root", root)
                finally:
                    self.tmp = original_tmp
                out = result.stdout + result.stderr
                self.assertNotIn("Traceback", out)
                self.assertIn(expected, out)
                self.assertIn("应为", out)

    def test_valid_float_thresholds_are_accepted(self):
        """反向边界：阈值允许小数（如 0.5/7.0），不能被类型校验误伤。"""
        self.init_book()
        self.mutate_checks(lambda c: c["rhythm"].__setitem__("turn_min_per_1000", 0.5))
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n正文。\n")

        result = run_cli("consistency_check.py", "--root", self.tmp)

        out = result.stdout + result.stderr
        self.assertNotIn("应为", out)
        self.assertEqual(result.returncode, 0, out)


class TemplateSafetyTests(TempDirTest):
    """模板里的**示例留痕行**绝不能构成一条可用凭证。

    差点当成 bug 改掉的一处：`templates/now.md` 里写着
    `[流程] ch-1 自查0 三连0 复查0 回写OK` 作为样例——如果它没被反引号包住，
    就会成为第 1 章的「现成凭证」（忘删 = 第 1 章免检）。本用例把这个性质钉住。
    """

    def test_now_template_example_line_is_not_a_credential(self):
        path = os.path.join(ROOT, "templates", "now.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()

        traces = re.findall(r"^\s*\[流程\]\s*ch-?0*(\d+)\b", text, re.M)

        self.assertEqual(traces, [], "模板里的示例留痕行会被闸门当成真凭证：%s" % traces)


class CnNumeralTitleTests(TempDirTest):
    """中文数字章标题（「第一章　药圃」）必须能识别章号——仙侠类书从装机起
    preflight 就没通过过的根因（默认 title_number_regex 只认阿拉伯数字）。
    兜底转换属于 kit 全局，不要求书级配置改正则。"""

    def test_cn_numeral_title_is_recognized(self):
        run_cli("init_book.py", "--dir", self.tmp, "--title", "中文数字测试书",
                "--genre", "测试", "--platform", "测试", "--tags", "urban", "--json")
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第一章　测试\n\n正文。\n")
        with open(os.path.join(self.tmp, "chapters", "ch-2.md"), "w", encoding="utf-8") as f:
            f.write("# 第十二章　测试\n\n正文。\n")

        result = run_cli("ledger.py", "--root", self.tmp)
        output = result.stdout + result.stderr
        self.assertNotIn("无法从标题识别章号", output, output[:400])
        self.assertIn("文件名章号 2 与标题章号 12 不一致", output,
                      "中文数字「十二」应转换为 12（顺便钉住转换正确性）")


class DraftFreeTests(TempDirTest):
    """起草自由（gate.draft_free）：风格发现降为「提示」、brief 撤禁令、多样本草稿。

    依据：2026-09-14/15 作者与 Gemini 的结论 + 三次实测——负向禁令不该在验收时
    惩罚「自由起草」的正文（docs/17、9-14 日志）。反向边界：关掉开关 = 旧行为；
    硬口径（锁定细节/口径冲突/账本）任何模式下都拦。
    """

    def init_book(self):
        run_cli("init_book.py", "--dir", self.tmp, "--title", "起草自由测试书",
                "--genre", "测试", "--platform", "测试", "--tags", "urban", "--json")

    def set_flag(self, value):
        import json as _json
        p = os.path.join(self.tmp, ".soloent", "book.json")
        d = _json.load(open(p, encoding="utf-8-sig"))
        d["gate"]["draft_free"] = value
        _json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def write_chapter(self):
        # 全 4 字短句 ×90：必然触发「句长节奏」风格发现（句均远低于任何下限）
        body = "他走了。她笑了。\n\n" * 90
        with open(os.path.join(self.tmp, "chapters", "ch-1.md"), "w", encoding="utf-8") as f:
            f.write("# 第001章 测试\n\n" + body + "师父剑神。")

    def test_draft_free_demotes_style_but_keeps_hard(self):
        self.init_book()
        self.write_chapter()
        import json as _json
        p = os.path.join(self.tmp, ".soloent", "book.json")
        d = _json.load(open(p, encoding="utf-8-sig"))
        d["checks"]["locked_details"] = [["测试锁定", ["师父"], ["剑神"], "测试硬口径拦截"]]
        _json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        self.set_flag(True)
        out = run_cli("consistency_check.py", "--root", self.tmp).stdout
        self.assertIn("严重 1", out, "硬口径（锁定细节）必须保持拦截：" + out[:300])
        self.assertRegex(out, r"提示 [1-9]", "风格发现应降为提示：" + out[:300])
        self.assertIn("[测试锁定]", out)

        self.set_flag(False)
        out2 = run_cli("consistency_check.py", "--root", self.tmp).stdout
        self.assertRegex(out2, r"严重 [2-9]", "关掉开关必须回到旧行为（风格=严重）：" + out2[:300])

    def test_brief_draft_free_hides_style_bans(self):
        self.init_book()
        self.set_flag(True)
        out = run_cli("brief.py", "--root", self.tmp).stdout
        self.assertIn("起草自由模式", out)
        self.assertNotIn("黑名单词（", out, "起草上下文里不该出现风格禁令词表")
        self.set_flag(False)
        out2 = run_cli("brief.py", "--root", self.tmp).stdout
        self.assertNotIn("起草自由模式", out2)

    def test_draft_samples_alternate_profiles(self):
        self.init_book()
        result = run_cli("draft.py", "--root", self.tmp,
                         "--name", "对比试写", "--samples", "2", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import json as _json
        data = _json.loads(result.stdout)
        self.assertEqual(len(data["drafts"]), 2)
        bodies = [open(os.path.join(self.tmp, rel), encoding="utf-8").read()
                  for rel in data["drafts"]]
        self.assertIn("自由口径", bodies[0])
        self.assertIn("红线口径（对照）", bodies[1])
        self.assertIn("章级三限", bodies[1], "对照样本必须带完整禁令清单")
        for b in bodies:
            self.assertIn("NON-CANONICAL", b)


class RuleResolutionTests(TempDirTest):
    """书作者自己写的规则必须真的生效，不能因为只查插件目录而变成死路径。

    两处共用 `book.resolve_rule`（kit 资产优先，缺失时回落到 `.soloent/rules/`）：
      · `sync.py` 渲染 AGENTS.md 的路径
      · `hooks/inject_canon.py` 的 SessionStart 规则正文注入
    以前两处都只查 kit 资产，于是书自写的规则在 AGENTS.md 里是一条**不存在的路径**、
    在注入里被当成「缺失文件」跳过 —— 规则写了却从来没生效（2026-09-20 实测）。
    """

    def init_book(self):
        run_cli("init_book.py", "--dir", self.tmp, "--title", "规则定位测试书",
                "--genre", "测试", "--platform", "测试", "--tags", "urban", "--json")

    def _load(self):
        sys.path.insert(0, SCRIPTS)
        import kit as _kit
        return _kit, _kit.load_book(["--root", self.tmp])

    def test_book_authored_rule_is_preferred_when_kit_lacks_it(self):
        """kit 里没有的规则 → 必须指向本书 `.soloent/rules/` 下的自写副本。"""
        self.init_book()
        local = os.path.join(self.tmp, ".soloent", "rules", "my-hard-rules.md")
        with open(local, "w", encoding="utf-8") as f:
            f.write("# 本书硬口径\n")

        _kit, book = self._load()
        hit = book.resolve_rule("my-hard-rules.md")

        self.assertIsNotNone(hit, "书自写的规则定位不到")
        self.assertEqual(hit[1], "book")
        self.assertEqual(os.path.normcase(os.path.abspath(hit[0])),
                         os.path.normcase(os.path.abspath(local)))

    def test_kit_rule_wins_over_stale_book_copy(self):
        """反向边界：共有规则以插件版本为准，某本书留着的旧副本不该被优先读到。"""
        self.init_book()
        _kit, book = self._load()
        shared = "ai-anti-patterns.md"
        kit_path = os.path.join(book.vault, "rules", shared)
        if not os.path.isfile(kit_path):
            self.skipTest("插件缺共享规则 %s" % shared)

        hit = book.resolve_rule(shared)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "kit", "共有规则应读插件版本，避免某本书读到过期副本")

    def test_missing_rule_resolves_to_none(self):
        """两边都没有 → None（调用方把它列进「未能注入」，不能猜一个路径出来）。"""
        self.init_book()
        _kit, book = self._load()
        self.assertIsNone(book.resolve_rule("no-such-rule.md"))


class RuleInjectionBudgetTests(TempDirTest):
    """注入超预算时必须**报警**，绝不能静默丢规则。

    踩到的坑（2026-09-20）：超预算分支只把「后面还没处理的」列进提示，
    **当前这条自己被漏掉**——于是被挤掉的若是最后一条，就没有任何警告，
    规则静默消失。而最后一条往往是题材文风规则（最该注入的那条）。
    """

    def _run(self, n_files, size_each, budget):
        """造一个临时「vault/rules」并跑 collect_rules。"""
        sys.path.insert(0, os.path.join(ROOT, "hooks"))
        import importlib
        ic = importlib.import_module("inject_canon")
        vault = os.path.join(self.tmp, "vault")
        rdir = os.path.join(vault, "rules")
        os.makedirs(rdir, exist_ok=True)
        load = []
        for i in range(n_files):
            name = "r%d.md" % i
            load.append(name)
            with open(os.path.join(rdir, name), "w", encoding="utf-8") as f:
                f.write("规" * size_each)

        class _B:
            cfg = {"rules": {"load": load}}

            def sec(self, _k):
                return {"load": load}

            def resolve_rule(self, rel):
                # 方法体**会**捕获外层局部变量（类体不会，见下）
                return (os.path.join(vault, "rules", str(rel).replace("/", os.sep)), "kit")

        # ⚠️ 不能在类体里写 `vault = vault`：类作用域不捕获外层函数局部变量（NameError）。
        #    类外赋值。
        _B.vault = vault

        return ic.collect_rules(_B(), max_bytes=budget)

    def test_last_file_dropped_by_budget_is_reported(self):
        """被挤掉的正好是最后一条时，也必须出现在警告里（不能静默消失）。"""
        txt, names = self._run(n_files=3, size_each=2000, budget=3000)
        self.assertTrue(len(names) < 3, "预算本应不够装下 3 条")
        self.assertIn("未能注入", txt, "超预算却没有任何提示 = 规则静默消失")
        self.assertIn("r2.md", txt, "被挤掉的最后一条必须点名")

    def test_within_budget_reports_nothing(self):
        """反向边界：装得下时不该报警告，且全部注入。"""
        txt, names = self._run(n_files=3, size_each=100, budget=20000)
        self.assertEqual(len(names), 3)
        self.assertNotIn("未能注入", txt)


class InstallAndLibraryScanTests(TempDirTest):
    """`install.py`（零用例）与 `doctor --lib`（仅 1 处引用）——两个覆盖面最大的入口。

    install 装错地方 = 所有书都跑不起来；doctor --lib 是唯一一次看全库的地方，
    它要能逐本报出「哪本书坏了」而不是笼统说一句。
    """

    def test_install_list_and_custom_dir(self):
        listed = run_cli("install.py", "--list")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)

        target = os.path.join(self.tmp, "skills")
        installed = run_cli("install.py", "--dir", target)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.assertTrue(os.path.isfile(os.path.join(target, "novel-writing", "SKILL.md")),
                        "装完后目标目录下应有 novel-writing/SKILL.md")

        checked = run_cli("install.py", "--dir", target, "--check")
        self.assertEqual(checked.returncode, 0,
                         "刚装完 --check 应判一致：" + checked.stdout + checked.stderr)

        # 改动已装副本 → --check 必须发现差异（而不是永远说一致）
        with open(os.path.join(target, "novel-writing", "SKILL.md"),
                  "a", encoding="utf-8") as f:
            f.write("\n<!-- 手工改动 -->\n")
        drifted = run_cli("install.py", "--dir", target, "--check")
        self.assertEqual(drifted.returncode, 1,
                         "已装副本被改动，--check 必须报差异：" + drifted.stdout + drifted.stderr)

    def init_book(self, root, tags="urban"):
        result = run_cli("init_book.py", "--dir", root, "--title", os.path.basename(root),
                         "--genre", "测试", "--platform", "测试", "--tags", tags, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_doctor_lib_reports_every_book(self):
        lib = os.path.join(self.tmp, "lib")
        os.makedirs(lib, exist_ok=True)
        self.init_book(os.path.join(lib, "好甲"))
        self.init_book(os.path.join(lib, "好乙"))
        # 造一本坏的：book.json 被写坏
        bad = os.path.join(lib, "坏丙")
        self.init_book(bad)
        with open(os.path.join(bad, ".soloent", "book.json"), "w", encoding="utf-8") as f:
            f.write("{ 这不是 JSON")

        result = run_cli("doctor.py", "--lib", lib)

        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, out)      # 有 err → 退出码 2
        for name in ("好甲", "好乙", "坏丙"):
            self.assertIn(name, out, "库级体检漏掉了 %s" % name)
        self.assertIn("坏丙", out)


class RhythmCalibrationTests(TempDirTest):
    """`--suggest-rhythm`：把「等拆完样板书再校准」变成一条命令，且**只读**。"""

    def init_book(self):
        result = run_cli("init_book.py", "--dir", self.tmp, "--title", "校准测试书",
                         "--genre", "测试", "--platform", "测试", "--tags", "urban", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_chapters(self, n=4):
        # 句长节奏按章统计，样本必须超过 min_chars（默认 300 汉字），否则整章被跳过
        block = ("夜里十一点半，林澈把最后一单送进城西老巷，回来时车轮碾过积水，"
                 "车架上的反光条在路灯下一明一暗，楼道口的风把单子吹得哗哗响。\n\n"
                 "但是他没有慌。七个月跑单教会他一件事，那就是任何东西都要留第二本账，"
                 "账目清楚，日子才不至于被一笔意外的支出压垮。\n\n"
                 "不过今天多了一笔谁都没想到的收入，他把这笔钱单独记了一行，"
                 "又在旁边画了个小圈，提醒自己明天去问问平台。\n\n")
        body = block * 3
        os.makedirs(os.path.join(self.tmp, "chapters"), exist_ok=True)
        for i in range(1, n + 1):
            with open(os.path.join(self.tmp, "chapters", "ch-%d.md" % i),
                      "w", encoding="utf-8") as f:
                f.write("# 第%03d章 测试\n\n%s" % (i, body))

    def test_suggest_rhythm_prints_pasteable_thresholds(self):
        self.init_book()
        self.write_chapters()
        cfg_path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(cfg_path, encoding="utf-8-sig") as f:
            before = f.read()

        result = run_cli("consistency_check.py", "--root", self.tmp, "--suggest-rhythm")

        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("句长节奏阈值建议", out)
        for key in ("narr_avg_min", "narr_long_min_pct", "short_ge10_max_pct",
                    "dialogue_max_pct", "turn_min_per_1000"):
            self.assertIn(key, out)
        # 只读：不改 book.json，也不落对账报告
        with open(cfg_path, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), before)
        notes = os.path.join(self.tmp, "notes")
        self.assertEqual([f for f in os.listdir(notes) if f.startswith("对账-")], [],
                         "校准模式不该写对账报告")

    def test_suggest_rhythm_without_samples_explains_itself(self):
        """反向边界：没有样本时要说明白，而不是抛栈或给出空建议。"""
        self.init_book()

        result = run_cli("consistency_check.py", "--root", self.tmp, "--suggest-rhythm")

        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("没有章节", out)
        self.assertNotIn("Traceback", out)


class BoundaryPreconditionTests(TempDirTest):
    """拆书前置：**声明了对标却不拆**要拦；没有对标只提醒；写明理由即放行。"""

    def init_book(self):
        result = run_cli("init_book.py", "--dir", self.tmp, "--title", "边界测试书",
                         "--genre", "测试", "--platform", "测试", "--tags", "urban", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_expectation(self, benchmark=None):
        text = "# 预期\n\n## 题材边界\n\n- 核心类型：测试\n"
        if benchmark:
            text += "\n- **对标作品**：%s\n" % benchmark
        with open(os.path.join(self.tmp, "1-边界", "预期.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def opt_out(self, reason):
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        cfg.setdefault("boundary", {})["opt_out_reason"] = reason
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def brief(self):
        r = run_cli("brief.py", "--root", self.tmp, "1")
        return r.stdout + r.stderr

    def test_declared_benchmark_without_breakdown_blocks(self):
        self.init_book()
        self.write_expectation("《某本对标书》的钩子密度")

        out = self.brief()

        self.assertIn("对标", out)                       # 文案要点出你声明了什么
        self.assertIn("拆书成果", out)
        self.assertIn("拦截：正式模式", out)

    def test_breakdown_artifact_satisfies_the_precondition(self):
        self.init_book()
        self.write_expectation("《某本对标书》的钩子密度")
        with open(os.path.join(self.tmp, "1-边界", "1.1_开篇拆解.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 开篇拆解\n")

        out = self.brief()

        self.assertNotIn("拆书成果", out)

    def test_declared_opt_out_reason_is_respected(self):
        self.init_book()
        self.write_expectation("《某本对标书》的钩子密度")
        self.opt_out("对标只是参考方向，这本要做的是另一种结构，不拆。")

        out = self.brief()

        self.assertNotIn("拆书成果", out)

    def test_no_benchmark_is_only_a_warning(self):
        """反向边界：没有对标也没拆书，只提醒不拦（「没有样板书」本来就是允许的起点）。"""
        self.init_book()
        self.write_expectation(benchmark=None)

        out = self.brief()

        # 只提醒不拦截：阻断式文案不该出现，提醒式文案该出现
        self.assertNotIn("拦截：正式模式：预期里声明了对标", out)
        self.assertIn("⚠️ 没有拆书成果", out)


class LedgerCollectionTests(TempDirTest):
    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "集合核对测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_chapter(self, filename, title_chapter):
        path = os.path.join(self.tmp, "chapters", filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 第{title_chapter:03d}章 测试\n\n正文。\n")

    def write_ledger(self, chapter_numbers):
        config_path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(config_path, encoding="utf-8-sig") as f:
            config = json.load(f)
        ledger = config["ledger"]
        columns = ledger["columns"]
        int_columns = set(ledger.get("int_columns") or [])
        chapter_column = ledger.get("chapter_column", "ch")
        rank_column = ledger.get("rank_column")
        rank = (ledger.get("rank_order") or ["F⁻"])[0]
        rows = []
        for chapter in chapter_numbers:
            cells = []
            for column in columns:
                if column == chapter_column:
                    cells.append(str(chapter))
                elif column in int_columns:
                    cells.append("0")
                elif column == rank_column:
                    cells.append(rank)
                else:
                    cells.append("—")
            rows.append("\t".join(cells))
        ledger_path = os.path.join(self.tmp, config["paths"]["ledger"])
        with open(ledger_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    def test_chapter_number_gaps_are_reported_from_chapter_files(self):
        self.init_book()
        self.write_chapter("ch-1.md", 1)
        self.write_chapter("ch-3.md", 3)
        self.write_ledger([1, 3])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("正文章号有缺口：2", result.stdout + result.stderr)

    def test_duplicate_chapter_numbers_are_reported(self):
        self.init_book()
        self.write_chapter("ch-1.md", 1)
        self.write_chapter("ch-01.md", 1)
        self.write_ledger([1])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("正文第 1 章对应 2 个文件（重号）", result.stdout + result.stderr)

    def test_ledger_must_start_at_chapter_one(self):
        self.init_book()
        self.write_chapter("ch-5.md", 5)
        self.write_ledger([5])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("账本必须从第 1 章开始，当前首章为 5", result.stdout + result.stderr)

    def test_chapter_and_ledger_number_sets_must_match_exactly(self):
        self.init_book()
        for chapter in (1, 2, 4):
            self.write_chapter(f"ch-{chapter}.md", chapter)
        self.write_ledger([1, 3, 4])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("正文有、账本缺：2", output)
        self.assertIn("账本有、正文缺：3", output)

    def test_filename_and_title_chapter_numbers_must_match(self):
        self.init_book()
        self.write_chapter("ch-1.md", 2)
        self.write_ledger([1])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("文件名章号 1 与标题章号 2 不一致", output)

    def test_unparseable_chapter_title_number_is_blocked(self):
        self.init_book()
        path = os.path.join(self.tmp, "chapters", "ch-1.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 没有章号的标题\n\n正文。\n")
        self.write_ledger([1])

        result = run_cli("ledger.py", "--root", self.tmp, "--quiet")

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("无法从标题识别章号：ch-1.md", output)


class AutoEntryTests(TempDirTest):
    """自动入口去重、增量与并发保护（NW-P1-008）。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "自动入口测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_chapter(self, n=1):
        with open(os.path.join(self.tmp, "chapters", f"ch-{n}.md"), "w", encoding="utf-8") as f:
            f.write(f"# 第{n:03d}章 测试\n\n第 {n} 章正文。\n")

    def write_flow_line(self, n=1):
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        with open(now_path, "a", encoding="utf-8") as f:
            f.write(f"\n[流程] ch-{n} 自查0 三连0 复查0 回写OK\n")

    def counting_child(self, counter, label, summary, sleep=0.0):
        """子检查替身：记录 start/end 时间戳到 counter，输出合法摘要。

        ⚠️ 记账逻辑必须放在 `if __name__ == '__main__':` 之内——替身要像真实脚本一样
        **只在被当脚本执行时留痕**。真实 consistency_check.py 的模块体只有定义与常量，
        import 它没有任何副作用；而 preflight 的「留痕对撞」层会 `import consistency_check`
        复用它的判据（不另跑一遍扫描）。把 import 也算成一次执行，会让
        「一次保存只跑一遍正文扫描」的断言误报（2026-09-20 踩过）。
        """
        sleep_line = f"    time.sleep({sleep})\n" if sleep else ""
        return (
            "import time\n"
            "if __name__ == '__main__':\n"
            f"    with open({counter!r}, 'a', encoding='utf-8') as f:\n"
            f"        f.write({label!r} + ' start ' + repr(time.time()) + chr(10))\n"
            + sleep_line +
            f"    with open({counter!r}, 'a', encoding='utf-8') as f:\n"
            f"        f.write({label!r} + ' end ' + repr(time.time()) + chr(10))\n"
            f"    print({summary!r})\n"
        )

    def isolated_plugin(self, replacements=None, name="isolated-plugin"):
        """复制一份独立插件（scripts/ + hooks/），替换指定子脚本。"""
        root = os.path.join(self.tmp, name)
        shutil.copytree(SCRIPTS, os.path.join(root, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(os.path.join(ROOT, "hooks"), os.path.join(root, "hooks"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for script, source in (replacements or {}).items():
            with open(os.path.join(root, "scripts", script), "w", encoding="utf-8") as f:
                f.write(source)
        return root

    def run_hook(self, plugin_root, chapter_path):
        payload = {"tool_name": "Write", "tool_input": {"file_path": chapter_path}}
        return subprocess.run(
            [sys.executable, os.path.join(plugin_root, "hooks", "after_chapter_write.py")],
            input=json.dumps(payload),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )

    def test_hook_triggers_exactly_one_consistency_check(self):
        self.init_book()
        self.write_chapter(1)
        self.write_flow_line(1)
        counter = os.path.join(self.tmp, "invocations.log")
        children = {
            "consistency_check.py": self.counting_child(counter, "consistency_check",
                                                        "严重 0 · 中等 0 · 轻微 0"),
            "state_sync.py": self.counting_child(counter, "state_sync", "结果：✅ 状态一致。"),
            "ledger.py": self.counting_child(counter, "ledger", "结果：✅ 平账。（1 章）"),
        }
        plugin = self.isolated_plugin(children)
        chapter_path = os.path.join(self.tmp, "chapters", "ch-1.md")

        result = self.run_hook(plugin, chapter_path)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("章节对账结果", context)
        self.assertIn("通过闸门", context)
        with open(counter, encoding="utf-8") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
        # 一次保存只允许一遍正文扫描：consistency 由 preflight 编排执行一次，
        # 钩子不得再自己跑一遍（历史行为是两遍全量扫描）
        self.assertEqual(sum(1 for l in lines if l.startswith("consistency_check start")), 1, lines)
        self.assertEqual(sum(1 for l in lines if l.startswith("state_sync start")), 1, lines)
        self.assertEqual(sum(1 for l in lines if l.startswith("ledger start")), 1, lines)

    def test_hook_uses_hook_tag_for_report(self):
        self.init_book()
        self.write_chapter(1)
        self.write_flow_line(1)
        children = {
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }
        plugin = self.isolated_plugin(children, name="isolated-tag")
        chapter_path = os.path.join(self.tmp, "chapters", "ch-1.md")

        result = self.run_hook(plugin, chapter_path)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import glob
        reports = glob.glob(os.path.join(self.tmp, "notes", "对账-hook-*.md"))
        self.assertTrue(reports, "钩子入口的对账报告应带 hook tag 独立落盘")

    def test_concurrent_preflights_are_serialized(self):
        self.init_book()
        self.write_chapter(1)
        self.write_flow_line(1)
        counter = os.path.join(self.tmp, "concurrency.log")
        children = {
            "consistency_check.py": self.counting_child(counter, "consistency_check",
                                                        "严重 0 · 中等 0 · 轻微 0", sleep=0.6),
            "state_sync.py": "print('结果：✅ 状态一致。')\n",
            "ledger.py": "print('结果：✅ 平账。（1 章）')\n",
        }
        plugin = self.isolated_plugin(children, name="isolated-concurrency")
        preflight = os.path.join(plugin, "scripts", "preflight.py")
        procs = [
            subprocess.Popen(
                [sys.executable, preflight, "--root", self.tmp, "--no-advance"],
                cwd=ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            )
            for _ in range(2)
        ]
        outs = [p.communicate(timeout=120) for p in procs]
        for p, (out, err) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, out + err)

        events = []
        with open(counter, encoding="utf-8") as f:
            for line in f.read().splitlines():
                label, phase, ts = line.rsplit(" ", 2)[0], line.split()[1], float(line.split()[2])
                events.append((label, phase, ts))
        starts = sorted(t for label, phase, t in events if label == "consistency_check" and phase == "start")
        ends = sorted(t for label, phase, t in events if label == "consistency_check" and phase == "end")
        self.assertEqual(len(starts), 2)
        # 串行：第二次开始不得早于第一次结束（允许极小计时误差）
        self.assertGreaterEqual(starts[1], ends[0] - 0.05, events)

    def test_lock_busy_preflight_skips_with_exit_code_3(self):
        self.init_book()
        self.write_chapter(1)
        self.write_flow_line(1)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time\n"
             f"sys.path.insert(0, {SCRIPTS!r})\n"
             "import kit\n"
             f"with kit.project_lock({self.tmp!r}, 'preflight', timeout=5):\n"
             "    time.sleep(4)\n"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            time.sleep(1.0)
            result = run_cli("preflight.py", "--root", self.tmp, "--lock-timeout", "1")
        finally:
            holder.wait(timeout=30)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("跳过", output)
        self.assertIn("不等于通过", output)

    def test_concurrent_trend_writes_stay_valid(self):
        self.init_book()
        self.write_chapter(1)
        procs = [
            subprocess.Popen(
                [sys.executable, os.path.join(SCRIPTS, "consistency_check.py"),
                 "--root", self.tmp],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            for _ in range(4)
        ]
        for p in procs:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, out + err)

        import csv
        import datetime
        trend = os.path.join(self.tmp, "notes", "对账趋势.csv")
        self.assertTrue(os.path.isfile(trend))
        with open(trend, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]
        self.assertEqual(rows[0], ["日期", "严重", "中等", "轻微", "扫描章数"])
        today = datetime.date.today().isoformat()
        today_rows = [r for r in rows[1:] if r and r[0] == today]
        self.assertEqual(len(today_rows), 1, rows)
        self.assertEqual(len(today_rows[0]), 5)


class DocumentationConsistencyTests(unittest.TestCase):
    def read_project_file(self, relative_path):
        with open(os.path.join(ROOT, *relative_path.split("/")), encoding="utf-8-sig") as f:
            return f.read()

    def test_process_numbering_uses_one_explicit_mapping(self):
        skill = self.read_project_file("SKILL.md")
        chapter_doc = self.read_project_file("docs/06-正文.md")
        preflight = self.read_project_file("scripts/preflight.py")
        now_template = self.read_project_file("templates/now.md")
        overview = self.read_project_file("docs/00-总览与流程.md")

        self.assertIn("**B3 句法自查**", skill)
        self.assertIn("**B4 技能三连**", skill)
        self.assertIn("**B5 连续性复查**", skill)
        self.assertIn("### B6. 收尾回写 + 出章闸门", skill)
        self.assertIn("## 2. 句法自查", chapter_doc)
        self.assertIn("## 5. 收尾回写", chapter_doc)
        mapping = "正文步骤 2–5（SKILL B3–B6）"
        self.assertIn(mapping, preflight)
        self.assertIn(mapping, now_template)
        self.assertNotIn("B0 接地", overview)
        self.assertIn("B1 接地", overview)

    def test_formal_vs_draft_boundary_is_documented_consistently(self):
        readme = self.read_project_file("README.md")
        skill = self.read_project_file("SKILL.md")
        overview = self.read_project_file("docs/00-总览与流程.md")
        init_doc = self.read_project_file("docs/01-初始化.md")
        brief = self.read_project_file("scripts/brief.py")

        for text in (readme, skill, overview, init_doc):
            self.assertIn("探索草稿", text)
            self.assertNotIn("直接写第一章", text)
        # 草稿不进正史：位置、标记与工具入口在三份关键文档里口径一致
        for text in (skill, overview):
            self.assertIn("drafts/", text)
            self.assertIn("非正典", text)
        self.assertIn("tools/draft.py", readme)
        self.assertIn("tools/draft.py", skill)
        self.assertIn("tools/draft.py", brief)


class ConfigContractTests(TempDirTest):
    """配置契约与迁移（NW-P2-011）。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "配置契约测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def edit_config(self, mutate):
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        mutate(cfg)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def test_tools_reject_config_with_missing_required_keys(self):
        self.init_book()
        self.edit_config(lambda cfg: cfg["paths"].pop("now"))

        result = run_cli("brief.py", "--root", self.tmp)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("配置结构校验未通过", output)
        self.assertIn("paths.now", output)

    def test_sync_refuses_generation_when_schema_mismatches(self):
        self.init_book()
        self.edit_config(lambda cfg: cfg.__setitem__("_schema", 0))

        result = run_cli("sync.py", "--root", self.tmp)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("_schema", output)

    def test_migrate_upgrades_legacy_config_and_backs_up(self):
        self.init_book()
        def break_it(cfg):
            cfg["_schema"] = 0
            cfg["paths"].pop("now")
            cfg.pop("chapter")
        self.edit_config(break_it)

        result = run_cli("migrate_config.py", "--root", self.tmp)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["_schema"], 1)
        self.assertEqual(cfg["paths"]["now"], ".soloent/memory/now.md")
        self.assertEqual(cfg["chapter"]["file_regex"], "^ch-(\\d+)\\.md$")
        # 用户已有值不能被迁移改写
        self.assertEqual(cfg["book"]["title"], "配置契约测试书")
        import glob
        backups = glob.glob(os.path.join(self.tmp, ".soloent", "backups", "*-migrate", "book.json"))
        self.assertTrue(backups, output)

        # 迁移后工具恢复正常
        brief = run_cli("brief.py", "--root", self.tmp)
        self.assertNotEqual(brief.returncode, 2, brief.stdout + brief.stderr)

    def test_migrate_refuses_to_write_when_unfixable(self):
        self.init_book()
        def break_hard(cfg):
            cfg["_schema"] = 0
            cfg["book"].pop("title")
            cfg["ledger"]["columns"] = []
        self.edit_config(break_hard)

        result = run_cli("migrate_config.py", "--root", self.tmp)

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("未写入任何文件", output)
        path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["_schema"], 0)
        self.assertNotIn("title", cfg["book"])


class BookLocationTests(TempDirTest):
    """多书定位禁止猜测（NW-P1-006）：定位不了书就一根正典都不许注入。"""

    def init_book(self, name):
        book_dir = os.path.join(self.tmp, name)
        os.makedirs(book_dir, exist_ok=True)
        result = run_cli(
            "init_book.py",
            "--dir", book_dir,
            "--title", name,
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return book_dir

    def run_hook(self, cwd, *args):
        env = dict(os.environ)
        # 隔离外部定位信号，保证测试只考察 cwd / --root / --lib 的行为
        for key in ("NOVEL_BOOK_ROOT", "ZCODE_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
            env.pop(key, None)
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "hooks", "inject_canon.py"), *map(str, args)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )

    def hook_context(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_multiple_candidate_books_prevent_canon_injection(self):
        self.init_book("甲书")
        self.init_book("乙书")

        context = self.hook_context(self.run_hook(self.tmp))

        self.assertIn("检测到多本书", context)
        self.assertIn("未注入任何一本书的正典", context)
        self.assertNotIn("本书正典速查表", context)
        self.assertNotIn("SOLOENT.md 当前状态", context)

    def test_specific_book_directory_injects_only_that_book(self):
        self.init_book("甲书")
        self.init_book("乙书")
        book_a = os.path.join(self.tmp, "甲书")

        context = self.hook_context(self.run_hook(book_a))

        self.assertIn("本书正典速查表", context)
        self.assertIn("甲书", context)
        self.assertNotIn("乙书", context)
        self.assertIn("定位依据", context)

    def test_single_book_library_injects_that_book(self):
        self.init_book("孤本书")

        context = self.hook_context(self.run_hook(self.tmp))

        self.assertIn("本书正典速查表", context)
        self.assertIn("孤本书", context)
        self.assertIn("定位依据", context)
        self.assertNotIn("检测到多本书", context)

    def test_explicit_root_wins_over_ambiguous_library(self):
        self.init_book("甲书")
        self.init_book("乙书")
        book_b = os.path.join(self.tmp, "乙书")

        context = self.hook_context(self.run_hook(self.tmp, "--root", book_b))

        self.assertIn("本书正典速查表", context)
        self.assertIn("乙书", context)
        self.assertNotIn("甲书", context)
        self.assertNotIn("检测到多本书", context)

    def test_mtime_does_not_decide_between_books(self):
        # 即使某本书的 book.json 明显更新，多候选时也绝不按 mtime 挑一本注入
        older = self.init_book("甲书")
        newer = self.init_book("乙书")
        older_time = os.path.getmtime(os.path.join(older, ".soloent", "book.json")) - 3600
        newer_time = os.path.getmtime(os.path.join(newer, ".soloent", "book.json"))
        os.utime(os.path.join(older, ".soloent", "book.json"), (older_time, older_time))
        os.utime(os.path.join(newer, ".soloent", "book.json"), (newer_time, newer_time))

        context = self.hook_context(self.run_hook(self.tmp))

        self.assertIn("检测到多本书", context)
        self.assertNotIn("本书正典速查表", context)


class SecretSafetyTests(TempDirTest):
    """密钥与 Git 安全（NW-P1-007 / NW-P2-009）。"""

    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "密钥安全测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_init_creates_gitignore_block_and_env_example(self):
        self.init_book()
        gitignore_path = os.path.join(self.tmp, ".gitignore")
        self.assertTrue(os.path.isfile(gitignore_path))
        with open(gitignore_path, encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn(".env", content)
        self.assertIn(".soloent/.api.env", content)
        self.assertIn("!.env.example", content)
        self.assertIn("# >>> novel-writing (managed) >>>", content)

        example_path = os.path.join(self.tmp, ".env.example")
        self.assertTrue(os.path.isfile(example_path))
        with open(example_path, encoding="utf-8-sig") as f:
            self.assertIn("NOVEL_API_KEY=", f.read())

    def test_sync_preserves_user_gitignore_lines_and_stays_idempotent(self):
        gitignore_path = os.path.join(self.tmp, ".gitignore")
        custom = "我的自定义产物/\n*.secret\n"
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(custom)

        self.init_book()

        with open(gitignore_path, encoding="utf-8-sig") as f:
            merged = f.read()
        self.assertIn("我的自定义产物/", merged)
        self.assertIn("*.secret", merged)
        self.assertIn(".soloent/.api.env", merged)

        result = run_cli("sync.py", "--root", self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(gitignore_path, encoding="utf-8-sig") as f:
            resynced = f.read()
        self.assertIn("我的自定义产物/", resynced)
        self.assertEqual(resynced.count("# >>> novel-writing (managed) >>>"), 1)

        check = run_cli("sync.py", "--root", self.tmp, "--check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_doctor_flags_tracked_secret_file(self):
        if shutil.which("git") is None:
            self.skipTest("git 不可用")
        self.init_book()
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "regression@test.local"],
            ["git", "config", "user.name", "regression"],
        ):
            subprocess.run(cmd, cwd=self.tmp, capture_output=True, timeout=30)
        env_path = os.path.join(self.tmp, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("NOVEL_API_KEY=leaked-key\n")
        # -f 是刻意的：.gitignore 托管段本来就会拦住 .env，这里模拟"绕过拦截的误提交"
        subprocess.run(["git", "add", "-f", ".env"], cwd=self.tmp, capture_output=True, timeout=30)
        subprocess.run(["git", "commit", "-m", "误提交密钥"],
                       cwd=self.tmp, capture_output=True, timeout=30)

        result = run_cli("doctor.py", "--root", self.tmp, "--json")

        rows = json.loads(result.stdout)
        tracked = [r for r in rows if "已被 Git 跟踪" in r.get("what", "")]
        self.assertTrue(tracked, rows)
        self.assertTrue(any(".env" in r["what"] for r in tracked))
        self.assertTrue(all(r["level"] == "err" for r in tracked))

    def test_doctor_passes_secret_check_when_not_tracked(self):
        self.init_book()
        result = run_cli("doctor.py", "--root", self.tmp, "--json")
        rows = json.loads(result.stdout)
        # where 形如「<书名> · 密钥」；书名本身含「密钥」二字，不能直接 in 匹配
        secret_rows = [r for r in rows if r.get("where", "").endswith("· 密钥")]
        self.assertTrue(secret_rows)
        self.assertFalse(any(r["level"] == "err" for r in secret_rows))


class BriefModeTests(TempDirTest):
    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "模式边界测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fill_style_layer(self):
        """把风格/红线层填实——2026-09-20 起它也是「正式模式的前置资料」之一。"""
        for rel, text in (
            (os.path.join(".soloent", "rules", "story-style.md"), "# 本书风格规则\n\n已填写。\n"),
            (os.path.join(".soloent", "constitution", "MASTER.md"), "# 创作宪法\n\n已填写。\n"),
            (os.path.join("1-边界", "预期.md"), "# 预期\n\n已填写。\n"),
        ):
            with open(os.path.join(self.tmp, rel), "w", encoding="utf-8") as f:
                f.write(text)

    def test_formal_brief_fails_when_story_inputs_are_not_ready(self):
        self.init_book()

        result = run_cli("brief.py", "--root", self.tmp, "1")

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("正式模式：第 1 章细纲未就绪", output)
        self.assertIn("正式模式：账本未准备第 1 章", output)
        self.assertIn("正式模式：正典未就绪", output)

    def test_formal_brief_succeeds_when_required_inputs_are_ready(self):
        self.init_book()
        config_path = os.path.join(self.tmp, ".soloent", "book.json")
        with open(config_path, encoding="utf-8-sig") as f:
            config = json.load(f)
        canon_path = os.path.join(self.tmp, config["paths"]["canon"])
        with open(canon_path, "w", encoding="utf-8") as f:
            f.write("# 正典\n\n## 一、人物\n\n| 角色 | 唯一写法 |\n|---|---|\n| 主角 | 林一 |\n")
        outline_path = os.path.join(self.tmp, config["paths"]["outline"])
        os.makedirs(os.path.dirname(outline_path), exist_ok=True)
        with open(outline_path, "w", encoding="utf-8") as f:
            f.write(
                "# 第一卷细纲\n\n## 第1章 开始\n"
                "**核心概括**：林一进入故事。\n\n"
                "**章末钩子**：悬念·门外有人敲门。\n"
            )
        ledger = config["ledger"]
        cells = []
        for column in ledger["columns"]:
            if column == ledger.get("chapter_column", "ch"):
                cells.append("1")
            elif column in set(ledger.get("int_columns") or []):
                cells.append("0")
            elif column == ledger.get("rank_column"):
                cells.append((ledger.get("rank_order") or ["F⁻"])[0])
            else:
                cells.append("—")
        ledger_path = os.path.join(self.tmp, config["paths"]["ledger"])
        with open(ledger_path, "w", encoding="utf-8") as f:
            f.write("\t".join(cells) + "\n")
        self.fill_style_layer()

        result = run_cli("brief.py", "--root", self.tmp, "1")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("标题：第001章 开始", result.stdout)


class DraftModeTests(TempDirTest):
    def init_book(self):
        result = run_cli(
            "init_book.py",
            "--dir", self.tmp,
            "--title", "探索草稿测试书",
            "--genre", "测试",
            "--platform", "测试",
            "--tags", "urban",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exploration_draft_stays_out_of_formal_state(self):
        self.init_book()
        now_path = os.path.join(self.tmp, ".soloent", "memory", "now.md")
        ledger_path = os.path.join(self.tmp, ".soloent", "ledger.tsv")
        with open(now_path, encoding="utf-8-sig") as f:
            now_before = f.read()
        with open(ledger_path, encoding="utf-8-sig") as f:
            ledger_before = f.read()

        result = run_cli(
            "draft.py",
            "--root", self.tmp,
            "--name", "开篇试写",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        relative = payload["draft"]
        self.assertTrue(relative.replace("\\", "/").startswith("drafts/"), payload)
        draft_path = os.path.join(self.tmp, relative)
        with open(draft_path, encoding="utf-8-sig") as f:
            draft_text = f.read()
        self.assertIn("NON-CANONICAL", draft_text)
        self.assertIn("非正典", draft_text)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "chapters")), [])
        with open(now_path, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), now_before)
        with open(ledger_path, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), ledger_before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".soloent", "checkpoint.json")))

        preflight = run_cli("preflight.py", "--root", self.tmp)
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
        self.assertIn("无章节可校验", preflight.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
