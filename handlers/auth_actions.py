"""Admin auth-management card actions (extracted from card_actions.py)."""

import asyncio

from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

from card_builder import CardBuilder
from cards.auth import TIER_LABELS
from lark_client import patch_interactive_card_sdk, send_card_to_chat_async
from utils.auth import SCOPE_TIERS, is_admin, set_session_role
import app_state

_AUTH_ACTIONS = (
    "auth_approve", "auth_deny", "auth_ban",
    "user_action", "user_page", "user_edit", "user_set_tier", "user_edit_cancel",
)


async def _refresh_panel(card_message_id):
    """Rebuild and patch the admin management panel card."""
    from database import list_auth_sessions
    from utils.auth import start_display_name_refresh
    sessions = list_auth_sessions()
    task = start_display_name_refresh(sessions)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    except asyncio.TimeoutError:
        pass
    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_panel_card(sessions)),
    )


def handle_auth_card_action(action_value, chat_id, card_message_id):
    """Handle admin auth-management card actions.

    Returns a P2CardActionTriggerResponse if the action belongs to the auth
    domain (admin-only), otherwise returns None so the caller can continue
    dispatching other actions.
    """
    action = action_value.get("action")
    if action not in _AUTH_ACTIONS:
        return None

    if not is_admin(chat_id):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "仅管理员可执行此操作。"}})

    try:
        return _dispatch_auth_action(action_value, action, chat_id, card_message_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"操作处理失败：{e}"}})


def _dispatch_auth_action(action_value, action, chat_id, card_message_id):

    if action == "auth_approve":
        target_chat = action_value.get("chat_id", "")
        tier = action_value.get("tier", "basic")
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])

        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_approve():
                set_session_role(target_chat, "user", scopes, operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(True, f"✅ 授权成功（{tier_label}）。现在可以使用该机器人了。"),
                )
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_approve(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已授权（{tier_label}）"}})

    if action == "auth_deny":
        target_chat = action_value.get("chat_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_deny():
                set_session_role(target_chat, "guest", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "您的权限申请已被管理员拒绝。"),
                )
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_deny(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拒绝该申请"}})

    if action == "auth_ban":
        target_chat = action_value.get("chat_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_ban():
                set_session_role(target_chat, "banned", [], operator=chat_id)
                await send_card_to_chat_async(
                    target_chat,
                    CardBuilder.build_auth_result_card(False, "该会话已被管理员加入黑名单。"),
                )
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_ban(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拉黑该会话"}})

    if action == "user_action":
        op = action_value.get("op", "")
        target_chat = action_value.get("chat_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_user_action():
                if op == "revoke":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                elif op == "promote":
                    set_session_role(target_chat, "admin", [], operator=chat_id)
                elif op == "unban":
                    set_session_role(target_chat, "guest", [], operator=chat_id)
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_user_action(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "操作成功"}})

    if action == "user_edit":
        target_chat = action_value.get("chat_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_edit():
                from database import get_auth_session
                sess = get_auth_session(target_chat) or {"chat_id": target_chat}
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_edit_card(sess)),
                )
            asyncio.run_coroutine_threadsafe(do_edit(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在编辑该会话权限"}})

    if action == "user_set_tier":
        target_chat = action_value.get("chat_id", "")
        tier = action_value.get("tier", "basic")
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        set_session_role(target_chat, "user", scopes, operator=chat_id)
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_set_tier():
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_set_tier(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已设为{tier_label}"}})

    if action == "user_edit_cancel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_cancel_edit():
                await _refresh_panel(card_message_id)
            asyncio.run_coroutine_threadsafe(do_cancel_edit(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消编辑"}})

    if action == "user_page":
        page = int(action_value.get("page", 1))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_user_page():
                from database import list_auth_sessions
                from utils.auth import start_display_name_refresh
                sessions = list_auth_sessions()
                task = start_display_name_refresh(sessions)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: patch_interactive_card_sdk(
                        card_message_id,
                        CardBuilder.build_user_panel_card(sessions, page=page),
                    ),
                )
            asyncio.run_coroutine_threadsafe(do_user_page(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已翻页"}})

    return None
