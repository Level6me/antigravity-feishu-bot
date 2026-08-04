"""Feishu WebSocket IM event entrypoints."""
import asyncio
import json
import time
from collections import OrderedDict

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from config import ALLOWED_USERS, ALLOWED_CHATS
from database import get_auth_session, mark_message_seen, save_auth_session
from logger import log
import app_state
from handlers.messages import handle_message_async
from card_builder import CardBuilder
from lark_client import (
    send_card_to_chat_async,
    send_text_to_chat_async,
)
from utils.auth import (
    allow_message,
    get_admin_chat_id,
    get_role,
    is_bootstrapped,
    request_access,
    try_bootstrap_admin,
)


async def _resolve_display_name(chat_id, chat_type, sender_open_id):
    from utils.auth import resolve_display_name as _resolve
    return await _resolve(chat_id, chat_type, sender_open_id)


async def _handle_auth_request(chat_id, chat_type, sender_open_id, message_text):
    """Guest /auth flow: persist request, resolve name, notify admin."""
    status = request_access(chat_id, chat_type, sender_open_id, message_text)
    if status == "ok":
        display = await _resolve_display_name(chat_id, chat_type, sender_open_id)
        if display:
            sess = get_auth_session(chat_id) or {}
            sess["display_name"] = display
            save_auth_session(sess)

        admin_id = get_admin_chat_id()
        if admin_id:
            sess = get_auth_session(chat_id) or {}
            await send_card_to_chat_async(admin_id, CardBuilder.build_auth_request_card(sess))
            log.info(f"[auth] access request from {chat_id} notified admin {admin_id}")
        await send_text_to_chat_async(chat_id, "📨 已向管理员发送授权申请，请等待审批。")
    elif status == "rate":
        await send_text_to_chat_async(chat_id, "⏳ 申请过于频繁，请 10 分钟后再试。")
    elif status == "already":
        await send_text_to_chat_async(chat_id, "✅ 当前会话已授权，无需重复申请。")
    # admin / banned: 静默


def _extract_text(message_type, content_raw):
    if message_type == "text" and isinstance(content_raw, str):
        try:
            parsed = json.loads(content_raw)
            if isinstance(parsed, dict):
                return (parsed.get("text") or "").strip()
        except Exception:
            pass
    return ""


async def _handle_guest_message(chat_id, chat_type, sender_open_id, role, message_type, content_raw):
    """Silent mode for guests/pending chats:
    - /auth triggers an access request
    - pending chats stay fully silent while awaiting approval
    - guests get a one-time hint per 24h, then silent"""
    text = _extract_text(message_type, content_raw)
    if text.startswith("/auth"):
        await _handle_auth_request(chat_id, chat_type, sender_open_id, text)
        return
    if role == "pending":
        return

    now = int(time.time())
    sess = get_auth_session(chat_id) or {}
    last_hint = sess.get("last_hint_at") or 0
    if now - last_hint >= 86400:
        sess["chat_id"] = chat_id
        sess["chat_type"] = chat_type
        sess["sender_open_id"] = sender_open_id
        sess["last_hint_at"] = now
        sess["updated_at"] = now
        save_auth_session(sess)
        await send_card_to_chat_async(chat_id, CardBuilder.build_auth_hint_card())

# 有界 LRU 集合：防止飞书 WebSocket 重连重发导致同一条消息被处理两次
# 1000 条足够覆盖网络抖动窗口内的消息量，内存占用可忽略
_SEEN_MESSAGE_IDS = OrderedDict()
_SEEN_MESSAGE_IDS_MAX = 1000


def _mark_seen(message_id: str, chat_id: str, create_time=None) -> bool:
    """内存 LRU + SQLite 双层去重：先查内存，再落库（12h 窗口，重启后仍生效）。"""
    if message_id in _SEEN_MESSAGE_IDS:
        _SEEN_MESSAGE_IDS.move_to_end(message_id)
        return False
    _SEEN_MESSAGE_IDS[message_id] = None
    if len(_SEEN_MESSAGE_IDS) > _SEEN_MESSAGE_IDS_MAX:
        _SEEN_MESSAGE_IDS.popitem(last=False)
    return mark_message_seen(message_id, chat_id, create_time)


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if not data or not data.event or not data.event.message:
        log.warning("Received malformed message event")
        return
        
    # 最前端事件日志：只记录元信息，不记录消息正文，避免隐私内容落盘
    log.info(f"[RAW RECEIVE EVENT] message_id={data.event.message.message_id}, message_type={data.event.message.message_type}, chat_id={data.event.message.chat_id}")
    
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    chat_type = data.event.message.chat_type or "p2p"
    sender_open_id = None
    if data.event.sender and data.event.sender.sender_id:
        sender_open_id = data.event.sender.sender_id.open_id
    sender_open_id = sender_open_id or ""
    
    create_time = data.event.message.create_time
    if create_time:
        create_time = int(create_time) // 1000
    if not _mark_seen(message_id, chat_id, create_time):
        log.warning(f"[DEDUP] Ignoring duplicate message_id={message_id} chat_id={chat_id}")
        return
    
    if not isinstance(content_raw, str):
        log.warning(f"Invalid content type received: {type(content_raw)}")
        return
    
    # Legacy whitelist (if configured): non-matching chats stay blocked.
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        if ALLOWED_USERS and sender_open_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
        if not is_allowed:
            log.warning(f"Unauthorized message event ignored. chat_id: {chat_id}, sender_id: {sender_open_id}")
            return

    # Bootstrap: bind the first p2p chat as admin.
    if not is_bootstrapped():
        if try_bootstrap_admin(chat_id, chat_type):
            log.info(f"[auth] Admin bound to chat {chat_id}")
            async def _admin_welcome():
                await send_card_to_chat_async(chat_id, CardBuilder.build_admin_welcome_card())
            if app_state.main_loop and app_state.main_loop.is_running():
                asyncio.run_coroutine_threadsafe(_admin_welcome(), app_state.main_loop)
        else:
            # No admin yet and this is not a bindable p2p chat → silent.
            log.info(f"[auth] Bootstrap pending; ignoring message from chat {chat_id}")
            return

    # Permission gate
    role = get_role(chat_id, sender_open_id)
    if role == "banned":
        log.info(f"[auth] Banned chat {chat_id} message ignored")
        return
    if role in ("guest", "pending"):
        async def _guest_async():
            await _handle_guest_message(chat_id, chat_type, sender_open_id, role, message_type, content_raw)
        if app_state.main_loop and app_state.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_guest_async(), app_state.main_loop)
        return

    # Authorized chats: apply rate limiting (admins exempt).
    if role == "user" and not allow_message(chat_id):
        async def _rate_hint():
            await send_card_to_chat_async(chat_id, CardBuilder.build_rate_limit_card())
        if app_state.main_loop and app_state.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_rate_hint(), app_state.main_loop)
        return
        
    if app_state.main_loop and app_state.main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), app_state.main_loop)
    else:
        log.error("main_loop is not running!")
