"""Feishu WebSocket IM event entrypoints."""
import asyncio

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from config import ALLOWED_USERS, ALLOWED_CHATS
from logger import log
import app_state
from handlers.messages import handle_message_async


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if not data or not data.event or not data.event.message:
        log.warning("Received malformed message event")
        return
        
    # 最前端 Raw 物理日志打印，百分百捕捉 WebSocket 传入的一切数据包
    log.info(f"[RAW RECEIVE EVENT] message_id={data.event.message.message_id}, message_type={data.event.message.message_type}, content_raw={data.event.message.content}")
    
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    
    if not isinstance(content_raw, str):
        log.warning(f"Invalid content type received: {type(content_raw)}")
        return
    
    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        sender_id = data.event.sender.sender_id.open_id if data.event.sender and data.event.sender.sender_id else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
            
    if not is_allowed:
        log.warning(f"Unauthorized message event ignored. chat_id: {chat_id}, sender_id: {sender_id if 'sender_id' in locals() else None}")
        return
        
    if app_state.main_loop and app_state.main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), app_state.main_loop)
    else:
        log.error("main_loop is not running!")

