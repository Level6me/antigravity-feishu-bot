"""Unit tests for the interactive Feishu MCP management panel (`/mcp`)."""
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


class TestMcpManagerAndCard(unittest.TestCase):
    def test_parse_agy_mcp_list_and_mask_secrets(self):
        from utils.mcp_manager import mask_sensitive_text, parse_agy_mcp_list_output

        self.assertEqual(parse_agy_mcp_list_output("No MCP servers configured.\n"), [])

        sample = (
            "filesystem (stdio) [enabled]: npx -y @modelcontextprotocol/server-filesystem /work\n"
            "github-api (http) [disabled]: https://example.com/mcp?api_key=supersecret123\n"
        )
        parsed = parse_agy_mcp_list_output(sample)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "filesystem")
        self.assertEqual(parsed[0]["transport"], "stdio")
        self.assertTrue(parsed[0]["enabled"])

        self.assertEqual(parsed[1]["name"], "github-api")
        self.assertEqual(parsed[1]["transport"], "http")
        self.assertFalse(parsed[1]["enabled"])
        self.assertNotIn("supersecret123", parsed[1]["target"])

        self.assertEqual(
            mask_sensitive_text("Authorization: Bearer tok_abc123 GITHUB_TOKEN=ghp_999"),
            "Authorization: Bearer *** GITHUB_TOKEN=***",
        )

    def test_load_local_mcp_config_masks_values_and_keeps_keys(self):
        from utils.mcp_manager import _load_local_mcp_config_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "mcp_config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "mcpServers": {
                            "gh": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-github"],
                                "env": {"GITHUB_TOKEN": "ghp_very_secret_value"},
                                "disabled": False,
                            },
                            "remote": {
                                "url": "https://mcp.example.com/sse",
                                "headers": {"Authorization": "Bearer secret_bearer_token"},
                                "disabled": True,
                            },
                        }
                    },
                    f,
                )
            meta = _load_local_mcp_config_metadata(config_paths=[cfg_path])
            self.assertIn("gh", meta)
            self.assertEqual(meta["gh"]["env_keys"], ["GITHUB_TOKEN"])
            self.assertNotIn("ghp_very_secret_value", json.dumps(meta))
            self.assertIn("remote", meta)
            self.assertEqual(meta["remote"]["transport"], "http")
            self.assertFalse(meta["remote"]["enabled"])
            self.assertEqual(meta["remote"]["header_keys"], ["Authorization"])
            self.assertNotIn("secret_bearer_token", json.dumps(meta))

    def test_add_mcp_server_builds_valid_flag_order(self):
        from utils.mcp_manager import add_mcp_server

        captured_argv = []

        async def fake_exec(argv_tail, timeout_seconds=25.0):
            captured_argv.append(argv_tail)
            return True, "Added", ""

        async def run():
            with patch("utils.mcp_manager._exec_agy_mcp", side_effect=fake_exec):
                ok, msg = await add_mcp_server(
                    name="gh",
                    target="npx -y @modelcontextprotocol/server-github",
                    transport="stdio",
                    env_pairs=["GITHUB_TOKEN=secret_val"],
                )
                self.assertTrue(ok)
                self.assertNotIn("secret_val", msg)

                ok2, _ = await add_mcp_server(
                    name="remote-api",
                    target="https://example.com/mcp",
                    transport="auto",
                    headers=["Authorization: Bearer abc"],
                )
                self.assertTrue(ok2)

        asyncio.run(run())
        self.assertEqual(len(captured_argv), 2)
        # Flags MUST precede <name>, and `--` separates `-y` args
        self.assertEqual(
            captured_argv[0],
            ["add", "--type", "stdio", "--env", "GITHUB_TOKEN=secret_val", "gh", "--", "npx", "-y", "@modelcontextprotocol/server-github"],
        )
        self.assertEqual(
            captured_argv[1],
            ["add", "--type", "http", "--header", "Authorization: Bearer abc", "remote-api", "https://example.com/mcp"],
        )

    def test_build_mcp_panel_card_dual_mode_and_reload_banner(self):
        from card_builder import CardBuilder

        servers = [
            {
                "name": "filesystem",
                "transport": "stdio",
                "enabled": True,
                "target": "npx -y @modelcontextprotocol/server-filesystem /work",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/work"],
                "env_keys": ["FS_ROOT"],
                "header_keys": [],
            },
            {
                "name": "remote-api",
                "transport": "http",
                "enabled": False,
                "target": "https://example.com/mcp",
                "args": [],
                "env_keys": [],
                "header_keys": ["Authorization"],
            },
        ]

        # List tab with detail expanded & reload banner
        card_list = CardBuilder.build_mcp_panel_card(
            servers,
            active_tab="list",
            expanded_name="filesystem",
            notice="已启用 MCP 服务 `filesystem`",
            notice_level="success",
            needs_session_reload=True,
            is_admin_user=True,
        )
        raw_list = json.dumps(card_list, ensure_ascii=False)
        self.assertIn("MCP 服务管理中心", raw_list)
        self.assertIn("重建当前会话以加载新 MCP", raw_list)
        self.assertIn("mcp_reload_session", raw_list)
        self.assertIn("确认移除 MCP 服务", raw_list)
        self.assertIn("FS_ROOT", raw_list)

        # Add tab has dual mode (curated templates + form)
        card_add = CardBuilder.build_mcp_panel_card(
            servers,
            active_tab="add",
            is_admin_user=True,
            workspace_dir="/tmp",
        )
        raw_add = json.dumps(card_add, ensure_ascii=False)
        self.assertIn("模式一：常用官方 MCP 模板", raw_add)
        self.assertIn("模式二：自定义添加 MCP 服务", raw_add)
        self.assertIn('"tag": "form"', raw_add)
        self.assertIn("mcp_quick_add", raw_add)
        self.assertIn("mcp_add_submit", raw_add)

        # Non-admin read-only view omits write buttons & form
        card_ro = CardBuilder.build_mcp_panel_card(servers, active_tab="add", is_admin_user=False)
        raw_ro = json.dumps(card_ro, ensure_ascii=False)
        self.assertNotIn('"tag": "form"', raw_ro)
        self.assertNotIn("mcp_quick_add", raw_ro)
        self.assertIn("仅管理员", raw_ro)

    def test_mcp_command_sends_interactive_card_and_add_tab(self):
        from commands import handle_slash_command

        sent_cards = []
        session_data = {"project": ROOT_DIR}

        async def run():
            with patch("utils.mcp_manager.list_mcp_servers", AsyncMock(return_value=([], None))), \
                 patch("utils.mcp_manager.run_raw_mcp_cli", AsyncMock(return_value=(True, "Disabled fs"))), \
                 patch("commands.is_admin", return_value=True), \
                 patch("commands.send_interactive_card_sdk", side_effect=lambda mid, card: sent_cards.append(card) or "om_1"):
                h1, _ = await handle_slash_command("/mcp", "m1", "oc_mcp", session_data, {}, {}, {})
                self.assertTrue(h1)

                h2, _ = await handle_slash_command("/mcp add", "m2", "oc_mcp", session_data, {}, {}, {})
                self.assertTrue(h2)

                h3, _ = await handle_slash_command("/mcp disable fs", "m3", "oc_mcp", session_data, {}, {}, {})
                self.assertTrue(h3)

        asyncio.run(run())
        self.assertEqual(len(sent_cards), 3)
        self.assertEqual(sent_cards[0]["header"]["template"], "indigo")
        self.assertEqual(sent_cards[1]["header"]["template"], "blue")
        self.assertIn("mcp_add_form", json.dumps(sent_cards[1], ensure_ascii=False))
        self.assertIn("mcp_reload_session", json.dumps(sent_cards[2], ensure_ascii=False))

    def test_mcp_card_actions_admin_gate_form_submit_and_session_reload(self):
        import app_state
        from handlers.mcp_actions import handle_mcp_card_action

        patched_cards = []

        async def run():
            loop = asyncio.get_running_loop()
            old_loop = app_state.main_loop
            app_state.main_loop = loop
            try:
                # 1. Non-admin write action must be blocked immediately
                with patch("handlers.mcp_actions.is_admin", return_value=False):
                    resp_deny = handle_mcp_card_action(
                        {"action": "mcp_toggle", "name": "fs", "enable": False},
                        "oc_user",
                        "om_card",
                    )
                self.assertIsNotNone(resp_deny)
                self.assertIn("仅管理员", resp_deny.toast.content)

                # 2. Admin custom form submit triggers add_mcp_server & patches card with reload banner
                mock_add = AsyncMock(return_value=(True, "已添加/更新 MCP 服务 `my-api`（`http`）"))
                mock_list = AsyncMock(return_value=([{"name": "my-api", "transport": "http", "enabled": True, "target": "https://example.com/mcp"}], None))
                with patch("handlers.mcp_actions.is_admin", return_value=True), \
                     patch("handlers.mcp_actions.add_mcp_server", mock_add), \
                     patch("handlers.mcp_actions.list_mcp_servers", mock_list), \
                     patch("handlers.mcp_actions.get_session_async", AsyncMock(return_value={"project": ROOT_DIR})), \
                     patch("handlers.mcp_actions.patch_interactive_card_sdk", side_effect=lambda mid, c: patched_cards.append(c) or True):
                    resp_sub = handle_mcp_card_action(
                        {"action": "mcp_add_submit"},
                        "oc_admin",
                        "om_card",
                        form_value={
                            "mcp_name": "my-api",
                            "mcp_transport": "auto",
                            "mcp_target": "https://example.com/mcp",
                            "mcp_args": "",
                            "mcp_kv": "Authorization: Bearer tok_123",
                        },
                    )
                    self.assertIsNotNone(resp_sub)
                    await asyncio.sleep(0.08)

                mock_add.assert_awaited_once()
                self.assertGreaterEqual(len(patched_cards), 1)
                self.assertIn("mcp_reload_session", json.dumps(patched_cards[-1], ensure_ascii=False))

                # 3. Admin clicks reload session -> resets idle session & clears reload banner
                mock_reset = AsyncMock()
                with patch("handlers.mcp_actions.is_admin", return_value=True), \
                     patch("handlers.mcp_actions.list_mcp_servers", mock_list), \
                     patch("handlers.mcp_actions.get_session_async", AsyncMock(return_value={"project": ROOT_DIR})), \
                     patch("handlers.mcp_actions.patch_interactive_card_sdk", side_effect=lambda mid, c: patched_cards.append(c) or True), \
                     patch("session_pool.session_pool.get_active_session", return_value=None), \
                     patch("session_pool.session_pool.reset_session", mock_reset):
                    resp_rel = handle_mcp_card_action(
                        {"action": "mcp_reload_session", "tab": "list"},
                        "oc_admin",
                        "om_card",
                    )
                    self.assertIsNotNone(resp_rel)
                    await asyncio.sleep(0.08)

                mock_reset.assert_awaited_once_with("oc_admin")
                self.assertIn("已重建当前会话连接", json.dumps(patched_cards[-1], ensure_ascii=False))
            finally:
                app_state.main_loop = old_loop

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
