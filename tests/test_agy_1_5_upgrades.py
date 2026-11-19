"""Comprehensive unit & integration tests for agy 1.5.x P0 / P1 / P2 upgrades."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class TestP0Upgrades(unittest.TestCase):
    def test_default_model_upgraded_from_3_7(self):
        import config
        self.assertNotIn("gemini-3.7", config.Settings().default_model)
        self.assertEqual(config.Settings().default_model, "gemini-3.8-flash-low")

    def test_new_tool_status_mappings(self):
        from executor import format_tool_action
        self.assertIn("蜂群", format_tool_action("swarm", {"action": "add"}))
        self.assertIn("工作流", format_tool_action("run_workflow", {"Description": "并行扫描"}))
        self.assertIn("定时调度", format_tool_action("schedule", {}))
        self.assertIn("子智能体", format_tool_action("manage_subagents", {}))
        self.assertIn("交互确认", format_tool_action("ask_question", {}))

    def test_post_message_downloads_all_images(self):
        from handlers.media import _process_post_message

        downloaded = []

        def fake_dl(msg_id, key, rtype, out_path):
            downloaded.append((key, out_path))
            return True

        content_json = {
            "content": [
                [
                    {"tag": "text", "text": "对比这两张图"},
                    {"tag": "img", "image_key": "img_aaa111"},
                    {"tag": "img", "image_key": "img_bbb222"},
                ]
            ]
        }

        async def run():
            loop = asyncio.get_running_loop()
            with patch("handlers.media.send_interactive_card_sdk", return_value="om_1"), \
                 patch("handlers.media.download_message_resource_sdk", side_effect=fake_dl):
                return await _process_post_message(loop, "om_src", content_json)

        text, fname, ok, _ = asyncio.run(run())
        self.assertTrue(ok)
        self.assertEqual(len(downloaded), 2)
        self.assertIn("img_aaa111", text)
        self.assertIn("img_bbb222", text)
        self.assertEqual(fname, "2 张图片")


class TestP1Upgrades(unittest.TestCase):
    def test_session_settings_json_and_cli_mode(self):
        from session_pool import build_session_settings_json, normalize_cli_mode
        self.assertEqual(normalize_cli_mode("plan"), "plan")
        self.assertEqual(normalize_cli_mode("accept-edits"), "accept-edits")
        self.assertEqual(normalize_cli_mode("auto"), "auto")

        raw = build_session_settings_json("plan", sandbox_enabled=True, project_dir="/tmp")
        obj = json.loads(raw)
        self.assertEqual(obj["agentMode"], "plan")
        self.assertFalse(obj["allowNonWorkspaceAccess"])
        self.assertIn("/tmp", obj["trustedWorkspaces"])

    def test_hooks_install_isolated_to_workspace_by_default(self):
        from hooks.agy_hooks import ensure_agy_hooks_installed
        with tempfile.TemporaryDirectory() as tmpdir:
            path = ensure_agy_hooks_installed(workspace_dir=tmpdir, global_install=False)
            self.assertEqual(path, os.path.join(tmpdir, ".agents", "hooks.json"))
            self.assertTrue(os.path.isfile(path))
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("PreInvocation", data["hooks"])
            self.assertIn("PreToolUse", data["hooks"])

    def test_native_core_rule_exists(self):
        rule_path = os.path.join(ROOT_DIR, ".agents", "rules", "feishu-bot-core.md")
        self.assertTrue(os.path.isfile(rule_path))

    def test_structured_agy_payload_extraction(self):
        from utils.structured_agy import _extract_json_payload
        self.assertEqual(_extract_json_payload('{"ok": true}'), {"ok": True})
        self.assertEqual(
            _extract_json_payload('{"status":"SUCCESS","response":"{\\"intent\\":\\"code\\"}"}'),
            {"intent": "code"},
        )
        self.assertEqual(
            _extract_json_payload('some header\n{"result":{"response":"{\\"score\\":9}"}}'),
            {"score": 9},
        )


class TestP2AcpAndCommands(unittest.TestCase):
    def test_acp_update_mapping_and_multimodal_blocks(self):
        from acp_client import (
            _acp_update_to_stream_event,
            build_acp_prompt_blocks,
            normalize_acp_mode,
        )
        self.assertEqual(normalize_acp_mode("plan"), "plan")
        self.assertEqual(normalize_acp_mode("accept-edits"), "accept-edits")
        self.assertEqual(normalize_acp_mode("auto"), "default")

        ev_msg = _acp_update_to_stream_event({
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "hello"},
        })
        self.assertEqual(ev_msg, {"event": "message", "role": "assistant", "content": "hello"})

        ev_tool = _acp_update_to_stream_event({
            "sessionUpdate": "tool_call",
            "toolCallId": "call_1",
            "name": "run_command",
            "title": "Run pytest",
            "status": "in_progress",
            "rawInput": {"CommandLine": "timeout 30 pytest -q " + "x" * 600},
        })
        self.assertEqual(ev_tool["event"], "step_update")
        self.assertGreater(len(ev_tool["step_update"]["parameters"]["CommandLine"]), 600)

        # Multimodal image block attachment from /tmp
        with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".png", delete=False) as tf:
            tf.write(b"\x89PNG\r\n\x1a\nfake")
            tmp_img = tf.name
        try:
            blocks = build_acp_prompt_blocks(f"请看图\n[附加图片路径: {tmp_img}]")
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0]["type"], "text")
            self.assertEqual(blocks[1]["type"], "image")
            self.assertEqual(blocks[1]["mimeType"], "image/png")
        finally:
            os.remove(tmp_img)

    def test_acp_permission_future_resolution_and_card(self):
        from acp_client import _PENDING_PERMISSIONS, resolve_pending_permission
        from card_builder import CardBuilder

        card = CardBuilder.build_acp_permission_card(
            "tok_123",
            {
                "toolCall": {"title": "Run command", "kind": "execute", "rawInput": {"CommandLine": "ls -la"}},
                "options": [
                    {"optionId": "allow_once", "name": "允许一次", "kind": "allow_once"},
                    {"optionId": "reject_once", "name": "拒绝", "kind": "reject_once"},
                ],
            },
        )
        self.assertEqual(card["header"]["template"], "orange")

        async def run():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            _PENDING_PERMISSIONS["tok_123"] = {"future": fut, "chat_id": "oc_chat1", "options": []}
            # Wrong chat rejected
            ok_wrong, _ = resolve_pending_permission("tok_123", "oc_other", "allow_once")
            self.assertFalse(ok_wrong)
            # Correct chat resolves
            ok_right, _ = resolve_pending_permission("tok_123", "oc_chat1", "allow_once")
            self.assertTrue(ok_right)
            self.assertEqual(await fut, "allow_once")

        asyncio.run(run())

    def test_live_acp_session_reused_on_model_and_mode_change(self):
        from session_pool import SessionPool

        pool = SessionPool()
        s1 = pool.get_or_create("oc_acp_test", "gemini-3.8-flash-low", "/tmp", "conv-1", effort="low", transport="acp")
        # Simulate live process
        s1.process = MagicMock(returncode=None)
        s2 = pool.get_or_create("oc_acp_test", "gemini-4-neon-high", "/tmp", "conv-1", effort="high", agent_mode="plan", transport="acp")
        # Must reuse same live ACP session instead of killing & respawning!
        self.assertIs(s1, s2)

    def test_commands_mode_adddir_transport_steer(self):
        from commands import handle_slash_command

        replies = []

        async def run():
            session_data = {"model": "gemini-3.8-flash-low", "project": ROOT_DIR}
            with patch("commands.is_admin", return_value=True), \
                 patch("commands.send_reply_sdk", side_effect=lambda mid, txt: replies.append(txt)), \
                 patch("commands.save_session_async", AsyncMock()):
                h1, _ = await handle_slash_command("/mode plan", "m1", "oc_cmd", session_data, {}, {}, {})
                self.assertTrue(h1)
                self.assertEqual(session_data["agent_mode"], "plan")

                h2, _ = await handle_slash_command("/adddir /tmp", "m2", "oc_cmd", session_data, {}, {}, {})
                self.assertTrue(h2)
                self.assertIn(os.path.abspath("/tmp"), session_data["extra_dirs"])

                h3, _ = await handle_slash_command("/transport stream-json", "m3", "oc_cmd", session_data, {}, {}, {})
                self.assertTrue(h3)
                self.assertEqual(session_data["transport"], "stream-json")

                h4, _ = await handle_slash_command("/steer 改用单元测试", "m4", "oc_cmd", session_data, {}, {}, {})
                self.assertTrue(h4)

        asyncio.run(run())
        self.assertEqual(len(replies), 4)

    def test_real_agy_acp_handshake_set_model_and_mode(self):
        """Real smoke test against local `agy 1.5.12` ACP stdio server."""
        import config
        from acp_client import AcpPersistentSession

        bin_path = config.ANTIGRAVITY_BIN or config.find_antigravity_bin()
        if not bin_path:
            self.skipTest("agy binary not installed")

        async def run():
            sess = AcpPersistentSession(
                chat_id="oc_real_smoke",
                model="gemini-3.8-flash-low",
                project_dir=ROOT_DIR,
                effort="low",
                agent_mode="plan",
            )
            try:
                await sess.start()
                self.assertTrue(sess.is_alive())
                self.assertTrue(bool(sess.conversation_id))
                self.assertTrue(await sess.set_mode("accept-edits"))
                self.assertEqual(sess.agent_mode, "accept-edits")
                self.assertTrue(await sess.set_model("gemini-3.8-flash-medium", effort="medium"))
                self.assertEqual(sess.model, "gemini-3.8-flash-medium")
            finally:
                await sess.close()
                self.assertFalse(sess.is_alive())

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
