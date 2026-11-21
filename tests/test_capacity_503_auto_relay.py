"""Unit tests for HTTP 503 model-capacity detection and seamless model-switch auto-relay."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from agent_memory.store import (
    check_and_prepare_session_relay,
    extract_recent_conversation_turns,
    inspect_conversation_capacity_health,
    relay_conversation_for_model_switch,
)
from cards.models import build_model_panel, build_model_switch_result_card
from executor import (
    detect_and_format_agent_system_error,
    execute_antigravity,
    extract_capacity_error_model,
    is_model_capacity_error,
)
from utils.model_health import (
    clear_health_cache,
    get_all_health_statuses,
    get_model_health,
    is_model_circuit_open,
    mark_model_healthy,
    mark_model_unhealthy,
    pick_healthy_fallback_model,
)


def _write_transcript(path: str, entries: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for obj in entries:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class _FakeTurnStream:
    def __init__(self, events):
        self._events = list(events)
        self.is_done = False

    async def get_event(self, timeout=None):
        if self._events:
            ev = self._events.pop(0)
            if ev.get("event") in ("result", "process_exit", "error"):
                self.is_done = True
            return ev
        self.is_done = True
        return None

    def drain_all(self):
        return []

    async def aclose(self):
        self.is_done = True


class _FakeSession:
    def __init__(self, events, returncode=3):
        self.lock = asyncio.Lock()
        self.conversation_id = "conv-503-test"
        self.process = MagicMock(pid=99998, returncode=returncode)
        self._events = events
        self.closed = False
        self.send_count = 0

    async def start(self):
        return None

    def is_alive(self):
        return not self.closed

    async def send_prompt_and_stream(self, prompt_text):
        self.send_count += 1
        return _FakeTurnStream(self._events)

    def get_recent_stderr(self):
        return ""

    async def close(self):
        self.closed = True


class TestCapacityHealthAndRelay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem_root = os.path.join(self.tmp.name, "mem")
        self.tx_path = os.path.join(self.tmp.name, "conv-1", "transcript.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_healthy_transcript_not_tainted(self):
        _write_transcript(
            self.tx_path,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>\n帮我查一下 nginx 状态\n</USER_REQUEST>"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "nginx 运行正常，当前监听 80 和 443 端口。"},
            ],
        )
        health = inspect_conversation_capacity_health(self.tx_path)
        self.assertTrue(health["exists"])
        self.assertFalse(health["is_capacity_tainted"])
        self.assertEqual(health["trailing_capacity_errors"], 0)
        self.assertTrue(health["has_any_planner_reply"])

        session_data = {"conversation": "conv-1", "model": "gemini-4-neon-high", "project": "/tmp/demo"}
        res = relay_conversation_for_model_switch(
            chat_id="oc_test",
            session_data=session_data,
            new_model="gemini-4-neon-high",
            old_model="gemini-4-hydrogen-high",
            project_dir="/tmp/demo",
            transcript_path_override=self.tx_path,
            custom_root=self.mem_root,
        )
        self.assertFalse(res["relayed"])
        self.assertEqual(session_data["conversation"], "conv-1")

    def test_02_recovered_transient_503_not_tainted(self):
        _write_transcript(
            self.tx_path,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "测试"},
                {
                    "step_index": 1,
                    "type": "ERROR_MESSAGE",
                    "error": "API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                },
                {"step_index": 2, "type": "PLANNER_RESPONSE", "content": "恢复成功，这里是回复。"},
            ],
        )
        health = inspect_conversation_capacity_health(self.tx_path)
        self.assertFalse(health["is_capacity_tainted"])
        self.assertEqual(health["trailing_capacity_errors"], 0)
        self.assertEqual(health["total_capacity_errors"], 1)

    def test_03_model_switch_auto_relays_tainted_conv_and_preserves_recent_turns(self):
        _write_transcript(
            self.tx_path,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>\n我们正在排查树莓派风扇脚本问题\n</USER_REQUEST>"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "[TASK_PLAN] 检查脚本 | 定位错误 [/TASK_PLAN]\n已定位问题：fan.py 第 42 行 GPIO 编号写错了。"},
                {"step_index": 2, "type": "USER_INPUT", "content": "<USER_REQUEST>\n帮我把第 42 行改成 GPIO18\n</USER_REQUEST>"},
                {
                    "step_index": 3,
                    "type": "ERROR_MESSAGE",
                    "error": "API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                },
                {
                    "step_index": 4,
                    "type": "ERROR_MESSAGE",
                    "error": "API error (attempt 2): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                },
            ],
        )
        health = inspect_conversation_capacity_health(self.tx_path)
        self.assertTrue(health["is_capacity_tainted"])
        self.assertEqual(health["trailing_capacity_errors"], 2)
        self.assertEqual(health["failed_models"], ["gemini-4-hydrogen-high"])

        recent_md = extract_recent_conversation_turns(self.tx_path)
        self.assertIn("树莓派风扇脚本", recent_md)
        self.assertIn("GPIO 编号写错了", recent_md)
        self.assertNotIn("[TASK_PLAN]", recent_md)

        session_data = {
            "conversation": "conv-1",
            "model": "gemini-4-neon-high",
            "project": "/tmp/demo",
            "_sys_prompt_injected_conv": "conv-1",
            "_capacity_tainted_conv": "conv-1",
            "_capacity_error_model": "gemini-4-hydrogen-high",
        }
        res = relay_conversation_for_model_switch(
            chat_id="oc_test",
            session_data=session_data,
            new_model="gemini-4-neon-high",
            old_model="gemini-4-hydrogen-high",
            project_dir="/tmp/demo",
            transcript_path_override=self.tx_path,
            custom_root=self.mem_root,
        )
        self.assertTrue(res["relayed"])
        self.assertEqual(res["preserved_turns"], 1)
        self.assertEqual(session_data["conversation"], "")
        self.assertNotIn("_capacity_tainted_conv", session_data)
        self.assertNotIn("_sys_prompt_injected_conv", session_data)
        self.assertIn("GPIO 编号写错了", session_data["_relay_handoff_context"])
        self.assertEqual(len(session_data["conversation_lineage"]), 1)

    def test_04_pipeline_safety_net_relays_when_model_changed_or_heavily_tainted(self):
        _write_transcript(
            self.tx_path,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "问题1"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "回答1：关键上下文ABC"},
                {"step_index": 2, "type": "USER_INPUT", "content": "问题2"},
                {
                    "step_index": 3,
                    "type": "ERROR_MESSAGE",
                    "error": "API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                },
            ],
        )
        session_data = {"conversation": "conv-1", "model": "gemini-4-neon-high", "project": "/tmp/demo"}
        res = check_and_prepare_session_relay(
            chat_id="oc_test",
            session_data=session_data,
            project_dir="/tmp/demo",
            transcript_path_override=self.tx_path,
            custom_root=self.mem_root,
            current_model="gemini-4-neon-high",
        )
        self.assertTrue(res["relayed"])
        self.assertTrue(res["capacity_relayed"])
        self.assertEqual(session_data["conversation"], "")
        self.assertIn("关键上下文ABC", session_data["_relay_handoff_context"])

    def test_05_live_poll_min_byte_offset(self):
        _write_transcript(
            self.tx_path,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "old"},
                {
                    "step_index": 1,
                    "type": "ERROR_MESSAGE",
                    "error": "API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                },
            ],
        )
        offset = os.path.getsize(self.tx_path)
        self.assertEqual(inspect_conversation_capacity_health(self.tx_path, min_byte_offset=offset)["trailing_capacity_errors"], 0)
        with open(self.tx_path, "a", encoding="utf-8") as f:
            for idx in range(3):
                f.write(
                    json.dumps(
                        {
                            "step_index": 2 + idx,
                            "type": "ERROR_MESSAGE",
                            "error": f"API error (attempt {idx + 1}): UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server",
                        }
                    )
                    + "\n"
                )
        live = inspect_conversation_capacity_health(self.tx_path, min_byte_offset=offset)
        self.assertEqual(live["trailing_capacity_errors"], 3)

    def test_06_model_switch_card_shows_auto_relay_note(self):
        card_normal = build_model_switch_result_card("gemini-4-neon-high", "gemini-4-hydrogen-high", auto_relayed=False)
        card_relayed = build_model_switch_result_card("gemini-4-neon-high", "gemini-4-hydrogen-high", auto_relayed=True)
        self.assertNotIn("无需手动 `/clear`", card_normal["elements"][0]["content"])
        self.assertIn("无需手动 `/clear`", card_relayed["elements"][0]["content"])


class TestExecutorCapacityErrorHandling(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_health_cache()

    def tearDown(self):
        clear_health_cache()

    async def test_503_without_fallback_skips_attempt2_skips_system_one_and_taints_session(self):
        err_msg = (
            "Our servers are experiencing high traffic right now, please try again in a minute. "
            "(UNAVAILABLE (code 503): No capacity available for model gemini-4-hydrogen-high on the server)"
        )
        self.assertTrue(is_model_capacity_error(err_msg))
        self.assertEqual(extract_capacity_error_model(err_msg), "gemini-4-hydrogen-high")
        formatted = detect_and_format_agent_system_error(err_msg, model_name="gemini-4-hydrogen-high")
        self.assertIn("HTTP 503 No Capacity", formatted)
        self.assertIn("- /model", formatted)

        events = [
            {"event": "init", "conversation_id": "conv-503-test"},
            {"event": "result", "result": {"status": "ERROR", "error": err_msg, "response": ""}},
        ]
        fake_sess = _FakeSession(events, returncode=3)
        fake_pool = MagicMock()
        fake_pool.get_or_create.return_value = fake_sess
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.reset_session = AsyncMock()

        captured_cards = []

        def fake_build_capacity_choice_card(*args, **kwargs):
            captured_cards.append((args, kwargs))
            return {"mock_capacity_card": True}

        session_data = {
            "conversation": "conv-503-test",
            "model": "gemini-4-hydrogen-high",
            "project": "默认",
            "reasoning_effort": "high",
        }
        eval_mock = AsyncMock(return_value=MagicMock(error_category="general_error", confidence=0.79, recovery_suggestion="x"))
        quota_mock = AsyncMock(return_value=None)

        with patch("config.AUTO_FALLBACK_ON_CAPACITY_ERROR", False), \
             patch("session_pool.session_pool", fake_pool), \
             patch("executor.save_session_async", AsyncMock()), \
             patch("executor.send_interactive_card_sdk", return_value="om_init"), \
             patch("executor.patch_interactive_card_sdk", return_value=True), \
             patch("executor.CardBuilder.build_capacity_choice_card", side_effect=fake_build_capacity_choice_card), \
             patch("system_one_gate.evaluate_tool_error", eval_mock), \
             patch("utils.quota.detect_and_format_quota_error", quota_mock), \
             patch("plugin_manager.plugin_manager.dispatch_after_ai", AsyncMock(side_effect=lambda txt, *a, **k: txt)):
            is_err = await execute_antigravity(
                chat_id="oc_test_503",
                message_id="om_user_503",
                user_text="帮我查树莓派日志",
                final_prompt="帮我查树莓派日志",
                system_instruction="",
                session_data=session_data,
                running_processes={},
                bot_reply_msg_id=None,
                is_new_conversation=False,
                downloaded_file_name=None,
                download_success=False,
            )

        self.assertFalse(is_err)
        self.assertTrue(session_data.get("last_execution_paused"))
        self.assertIsNotNone(session_data.get("paused_capacity_state"))
        # Must NOT retry Attempt 2 on the same 503 model
        self.assertEqual(fake_sess.send_count, 1)
        self.assertTrue(fake_sess.closed)
        # Circuit breaker must now be OPEN for gemini-4-hydrogen-high
        self.assertTrue(is_model_circuit_open("gemini-4-hydrogen-high"))
        # Must NOT fall through to System One general_error
        eval_mock.assert_not_awaited()
        # Must NOT invoke remote quota API for a clear 503 capacity error
        quota_mock.assert_not_awaited()
        # Session must be marked tainted for seamless model-switch relay
        self.assertEqual(session_data.get("_capacity_tainted_conv"), "conv-503-test")
        self.assertEqual(session_data.get("_capacity_error_model"), "gemini-4-hydrogen-high")
        # Final Feishu card must be the interactive capacity choice card
        self.assertTrue(captured_cards)
        args, kwargs = captured_cards[-1]
        self.assertEqual(kwargs.get("failed_model"), "gemini-4-hydrogen-high")
        self.assertEqual(kwargs.get("user_text"), "帮我查树莓派日志")

    async def test_503_mid_turn_auto_fallback_switches_model_and_succeeds(self):
        err_msg = "UNAVAILABLE (code 503): No capacity available for model gemini-4-argon-high on the server"
        sess1 = _FakeSession(
            [
                {"event": "init", "conversation_id": "conv-argon-old"},
                {"event": "result", "result": {"status": "ERROR", "error": err_msg, "response": ""}},
            ],
            returncode=3,
        )
        sess2 = _FakeSession(
            [
                {"event": "init", "conversation_id": "conv-neon-new"},
                {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "备用模型已成功完成分析。"}},
                {"event": "result", "result": {"status": "SUCCESS", "response": "备用模型已成功完成分析。"}},
            ],
            returncode=0,
        )
        sess_calls = []

        def fake_get_or_create(chat_id, model, *args, **kwargs):
            sess_calls.append(model)
            return sess1 if len(sess_calls) == 1 else sess2

        fake_pool = MagicMock()
        fake_pool.get_or_create.side_effect = fake_get_or_create
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.reset_session = AsyncMock()

        captured_cards = []

        def fake_build_ai_response(*args, **kwargs):
            captured_cards.append((args, kwargs))
            return {"mock_card": True}

        session_data = {
            "conversation": "conv-argon-old",
            "model": "gemini-4-argon-high",
            "base_model": "gemini-4-argon-high",
            "project": "默认",
            "reasoning_effort": "high",
        }
        with patch("config.AUTO_FALLBACK_ON_CAPACITY_ERROR", True), \
             patch("utils.model_health.pick_healthy_fallback_model", return_value="gemini-4-neon-high"), \
             patch("session_pool.session_pool", fake_pool), \
             patch("executor.save_session_async", AsyncMock()), \
             patch("executor.send_interactive_card_sdk", return_value="om_init"), \
             patch("executor.patch_interactive_card_sdk", return_value=True), \
             patch("executor.CardBuilder.build_ai_response", side_effect=fake_build_ai_response), \
             patch("plugin_manager.plugin_manager.dispatch_after_ai", AsyncMock(side_effect=lambda txt, *a, **k: txt)):
            is_err = await execute_antigravity(
                chat_id="oc_test_fb",
                message_id="om_user_fb",
                user_text="帮我分析架构",
                final_prompt="帮我分析架构",
                system_instruction="",
                session_data=session_data,
                running_processes={},
                bot_reply_msg_id=None,
                is_new_conversation=False,
                downloaded_file_name=None,
                download_success=False,
            )

        self.assertFalse(is_err)
        self.assertEqual(sess_calls, ["gemini-4-argon-high", "gemini-4-neon-high"])
        self.assertTrue(is_model_circuit_open("gemini-4-argon-high"))
        self.assertEqual(get_model_health("gemini-4-neon-high")["status"], "healthy")
        self.assertEqual(session_data["model"], "gemini-4-neon-high")
        self.assertEqual(session_data["conversation"], "conv-neon-new")
        args, kwargs = captured_cards[-1]
        reply_text = args[0] if args else kwargs.get("reply_text", "")
        self.assertIn("已自动无感接力上下文并切换至", reply_text)
        self.assertIn("gemini-4-neon-high", reply_text)
        self.assertIn("备用模型已成功完成分析", reply_text)

    async def test_sidecar_probe_fast_fails_silent_hang_and_auto_fallbacks(self):
        """Scheme B: main task hangs silently -> sidecar probe hits 503 -> fast-fail + auto-fallback."""

        class _HangingStream:
            def __init__(self):
                self.is_done = False

            async def get_event(self, timeout=None):
                await asyncio.sleep(0.05)
                return None

            def drain_all(self):
                return []

            async def aclose(self):
                self.is_done = True

        class _HangingSession(_FakeSession):
            async def send_prompt_and_stream(self, prompt_text):
                self.send_count += 1
                return _HangingStream()

        sess1 = _HangingSession([], returncode=None)
        sess2 = _FakeSession(
            [
                {"event": "init", "conversation_id": "conv-neon-fb"},
                {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "探针 fast-fail 后自动切 Neon 成功！"}},
                {"event": "result", "result": {"status": "SUCCESS", "response": "探针 fast-fail 后自动切 Neon 成功！"}},
            ],
            returncode=0,
        )
        sess_calls = []

        def fake_get_or_create(chat_id, model, *args, **kwargs):
            sess_calls.append(model)
            return sess1 if len(sess_calls) == 1 else sess2

        fake_pool = MagicMock()
        fake_pool.get_or_create.side_effect = fake_get_or_create
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.reset_session = AsyncMock()

        async def fake_probe(model_id, timeout_seconds=18.0, force=False):
            return {
                "status": "unhealthy",
                "model": model_id,
                "latency_ms": 2100.0,
                "error": f"UNAVAILABLE (code 503): No capacity available for model {model_id} on the server",
            }

        session_data = {
            "conversation": "conv-hang-86k",
            "model": "gemini-4-argon-high",
            "base_model": "gemini-4-argon-high",
            "project": "默认",
            "reasoning_effort": "high",
        }
        with patch("config.AUTO_FALLBACK_ON_CAPACITY_ERROR", True), \
             patch("config.CAPACITY_PROBE_DELAY_SECONDS", 0.05), \
             patch("utils.model_health.probe_model_capacity", side_effect=fake_probe), \
             patch("utils.model_health.pick_healthy_fallback_model", return_value="gemini-4-neon-high"), \
             patch("session_pool.session_pool", fake_pool), \
             patch("executor.save_session_async", AsyncMock()), \
             patch("executor.send_interactive_card_sdk", return_value="om_init"), \
             patch("executor.patch_interactive_card_sdk", return_value=True), \
             patch("executor.CardBuilder.build_ai_response", return_value={"mock_card": True}), \
             patch("plugin_manager.plugin_manager.dispatch_after_ai", AsyncMock(side_effect=lambda txt, *a, **k: txt)):
            is_err = await execute_antigravity(
                chat_id="oc_test_sidecar",
                message_id="om_user_sidecar",
                user_text="深度分析",
                final_prompt="深度分析",
                system_instruction="",
                session_data=session_data,
                running_processes={},
                bot_reply_msg_id=None,
                is_new_conversation=False,
                downloaded_file_name=None,
                download_success=False,
            )

        self.assertFalse(is_err)
        self.assertTrue(sess1.closed)
        self.assertEqual(sess_calls, ["gemini-4-argon-high", "gemini-4-neon-high"])
        self.assertEqual(session_data["model"], "gemini-4-neon-high")

    async def test_sidecar_probe_healthy_does_not_interrupt_deep_thinking(self):
        """Scheme B: main task thinks silently, sidecar probe returns healthy -> must NOT kill main task."""

        class _SlowThinkingStream:
            def __init__(self):
                self.is_done = False
                self._ticks = 0

            async def get_event(self, timeout=None):
                self._ticks += 1
                if self._ticks <= 4:
                    await asyncio.sleep(0.32)
                    return None
                if self._ticks == 5:
                    return {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "深度思考完成。"}}
                self.is_done = True
                return {"event": "result", "result": {"status": "SUCCESS", "response": "深度思考完成。"}}

            def drain_all(self):
                return []

            async def aclose(self):
                self.is_done = True

        class _SlowSession(_FakeSession):
            async def send_prompt_and_stream(self, prompt_text):
                self.send_count += 1
                return _SlowThinkingStream()

        sess = _SlowSession([], returncode=0)
        fake_pool = MagicMock()
        fake_pool.get_or_create.return_value = sess
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.reset_session = AsyncMock()

        probe_mock = AsyncMock(return_value={"status": "healthy", "model": "gemini-4-neon-high", "latency_ms": 3200.0, "error": ""})
        session_data = {
            "conversation": "conv-think",
            "model": "gemini-4-neon-high",
            "base_model": "gemini-4-neon-high",
            "project": "默认",
            "reasoning_effort": "high",
        }
        with patch("config.CAPACITY_PROBE_DELAY_SECONDS", 0.02), \
             patch("utils.model_health.probe_model_capacity", probe_mock), \
             patch("session_pool.session_pool", fake_pool), \
             patch("executor.save_session_async", AsyncMock()), \
             patch("executor.send_interactive_card_sdk", return_value="om_init"), \
             patch("executor.patch_interactive_card_sdk", return_value=True), \
             patch("executor.CardBuilder.build_ai_response", return_value={"mock_card": True}), \
             patch("plugin_manager.plugin_manager.dispatch_after_ai", AsyncMock(side_effect=lambda txt, *a, **k: txt)):
            is_err = await execute_antigravity(
                chat_id="oc_test_deep",
                message_id="om_user_deep",
                user_text="复杂推导",
                final_prompt="复杂推导",
                system_instruction="",
                session_data=session_data,
                running_processes={},
                bot_reply_msg_id=None,
                is_new_conversation=False,
                downloaded_file_name=None,
                download_success=False,
            )

        self.assertFalse(is_err)
        self.assertFalse(sess.closed)
        self.assertEqual(sess.send_count, 1)
        probe_mock.assert_awaited_once()


class TestModelHealthCircuitBreakerAndCards(unittest.TestCase):
    def setUp(self):
        clear_health_cache()

    def tearDown(self):
        clear_health_cache()

    def test_circuit_breaker_and_cross_family_fallback_selection(self):
        models = [
            "gemini-4-argon-high\tGemini 4 Argon (High)",
            "gemini-4-argon-medium\tGemini 4 Argon (Medium)",
            "gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)",
            "gemini-4-neon-high\tGemini 4 Neon (High)",
            "gemini-4-neon-low\tGemini 4 Neon (Low)",
            "gemini-pro-orchestrator-neon\tOrchestrator (Neon)",
            "claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)",
        ]
        mark_model_unhealthy("gemini-4-argon-high", "503 No capacity", ttl_seconds=120)
        mark_model_unhealthy("gemini-4-hydrogen-high", "503 No capacity", ttl_seconds=120)
        self.assertTrue(is_model_circuit_open("gemini-4-argon-high"))
        self.assertFalse(is_model_circuit_open("gemini-4-neon-high"))

        # Must pick cross-family flagship gemini-4-neon-high matching tier 'high'
        self.assertEqual(
            pick_healthy_fallback_model("gemini-4-argon-high", available_models=models, preferred_tier="high"),
            "gemini-4-neon-high",
        )
        # If claude-sonnet-5-5-high is explicitly verified healthy while neon is unknown, prefer verified healthy
        mark_model_healthy("claude-sonnet-5-5-high", latency_ms=2500.0)
        self.assertEqual(
            pick_healthy_fallback_model("gemini-4-argon-high", available_models=models, preferred_tier="high"),
            "claude-sonnet-5-5-high",
        )
        # Model panel card must badge congested models with 🔴 and healthy with 🟢
        panel = build_model_panel(models, "gemini-4-argon-high", health_statuses=get_all_health_statuses())
        panel_str = json.dumps(panel, ensure_ascii=False)
        self.assertIn("🔴 Gemini 4 Argon (High) (503拥堵)", panel_str)
        self.assertIn("🟢 Claude Sonnet 5.5 (High)", panel_str)

    def test_switch_result_card_probe_states(self):
        c_unhealthy = build_model_switch_result_card(
            "gemini-4-argon-high",
            "gemini-4-neon-high",
            probe_status="unhealthy",
            suggested_fallback="gemini-4-neon-high",
        )
        self.assertEqual(c_unhealthy["header"]["template"], "red")
        c_str = json.dumps(c_unhealthy, ensure_ascii=False)
        self.assertIn("HTTP 503 无容量", c_str)
        self.assertIn("一键改切至 gemini-4-neon-high", c_str)

        c_healthy = build_model_switch_result_card(
            "gemini-4-neon-high",
            "gemini-4-argon-high",
            probe_status="healthy",
            probe_latency_ms=4200.0,
        )
        self.assertIn("算力探活正常", json.dumps(c_healthy, ensure_ascii=False))
        self.assertIn("4.2s", json.dumps(c_healthy, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
