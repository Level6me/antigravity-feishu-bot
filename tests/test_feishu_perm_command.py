# -*- coding: utf-8 -*-
"""Unit tests for `/perm` Feishu Open Platform permission diagnostics & hot-healing."""

import asyncio
import json
import os
import sys
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app_state
import config
from cards import CardBuilder
from commands import handle_slash_command
from handlers.card_actions import do_p2_card_action_trigger
from lark_client import _CARDKIT_CAPABILITY_STATE
from plugin_manager import DEFAULT_SYSTEM_COMMANDS, plugin_manager
from utils.feishu_perm_checker import check_feishu_permissions


class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, route_handler):
        self._handler = route_handler

    async def request(self, method: str, path: str, json=None, headers=None):
        return _FakeResp(self._handler(method, path))


class TestFeishuPermCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_cardkit = _CARDKIT_CAPABILITY_STATE.get("supported")
        plugin_manager.register_system_commands(DEFAULT_SYSTEM_COMMANDS)

    def tearDown(self):
        _CARDKIT_CAPABILITY_STATE["supported"] = self._orig_cardkit

    def test_01_perm_registered_as_system_slash_command(self):
        self.assertTrue(plugin_manager.is_slash_command("/perm"))
        self.assertTrue(plugin_manager.is_slash_command("/perms"))

    async def test_02_non_admin_blocked_from_perm_command_and_card_action(self):
        replies = []
        with patch("commands.is_admin", return_value=False), \
             patch("commands.send_reply_sdk", side_effect=lambda mid, txt: replies.append(txt)):
            handled, _ = await handle_slash_command("/perm", "om_1", "oc_user", {}, {}, {}, {})
        self.assertTrue(handled)
        self.assertEqual(len(replies), 1)
        self.assertIn("仅管理员可用", replies[0])

        ev = MagicMock()
        ev.event.action.value = {"action": "refresh_feishu_perm"}
        ev.event.action.form_value = {}
        ev.event.context.open_chat_id = "oc_user"
        ev.event.context.open_message_id = "om_card"
        ev.event.operator.open_id = "ou_user"
        with patch("handlers.card_actions.get_role", return_value="user"), \
             patch("handlers.card_actions.is_admin", return_value=False):
            resp = do_p2_card_action_trigger(ev)
        self.assertIn("仅管理员", resp.toast.content)

    async def test_03_perm_probe_detects_missing_scopes_and_hot_heals_cardkit(self):
        # Round 1: cardkit:card:write missing (99991672), others ok (0 or non-perm business code 230001)
        def _round1(method: str, path: str):
            if "/cardkit/v1/cards" in path:
                return {
                    "code": 99991672,
                    "msg": "Access denied. Scopes required: [cardkit:card:write]",
                    "error": {"permission_violations": [{"subject": "cardkit:card:write"}]},
                }
            if "/resources/" in path:
                return {"code": 234001, "msg": "invalid message_id"}
            return {"code": 0, "data": {"card_id": "c_1"}}

        with patch.object(config, "APP_ID", "cli_test1234567890"), \
             patch.object(config, "APP_SECRET", "sec_test"), \
             patch("utils.feishu_perm_checker._get_tenant_access_token_async", AsyncMock(return_value="t_tok")), \
             patch("utils.feishu_perm_checker._get_async_http_client", return_value=_FakeAsyncClient(_round1)):
            rep1 = await check_feishu_permissions(chat_id="oc_admin", sender_open_id="ou_admin")

        self.assertTrue(rep1["ok"])  # Optional missing -> still ok=True, degraded
        self.assertEqual(rep1["missing_required"], [])
        self.assertIn("cardkit:card:write", rep1["missing_optional"])
        self.assertFalse(_CARDKIT_CAPABILITY_STATE["supported"])

        card1 = CardBuilder.build_perm_check_card(rep1)
        self.assertEqual(card1["header"]["template"], "orange")
        raw1 = json.dumps(card1, ensure_ascii=False)
        self.assertIn("cardkit:card:write", raw1)
        self.assertIn("https://open.feishu.cn/app/cli_test1234567890/auth", raw1)

        # Round 2: admin enables cardkit:card:write in Feishu console and clicks re-check -> hot-heals!
        def _round2(method: str, path: str):
            return {"code": 0, "data": {"card_id": "c_ok"}}

        with patch.object(config, "APP_ID", "cli_test1234567890"), \
             patch.object(config, "APP_SECRET", "sec_test"), \
             patch("utils.feishu_perm_checker._get_tenant_access_token_async", AsyncMock(return_value="t_tok")), \
             patch("utils.feishu_perm_checker._get_async_http_client", return_value=_FakeAsyncClient(_round2)):
            rep2 = await check_feishu_permissions(chat_id="oc_admin", sender_open_id="ou_admin")

        self.assertTrue(rep2["ok"])
        self.assertTrue(rep2["cardkit_newly_activated"])
        self.assertTrue(_CARDKIT_CAPABILITY_STATE["supported"])
        card2 = CardBuilder.build_perm_check_card(rep2)
        self.assertEqual(card2["header"]["template"], "green")

    async def test_04_missing_required_scope_and_slash_command_dispatch(self):
        def _missing_resource(method: str, path: str):
            if "/resources/" in path:
                return {"code": 99991672, "msg": "Access denied: [im:resource]"}
            return {"code": 0}

        sent_cards = []
        with patch.object(config, "APP_ID", "cli_test1234567890"), \
             patch.object(config, "APP_SECRET", "sec_test"), \
             patch("commands.is_admin", return_value=True), \
             patch("commands.send_interactive_card_sdk", side_effect=lambda mid, c: sent_cards.append(c) or "om_2"), \
             patch("utils.feishu_perm_checker._get_tenant_access_token_async", AsyncMock(return_value="t_tok")), \
             patch("utils.feishu_perm_checker._get_async_http_client", return_value=_FakeAsyncClient(_missing_resource)):
            handled, _ = await handle_slash_command("/perm", "om_req", "oc_admin", {}, {}, {}, {})

        self.assertTrue(handled)
        self.assertEqual(len(sent_cards), 1)
        self.assertEqual(sent_cards[0]["header"]["template"], "red")
        self.assertIn("im:resource", json.dumps(sent_cards[0], ensure_ascii=False))

        # Also verify worker-thread callback refresh returns synchronous card
        old_loop = app_state.main_loop
        app_state.main_loop = asyncio.get_running_loop()
        try:
            holder = {}
            with patch("handlers.card_actions.get_role", return_value="admin"), \
                 patch("handlers.card_actions.is_admin", return_value=True), \
                 patch.object(config, "APP_ID", "cli_test1234567890"), \
                 patch.object(config, "APP_SECRET", "sec_test"), \
                 patch("utils.feishu_perm_checker._get_tenant_access_token_async", AsyncMock(return_value="t_tok")), \
                 patch("utils.feishu_perm_checker._get_async_http_client", return_value=_FakeAsyncClient(lambda m, p: {"code": 0})):
                def _worker():
                    ev = MagicMock()
                    ev.event.action.value = {"action": "refresh_feishu_perm"}
                    ev.event.action.form_value = {}
                    ev.event.context.open_chat_id = "oc_admin"
                    ev.event.context.open_message_id = "om_perm_card"
                    ev.event.operator.open_id = "ou_admin"
                    holder["resp"] = do_p2_card_action_trigger(ev)

                t = threading.Thread(target=_worker)
                t.start()
                while t.is_alive():
                    await asyncio.sleep(0.01)
                t.join()
            self.assertIsNotNone(getattr(holder["resp"], "card", None))
        finally:
            app_state.main_loop = old_loop


if __name__ == "__main__":
    unittest.main()
