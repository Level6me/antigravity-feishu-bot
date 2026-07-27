"""Message intake, slash-command routing, and media batch debounce."""
import asyncio
import re

from commands import handle_slash_command
from database import get_session_async
from lark_client import send_reply_sdk
from logger import log
import stats
import app_state
from handlers.pipeline import process_chat_queue

running_processes = app_state.running_processes
chat_queues = app_state.chat_queues
chat_workers = app_state.chat_workers
chat_media_batches = app_state.chat_media_batches


async def handle_message_async(message_id, chat_id, message_type, content_raw):
    try:
        stats.record_request()
        await _handle_message_async_internal(message_id, chat_id, message_type, content_raw)
    except Exception as e:
        stats.record_failure()
        import traceback
        log.error(f"[FATAL ERROR in handle_message_async]: {e}")
        traceback.print_exc()


async def _handle_message_async_internal(message_id, chat_id, message_type, content_raw):
    loop = asyncio.get_running_loop()
    bot_reply_msg_id = None

    try:
        content_json = json.loads(content_raw)
    except Exception as e:
        log.error(f"Failed to parse content_raw JSON: {e}")
        return

    # Quick parsing for slash commands
    raw_text = ""
    if message_type == "text":
        if isinstance(content_json, dict):
            raw_text = content_json.get("text", "") if content_json.get("text") else content_raw
        else:
            raw_text = str(content_json)
        raw_text = raw_text.strip()
    elif message_type == "post":
        # 兼容飞书将 URL 或富文本转换为 post 的行为，优先抽取 a 标签的 href 真实的 URL，避免友好文本屏蔽 URL
        texts = []
        if isinstance(content_json, dict):
            for line in content_json.get("content", []):
                for elem in line:
                    if elem.get("tag") == "text":
                        texts.append(elem.get("text", ""))
                    elif elem.get("tag") == "a":
                        texts.append(elem.get("href", elem.get("text", "")))
        raw_text = " ".join(texts).strip()
    elif message_type == "link":
        if isinstance(content_json, dict):
            raw_text = content_json.get("url", content_json.get("href", ""))
        else:
            raw_text = str(content_json)
        raw_text = raw_text.strip()

    # Load sessions early for slash commands
    session_data = await get_session_async(chat_id)
    log.info(f"Message received: chat_id={chat_id}, message_type={message_type}, raw_text='{raw_text}', pending_command='{session_data.get('pending_command')}'")

    # Handle slash commands first (this allows /stop to bypass the lock)
    import re
    cleaned_text = raw_text
    if isinstance(cleaned_text, str):
        cleaned_text = re.sub(r'^<at\s+user_id="[^"]*">[^<]*</at>\s*', '', cleaned_text).strip()
        cleaned_text = re.sub(r'^@\S+\s*', '', cleaned_text).strip()
        cleaned_text = cleaned_text.lstrip('\ufeff\u200b\u200c\u200d\u00a0').strip()

    if session_data.get("pending_command") or (cleaned_text.startswith("/") and message_type in ["text", "post", "link"]):
        handled, override_text = await handle_slash_command(cleaned_text, message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
        if handled:
            stats.record_success()
            return
        if override_text:
            raw_text = override_text

    # 辅助任务分发函数
    async def dispatch_task(c_id, msg_id, m_type, c_json, c_raw, r_text):
        if c_id not in chat_queues:
            chat_queues[c_id] = asyncio.Queue()
            
        task_payload = {
            "message_id": msg_id,
            "message_type": m_type,
            "content_json": c_json,
            "content_raw": c_raw,
            "raw_text": r_text
        }
        
        if c_id in chat_workers and not chat_workers[c_id].done():
            qsize = chat_queues[c_id].qsize()
            warning_msg = f"⏳ 收到！当前有任务正在执行，该请求已加入队列排队处理 (前方还有 {qsize + 1} 个任务)..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, warning_msg))
            await chat_queues[c_id].put(task_payload)
        else:
            await chat_queues[c_id].put(task_payload)
            chat_workers[c_id] = asyncio.create_task(process_chat_queue(c_id))

    # 方案四：多模态多图合并批处理防抖机制
    if message_type in ["image", "file", "audio", "media"]:
        if chat_id not in chat_media_batches:
            chat_media_batches[chat_id] = {
                "items": [],
                "timer_task": None
            }
            
        batch = chat_media_batches[chat_id]
        batch["items"].append({
            "message_id": message_id,
            "message_type": message_type,
            "content_json": content_json,
            "content_raw": content_raw
        })
        
        if batch["timer_task"] and not batch["timer_task"].done():
            batch["timer_task"].cancel()
            
        async def delay_dispatch():
            try:
                # Dynamic debounce: 1.5s + 0.1s per item, up to 3.0s max
                delay = min(1.5 + len(batch["items"]) * 0.1, 3.0)
                await asyncio.sleep(delay)
                items = batch["items"]
                chat_media_batches.pop(chat_id, None)
                
                if len(items) == 1:
                    single = items[0]
                    await dispatch_task(
                        chat_id, single["message_id"], single["message_type"], 
                        single["content_json"], single["content_raw"], raw_text
                    )
                else:
                    target_msg_id = items[-1]["message_id"]
                    await dispatch_task(
                        chat_id, target_msg_id, "batch_media", 
                        {"items": items}, "", ""
                    )
            except asyncio.CancelledError:
                pass
                
        batch["timer_task"] = asyncio.create_task(delay_dispatch())
        return
        
    # 普通非媒体消息直接分发
    await dispatch_task(chat_id, message_id, message_type, content_json, content_raw, raw_text)


