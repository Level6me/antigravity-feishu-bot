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

import config
import database
import utils
from agent_memory.store import classify_task_scope_and_budget
from cards import CardBuilder
from commands import get_version_string
from executor import _RE_CARD_TIMER_TICK, extract_final_chinese_response
from hooks.agy_hooks import PRE_TOOL_USE_MATCHER, ensure_agy_hooks_installed, handle_hook_payload
from math_decision_model import (
    ProcessVitalityState,
    _collect_descendant_pids_fast,
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
        self.assertEqual(config.BASE_VERSION_PREFIX, "v3.3.")
        ver = get_version_string("HEAD")
        self.assertTrue(ver.startswith("v3.3."), f"Expected v3.3.x, got {ver}")

    def test_02_scope_classifier_handles_start_doing_with_version_bump(self):
        res = classify_task_scope_and_budget("开始做吧  记得版本号提升到3.3", tier="High")
        self.assertEqual(res["scope"], "modify")
        self.assertTrue(res["allow_write"])

    def test_03_card_timer_signature_and_execution_trace_summary(self):
        # 1. Verify timer tick normalization collapses pure second ticks while distinguishing step changes
        s1 = "[{'tag': 'markdown', 'content': '🧠 已思考 4s'}]"
        s2 = "[{'tag': 'markdown', 'content': '🧠 已思考 5s'}]"
        s3 = "[{'tag': 'markdown', 'content': '⚙️ 正在读取 config.py (5s)'}]"
        self.assertEqual(_RE_CARD_TIMER_TICK.sub("<T>", s1), _RE_CARD_TIMER_TICK.sub("<T>", s2))
        self.assertNotEqual(_RE_CARD_TIMER_TICK.sub("<T>", s2), _RE_CARD_TIMER_TICK.sub("<T>", s3))

        # 2. Verify final AI response card renders compact collapsed execution trace in valid Card 1.0 schema
        card = CardBuilder.build_ai_response(
            "已完成全部性能优化。",
            current_model="gemini-3.1-pro-high",
            current_project="/home/ubuntu/antigravity-feishu-bot",
            completed_steps=[
                "正在读取文件: config.py",
                "正在读取文件: config.py",
                "正在编辑文件: executor.py",
                "正在运行命令: pytest",
            ],
            think_seconds=12,
        )
        allowed_tags = {"markdown", "hr", "action", "note"}
        for el in card["elements"]:
            self.assertIn(el["tag"], allowed_tags)
        full_md = "\n".join(el.get("content", "") for el in card["elements"] if el.get("tag") == "markdown")
        self.assertIn("执行轨迹", full_md)
        self.assertIn("12s", full_md)
        self.assertIn("共 4 步", full_md)
        self.assertIn("×2", full_md)

    def test_04_context_usage_o1_acp_and_incremental_transcript_cursor(self):
        # 1. O(1) ACP live usage telemetry (zero disk I/O)
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
        self.assertEqual(stats_acp["steps_count"], 7)

        # 2. Incremental byte-offset cursor on growing transcript.jsonl
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

                # Append 2 more steps and ensure incremental cursor advances and matches cold calculation
                line3 = json.dumps({"type": "TOOL_RESULT", "output": "Passed 136 tests in 4.2s"}, ensure_ascii=False)
                line4 = json.dumps({"type": "PLANNER_RESPONSE", "content": "全部 136 项单元测试已通过！"}, ensure_ascii=False)
                with open(tx_file, "a", encoding="utf-8") as f:
                    f.write(line3 + "\n" + line4 + "\n")

                s_inc = utils.get_context_usage_stats({"conversation": conv_id, "model": "Gemini 3.6 Flash (High)"})
                self.assertEqual(s_inc["steps_count"], 4)
                self.assertGreater(utils._TRANSCRIPT_INCREMENTAL_CACHE[tx_file]["offset"], first_offset)

                # Clear caches and verify cold full scan gives identical token breakdown
                utils._TRANSCRIPT_INCREMENTAL_CACHE.clear()
                utils._SESSION_TOKEN_CACHE.clear()
                s_cold = utils.get_context_usage_stats({"conversation": conv_id, "model": "Gemini 3.6 Flash (High)"})
                self.assertEqual(s_inc["total_tokens"], s_cold["total_tokens"])
                self.assertEqual(s_inc["steps_count"], s_cold["steps_count"])

    async def test_05_persistent_aiosqlite_connection_reuse(self):
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

                sess2 = await database.get_session_async("oc_perf_1")
                self.assertEqual(sess2["model"], "gemini-3.1-pro-high")
                await database.save_profile_async("ou_user_1", ["偏好简洁中文回复"])
                prof = await database.get_profile_async("ou_user_1")
                self.assertEqual(prof, ["偏好简洁中文回复"])

                conn_second = database._async_db_pool[pool_key][1]
                self.assertIs(conn_first, conn_second)
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
            pre_inv = cfg["hooks"]["PreInvocation"][0]
            pre_tool = cfg["hooks"]["PreToolUse"][0]
            self.assertEqual(pre_inv["matcher"], "*")
            self.assertEqual(pre_tool["matcher"], PRE_TOOL_USE_MATCHER)

            # Read-only tool must fast-return continue=True without touching turn contracts
            resp = handle_hook_payload(
                {
                    "hookEventName": "PreToolUse",
                    "toolName": "read_file",
                    "toolInput": {"Path": "/etc/hosts"},
                },
                custom_root=tmpdir,
            )
            self.assertEqual(resp, {"continue": True})

    async def test_08_streaming_tts_pipe_zero_mp3_disk_write(self):
        class _FakeCommunicate:
            def __init__(self, text, voice):
                self.text = text
                self.voice = voice

            async def stream(self):
                yield {"type": "WordBoundary", "offset": 0}
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
                # Ensure no sibling .mp3 file was created
                mp3_candidate = opus_path.replace(".opus", ".mp3")
                self.assertFalse(os.path.exists(mp3_candidate))
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
                t0 = time.time()
                summary = extract_and_upload_resources(
                    md_text,
                    message_id="om_test_concurrent",
                    api_client=MagicMock(),
                    additional_safe_dirs=[tmpdir],
                    chat_id="oc_test_concurrent",
                )
                elapsed = time.time() - t0

            self.assertEqual(len(summary["uploaded"]), 3)
            # Even though report_2 finished uploading 110ms before report_1, Feishu messages arrive in [1, 2, 3] order!
            self.assertEqual(sent_keys, ["key_report_1.pdf", "key_report_2.pdf", "key_report_3.pdf"])
            self.assertLess(elapsed, 0.30)

    async def test_10_chinese_response_fastpath_and_shared_acp_probe(self):
        dirty = (
            "[TASK_PLAN] 探查代码 | 实施优化 | 运行测试 [/TASK_PLAN]\n"
            "<thought>Internal chain of thought here</thought>\n"
            "I will respond in Simplified Chinese.\n\n"
            "已完成 v3.3 性能优化升级，所有核心指标均符合预期。"
        )
        cleaned = extract_final_chinese_response(dirty)
        self.assertEqual(cleaned, "已完成 v3.3 性能优化升级，所有核心指标均符合预期。")

        # Pure Chinese streaming benchmark: 200 calls over 4KB clean Chinese text
        clean_stream = "这是一段干净的流式中文回复内容，包含代码路径 `executor.py` 与性能指标说明。\n" * 60
        t0 = time.perf_counter()
        for _ in range(200):
            out = extract_final_chinese_response(clean_stream)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(out, clean_stream.strip())
        self.assertLess(elapsed_ms, 150.0)

        # Shared ACP capacity probe fast-path
        fake_acp_verdict = {
            "status": "healthy",
            "model": "gemini-3.1-pro-high",
            "latency_ms": 620.0,
            "error": "",
        }
        with patch(
            "session_pool.session_pool.probe_model_via_shared_acp",
            new=AsyncMock(return_value=fake_acp_verdict),
        ) as mock_acp_probe:
            res = await probe_model_capacity("gemini-3.1-pro-high", force=True)
            self.assertEqual(res["status"], "healthy")
            self.assertEqual(res["latency_ms"], 620.0)
            mock_acp_probe.assert_awaited_once()
            self.assertEqual(get_model_health("gemini-3.1-pro-high")["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
