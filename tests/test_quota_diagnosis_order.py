# -*- coding: utf-8 -*-
"""
Unit tests verifying that model quota exhaustion errors are detected and formatted
before System One error diagnosis (`evaluate_tool_error`) even when partial output
such as `[TASK_PLAN]` or intermediate text was already emitted.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import executor


class _FakeTurnStream:
    def __init__(self, events):
        self._events = list(events)

    async def get_event(self, timeout=0.2):
        if self._events:
            return self._events.pop(0)
        return None

    def drain_all(self):
        rest = list(self._events)
        self._events.clear()
        return rest

    async def aclose(self):
        return None


class _FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.pid = 999999


class _FakeSession:
    def __init__(self, events, returncode=0):
        self._events = events
        self.lock = asyncio.Lock()
        self.process = _FakeProcess(returncode=returncode)
        self.stderr_tail = ""

    def is_alive(self):
        return True

    async def start(self):
        return None

    async def close(self):
        return None

    async def send_prompt_and_stream(self, prompt):
        return _FakeTurnStream(self._events)


class TestQuotaDiagnosisOrder(unittest.IsolatedAsyncioTestCase):
    async def _run_case(self, events, *, returncode=0, quota_reply=None):
        fake_sess = _FakeSession(events, returncode=returncode)
        captured_cards = []

        fake_pool = MagicMock()
        fake_pool.get_or_create.return_value = fake_sess
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.reset_session = AsyncMock()

        async def _fake_feishu_call(sync_fn, timeout=30.0, label=""):
            res = sync_fn()
            return res or "om_mock_reply"

        def _capture_card(reply_text, **kwargs):
            captured_cards.append((reply_text, kwargs))
            return {"mock_card": True, "reply_text": reply_text, **kwargs}

        eval_mock = AsyncMock()
        quota_mock = AsyncMock(
            return_value=quota_reply
            or "⚠ Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 155h41m51s."
        )

        session_data = {
            "model": "gemini-4-argon-medium",
            "project": BASE_DIR,
            "workspace_root": BASE_DIR,
            "conversation": "",
            "effort": "medium",
        }

        with patch("session_pool.session_pool", fake_pool), \
             patch("executor.allow_execution", return_value=True), \
             patch("executor.patch_interactive_card_sdk", return_value="ok"), \
             patch("executor.send_interactive_card_sdk", return_value="om_init"), \
             patch("executor.save_session_async", new=AsyncMock()), \
             patch("executor.CardBuilder.build_ai_response", side_effect=_capture_card), \
             patch("utils.quota.detect_and_format_quota_error", quota_mock), \
             patch("system_one_gate.evaluate_tool_error", eval_mock), \
             patch("executor.config.TYPESAFE_TIER", "copilot"), \
             patch("executor.config.TYPESAFE_ENABLED", True):
            is_err = await executor.execute_antigravity(
                chat_id="oc_test_quota",
                user_text="怎么样了",
                message_id="om_test_msg",
                bot_reply_msg_id="om_bot_card",
                session_data=session_data,
                is_new_conversation=True,
                system_instruction="",
                final_prompt="怎么样了",
                downloaded_file_name=None,
                download_success=False,
                running_processes={},
            )

        return is_err, captured_cards, eval_mock, quota_mock

    async def test_quota_error_after_task_plan_emitted_skips_system_one(self):
        """Model emits [TASK_PLAN] then fails with Individual quota reached -> must show quota notice, NOT System One general_error."""
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "PLANNER_RESPONSE",
                    "text": "[TASK_PLAN] 检查状态 | 总结回复 [/TASK_PLAN]",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 155h41m51s.",
                },
            },
        ]
        is_err, cards, eval_mock, quota_mock = await self._run_case(events)
        self.assertTrue(is_err)
        eval_mock.assert_not_awaited()
        quota_mock.assert_awaited_once()
        self.assertEqual(len(cards), 1)
        reply_text, kwargs = cards[0]
        self.assertTrue(kwargs.get("is_quota"))
        self.assertTrue(kwargs.get("is_error"))
        self.assertIn("Individual quota reached", reply_text)
        self.assertIn("155h41m51s", reply_text)
        self.assertNotIn("System One 故障诊断", reply_text)

    async def test_quota_error_after_partial_text_still_reports_quota(self):
        """Model emits partial normal text then fails with Individual quota reached -> must report quota exhaustion."""
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "PLANNER_RESPONSE",
                    "text": "[TASK_PLAN] 步骤1 | 步骤2 [/TASK_PLAN]\n正在查看日志...",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "Individual quota reached. Resets in 12h.",
                },
            },
        ]
        is_err, cards, eval_mock, _ = await self._run_case(
            events,
            quota_reply="⚠ Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 12h.",
        )
        self.assertTrue(is_err)
        eval_mock.assert_not_awaited()
        reply_text, kwargs = cards[0]
        self.assertTrue(kwargs.get("is_quota"))
        self.assertIn("Individual quota reached", reply_text)

    async def test_washed_empty_reply_with_generic_error_checks_quota_before_system_one(self):
        """When reply_text washes to empty and stream_error lacks keyword, quota API fallback must still precede System One."""
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "PLANNER_RESPONSE",
                    "text": "[TASK_PLAN] 步骤1 | 步骤2 [/TASK_PLAN]",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "model turn failed unexpectedly",
                },
            },
        ]
        is_err, cards, eval_mock, quota_mock = await self._run_case(events)
        self.assertTrue(is_err)
        eval_mock.assert_not_awaited()
        quota_mock.assert_awaited()
        reply_text, kwargs = cards[0]
        self.assertTrue(kwargs.get("is_quota"))
        self.assertIn("Individual quota reached", reply_text)

    async def test_normal_reply_mentioning_quota_text_does_not_trigger_quota_error(self):
        """Successful reply discussing quota error strings must NOT be misclassified as quota exhaustion."""
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "PLANNER_RESPONSE",
                    "text": "代码里 `is_quota_error()` 会匹配 `individual quota reached` 字符串。",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "代码里 `is_quota_error()` 会匹配 `individual quota reached` 字符串。",
                },
            },
        ]
        is_err, cards, eval_mock, quota_mock = await self._run_case(events)
        self.assertFalse(is_err)
        eval_mock.assert_not_awaited()
        quota_mock.assert_not_awaited()
        reply_text, kwargs = cards[0]
        self.assertFalse(kwargs.get("is_quota"))
        self.assertFalse(kwargs.get("is_error"))
        self.assertIn("is_quota_error()", reply_text)

    async def test_stale_quota_error_after_full_reply_on_healthy_model_is_ignored(self):
        """When a healthy-quota model emits a full answer but agy returns stale quota error from prior turn, preserve the answer."""
        full_answer = (
            "在 Gemini 4 Hydrogen 模型中，High 与 xHigh 对应推理深度的档位差异。"
            "日常复杂编码优先使用 High 即可，仅遇到极高难度难题时再切换至 xHigh。"
        )
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": full_answer,
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 155h41m51s.",
                },
            },
        ]
        with patch("utils.quota.is_model_quota_healthy", AsyncMock(return_value=True)):
            is_err, cards, eval_mock, quota_mock = await self._run_case(events)
        self.assertFalse(is_err)
        eval_mock.assert_not_awaited()
        quota_mock.assert_not_awaited()
        reply_text, kwargs = cards[0]
        self.assertFalse(kwargs.get("is_quota"))
        self.assertFalse(kwargs.get("is_error"))
        self.assertIn("Gemini 4 Hydrogen", reply_text)
        self.assertNotIn("Individual quota reached", reply_text)


if __name__ == "__main__":
    unittest.main()
