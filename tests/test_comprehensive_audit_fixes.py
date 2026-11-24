import asyncio
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app_state
import database
import lark_client
import plugin_store
from cron_engine import CronEngine
from handlers import events as events_mod
from handlers import messages as messages_mod
from handlers import pipeline as pipeline_mod


class TestComprehensiveAuditFixes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_db_file = database.DB_FILE
        database.DB_FILE = os.path.join(self._tmp_dir.name, "test_audit.db")
        database.init_db()

    def tearDown(self):
        database.DB_FILE = self._orig_db_file
        self._tmp_dir.cleanup()

    def test_single_line_overlong_text_pagination_stays_within_limit(self):
        # Single line without newlines > 45000 chars + pagination header must never exceed 20000 per chunk
        single_line = "A" * 45000
        chunks = lark_client._build_paginated_text_chunks(single_line, max_chunk_size=20000)
        self.assertGreaterEqual(len(chunks), 3)
        for ch in chunks:
            self.assertLessEqual(len(ch), 20000)

    def test_plugin_store_path_traversal_blocked_and_atomic_json_write(self):
        for bad_id in ("../outside", "..", "a/b", "a\\b", "", ".hidden"):
            with self.assertRaises(ValueError):
                plugin_store._resolve_safe_plugin_dir(bad_id)
            ok, msg = plugin_store.uninstall_plugin(bad_id)
            self.assertFalse(ok)
            self.assertIn("非法插件标识符", msg)

        target_json = os.path.join(self._tmp_dir.name, "atomic_test.json")
        plugin_store._atomic_write_json(target_json, [{"id": "demo", "ok": True}])
        with open(target_json, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, [{"id": "demo", "ok": True}])

    async def test_session_three_way_merge_prevents_lost_update(self):
        chat_id = "oc_merge_test"
        initial = {
            "conversation": "",
            "model": "Gemini 4 Neon",
            "project": "/old/proj",
            "notes": ["note1"],
        }
        await database.save_session_async(chat_id, initial)

        # Executor starts turn with baseline snapshot
        exec_session = await database.get_session_async(chat_id)
        baseline = copy.deepcopy(exec_session)

        # While executor is running, user switches model and adds a note via slash command
        concurrent_user_edit = await database.get_session_async(chat_id)
        concurrent_user_edit["model"] = "Gemini 4 Carbon (High)"
        concurrent_user_edit["notes"] = ["note1", "note2_from_user"]
        concurrent_user_edit["project"] = "/new/proj"
        await database.save_session_async(chat_id, concurrent_user_edit)

        # Executor finishes turn and updates conversation + last_ai_summary
        exec_session["conversation"] = "conv-uuid-1234"
        exec_session["last_ai_summary"] = "Audit completed."
        await database.merge_and_save_session_async(chat_id, exec_session, baseline=baseline)

        final_saved = await database.get_session_async(chat_id)
        self.assertEqual(final_saved["conversation"], "conv-uuid-1234")
        self.assertEqual(final_saved["last_ai_summary"], "Audit completed.")
        # User's concurrent changes MUST be preserved!
        self.assertEqual(final_saved["model"], "Gemini 4 Carbon (High)")
        self.assertEqual(final_saved["notes"], ["note1", "note2_from_user"])
        self.assertEqual(final_saved["project"], "/new/proj")

    def test_auth_session_atomic_merge_and_monotonic_pending_task_ts(self):
        database.save_auth_session({"chat_id": "oc_u1", "role": "user", "scopes": ["shell", "project"]})
        # Concurrent partial display_name update must preserve role & scopes
        database.save_auth_session({"chat_id": "oc_u1", "display_name": "Alice"})
        sess = database.get_auth_session("oc_u1")
        self.assertEqual(sess["role"], "user")
        self.assertEqual(set(sess["scopes"]), {"shell", "project"})
        self.assertEqual(sess["display_name"], "Alice")

        # Rapid pending task saves in the same millisecond must never overwrite each other
        for idx in range(5):
            database.save_pending_task("oc_q1", {"message_id": f"om_{idx}", "raw_text": f"msg {idx}"})
        loaded = database.load_pending_tasks()
        self.assertEqual(len(loaded), 5)
        # Updating retry_count without changing created_at must replace in-place, not duplicate rows
        cid, first_task, first_ts = loaded[0]
        first_task["retry_count"] = 1
        database.save_pending_task(cid, first_task)
        reloaded = database.load_pending_tasks()
        self.assertEqual(len(reloaded), 5)
        self.assertEqual(reloaded[0][2], first_ts)
        self.assertEqual(reloaded[0][1]["retry_count"], 1)

    async def test_profile_merges_sender_and_fallback_chat(self):
        await database.save_profile_async("oc_group_1", ["团队偏好A"])
        await database.save_profile_async("ou_sender_1", ["个人偏好B", "团队偏好A"])
        merged = await database.get_profile_async("ou_sender_1", fallback_chat_id="oc_group_1")
        self.assertEqual(merged, ["个人偏好B", "团队偏好A"])

    async def test_dispatch_task_queue_race_never_raises_keyerror_or_stalls(self):
        chat_id = "oc_race_chat"
        processed_msgs = []
        worker_finish_gate = asyncio.Event()

        async def _fake_single_task(c_id, task):
            processed_msgs.append(task["message_id"])
            if task["message_id"] == "om_first":
                await worker_finish_gate.wait()

        def _slow_send_reply(msg_id, text):
            # Simulate the first worker finishing right while send_reply_sdk is in flight
            app_state.main_loop.call_soon_threadsafe(worker_finish_gate.set)

        app_state.main_loop = asyncio.get_running_loop()
        with patch.object(pipeline_mod, "_process_single_task", side_effect=_fake_single_task), \
             patch.object(messages_mod, "send_reply_sdk", side_effect=_slow_send_reply), \
             patch("plugin_manager.plugin_manager.dispatch_message", AsyncMock(return_value=False)):
            await messages_mod.handle_message_async("om_first", chat_id, "text", json.dumps({"text": "task 1"}))
            await asyncio.sleep(0.02)
            # Second message arrives while first worker is still active, triggering send_reply_sdk wait
            await messages_mod.handle_message_async("om_second", chat_id, "text", json.dumps({"text": "task 2"}))
            worker = app_state.chat_workers.get(chat_id)
            if worker:
                await asyncio.wait_for(worker, timeout=3.0)

        self.assertEqual(processed_msgs, ["om_first", "om_second"])

    def test_message_log_redacts_tokens(self):
        fake_tok = "ghp_" + "A1b2C3d4E5f6" * 3
        preview = messages_mod._redact_for_log(f"请保存令牌 {fake_tok}")
        self.assertNotIn(fake_tok, preview)

    async def test_cron_engine_records_failed_status_when_agent_returns_error(self):
        engine = CronEngine()
        recorded = []
        fake_task = {
            "id": "cron_err_1",
            "chat_id": "oc_cron_1",
            "name": "Nightly Audit",
            "action_type": "ai_agent",
            "task_type": "cron",
            "cron_expr": "0 9 * * *",
            "prompt": "run check",
        }
        with patch("cron_engine.send_card_to_chat_sdk", return_value=True), \
             patch("cron_engine.get_session_async", AsyncMock(return_value={"model": "Default"})), \
             patch("executor.execute_antigravity", AsyncMock(return_value=True)), \
             patch("cron_engine.record_cron_log", side_effect=lambda *args, **kw: recorded.append((args, kw))), \
             patch("cron_engine.update_cron_task_run", return_value=True):
            await engine._run_task_wrapper(fake_task)

        self.assertEqual(len(recorded), 1)
        args, _ = recorded[0]
        self.assertEqual(args[0], "cron_err_1")
        self.assertEqual(args[1], "failed")

    async def test_ws_frame_patch_ignores_malformed_headers_and_unknown_enum(self):
        import main as main_mod
        dummy_client = MagicMock()
        dummy_client._write_message = AsyncMock()
        dummy_client._fmt_log = lambda fmt, *a: fmt.format(*a)

        frame = MagicMock()
        h_type = MagicMock()
        h_type.key = main_mod.HEADER_TYPE
        h_type.value = "unknown_future_frame_type"
        h_sum = MagicMock()
        h_sum.key = main_mod.HEADER_SUM
        h_sum.value = ""
        frame.headers = [h_type, h_sum]
        frame.payload = b"{}"

        # Must return cleanly without raising ValueError/TypeError
        await main_mod._patched_handle_data_frame(dummy_client, frame)
        dummy_client._write_message.assert_not_called()

    def test_events_dedup_is_thread_safe(self):
        events_mod._SEEN_MESSAGE_IDS.clear()
        self.assertTrue(events_mod._mark_seen("om_dedup_1", "oc_1", 1700000000))
        self.assertFalse(events_mod._mark_seen("om_dedup_1", "oc_1", 1700000000))


if __name__ == "__main__":
    unittest.main()
