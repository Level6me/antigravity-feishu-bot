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
    strip_task_plan_directives_from_system_prompt,
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

    async def test_session_not_found_on_attempt_1_preserves_plan_only_nudge_on_attempt_2(self):
        """Regression: infra error on attempt 1 must not consume the plan_only auto-nudge budget."""
        attempt_1_events = [
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "error": "Session not found: 734fcc37-5395-4d6e-aaa4-bc11addb415e",
                },
            },
        ]
        attempt_2_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "[TASK_PLAN] 校验容器 | 核验反代 [/TASK_PLAN]",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "[TASK_PLAN] 校验容器 | 核验反代 [/TASK_PLAN]",
                },
            },
        ]
        attempt_3_events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "text_delta": "已完成容器与反代核验，一切正常。",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "已完成容器与反代核验，一切正常。",
                },
            },
        ]
        is_err, cards, fake_sess = await self._run_executor([attempt_1_events, attempt_2_events, attempt_3_events])
        self.assertFalse(is_err)
        self.assertEqual(len(fake_sess.sent_prompts), 3)
        self.assertIn("纯规划空回自动续推", fake_sess.sent_prompts[2])
        self.assertEqual(len(cards), 1)
        self.assertFalse(cards[0][1].get("is_error"))
        self.assertIn("已完成容器与反代核验", cards[0][0])

    def test_strip_task_plan_directives_on_retry_prompts(self):
        sys_slim = (
            "[S1 scope=modify]\n"
            "[TASK_PLAN Rule: 若需调用工具，首行先输出 `[TASK_PLAN]`]\n"
            "[User Notes]\n"
        )
        cleaned_slim = strip_task_plan_directives_from_system_prompt(sys_slim)
        self.assertNotIn("[TASK_PLAN Rule:", cleaned_slim)
        self.assertIn("[S1 scope=modify]", cleaned_slim)
        self.assertIn("[User Notes]", cleaned_slim)

        sys_full = (
            "[System Rule: ...]\n\n"
            "[System Mandatory Task Planning Directive / 任务规划强制规范]\n"
            "【必须执行】：[TASK_PLAN] 步骤1 [/TASK_PLAN]\n\n"
            "[System Autonomous Execution Directive / 自主执行决策规范]\n"
            "必须执行\n"
        )
        cleaned_full = strip_task_plan_directives_from_system_prompt(sys_full)
        self.assertNotIn("System Mandatory Task Planning Directive", cleaned_full)
        self.assertIn("System Autonomous Execution Directive", cleaned_full)

    async def test_acp_is_alive_waits_for_initialize_handshake(self):
        """Regression: concurrent caller must not treat an initializing ACP subprocess as ready."""
        from acp_client import AcpPersistentSession

        sess = AcpPersistentSession(chat_id="oc_race_test", model="gemini-4-neon-high")
        handshake_gate = asyncio.Event()
        handshake_started = asyncio.Event()

        async def fake_init(cwd_dir):
            handshake_started.set()
            await handshake_gate.wait()
            sess.conversation_id = "conv-ready-1"

        fake_proc = MagicMock(pid=54321, returncode=None)
        fake_proc.stdin = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stderr = MagicMock()

        async def noop_coro():
            await asyncio.sleep(60)

        with (
            patch("acp_client.ANTIGRAVITY_BIN", "/usr/bin/agy"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)),
            patch.object(sess, "_initialize_and_open_session", side_effect=fake_init),
            patch.object(sess, "_drain_stderr", side_effect=noop_coro),
            patch.object(sess, "_read_stdout_loop", side_effect=noop_coro),
        ):
            prewarm_task = asyncio.create_task(sess.start())
            await handshake_started.wait()

            # Subprocess is spawned, but initialize/session/resume is still in flight
            self.assertTrue(sess._process_alive())
            self.assertFalse(sess.is_alive())

            # Concurrent executor call to start() must block until handshake completes
            exec_started = False

            async def executor_waiter():
                nonlocal exec_started
                if not sess.is_alive():
                    await sess.start()
                exec_started = sess.is_alive()

            waiter_task = asyncio.create_task(executor_waiter())
            await asyncio.sleep(0.02)
            self.assertFalse(waiter_task.done())

            handshake_gate.set()
            await asyncio.gather(prewarm_task, waiter_task)
            self.assertTrue(exec_started)
            self.assertTrue(sess.is_alive())
            self.assertEqual(sess.conversation_id, "conv-ready-1")
            await sess._terminate_process_only()


if __name__ == "__main__":
    unittest.main()
