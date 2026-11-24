"""Unit tests for plan-only/silent empty-turn self-healing and standalone TASK_PLAN directive counting."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from executor import (
    count_standalone_task_plan_directives,
    detect_plan_only_or_empty_turn,
    execute_antigravity,
)


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


class _SequentialFakeSession:
    """Simulates a warm ACP session serving multiple attempts in sequence."""

    def __init__(self, attempt_events_list, returncode=0):
        self.lock = asyncio.Lock()
        self.conversation_id = "conv-plan-only-test"
        self.process = MagicMock(pid=99997, returncode=returncode)
        self._attempt_events_list = list(attempt_events_list)
        self.closed = False
        self.sent_prompts = []

    async def start(self):
        return None

    def is_alive(self):
        return not self.closed

    async def send_prompt_and_stream(self, prompt_text):
        self.sent_prompts.append(prompt_text)
        idx = min(len(self.sent_prompts) - 1, len(self._attempt_events_list) - 1)
        return _FakeTurnStream(self._attempt_events_list[idx])

    def get_recent_stderr(self):
        return ""

    async def close(self):
        self.closed = True


class TestStandaloneTaskPlanCounter(unittest.TestCase):
    def test_ignores_prose_inline_and_fenced_code_mentions(self):
        text = (
            "[TASK_PLAN] 查日志 | 查代码 | 给结论 [/TASK_PLAN]\n"
            "正文分析：模型仅输出了 `[TASK_PLAN]` 标签，导致 `_RE_TASK_PLAN_PAIR` 剥离后正文为空。\n"
            "```python\n"
            "[TASK_PLAN] 示例步骤1 | 示例步骤2 [/TASK_PLAN]\n"
            "[TASK_PLAN] 示例步骤3 | 示例步骤4 [/TASK_PLAN]\n"
            "[TASK_PLAN] 示例步骤5 | 示例步骤6 [/TASK_PLAN]\n"
            "```\n"
            "另外行内再提一次 [TASK_PLAN] 和 [TASK_PLAN] 也不应被算作重规划。\n"
        )
        self.assertEqual(count_standalone_task_plan_directives(text), 1)

    def test_detects_real_repeated_standalone_replan_loop(self):
        text = (
            "[TASK_PLAN] 步骤1 | 步骤2 [/TASK_PLAN]\n"
            "中间输出\n"
            "  [TASK_PLAN] 步骤1 | 步骤2 | 步骤3 [/TASK_PLAN]\n"
            "再次重规划\n"
            "[TASK_PLAN] 步骤A | 步骤B [/TASK_PLAN]\n"
        )
        self.assertEqual(count_standalone_task_plan_directives(text), 3)


class TestEmptyTurnClassifier(unittest.TestCase):
    def test_classify_variants(self):
        self.assertEqual(detect_plan_only_or_empty_turn("你好", "你好"), "")
        self.assertEqual(
            detect_plan_only_or_empty_turn("[TASK_PLAN] A | B [/TASK_PLAN]", ""),
            "plan_only",
        )
        self.assertEqual(
            detect_plan_only_or_empty_turn("<thought>internal reasoning</thought>", ""),
            "chatter_only",
        )
        self.assertEqual(detect_plan_only_or_empty_turn("", ""), "silent_empty")


class TestEmptyReplySelfHealing(unittest.IsolatedAsyncioTestCase):
    async def _run_executor(self, attempt_events_list):
        fake_sess = _SequentialFakeSession(attempt_events_list, returncode=0)
        captured_cards = []
        session_data = {
            "model": "gemini-4-neon-high",
            "base_model": "gemini-4-neon-high",
            "reasoning_effort": "high",
            "project": "/tmp/luyuan-k50-passport-key",
            "conversation": "conv-plan-only-test",
            "notes": [],
        }

        def fake_build_ai_response(reply_text, **kwargs):
            captured_cards.append((reply_text, kwargs))
            return {"mock_card": reply_text}

        fake_pool = MagicMock()
        fake_pool.get_or_create.return_value = fake_sess
        fake_pool.update_conversation_id = MagicMock()
        fake_pool.sync_live_session_config = AsyncMock()

        with (
            patch("session_pool.session_pool", fake_pool),
            patch("executor.allow_execution", return_value=True),
            patch("executor.is_admin", return_value=True),
            patch("executor.save_session_async", new=AsyncMock()),
            patch("executor.send_interactive_card_sdk", return_value="om_card_1"),
            patch("executor.patch_interactive_card_sdk", return_value=True),
            patch("executor.extract_and_upload_resources", return_value={"failed": []}),
            patch("executor.CardBuilder.build_ai_response", side_effect=fake_build_ai_response),
            patch("utils.quota.detect_and_format_quota_error", new=AsyncMock(return_value=None)),
            patch("utils.quota.is_quota_error", return_value=False),
            patch("plugin_manager.plugin_manager.dispatch_after_ai", new=AsyncMock(side_effect=lambda txt, *a, **kw: txt)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            is_err = await execute_antigravity(
                chat_id="oc_test_empty_heal",
                user_text="排查一下设备反复休眠唤醒的原因",
                message_id="om_user_1",
                bot_reply_msg_id="om_card_1",
                session_data=session_data,
                is_new_conversation=False,
                system_instruction="[System] ",
                final_prompt="排查一下设备反复休眠唤醒的原因",
                downloaded_file_name=None,
                download_success=False,
                running_processes={},
            )
        return is_err, captured_cards, fake_sess

    async def test_plan_only_turn_auto_nudges_and_succeeds_on_attempt_2(self):
        attempt_1_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "[TASK_PLAN] 查看日志 | 审查固件代码 | 输出结论 [/TASK_PLAN]",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "[TASK_PLAN] 查看日志 | 审查固件代码 | 输出结论 [/TASK_PLAN]",
                },
            },
        ]
        attempt_2_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "已查明原因：固件 `refresh_eddystone_tlm_scan_response()` 未触发重启广播，导致休眠标志抖动。",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "已查明原因：固件 `refresh_eddystone_tlm_scan_response()` 未触发重启广播，导致休眠标志抖动。",
                },
            },
        ]

        is_err, cards, fake_sess = await self._run_executor([attempt_1_events, attempt_2_events])
        self.assertFalse(is_err)
        self.assertEqual(len(fake_sess.sent_prompts), 2)
        self.assertFalse(fake_sess.closed, "Warm session cache should be preserved for continuation nudge")
        self.assertIn("纯规划空回自动续推", fake_sess.sent_prompts[1])
        self.assertIn("查看日志 -> 审查固件代码 -> 输出结论", fake_sess.sent_prompts[1])
        self.assertEqual(len(cards), 1)
        reply_text, kwargs = cards[0]
        self.assertFalse(kwargs.get("is_error"))
        self.assertIn("refresh_eddystone_tlm_scan_response", reply_text)

    async def test_plan_only_both_attempts_shows_specific_diagnostic_hint(self):
        plan_only_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "[TASK_PLAN] 查日志 | 查固件 [/TASK_PLAN]",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "[TASK_PLAN] 查日志 | 查固件 [/TASK_PLAN]",
                },
            },
        ]
        is_err, cards, fake_sess = await self._run_executor([plan_only_events, plan_only_events])
        self.assertTrue(is_err)
        self.assertEqual(len(fake_sess.sent_prompts), 2)
        self.assertEqual(len(cards), 1)
        reply_text, kwargs = cards[0]
        self.assertTrue(kwargs.get("is_error"))
        self.assertIn("/compact", reply_text)
        self.assertIn("仅输出了任务规划", reply_text)

    async def test_prose_mentioning_task_plan_three_times_not_intercepted(self):
        prose_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": (
                        "[TASK_PLAN] 查日志 | 查代码 | 给结论 [/TASK_PLAN]\n"
                        "排查完成：模型输出 `[TASK_PLAN]` 后漏发工具调用，"
                        "因为清洗逻辑会删掉 `[TASK_PLAN]` 标签，"
                        "而且源码注释里还提到了 `[TASK_PLAN]`。\n"
                    ),
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": (
                        "排查完成：模型输出 `[TASK_PLAN]` 后漏发工具调用，"
                        "因为清洗逻辑会删掉 `[TASK_PLAN]` 标签，"
                        "而且源码注释里还提到了 `[TASK_PLAN]`。"
                    ),
                },
            },
        ]
        is_err, cards, fake_sess = await self._run_executor([prose_events])
        self.assertFalse(is_err)
        self.assertEqual(len(fake_sess.sent_prompts), 1)
        self.assertFalse(fake_sess.closed)
        self.assertEqual(len(cards), 1)
        self.assertIn("排查完成", cards[0][0])

    async def test_silent_empty_turn_auto_nudges_and_recovers(self):
        attempt_1_events = [
            {"event": "result", "result": {"status": "SUCCESS", "response": ""}},
        ]
        attempt_2_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "已完成排查，服务日志正常。",
                },
            },
            {
                "event": "result",
                "result": {"status": "SUCCESS", "response": "已完成排查，服务日志正常。"},
            },
        ]
        is_err, cards, fake_sess = await self._run_executor([attempt_1_events, attempt_2_events])
        self.assertFalse(is_err)
        self.assertEqual(len(fake_sess.sent_prompts), 2)
        self.assertIn("空回复自动续推", fake_sess.sent_prompts[1])
        self.assertEqual(len(cards), 1)
        self.assertIn("已完成排查", cards[0][0])


if __name__ == "__main__":
    unittest.main()
