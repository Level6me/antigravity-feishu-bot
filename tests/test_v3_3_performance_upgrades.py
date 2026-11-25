# -*- coding: utf-8 -*-
"""Unit & Regression Suite for Antigravity Feishu Bot v3.3 Performance & UX Upgrades."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app_state
import config
import cron_engine as cron_mod
import database
import utils
from acp_client import AcpPersistentSession
from agent_memory.store import (
    classify_task_scope_and_budget,
    inspect_and_backfill_transcript,
    record_episodic_turn,
    search_episodic_memory,
)
from cards import CardBuilder
from cards.common import convert_card_v1_to_v2
from commands import get_version_string
from executor import _RE_CARD_THINK_ROTATION, _RE_CARD_TIMER_TICK, extract_final_chinese_response
from handlers.messages import _handle_message_async_internal
from hooks.agy_hooks import PRE_TOOL_USE_MATCHER, ensure_agy_hooks_installed, handle_hook_payload
from lark_client import CardKitStreamController, _CARDKIT_CAPABILITY_STATE, _compact_json
from math_decision_model import (
    ProcessVitalityState,
    _collect_descendant_pids_fast,
    math_decision_engine,
    measure_process_tree_vitality,
    snapshot_process_group_pids,
)
from multimodal import extract_and_upload_resources
from utils.model_health import clear_health_cache, get_model_health, probe_model_capacity
from voice_service import text_to_opus


class TestV33PerformanceUpgrades(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_health_cache()

    def tearDown(self):
        clear_health_cache()

    def test_01_version_bumped_to_v3_3(self):
        self.assertIn(config.BASE_VERSION_PREFIX, ("v3.3.", "v3.4."))
        ver = get_version_string("HEAD")
        self.assertTrue(
            ver.startswith(("v3.3.", "v3.4.")),
            f"Expected v3.3.x or v3.4.x, got {ver}",
        )

    def test_02_scope_classifier_handles_start_doing_and_short_inquiry(self):
        res = classify_task_scope_and_budget("开始做吧  记得版本号提升到3.3", tier="High")
        self.assertEqual(res["scope"], "modify")
        self.assertTrue(res["allow_write"])

        # Short pure inquiry ("有什么改变吗") must skip 460ms remote JEV and classify as advise (not modify)
        fv = math_decision_engine.extract_features("有什么改变吗")
        st = math_decision_engine.get_session_state("oc_short_q_test")
        post, ent = math_decision_engine.compute_local_posterior(fv, st)
        self.assertFalse(math_decision_engine.should_call_remote_jev(fv, post, ent, has_context=True))
        orth = math_decision_engine.evaluate_decision("oc_short_q_test", "有什么改变吗", update_state=False)
        self.assertTrue(orth.is_pure_qa)
        self.assertFalse(orth.needs_task_plan)
        scope_q = classify_task_scope_and_budget("有什么改变吗", tier=orth.effort_tier, is_operation=False)
        self.assertEqual(scope_q["scope"], "advise")

    def test_03_card_quiet_signature_and_no_bottom_execution_trace(self):
        # 1. Verify BOTH timer ticks AND thinking rotation phrases normalize cleanly
        c1 = CardBuilder.build_typing_indicator(user_text="排查性能", think_seconds=2)
        c2 = CardBuilder.build_typing_indicator(user_text="排查性能", think_seconds=6)
        sig1 = _RE_CARD_THINK_ROTATION.sub("<R>", _RE_CARD_TIMER_TICK.sub("<T>", str(c1["elements"][:-1])))
        sig2 = _RE_CARD_THINK_ROTATION.sub("<R>", _RE_CARD_TIMER_TICK.sub("<T>", str(c2["elements"][:-1])))
        self.assertEqual(sig1, sig2)

        # 2. Verify bottom execution trace (`⚡ 执行轨迹`) is completely absent per user preference
        card = CardBuilder.build_ai_response(
            "已完成全部性能优化。",
            current_model="gemini-3.1-pro-high",
            current_project="/home/ubuntu/antigravity-feishu-bot",
            completed_steps=[
                "正在读取文件: config.py",
                "正在编辑文件: executor.py",
            ],
            think_seconds=12,
        )
        full_md = "\n".join(el.get("content", "") for el in card["elements"] if el.get("tag") == "markdown")
        self.assertNotIn("执行轨迹", full_md)

    def test_04_context_usage_o1_acp_and_incremental_transcript_cursor(self):
        stats_acp = utils.get_context_usage_stats(
            {
                "conversation": "conv-acp-1",
                "model": "Claude Sonnet 4.6",
                "acp_last_usage": {
                    "used": 40000,
                    "size": 200000,
                    "conv_id": "conv-acp-1",
                    "steps_count": 7,
                },
            }
        )
        self.assertEqual(stats_acp["total_tokens"], 40000)
        self.assertEqual(stats_acp["max_tokens"], 200000)
        self.assertEqual(stats_acp["free_tokens"], 160000)
        self.assertEqual(stats_acp["free_pct"], 80.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            conv_id = "conv-inc-test"
            tx_dir = os.path.join(tmpdir, conv_id, ".system_generated", "logs")
            os.makedirs(tx_dir, exist_ok=True)
            tx_file = os.path.join(tx_dir, "transcript.jsonl")

            line1 = json.dumps({"type": "USER_INPUT", "content": "请帮我优化飞书机器人的流式渲染性能"}, ensure_ascii=False)
            line2 = json.dumps({"type": "PLANNER_RESPONSE", "content": "好的，我将立即分析并优化卡片去重逻辑。"}, ensure_ascii=False)
            with open(tx_file, "w", encoding="utf-8") as f:
                f.write(line1 + "\n" + line2 + "\n")

            with patch("utils.get_transcript_path", return_value=tx_file):
                s_first = utils.get_context_usage_stats({"conversation": conv_id, "model": "Gemini 3.6 Flash (High)"})
                self.assertEqual(s_first["steps_count"], 2)
                first_offset = utils._TRANSCRIPT_INCREMENTAL_CACHE[tx_file]["offset"]
                self.assertGreater(first_offset, 0)

                line3 = json.dumps({"type": "TOOL_RESULT", "output": "Passed 140 tests in 4.2s"}, ensure_ascii=False)
                line4 = json.dumps({"type": "PLANNER_RESPONSE", "content": "全部单元测试已通过！"}, ensure_ascii=False)
                with open(tx_file, "a", encoding="utf-8") as f:
                    f.write(line3 + "\n" + line4 + "\n")

                s_inc = utils.get_context_usage_stats({"conversation": conv_id, "model": "Gemini 3.6 Flash (High)"})
                self.assertEqual(s_inc["steps_count"], 4)
                self.assertGreater(utils._TRANSCRIPT_INCREMENTAL_CACHE[tx_file]["offset"], first_offset)

                utils._TRANSCRIPT_INCREMENTAL_CACHE.clear()
                utils._SESSION_TOKEN_CACHE.clear()
                s_cold = utils.get_context_usage_stats({"conversation": conv_id, "model": "Gemini 3.6 Flash (High)"})
                self.assertEqual(s_inc["total_tokens"], s_cold["total_tokens"])

    async def test_05_persistent_aiosqlite_and_hot_write_through_cache(self):
        orig_db = database.DB_FILE
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = os.path.join(tmpdir, "perf_test.db")
            try:
                database.DB_FILE = tmp_db
                database.init_db()

                sess = await database.get_session_async("oc_perf_1")
                sess["model"] = "gemini-3.1-pro-high"
                await database.save_session_async("oc_perf_1", sess)

                loop = asyncio.get_running_loop()
                pool_key = (id(loop), os.path.abspath(tmp_db))
                conn_first = database._async_db_pool[pool_key][1]
                self.assertIsNotNone(conn_first)

                # Hot cache should serve subsequent reads without even hitting SQLite
                sess2 = await database.get_session_async("oc_perf_1")
                self.assertEqual(sess2["model"], "gemini-3.1-pro-high")
                await database.save_profile_async("ou_user_1", ["偏好简洁中文回复"])
                prof = await database.get_profile_async("ou_user_1")
                self.assertEqual(prof, ["偏好简洁中文回复"])

                # Sampled dedup prune works cleanly
                self.assertTrue(database.mark_message_seen("om_dedup_1", "oc_perf_1"))
                self.assertFalse(database.mark_message_seen("om_dedup_1", "oc_perf_1"))
            finally:
                database.DB_FILE = orig_db

    def test_06_fast_proc_subtree_discovery_and_shared_vitality(self):
        my_pid = os.getpid()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            time.sleep(0.08)
            fast_pids = _collect_descendant_pids_fast(my_pid)
            if fast_pids is not None:
                self.assertIn(my_pid, fast_pids)
                self.assertIn(child.pid, fast_pids)
            sub_pids = snapshot_process_group_pids(my_pid, fast_subtree=True)
            self.assertIn(child.pid, sub_pids)

            vstate = ProcessVitalityState(baseline_pids={my_pid})
            res = measure_process_tree_vitality(
                my_pid,
                vstate,
                precomputed_pids={my_pid, child.pid},
                precomputed_cpu_states={my_pid: ("S", 100), child.pid: ("R", 15)},
            )
            self.assertTrue(res["active"])
            self.assertIn(child.pid, res["new_active_pids"])
        finally:
            child.terminate()
            child.wait(timeout=3)

    def test_07_scoped_pre_tool_use_hook_matcher_and_readonly_fastpath(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = ensure_agy_hooks_installed(workspace_dir=tmpdir, global_install=False, cleanup_global=False)
            with open(hooks_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg["hooks"]["PreInvocation"][0]["matcher"], "*")
            self.assertEqual(cfg["hooks"]["PreToolUse"][0]["matcher"], PRE_TOOL_USE_MATCHER)
            resp = handle_hook_payload(
                {"hookEventName": "PreToolUse", "toolName": "read_file", "toolInput": {"Path": "/etc/hosts"}},
                custom_root=tmpdir,
            )
            self.assertEqual(resp, {"continue": True})

    async def test_08_streaming_tts_pipe_zero_mp3_disk_write(self):
        class _FakeCommunicate:
            def __init__(self, text, voice):
                self.text = text
                self.voice = voice

            async def stream(self):
                yield {"type": "audio", "data": b"FAKE_MP3_CHUNK_1" * 200}
                yield {"type": "audio", "data": b"FAKE_MP3_CHUNK_2" * 200}

        written_stdin = bytearray()

        class _FakeStdin:
            def is_closing(self):
                return False

            def write(self, b):
                written_stdin.extend(b)

            async def drain(self):
                return None

            def close(self):
                return None

            async def wait_closed(self):
                return None

        class _FakeStderr:
            async def read(self):
                return b"size=      12kB time=00:00:03.20 bitrate=  31.2kbits/s speed=12.4x\n"

        def _fake_spawn(*cmd_args, **kwargs):
            opus_target = cmd_args[-1]
            with open(opus_target, "wb") as f:
                f.write(b"OggS_FAKE_OPUS_BYTES")
            proc = MagicMock()
            proc.stdin = _FakeStdin()
            proc.stderr = _FakeStderr()
            proc.returncode = 0
            proc.wait = AsyncMock(return_value=0)
            return proc

        with patch("voice_service.edge_tts.Communicate", _FakeCommunicate), \
             patch("voice_service.asyncio.create_subprocess_exec", AsyncMock(side_effect=_fake_spawn)):
            opus_path, dur_ms = await text_to_opus("你好，这是一条流式零磁盘MP3落地的语音合成测试。")
            try:
                self.assertIsNotNone(opus_path)
                self.assertTrue(os.path.exists(opus_path))
                self.assertEqual(dur_ms, 3200)
                self.assertGreater(len(written_stdin), 4000)
                self.assertFalse(os.path.exists(opus_path.replace(".opus", ".mp3")))
            finally:
                if opus_path and os.path.exists(opus_path):
                    os.remove(opus_path)

    def test_09_concurrent_multimodal_upload_preserves_deterministic_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files = []
            for idx, delay in enumerate([0.12, 0.01, 0.05], start=1):
                fp = os.path.join(tmpdir, f"report_{idx}.pdf")
                with open(fp, "wb") as f:
                    f.write(f"PDF_CONTENT_{idx}".encode("utf-8"))
                files.append((fp, delay))

            md_text = "\n".join(f"📎 [report_{i}.pdf]({fp})" for i, (fp, _) in enumerate(files, start=1))
            delay_map = {os.path.realpath(fp): d for fp, d in files}
            sent_keys = []

            def _fake_upload(path, duration_ms=None):
                rp = os.path.realpath(path)
                time.sleep(delay_map.get(rp, 0.01))
                return f"key_{os.path.basename(rp)}"

            def _fake_reply_file(msg_id, fkey):
                sent_keys.append(fkey)
                return True

            with patch("multimodal.upload_file_sdk", side_effect=_fake_upload), \
                 patch("multimodal.reply_file_sdk", side_effect=_fake_reply_file):
                summary = extract_and_upload_resources(
                    md_text,
                    message_id="om_test_concurrent",
                    api_client=MagicMock(),
                    additional_safe_dirs=[tmpdir],
                    chat_id="oc_test_concurrent",
                )
            self.assertEqual(len(summary["uploaded"]), 3)
            self.assertEqual(sent_keys, ["key_report_1.pdf", "key_report_2.pdf", "key_report_3.pdf"])

    async def test_10_chinese_response_fastpath_and_shared_acp_probe(self):
        dirty = (
            "[TASK_PLAN] 探查代码 | 实施优化 | 运行测试 [/TASK_PLAN]\n"
            "<thought>Internal chain of thought here</thought>\n"
            "I will respond in Simplified Chinese.\n\n"
            "已完成 v3.3 性能优化升级，所有核心指标均符合预期。"
        )
        cleaned = extract_final_chinese_response(dirty)
        self.assertEqual(cleaned, "已完成 v3.3 性能优化升级，所有核心指标均符合预期。")

        clean_stream = "这是一段干净的流式中文回复内容，包含代码路径 `executor.py` 与性能指标说明。\n" * 60
        out = extract_final_chinese_response(clean_stream)
        self.assertEqual(out, clean_stream.strip())

        fake_acp_verdict = {"status": "healthy", "model": "gemini-3.1-pro-high", "latency_ms": 620.0, "error": ""}
        with patch("session_pool.session_pool.probe_model_via_shared_acp", new=AsyncMock(return_value=fake_acp_verdict)) as m_p:
            res = await probe_model_capacity("gemini-3.1-pro-high", force=True)
            self.assertEqual(res["status"], "healthy")
            m_p.assert_awaited_once()
            self.assertEqual(get_model_health("gemini-3.1-pro-high")["status"], "healthy")

    async def test_11_acp_set_model_singleflight_deduplication(self):
        sess = AcpPersistentSession(chat_id="oc_dedup_rpc", model="gemini-4-carbon-low", effort="low")
        sess.conversation_id = "conv-dedup-1"
        rpc_calls = []

        async def _fake_rpc(method, params, timeout=30.0):
            rpc_calls.append((method, params))
            await asyncio.sleep(0.05)
            return {}

        sess.is_alive = lambda: True
        sess._rpc_call = _fake_rpc

        # Two concurrent set_model calls for the exact same target model/effort must only fire ONE RPC!
        r1, r2 = await asyncio.gather(
            sess.set_model("gemini-4-carbon-medium", effort="medium"),
            sess.set_model("gemini-4-carbon-medium", effort="medium"),
        )
        self.assertTrue(r1 and r2)
        self.assertEqual(len(rpc_calls), 1)

    def test_12_episodic_memory_dedup_across_realtime_and_backfill_plus_utc_fix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_root = os.path.join(tmpdir, "mem")
            proj_dir = "/tmp/demo_proj_v33"
            # 1. Simulate executor.py post-turn write (step_index=0, local time)
            ok1 = record_episodic_turn(
                project_dir=proj_dir,
                user_request="你好，测试双写去重",
                reply_text="你好！这是测试回复内容。",
                conv_id="conv-dup-check",
                step_index=0,
                custom_root=mem_root,
            )
            self.assertTrue(ok1)

            # 2. Simulate next-turn transcript.jsonl backfill (step_index=31, ISO UTC 'Z' timestamp)
            tx_path = os.path.join(tmpdir, "conv-dup-check", "transcript.jsonl")
            os.makedirs(os.path.dirname(tx_path), exist_ok=True)
            with open(tx_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "step_index": 31,
                    "type": "USER_INPUT",
                    "content": "你好，测试双写去重",
                    "created_at": "2026-11-25T00:56:16Z",
                }, ensure_ascii=False) + "\n")
                f.write(json.dumps({
                    "step_index": 32,
                    "type": "PLANNER_RESPONSE",
                    "content": "你好！这是测试回复内容。",
                    "created_at": "2026-11-25T00:56:20Z",
                }, ensure_ascii=False) + "\n")

            bf_res = inspect_and_backfill_transcript(
                tx_path, project_dir=proj_dir, conv_id="conv-dup-check", custom_root=mem_root
            )
            # Must NOT insert a second duplicate row!
            self.assertEqual(bf_res["backfilled_turns"], 0)
            hits = search_episodic_memory(proj_dir, "测试双写去重", limit=5, custom_root=mem_root)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["step_index"], 31)

    async def test_13_event_driven_cron_wakeup_and_voice_zero_debounce(self):
        # 1. Event-driven CronEngine wakes up in <50ms instead of sleeping 3600s
        eng = cron_mod.CronEngine()
        eng._running = True
        eng._loop_ref = asyncio.get_running_loop()
        eng._wakeup_event = asyncio.Event()
        eng.notify_schedule_changed()
        await asyncio.sleep(0.01)
        self.assertTrue(eng._wakeup_event.is_set())

        # 2. Audio voice message bypasses chat_media_batches debounce immediately
        app_state.chat_media_batches.pop("oc_voice_fast", None)
        dispatched = []

        async def _fake_proc_queue(cid):
            q = app_state.chat_queues.get(cid)
            if q and not q.empty():
                dispatched.append(await q.get())

        with patch("handlers.messages.get_session_async", AsyncMock(return_value={"model": "Default"})), \
             patch("handlers.messages.save_pending_task_async", AsyncMock()), \
             patch("handlers.messages.process_chat_queue", side_effect=_fake_proc_queue), \
             patch("plugin_manager.plugin_manager.dispatch_message", AsyncMock(return_value=False)):
            await _handle_message_async_internal(
                "om_voice_1",
                "oc_voice_fast",
                "audio",
                json.dumps({"file_key": "fk_voice_123"}),
            )
            await asyncio.sleep(0.02)

        self.assertNotIn("oc_voice_fast", app_state.chat_media_batches)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["message_type"], "audio")

    async def test_14_compact_utf8_json_and_cardkit_v2_converter_with_permission_fallback(self):
        # 1. Compact UTF-8 JSON saves ~45% bytes over default ascii-escaped json.dumps
        card_v1 = CardBuilder.build_ai_response(
            "这是一段中文分析结论，验证紧凑 JSON 与 Card 2.0 协议转换。" * 10,
            choice_card_data={"question": "请选择下一步：", "options": ["A: 立即部署", "B: 稍后再说"]},
        )
        compact_str = _compact_json(card_v1)
        default_str = json.dumps(card_v1)
        self.assertLess(len(compact_str.encode("utf-8")), int(len(default_str.encode("utf-8")) * 0.75))

        # 2. Card 1.0 -> Card 2.0 converter eliminates forbidden `note` and `action` tags
        v2 = convert_card_v1_to_v2(card_v1, stream_element_id="stream_md", streaming_mode=True)
        self.assertEqual(v2["schema"], "2.0")
        self.assertTrue(v2["config"]["streaming_mode"])
        v2_tags = [el["tag"] for el in v2["body"]["elements"]]
        self.assertNotIn("note", v2_tags)
        self.assertNotIn("action", v2_tags)
        self.assertIn("column_set", v2_tags)
        self.assertEqual(v2["body"]["elements"][0]["element_id"], "stream_md")

        # 3. CardKitStreamController gracefully caches supported=False on 99991672 permission error
        _CARDKIT_CAPABILITY_STATE["supported"] = None
        fake_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"code": 99991672, "msg": "Lack of necessary permissions: cardkit:card:write"}
        fake_client.post = AsyncMock(return_value=fake_resp)

        with patch("lark_client._get_tenant_access_token_async", AsyncMock(return_value="t-fake")), \
             patch("lark_client._get_async_http_client", return_value=fake_client), \
             patch.object(config, "ENABLE_CARDKIT_STREAMING", True), \
             patch.object(config, "APP_ID", "cli_test"), \
             patch.object(config, "APP_SECRET", "sec_test"):
            ctrl = CardKitStreamController("om_msg_1", "oc_chat_1")
            mid = await ctrl.create_and_send(card_v1)
            self.assertEqual(mid, "")
            self.assertIs(_CARDKIT_CAPABILITY_STATE["supported"], False)
        _CARDKIT_CAPABILITY_STATE["supported"] = None


if __name__ == "__main__":
    unittest.main()
