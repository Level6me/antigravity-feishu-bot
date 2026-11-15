# -*- coding: utf-8 -*-
"""Regression tests for multi-turn `/notes` persistence and JIT credential gating."""

import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agent_memory.store import strip_injected_prompt_headers
from handlers.pipeline import _build_turn_notes_block, filter_relevant_notes


class TestNotesPersistence(unittest.TestCase):
    def setUp(self):
        self.notes = [
            "输出表格尽量简洁，默认用简体中文",
            "树莓派ip 43.108.18.47 用户名jiang 密码dummy_pw 端口2314",
            "阿里云韩国服务器 ip 1.2.3.4 端口29675 用户名root 密码dummy_prod_pw",
        ]

    def test_01_follow_up_turns_retain_regular_notes_and_block_unrelated_secrets(self):
        block = _build_turn_notes_block(
            self.notes,
            query="帮我重构一下当前项目的登录模块",
            context="",
            already_injected_static=True,
            is_lightweight_chat=False,
        )
        self.assertIn("[User's Relevant Notes]", block)
        self.assertIn("输出表格尽量简洁，默认用简体中文", block)
        self.assertNotIn("dummy_pw", block)
        self.assertNotIn("dummy_prod_pw", block)

    def test_02_follow_up_turn_mounts_matched_credential_plus_regular_notes(self):
        block = _build_turn_notes_block(
            self.notes,
            query="连上树莓派检查一下日志",
            context="",
            already_injected_static=True,
            is_lightweight_chat=False,
        )
        self.assertIn("输出表格尽量简洁，默认用简体中文", block)
        self.assertIn("43.108.18.47", block)
        self.assertNotIn("1.2.3.4", block)

    def test_03_broad_note_recall_without_false_positive_on_filename(self):
        recall_block = _build_turn_notes_block(
            self.notes,
            query="为什么不记得/notes 里面的笔记了？请列出笔记里记的内容",
            context="",
            already_injected_static=True,
            is_lightweight_chat=False,
        )
        self.assertIn("43.108.18.47", recall_block)
        self.assertIn("1.2.3.4", recall_block)

        doc_notes = filter_relevant_notes(self.notes, "帮我写一份学习笔记.md", context="")
        self.assertEqual(doc_notes, ["输出表格尽量简洁，默认用简体中文"])

    def test_04_lightweight_chat_gating(self):
        greeting_block = _build_turn_notes_block(
            self.notes,
            query="你好",
            context="",
            already_injected_static=True,
            is_lightweight_chat=True,
            action_type="casual_greeting",
        )
        self.assertEqual(greeting_block, "")

        qa_block = _build_turn_notes_block(
            self.notes,
            query="按照我的要求简要解释一下",
            context="",
            already_injected_static=True,
            is_lightweight_chat=True,
            action_type="informational_inquiry",
        )
        self.assertIn("输出表格尽量简洁，默认用简体中文", qa_block)
        self.assertNotIn("dummy_prod_pw", qa_block)

    def test_05_non_sensitive_size_cap_and_strip_headers(self):
        huge_notes = [f"常规备忘第{i}条：" + ("x" * 400) for i in range(6)]
        block = _build_turn_notes_block(
            huge_notes,
            query="继续开发",
            context="",
            already_injected_static=True,
            is_lightweight_chat=False,
        )
        self.assertLessEqual(len(block), 1600)
        raw_prompt = f"{block}用户真实的问题"
        self.assertEqual(strip_injected_prompt_headers(raw_prompt), "用户真实的问题")


if __name__ == "__main__":
    unittest.main()
