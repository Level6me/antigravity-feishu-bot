"""Admin auth-management card actions (extracted from card_actions.py)."""

import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError

from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

from card_builder import CardBuilder
from cards.auth import TIER_LABELS
from lark_client import patch_interactive_card_sdk, send_card_to_chat_async
from logger import log
from utils.auth import SCOPE_TIERS, is_admin, set_session_role
import app_state

_AUTH_ACTIONS = (
    "auth_approve", "auth_deny", "auth_ban",
    "user_action", "user_page", "user_edit", "user_set_tier", "user_edit_cancel",
)


def _schedule_bg_name_patch(card_message_id: str, sessions: list, page: int = 1):
    """Refresh missing Feishu display names in background without blocking initial/callback card render."""
    if not card_message_id:
        return
    needs_lookup = any(
        not (s.get("display_name") or "").strip() or str(s.get("display_name", "")).startswith("oc_")
        for s in (sessions or [])
    )
    if not needs_lookup:
        return

    async def _bg():
        from database import list_auth_sessions
        from utils.auth import start_display_name_refresh
        before_names = {s.get("chat_id"): s.get("display_name") for s in (sessions or [])}
        task = start_display_name_refresh(sessions)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=4.0)
        except Exception:
            pass
        fresh = list_auth_sessions()
        after_names = {s.get("chat_id"): s.get("display_name") for s in fresh}
        if after_names != before_names:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: patch_interactive_card_sdk(
                    card_message_id, CardBuilder.build_user_panel_card(fresh, page=page)
                ),
            )

    asyncio.create_task(_bg())


async def _refresh_panel(card_message_id, page: int = 1):
    """Rebuild and patch the admin management panel card immediately using cached display names."""
    from database import list_auth_sessions
    sessions = list_auth_sessions()
    res = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: patch_interactive_card_sdk(
            card_message_id, CardBuilder.build_user_panel_card(sessions, page=page)
        ),
    )
    _schedule_bg_name_patch(card_message_id, sessions, page=page)
    return res


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
        claim_key = f"auth_req:{card_message_id}:{target_chat}" if card_message_id else ""
        if claim_key and not app_state.claim_card_action(claim_key, ttl_seconds=600.0):
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "该授权申请已在处理中，请勿重复点击"}})

        if not (app_state.main_loop and app_state.main_loop.is_running()):
            if claim_key:
                app_state.release_card_action(claim_key)
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def do_approve():
            await asyncio.to_thread(set_session_role, target_chat, "user", scopes, operator=chat_id)
            await send_card_to_chat_async(
                target_chat,
                CardBuilder.build_auth_result_card(True, f"✅ 授权成功（{tier_label}）。现在可以使用该机器人了。"),
            )
            await _refresh_panel(card_message_id)

        app_state.schedule_bg_coro(do_approve, label="AuthApprove", claim_key=claim_key, release_on_error=True)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已授权（{tier_label}）"}})

    if action == "auth_deny":
        target_chat = action_value.get("chat_id", "")
        claim_key = f"auth_req:{card_message_id}:{target_chat}" if card_message_id else ""
        if claim_key and not app_state.claim_card_action(claim_key, ttl_seconds=600.0):
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "该授权申请已在处理中，请勿重复点击"}})
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            if claim_key:
                app_state.release_card_action(claim_key)
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def do_deny():
            await asyncio.to_thread(set_session_role, target_chat, "guest", [], operator=chat_id)
            await send_card_to_chat_async(
                target_chat,
                CardBuilder.build_auth_result_card(False, "您的权限申请已被管理员拒绝。"),
            )
            await _refresh_panel(card_message_id)

        app_state.schedule_bg_coro(do_deny, label="AuthDeny", claim_key=claim_key, release_on_error=True)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拒绝该申请"}})

    if action == "auth_ban":
        target_chat = action_value.get("chat_id", "")
        claim_key = f"auth_req:{card_message_id}:{target_chat}" if card_message_id else ""
        if claim_key and not app_state.claim_card_action(claim_key, ttl_seconds=600.0):
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "该授权申请已在处理中，请勿重复点击"}})
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            if claim_key:
                app_state.release_card_action(claim_key)
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def do_ban():
            await asyncio.to_thread(set_session_role, target_chat, "banned", [], operator=chat_id)
            await send_card_to_chat_async(
                target_chat,
                CardBuilder.build_auth_result_card(False, "该会话已被管理员加入黑名单。"),
            )
            await _refresh_panel(card_message_id)

        app_state.schedule_bg_coro(do_ban, label="AuthBan", claim_key=claim_key, release_on_error=True)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已拉黑该会话"}})

    if action == "user_action":
        op = action_value.get("op", "")
        target_chat = action_value.get("chat_id", "")
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def do_user_action():
            if op == "revoke":
                await asyncio.to_thread(set_session_role, target_chat, "guest", [], operator=chat_id)
            elif op == "promote":
                await asyncio.to_thread(set_session_role, target_chat, "admin", [], operator=chat_id)
            elif op == "unban":
                await asyncio.to_thread(set_session_role, target_chat, "guest", [], operator=chat_id)
            await _refresh_panel(card_message_id)

        app_state.schedule_bg_coro(do_user_action, label="AuthUserAction")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "操作成功"}})

    if action == "user_edit":
        target_chat = action_value.get("chat_id", "")
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def do_edit():
            from database import get_auth_session
            sess = await asyncio.to_thread(get_auth_session, target_chat) or {"chat_id": target_chat}
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: patch_interactive_card_sdk(card_message_id, CardBuilder.build_user_edit_card(sess)),
            )

        app_state.schedule_bg_coro(do_edit, label="AuthUserEdit")
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在编辑该会话权限"}})

    if action == "user_set_tier":
        target_chat = action_value.get("chat_id", "")
        tier = action_value.get("tier", "basic")
        tier_label = TIER_LABELS.get(tier, TIER_LABELS["basic"])
        scopes = list(SCOPE_TIERS.get(tier, SCOPE_TIERS["basic"]))
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            set_session_role(target_chat, "user", scopes, operator=chat_id)
            return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已设为{tier_label}"}})

        async def do_set_tier():
            await asyncio.to_thread(set_session_role, target_chat, "user", scopes, operator=chat_id)
            await _refresh_panel(card_message_id)

        app_state.schedule_bg_coro(do_set_tier, label="AuthSetTier")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已设为{tier_label}"}})

    if action == "user_edit_cancel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_cancel_edit():
                await _refresh_panel(card_message_id)
            app_state.schedule_bg_coro(do_cancel_edit, label="AuthCancelEdit")
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消编辑"}})

    if action == "user_page":
        try:
            page = max(1, int(action_value.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        if not (app_state.main_loop and app_state.main_loop.is_running()):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

        async def _build_page():
            from database import list_auth_sessions
            sessions = await asyncio.to_thread(list_auth_sessions)
            new_card = CardBuilder.build_user_panel_card(sessions, page=page)
            _schedule_bg_name_patch(card_message_id, sessions, page=page)
            return new_card

        if asyncio._get_running_loop() is None:
            fut = asyncio.run_coroutine_threadsafe(_build_page(), app_state.main_loop)
            try:
                new_card = fut.result(timeout=1.4)
                from cards.common import adapt_card_for_message
                return P2CardActionTriggerResponse({
                    "card": {"type": "raw", "data": adapt_card_for_message(new_card, message_id=card_message_id)},
                    "toast": {"type": "success", "content": f"第 {page} 页"},
                })
            except FuturesTimeoutError:
                async def _late_page_patch():
                    c = await asyncio.wrap_future(fut)
                    if c and card_message_id:
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: patch_interactive_card_sdk(card_message_id, c)
                        )
                app_state.schedule_bg_coro(_late_page_patch, label="AuthUserPageLatePatch")
                return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在加载第 {page} 页..."}})
            except Exception as exc:
                log.error(f"[AuthActions] user_page failed: {exc}", exc_info=True)
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"翻页失败：{str(exc)[:80]}"}})

        async def do_user_page_loop():
            new_card = await _build_page()
            if card_message_id and new_card:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, new_card)
                )

        app_state.schedule_bg_coro(do_user_page_loop, label="AuthUserPageLoop")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已翻页"}})

    return None
