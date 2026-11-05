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

    def test_07_session_pool_race_and_warm_reuse_and_proc_tree(self):
        from session_pool import SessionPool, _extract_model_family
        from executor import _get_process_tree_status

        pool = SessionPool()
        # 1. Prewarm creates session with process=None; immediate get_or_create MUST reuse it (no duplicate spawn)
        s1 = pool.get_or_create("chat_race_test", "gemini-3.8-flash-medium", "/tmp", "conv-123", effort="medium")
        s2 = pool.get_or_create("chat_race_test", "gemini-3.8-flash-medium", "/tmp", "conv-123", effort="medium")
        self.assertIs(s1, s2)

        # 2. Adaptive tier shift within same model family reuses warm session
        self.assertEqual(_extract_model_family("gemini-3.8-flash-high"), "gemini-3.8-flash")
        self.assertEqual(_extract_model_family("gemini-3.8-flash-low"), "gemini-3.8-flash")
        s3 = pool.get_or_create("chat_race_test", "gemini-3.8-flash-low", "/tmp", "conv-123", effort="low")
        self.assertIs(s1, s3)

        # 3. Pure /proc process tree status check works accurately
        tree_stat = _get_process_tree_status(os.getpid())
        self.assertIn(os.getpid(), tree_stat["all_pids"])
        self.assertGreaterEqual(tree_stat["total_cputime"], 0.0)

    def test_08_system_one_l3_tool_and_output_guard(self):
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        import config
        from system_one_gate import (
            evaluate_tool_execution,
            evaluate_output_guard,
            resolve_adaptive_model,
        )

        # 0. Adaptive model resolution supports gemini-4-argon-* and claude-* families
        self.assertEqual(resolve_adaptive_model("gemini-4-argon-high", "Medium"), "gemini-4-argon-medium")
        self.assertEqual(resolve_adaptive_model("gemini-4-argon-high", "Low"), "gemini-4-argon-low")
        self.assertEqual(resolve_adaptive_model("claude-sonnet-5-5-high", "Low"), "claude-sonnet-5-5-low")
        self.assertEqual(resolve_adaptive_model("gpt-oss-120b-medium", "High"), "gpt-oss-120b-medium")

        old_tier = getattr(config, "TYPESAFE_TIER", "gateway")
        old_enabled = getattr(config, "TYPESAFE_ENABLED", True)
        try:
            config.TYPESAFE_TIER = "copilot"
            config.TYPESAFE_ENABLED = True

            # 1. Read-only tool fast-pathed locally with is_fallback=False
            res_read = asyncio.run(evaluate_tool_execution("tool:read_file"))
            self.assertTrue(res_read.is_allowed)
            self.assertEqual(res_read.decision_source, "local")
            self.assertFalse(res_read.is_fallback)

            # 1b. Benign project file edit fast-pathed locally without 220ms HTTP blocking
            res_edit = asyncio.run(evaluate_tool_execution("edit_file target=/home/ubuntu/antigravity-feishu-bot/cards/cron.py"))
            self.assertTrue(res_edit.is_allowed)
            self.assertEqual(res_edit.decision_source, "local")
            self.assertEqual(res_edit.risk_level, "medium_risk")

            # 1c. Command with risk marker ('kill') but Jev says medium_risk & crit_prob=0.07 -> allowed (no false positive session kill)
            mock_client = MagicMock()
            mock_ans_risk = MagicMock(choice="medium_risk", confidence=0.50)
            mock_ans_crit = MagicMock(noul=0.07)
            mock_resp = MagicMock(answers={"risk_assessment": mock_ans_risk, "is_critical": mock_ans_crit})
            mock_client.system_one = AsyncMock(return_value=mock_resp)
            with patch("system_one_gate.get_typesafe_client", return_value=mock_client):
                res_kill = asyncio.run(evaluate_tool_execution("timeout 10 kill -TERM 12345"))
                self.assertTrue(res_kill.is_allowed)
                self.assertEqual(res_kill.decision_source, "jev")

            # 2. Catastrophic command blocked locally immediately
            res_crit = asyncio.run(evaluate_tool_execution("timeout 10 rm -rf /"))
            self.assertFalse(res_crit.is_allowed)
            self.assertEqual(res_crit.risk_level, "critical_risk")

            # 3. Output guard blocks hard secret pattern immediately
            fake_tok = "ghp_" + "A" * 36
            out_secret = asyncio.run(evaluate_output_guard(f"Leak: {fake_tok}"))
            self.assertFalse(out_secret.is_safe)

            # 4. Normal long prose (>120 chars) without secret/danger markers fast-paths locally
            normal_prose = "这是一段完全正常的架构分析说明文本，不涉及任何私钥、密码、令牌或破坏性系统指令。" * 5
            with patch("system_one_gate.get_typesafe_client") as mock_get_client:
                out_ok = asyncio.run(evaluate_output_guard(normal_prose))
                self.assertTrue(out_ok.is_safe)
                mock_get_client.assert_not_called()
        finally:
            config.TYPESAFE_TIER = old_tier
            config.TYPESAFE_ENABLED = old_enabled


if __name__ == "__main__":
    unittest.main()
