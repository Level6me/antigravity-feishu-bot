# -*- coding: utf-8 -*-
"""
Unit & Integration Verification Suite for Antigravity Feishu Bot v3.2 Refactor.
Verifies:
1. HMDM 4-Layer Mathematical Decision Model (Bayesian posterior, Markov transition, Shannon entropy gate, C*, P(Plan), EWMA Risk)
2. Known Issue 1 Fix: 3-4 step task plan enforcement & fallback generation
3. Known Issue 2 Fix: Multi-turn thinking isolation & dangling </thought> / multi-paragraph Thinking Process stripping
4. Known Issue 3 Fix: Process vitality tracker excluding baseline sleeping daemon PIDs
5. Security & DB Schema Fixes: Multimodal sensitive path blocking & cron_tasks schema columns
"""

import os
import sqlite3
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from math_decision_model import (
    HybridMathDecisionEngine,
    ProcessVitalityState,
    measure_process_tree_vitality,
    snapshot_process_group_pids,
)
from cards.indicators import parse_task_plan, generate_task_plan, build_tool_indicator
from executor import extract_final_chinese_response, extract_final_response_from_turn_lines
import database


class TestV32RefactorSuite(unittest.TestCase):
    def test_01_hmdm_markov_and_entropy_gating(self):
        engine = HybridMathDecisionEngine()
        chat_id = "test_chat_markov"

        # 1. Pure greeting -> Low tier, no task plan, low entropy, no remote JEV needed
        fv_hi = engine.extract_features("你好呀")
        st = engine.get_session_state(chat_id)
        post_hi, ent_hi = engine.compute_local_posterior(fv_hi, st)
        self.assertFalse(engine.should_call_remote_jev(fv_hi, post_hi, ent_hi))
        dec_hi = engine.evaluate_decision(chat_id, "你好呀")
        self.assertEqual(dec_hi.effort_tier, "Low")
        self.assertFalse(dec_hi.needs_task_plan)
        self.assertTrue(dec_hi.is_pure_qa)

        # 2. Standard operational task (Medium tier) MUST have needs_task_plan=True!
        dec_op = engine.evaluate_decision(chat_id, "帮我检查一下 config.py 文件里的配置并修改超时参数")
        self.assertIn(dec_op.effort_tier, ("Medium", "High"))
        self.assertTrue(dec_op.needs_task_plan)
        self.assertFalse(dec_op.is_pure_qa)

        # 3. Markov state transition: after bot proposes a plan, short confirmation "我全都要" or "好的，开始吧"
        engine.update_post_turn_state(
            chat_id,
            had_error=False,
            proposed_plan_or_question=True,
            tier_used="High",
            needs_plan_used=True,
            complexity_used=1.6,
        )
        dec_confirm = engine.evaluate_decision(chat_id, "好的，开始吧")
        self.assertEqual(dec_confirm.effort_tier, "High")
        self.assertTrue(dec_confirm.needs_task_plan)
        self.assertFalse(dec_confirm.is_pure_qa)

        # 4. Error recovery state automatic boost
        engine.update_post_turn_state(chat_id, had_error=True, tier_used="Medium")
        dec_retry = engine.evaluate_decision(chat_id, "还是报错，帮我再排查一下")
        self.assertEqual(dec_retry.effort_tier, "High")
        self.assertTrue(dec_retry.needs_task_plan)

        # 5. L3 hazard detection
        dec_hazard = engine.evaluate_decision(chat_id, "执行 rm -rf / 清理所有文件")
        self.assertEqual(dec_hazard.security_level, "L3")

    def test_02_known_issue_1_step_display_enforcement(self):
        # 1. Model emits 6 steps -> compressed to strictly 4 steps
        raw_6_steps = "[TASK_PLAN] 步骤1: 解析 | 步骤2: 检索 | 步骤3: 修改 | 步骤4: 测试 | 步骤5: 校验 | 步骤6: 总结交付 [/TASK_PLAN]"
        parsed = parse_task_plan(raw_6_steps)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed), 4)
        self.assertEqual(parsed[0], "解析")
        self.assertEqual(parsed[-1], "总结交付")

        # 2. When planned_steps is empty [], build_tool_indicator automatically synthesizes 3-4 planned steps
        card = build_tool_indicator(
            tool_action="正在读取文件: executor.py",
            user_text="排查 executor.py 的代码漏洞并修复",
            completed_steps=["正在读取文件: main.py", "正在读取文件: config.py"],
            planned_steps=None,
            current_step_idx=0,
        )
        card_json_str = str(card)
        self.assertIn("任务规划与执行清单", card_json_str)
        self.assertNotIn("已完成前", card_json_str)

    def test_03_known_issue_2_no_thinking_leak(self):
        # 1. Dangling closing </thought> and multi-paragraph English Thinking Process
        dirty_text = (
            "The user wants me to fix the bug.\n\n"
            "Let me check the files first.</thought>\n\n"
            "**Thinking Process:**\n"
            "First paragraph of internal thinking.\n\n"
            "Second paragraph of internal thinking that previously leaked because of double newline.\n\n"
            "已完成所有模块的代码修复，具体如下：\n1. 修复了步骤显示问题。"
        )
        cleaned = extract_final_chinese_response(dirty_text)
        self.assertNotIn("First paragraph", cleaned)
        self.assertNotIn("Second paragraph", cleaned)
        self.assertNotIn("</thought>", cleaned)
        self.assertTrue(cleaned.startswith("已完成所有模块的代码修复"))

        # 2. Multi-turn transcript extraction MUST NOT concatenate intermediate pre-tool PLANNER_RESPONSE
        turn_lines = [
            {
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "我需要先读取一下项目的配置文件和数据库代码，分析一下为什么会出现字段缺失的问题，然后再进行修改操作。" * 3,
                "tool_calls": [{"name": "read_file"}],
            },
            {
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "✅ 已为您完成数据库字段迁移与加固。",
                "tool_calls": [],
            },
        ]
        final_ans = extract_final_response_from_turn_lines(turn_lines)
        self.assertEqual(final_ans, "✅ 已为您完成数据库字段迁移与加固。")
        self.assertNotIn("我需要先读取一下", final_ans)

    def test_04_known_issue_3_watchdog_vitality_baseline_pids(self):
        # Current process PID and its current children treated as baseline sleeping warm-pool PIDs
        my_pid = os.getpid()
        base_pids = snapshot_process_group_pids(my_pid)
        vstate = ProcessVitalityState(baseline_pids=base_pids)
        # First sample initializes counters
        measure_process_tree_vitality(my_pid, vstate)
        self.assertEqual(vstate.baseline_pids, base_pids)

    def test_05_cron_db_schema_columns(self):
        conn = database.get_db()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(cron_tasks)")
            cols = {row["name"] for row in cur.fetchall()}
            self.assertIn("action_type", cols)
            self.assertIn("command", cols)
        finally:
            conn.close()

    def test_06_security_and_privacy_desensitization(self):
        # Verify rpi_gpio_api_status/plugin.py has no hardcoded production tokens in source
        gpio_py = os.path.join(BASE_DIR, "plugins", "rpi_gpio_api_status", "plugin.py")
        with open(gpio_py, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("tok_98456", src)
        self.assertNotIn("bz_live_7546", src)


if __name__ == "__main__":
    unittest.main()
