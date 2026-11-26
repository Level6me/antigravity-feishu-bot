"""Regression tests for Feishu interactive card reliability:
1. Card Schema V1 -> V2 fidelity & stateless `_card_schema: "2.0"` callback marker.
2. Automatic Schema 2.0 adaptation & Feishu `200830` self-healing in patch_interactive_card_sdk / async_patch_interactive_card.
3. L3 high-risk approval card V2 sync update, legacy V2 safe fallback, duplicate-click idempotency, and admin-check ordering.
4. Budget-pause & 503 capacity-choice idempotency + stale/superseded card guard when a new message arrived.
5. Callback 3-second timeout guard (`switch_model`, `acp_permission_decision`) and top-level fail-safe exception boundary.
"""
import asyncio
import json
import os
import sys
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app_state
from card_builder import CardBuilder
from cards.common import adapt_card_for_message, convert_card_v1_to_v2


def _make_card_event(action_value, chat_id="oc_test_chat", message_id="om_test_card", open_id="ou_admin", form_value=None):
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            action=types.SimpleNamespace(
                value=action_value,
                form_value=form_value or {},
            ),
            context=types.SimpleNamespace(
                open_chat_id=chat_id,
                open_message_id=message_id,
            ),
            operator=types.SimpleNamespace(open_id=open_id),
        )
    )


class _BackgroundLoopHarness:
    """Runs an isolated asyncio loop on a background thread (mirrors live WS callback thread)."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def start(self):
        self.thread.start()
        self._ready.wait(timeout=3.0)
        return self.loop

    def drain(self, timeout=2.0):
        async def _noop():
            await asyncio.sleep(0.05)

        asyncio.run_coroutine_threadsafe(_noop(), self.loop).result(timeout=timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3.0)
        self.loop.close()


class TestCardActionReliability(unittest.TestCase):
    def setUp(self):
        with app_state._CARD_STATE_LOCK:
            app_state._V2_CARD_MESSAGES.clear()
            app_state._CLAIMED_CARD_ACTIONS.clear()

    def test_convert_v1_to_v2_preserves_rich_elements_and_injects_marker(self):
        cap_card = CardBuilder.build_capacity_choice_card(
            failed_model="gemini-3.1-pro-high",
            user_text="排查构建失败原因",
            candidate_models=[
                {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "health": "healthy"},
                {"id": "gpt-5.4", "label": "GPT-5.4", "health": "unknown"},
            ],
            recommended_model="claude-sonnet-4-6",
            current_project="/tmp/demo",
        )
        # Also test div + confirm preservation
        cap_card["elements"].append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**附加说明**"},
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": "**列A**\n1"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": "**列B**\n2"}},
            ],
        })
        cap_card["elements"].append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "危险按钮"},
                "type": "danger",
                "confirm": {"title": {"tag": "plain_text", "content": "确认"}, "text": {"tag": "plain_text", "content": "确定?"}},
                "value": {"action": "danger_op"},
            }],
        })

        v2 = convert_card_v1_to_v2(cap_card, stream_element_id="", streaming_mode=False)
        self.assertEqual(v2.get("schema"), "2.0")
        self.assertTrue(v2["config"]["update_multi"])
        self.assertFalse(v2["config"]["streaming_mode"])

        body_elements = v2["body"]["elements"]
        tags = [e.get("tag") for e in body_elements]
        self.assertIn("form", tags, "form element must not be dropped in V2 conversion")
        self.assertIn("column_set", tags)
        self.assertNotIn("note", tags, "deprecated V1 note tag must be converted")
        self.assertNotIn("action", tags, "deprecated V1 action tag must be converted")

        # Verify form contains select_static and converted submit button with _card_schema=2.0
        form_el = next(e for e in body_elements if e.get("tag") == "form")
        form_str = json.dumps(form_el, ensure_ascii=False)
        self.assertIn("select_static", form_str)
        self.assertIn('"form_action_type": "submit"', form_str)
        self.assertIn('"_card_schema": "2.0"', form_str)

        # Verify confirm dialog preserved and original V1 button value NOT mutated
        all_str = json.dumps(v2, ensure_ascii=False)
        self.assertIn("危险按钮", all_str)
        self.assertIn('"confirm"', all_str)
        self.assertIn("**附加说明**", all_str)
        self.assertIn("**列A**", all_str)
        orig_btn_val = cap_card["elements"][-1]["actions"][0]["value"]
        self.assertNotIn("_card_schema", orig_btn_val)

    def test_patch_interactive_card_sdk_self_heals_on_200830(self):
        import lark_client

        sent_payloads = []

        def fake_patch(req):
            payload = json.loads(req.request_body.content)
            sent_payloads.append(payload)
            if len(sent_payloads) == 1:
                return types.SimpleNamespace(
                    code=230099,
                    msg="Failed to create card content, ext=ErrCode: 200830; ErrMsg: schemaV2 card can not change schemaV1; ",
                )
            return types.SimpleNamespace(code=0, msg="success")

        v1_card = CardBuilder.build_l3_confirm_result_card(
            status="success",
            command="echo hello",
            operator_id="ou_admin",
            output="hello",
            returncode=0,
            elapsed_seconds=0.5,
            scope="once",
        )
        with patch.object(lark_client.api_client.im.v1.message, "patch", side_effect=fake_patch):
            ok = lark_client.patch_interactive_card_sdk("om_v2_legacy_msg", v1_card)

        self.assertTrue(ok)
        self.assertEqual(len(sent_payloads), 2)
        self.assertNotIn("schema", sent_payloads[0])
        self.assertEqual(sent_payloads[1].get("schema"), "2.0")
        self.assertTrue(app_state.is_v2_card_message("om_v2_legacy_msg"))

        # Subsequent patch on same message_id should proactively send Schema 2.0 on first try
        sent_payloads.clear()
        with patch.object(lark_client.api_client.im.v1.message, "patch", side_effect=lambda req: (
            sent_payloads.append(json.loads(req.request_body.content)) or types.SimpleNamespace(code=0, msg="ok")
        )):
            ok2 = lark_client.patch_interactive_card_sdk("om_v2_legacy_msg", v1_card)
        self.assertTrue(ok2)
        self.assertEqual(len(sent_payloads), 1)
        self.assertEqual(sent_payloads[0].get("schema"), "2.0")

    def test_async_patch_interactive_card_self_heals_on_200830(self):
        import lark_client

        sent_cards = []

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        class FakeClient:
            async def patch(self, url, content=None, headers=None):
                outer = json.loads(content.decode("utf-8"))
                card = json.loads(outer["content"])
                sent_cards.append(card)
                if len(sent_cards) == 1:
                    return FakeResp({
                        "code": 230099,
                        "msg": "Failed to create card content, ext=ErrCode: 200830; ErrMsg: schemaV2 card can not change schemaV1;",
                    })
                return FakeResp({"code": 0, "msg": "ok"})

        v1_card = CardBuilder.build_budget_pause_card(
            user_text="测试任务",
            resumed_status="continued",
        )

        async def _run():
            with patch.object(lark_client, "_get_tenant_access_token_async", new=AsyncMock(return_value="tok_test")), \
                 patch.object(lark_client, "_get_async_http_client", return_value=FakeClient()):
                return await lark_client.async_patch_interactive_card("om_async_v2", v1_card)

        ok = asyncio.run(_run())
        self.assertTrue(ok)
        self.assertEqual(len(sent_cards), 2)
        self.assertEqual(sent_cards[1].get("schema"), "2.0")
        self.assertTrue(app_state.is_v2_card_message("om_async_v2"))

    def test_l3_confirm_action_v2_sync_update_and_duplicate_click_lock(self):
        from handlers import card_actions

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        executed_cmds = []

        async def fake_get_session(cid):
            return {
                "project": "/tmp",
                "model": "Default",
                "paused_l3_state": {
                    "command": "echo safe_l3_test",
                    "tool": "run_command",
                    "cwd": "/tmp",
                    "user_text": "原始任务",
                    "card_message_id": "om_l3_v2_card",
                },
            }

        async def fake_exec_proc(*args, **kwargs):
            executed_cmds.append(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
            return proc

        try:
            evt_v2 = _make_card_event(
                {
                    "action": "l3_confirm_action",
                    "decision": "approve",
                    "scope": "once",
                    "command": "echo safe_l3_test",
                    "tool": "run_command",
                    "cwd": "/tmp",
                    "call_id": "call_123",
                    "_card_schema": "2.0",
                },
                message_id="om_l3_v2_card",
            )
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", side_effect=fake_get_session), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch.object(card_actions, "patch_interactive_card_sdk", return_value=True), \
                 patch.object(card_actions, "_handle_message_async_internal", new=AsyncMock()), \
                 patch("asyncio.create_subprocess_exec", side_effect=fake_exec_proc):
                # 1st click: must return Schema 2.0 card synchronously
                resp1 = card_actions.do_p2_card_action_trigger(evt_v2)
                self.assertIsNotNone(resp1.card)
                self.assertEqual(resp1.card.data.get("schema"), "2.0")
                self.assertEqual(resp1.toast.type, "success")

                # 2nd duplicate click: must be blocked by idempotency lock!
                resp2 = card_actions.do_p2_card_action_trigger(evt_v2)
                self.assertIsNone(resp2.card)
                self.assertEqual(resp2.toast.type, "info")
                self.assertIn("请勿重复点击", resp2.toast.content)

                harness.drain(timeout=3.0)
                time.sleep(0.15)
                actual_execs = [a for a in executed_cmds if "-n" not in a]
                self.assertEqual(len(actual_execs), 1, "L3 command must execute strictly once despite double clicks")
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_l3_legacy_pre_restart_card_avoids_sync_200830_when_cardkit_enabled(self):
        from handlers import card_actions
        import lark_client

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        patched_mids = []

        try:
            evt_legacy = _make_card_event(
                {
                    "action": "l3_confirm_action",
                    "decision": "reject",
                    "command": "echo old_card",
                },
                message_id="om_legacy_untracked_l3",
            )
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", new=AsyncMock(return_value={})), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch.object(lark_client.CardKitStreamController, "can_attempt", return_value=True), \
                 patch.object(card_actions, "patch_interactive_card_sdk", side_effect=lambda mid, c: patched_mids.append(mid) or True):
                resp = card_actions.do_p2_card_action_trigger(evt_legacy)
                # Must NOT return a V1 card synchronously when CardKit is enabled & card schema is unknown!
                self.assertIsNone(resp.card)
                self.assertEqual(resp.toast.type, "info")
                harness.drain(timeout=2.0)
                time.sleep(0.1)
                self.assertEqual(patched_mids, ["om_legacy_untracked_l3"])
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_l3_non_admin_click_does_not_lock_out_admin(self):
        from handlers import card_actions

        evt = _make_card_event(
            {
                "action": "l3_confirm_action",
                "decision": "reject",
                "command": "rm -rf /tmp/x",
                "_card_schema": "2.0",
            },
            message_id="om_l3_perm_check",
        )
        with patch.object(card_actions, "ALLOWED_USERS", []), \
             patch.object(card_actions, "ALLOWED_CHATS", []), \
             patch.object(card_actions, "get_role", return_value="user"), \
             patch.object(card_actions, "is_admin", return_value=False):
            r_user = card_actions.do_p2_card_action_trigger(evt)
            self.assertEqual(r_user.toast.type, "error")

        with patch.object(card_actions, "ALLOWED_USERS", []), \
             patch.object(card_actions, "ALLOWED_CHATS", []), \
             patch.object(card_actions, "get_role", return_value="admin"), \
             patch.object(card_actions, "is_admin", return_value=True):
            r_admin = card_actions.do_p2_card_action_trigger(evt)
            self.assertEqual(r_admin.toast.type, "info")
            self.assertIn("已拒绝", r_admin.toast.content)
            self.assertEqual(r_admin.card.data.get("schema"), "2.0")

    def test_stale_budget_pause_card_after_new_message_does_not_rerun_last_query(self):
        from handlers import card_actions

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        resumed_calls = []
        patched_cards = []

        async def fake_get_session(cid):
            return {
                "model": "Default",
                "project": "/tmp/proj",
                "last_user_query": "用户后来新发的消息",
                # paused_task_state already belongs to a newer card!
                "paused_task_state": {
                    "user_text": "新任务的暂停",
                    "card_message_id": "om_newer_pause_card",
                },
            }

        async def fake_resume(*args, **kwargs):
            resumed_calls.append(args)

        try:
            evt_old = _make_card_event(
                {"action": "continue_long_task", "_card_schema": "2.0"},
                message_id="om_old_pause_card",
            )
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", side_effect=fake_get_session), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch.object(card_actions, "patch_interactive_card_sdk", side_effect=lambda mid, c: patched_cards.append((mid, c)) or True), \
                 patch.object(card_actions, "_handle_message_async_internal", side_effect=fake_resume):
                resp1 = card_actions.do_p2_card_action_trigger(evt_old)
                self.assertEqual(resp1.toast.type, "success")
                # Second click blocked by idempotency lock
                resp2 = card_actions.do_p2_card_action_trigger(evt_old)
                self.assertEqual(resp2.toast.type, "info")
                self.assertIn("请勿重复点击", resp2.toast.content)

                harness.drain(timeout=2.0)
                time.sleep(0.1)
                self.assertEqual(len(resumed_calls), 0, "Stale pause card must NOT trigger new AI turn")
                self.assertEqual(len(patched_cards), 1, "Stale pause card should be locked via patch")
                self.assertEqual(patched_cards[0][0], "om_old_pause_card")
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_switch_model_respects_feishu_3s_callback_deadline(self):
        from handlers import card_actions

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop

        async def slow_get_session(cid):
            await asyncio.sleep(2.5)
            return {"model": "old-model", "project": "默认"}

        try:
            evt = _make_card_event(
                {"action": "switch_model", "model": "gemini-3.1-pro-high"},
                message_id="om_model_card",
            )
            t0 = time.monotonic()
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", side_effect=slow_get_session), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch.object(card_actions, "patch_interactive_card_sdk", return_value=True):
                resp = card_actions.do_p2_card_action_trigger(evt)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.2, f"switch_model callback exceeded safe 3s Feishu deadline: {elapsed:.2f}s")
            self.assertEqual(resp.toast.type, "success")
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_top_level_exception_boundary_and_malformed_index_safe(self):
        from handlers import card_actions

        # 1. Malformed index on delete_note must NOT crash
        evt_bad_idx = _make_card_event(
            {"action": "delete_note", "index": "not_a_number", "page": "abc"},
            message_id="om_note_card",
        )
        with patch.object(card_actions, "ALLOWED_USERS", []), \
             patch.object(card_actions, "ALLOWED_CHATS", []), \
             patch.object(card_actions, "get_role", return_value="admin"), \
             patch.object(card_actions, "is_admin", return_value=True):
            resp = card_actions.do_p2_card_action_trigger(evt_bad_idx)
            self.assertIsNotNone(resp.toast)

        # 2. Unexpected internal crash must return clean error toast instead of bubbling up
        with patch.object(card_actions, "_do_p2_card_action_trigger_impl", side_effect=RuntimeError("boom")):
            resp_err = card_actions.do_p2_card_action_trigger(evt_bad_idx)
            self.assertEqual(resp_err.toast.type, "error")
            self.assertIn("异常", resp_err.toast.content)

    def test_sync_callback_surfaces_real_error_instead_of_fake_success_toast(self):
        from handlers import card_actions, mcp_actions

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        try:
            async def boom_coro():
                raise ValueError("db write failed")

            resp = card_actions._respond_card_sync_or_patch(
                boom_coro,
                "om_err_card",
                "本不该看到的成功提示",
                toast_type="success",
                sync_timeout=1.0,
                claim_key="test:boom_lock",
                release_on_error=True,
            )
            self.assertEqual(resp.toast.type, "error")
            self.assertIn("db write failed", resp.toast.content)
            self.assertIsNone(resp.card)

            with patch.object(mcp_actions, "_render_and_patch_mcp_card", side_effect=RuntimeError("mcp cli broken")):
                mcp_resp = mcp_actions._smart_mcp_read_response("oc_test", "om_mcp", "已刷新")
                self.assertEqual(mcp_resp.toast.type, "error")
                self.assertIn("mcp cli broken", mcp_resp.toast.content)
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_capacity_choice_patch_failure_does_not_abort_task_resume(self):
        from handlers import card_actions

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        resumed = []

        async def fake_get_session(cid):
            return {
                "model": "gemini-3.1-pro-high",
                "project": "/tmp/demo",
                "paused_capacity_state": {
                    "user_text": "继续编译固件",
                    "failed_model": "gemini-3.1-pro-high",
                    "recommended_model": "claude-sonnet-4-6",
                    "project_dir": "/tmp/demo",
                    "card_message_id": "om_cap_card",
                },
            }

        async def fake_resume(*args, **kwargs):
            resumed.append((args, kwargs))

        try:
            evt = _make_card_event(
                {
                    "action": "capacity_choice_decision",
                    "decision": "switch_form_and_continue",
                    "failed_model": "gemini-3.1-pro-high",
                    "_card_schema": "2.0",
                },
                message_id="om_cap_card",
                form_value={"capacity_target_model": ""},
            )
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", side_effect=fake_get_session), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch.object(card_actions, "patch_interactive_card_sdk", side_effect=RuntimeError("200830 simulated")), \
                 patch.object(card_actions, "_handle_message_async_internal", side_effect=fake_resume):
                resp = card_actions.do_p2_card_action_trigger(evt)
                self.assertEqual(resp.toast.type, "success")
                self.assertNotIn("切换至 ，", resp.toast.content)
                harness.drain(timeout=2.0)
                time.sleep(0.1)
                self.assertEqual(len(resumed), 1, "Task resume must still run even when card PATCH fails")
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_schedule_bg_coro_releases_claim_lock_on_background_exception(self):
        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        try:
            key = "bg:rollback_lock"
            self.assertTrue(app_state.claim_card_action(key, ttl_seconds=3600.0))
            self.assertFalse(app_state.claim_card_action(key, ttl_seconds=3600.0))

            async def failing_bg():
                raise RuntimeError("transient backend failure")

            fut = app_state.schedule_bg_coro(failing_bg, label="TestBG", claim_key=key, release_on_error=True)
            fut.result(timeout=2.0)
            # Lock must be released so user can retry clicking the button
            self.assertTrue(app_state.claim_card_action(key, ttl_seconds=3600.0))
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_guest_request_auth_adapts_v2_card_and_v2_form_sanitization(self):
        from handlers import card_actions

        app_state.register_v2_card_message("om_guest_v2")
        evt = _make_card_event({"action": "guest_request_auth"}, message_id="om_guest_v2", open_id="ou_guest")
        with patch.object(card_actions, "ALLOWED_USERS", []), \
             patch.object(card_actions, "ALLOWED_CHATS", []), \
             patch.object(card_actions, "get_role", return_value="guest"), \
             patch("utils.auth.request_access", return_value="pending"):
            resp = card_actions.do_p2_card_action_trigger(evt)
            self.assertIsNotNone(resp.card)
            self.assertEqual(resp.card.data.get("schema"), "2.0")

        v1_unnamed_form = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "form",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "提交"},
                            "action_type": "form_submit",
                            "value": {"action": "demo_submit"},
                        }
                    ],
                }
            ],
        }
        v2 = convert_card_v1_to_v2(v1_unnamed_form, stream_element_id="", streaming_mode=False)
        self.assertNotIn("wide_screen_mode", v2["config"])
        form_el = v2["body"]["elements"][0]
        self.assertEqual(form_el.get("name"), "auto_form")
        self.assertEqual(form_el["elements"][0].get("name"), "form_submit_btn")

    def test_auth_action_runs_before_plugin_broadcast_and_unknown_action_warns(self):
        from handlers import card_actions
        import plugin_manager as pm_mod

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        try:
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(pm_mod.plugin_manager, "dispatch_card_action", new=AsyncMock(return_value=False)) as mock_pm, \
                 patch("handlers.auth_actions.is_admin", return_value=True), \
                 patch("handlers.auth_actions._refresh_panel", new=AsyncMock()):
                # 1. Auth action must be intercepted BEFORE plugin_manager broadcast
                auth_evt = _make_card_event({"action": "user_edit_cancel"}, message_id="om_auth_card")
                r_auth = card_actions.do_p2_card_action_trigger(auth_evt)
                self.assertEqual(r_auth.toast.type, "info")
                harness.drain(timeout=2.0)
                mock_pm.assert_not_called()

                # 2. Truly unknown / deprecated action must show warning toast instead of silent no-op
                unk_evt = _make_card_event({"action": "deprecated_removed_btn"}, message_id="om_old_card")
                r_unk = card_actions.do_p2_card_action_trigger(unk_evt)
                self.assertEqual(r_unk.toast.type, "warning")
                self.assertIn("失效", r_unk.toast.content)
        finally:
            app_state.main_loop = prev_loop
            harness.stop()

    def test_switch_model_does_not_reset_busy_active_session(self):
        from handlers import card_actions
        import session_pool as sp_mod

        harness = _BackgroundLoopHarness()
        loop = harness.start()
        prev_loop = app_state.main_loop
        app_state.main_loop = loop
        try:
            busy_sess = MagicMock()
            busy_sess.transport_name = "acp"
            busy_sess.is_alive.return_value = True
            busy_sess.lock = MagicMock()
            busy_sess.lock.locked.return_value = True

            evt = _make_card_event({"action": "switch_model", "model": "gemini-4-neon-high"}, message_id="om_m_busy")
            with patch.object(card_actions, "ALLOWED_USERS", []), \
                 patch.object(card_actions, "ALLOWED_CHATS", []), \
                 patch.object(card_actions, "get_role", return_value="admin"), \
                 patch.object(card_actions, "is_admin", return_value=True), \
                 patch.object(card_actions, "get_session_async", new=AsyncMock(return_value={"model": "old", "project": "默认"})), \
                 patch.object(card_actions, "save_session_async", new=AsyncMock()), \
                 patch("utils.model_health.get_model_health", return_value={"status": "healthy", "age_seconds": 1.0, "latency_ms": 120.0}), \
                 patch.object(sp_mod.session_pool, "get_active_session", return_value=busy_sess), \
                 patch.object(sp_mod.session_pool, "reset_session", new=AsyncMock()) as mock_reset:
                resp = card_actions.do_p2_card_action_trigger(evt)
                self.assertEqual(resp.toast.type, "success")
                mock_reset.assert_not_called()
        finally:
            app_state.main_loop = prev_loop
            harness.stop()


if __name__ == "__main__":
    unittest.main()
