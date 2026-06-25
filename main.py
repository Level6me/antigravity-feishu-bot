import asyncio
import json
import subprocess
import os
import uuid
import re
import sys
import signal

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse

from config import APP_ID, APP_SECRET, SESSION_FILE, PROFILE_FILE, ANTIGRAVITY_BIN
from database import get_session_async, get_profile_async
from multimodal import extract_and_upload_resources
from lark_client import api_client, send_reply_sdk, send_interactive_card_sdk, patch_interactive_card_sdk, download_message_resource_sdk, set_emoji_sdk, delete_emoji_sdk
from commands import handle_slash_command
from logger import log
from card_builder import CardBuilder
from executor import execute_antigravity
from garbage_collection import garbage_collector
import time

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}

async def process_chat_queue(chat_id):
    queue = chat_queues[chat_id]
    while not queue.empty():
        task = await queue.get()
        try:
            await _process_single_task(chat_id, task)
        except Exception as e:
            log.error(f"Error processing queued task for {chat_id}: {e}")
        finally:
            queue.task_done()
    if chat_id in chat_workers:
        del chat_workers[chat_id]

async def _process_single_task(chat_id, task):
    message_id = task["message_id"]
    message_type = task["message_type"]
    content_json = task["content_json"]
    content_raw = task["content_raw"]
    raw_text = task["raw_text"]
    
    loop = asyncio.get_running_loop()
    session_data = await get_session_async(chat_id)
    downloaded_file_name = None
    download_success = True
    bot_reply_msg_id = None

    if message_type == "text":
        user_text = raw_text
    elif message_type == "image":
        image_key = content_json.get("image_key", "")
        if not image_key:
            match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
            if match:
                image_key = match.group(0)

        if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
            image_key = content_raw[8:-1]
        
        if image_key:
            os.makedirs("downloads", exist_ok=True)
            output_filename = f"downloads/img_{image_key}.jpg"
            
            dl_card = CardBuilder.build_download_indicator(os.path.basename(output_filename), "图片")
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
            
            output_path = os.path.abspath(output_filename)
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
            
            downloaded_file_name = os.path.basename(output_filename)
            download_success = success
            user_text = f"请查看这张图片并做出回应。图片路径: {output_path}"
        else:
            user_text = "[未获取到图片]"
    elif message_type == "post":
        texts = []
        image_keys = []
        for line in content_json.get("content", []):
            for elem in line:
                if elem.get("tag") == "text":
                    texts.append(elem.get("text", ""))
                elif elem.get("tag") == "img":
                    image_keys.append(elem.get("image_key", ""))
        
        user_text = " ".join(texts)
        if image_keys:
            image_key = image_keys[0]
            os.makedirs("downloads", exist_ok=True)
            output_filename = f"downloads/img_{image_key}.jpg"
            
            dl_card = CardBuilder.build_download_indicator("图片内容")
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
            
            output_path = os.path.abspath(output_filename)
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
            
            downloaded_file_name = os.path.basename(output_filename)
            download_success = success
            user_text += f"\n[附加图片路径: {output_path}]"
    elif message_type in ["file", "audio", "media"]:
        file_key = content_json.get("file_key", "")
        file_name = content_json.get("file_name", "")
        
        if file_key:
            if not file_name:
                if message_type == "audio":
                    file_name = f"audio_{file_key}.ogg"
                elif message_type == "media":
                    file_name = f"video_{file_key}.mp4"
                else:
                    file_name = f"file_{file_key}"
            
            if message_type == "media" and not file_name.lower().endswith(".mp4"):
                file_name = file_key + ".mp4"
            if message_type == "audio" and "." not in file_name:
                file_name = file_key + ".ogg"
            
            os.makedirs("downloads", exist_ok=True)
            output_filename = os.path.join("downloads", file_name)
            dl_card = CardBuilder.build_download_indicator(file_name, message_type)
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

            output_path = os.path.abspath(output_filename)
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, file_key, "file", output_path))
            
            downloaded_file_name = file_name
            download_success = success
            
            if message_type == "file":
                user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
            elif message_type == "audio":
                user_text = f"请仔细听这段语音内容（语音文件路径: {output_path}），并做出响应。"
            elif message_type == "media":
                user_text = f"请仔细观看这段视频内容（视频文件路径: {output_path}），并做出响应。"
        else:
            user_text = f"[未获取到{message_type}的资源键]"
    else:
        user_text = f"[暂不支持的消息类型: {message_type}]"

    if not user_text:
        return

    # Sessions ar    # Inject protocol into prompt
    system_instruction = "[System Rule: MUST ALWAYS communicate, reply, explain, and write responses in Simplified Chinese (简体中文). Any English text in the response must be limited to code syntax or technical names only. If you need the user to make a choice, format your options inside [CHOICE_CARD] Q: <Question> \n - <Option1> \n - <Option2> [/CHOICE_CARD] tags. NEVER ask normal text multi-choice questions. ONLY output plain text choices, avoid complex formatting inside choices.]\n\n"
    
    # Load long-term memory if this is a new conversation
    final_prompt = user_text
    is_new_conversation = not session_data.get("conversation")
    if is_new_conversation:
        memories = await get_profile_async(chat_id)
        if memories:
            memory_block = "\n".join([f"- {m}" for m in memories])
            final_prompt = f"[System Context: Please strictly follow the user's permanent preferences below:]\n{memory_block}\n\n[User's Message:]\n{user_text}"
            
    # Delegate execution to executor
    is_error = await execute_antigravity(
        chat_id, user_text, message_id, bot_reply_msg_id, session_data, 
        is_new_conversation, system_instruction, final_prompt, downloaded_file_name, 
        download_success, running_processes
    )
    
    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")




async def set_emoji(message_id, emoji_type):
    # Map custom / obsolete emojis to standard Lark emoji names
    mapping = {
        "StatusReading": "Typing",
        "CrossMark": "CrossMark",
        "DONE": "DONE"
    }
    mapped_type = mapping.get(emoji_type, emoji_type)
    
    loop = asyncio.get_running_loop()
    try:
        reaction_id = await loop.run_in_executor(None, lambda: set_emoji_sdk(message_id, mapped_type))
        return reaction_id
    except Exception as e:
        log.error(f"Failed to set emoji reaction {emoji_type}: {e}")
        return None

async def delete_emoji(message_id, reaction_id):
    if not reaction_id:
        return
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: delete_emoji_sdk(message_id, reaction_id))
    except Exception as e:
        log.error(f"Failed to delete emoji reaction: {e}")

# emoji_spinner removed

async def send_reply(message_id, reply_text):
    reply_proc = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "+messages-reply", 
        "--message-id", message_id,
        "--text", reply_text,
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await reply_proc.communicate()
    if reply_proc.returncode != 0:
        print(f"[Error send_reply] {stderr.decode()}", flush=True)

async def send_interactive_card(message_id, card_content):
    reply_proc = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "+messages-reply", 
        "--message-id", message_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card_content),
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await reply_proc.communicate()

async def handle_message_async(message_id, chat_id, message_type, content_raw):
    try:
        await _handle_message_async_internal(message_id, chat_id, message_type, content_raw)
    except Exception as e:
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
        raw_text = content_json.get("text", "") if content_json.get("text") else content_raw
        raw_text = raw_text.strip()

    # Load sessions early for slash commands
    session_data = await get_session_async(chat_id)

    # Handle slash commands first (this allows /stop to bypass the lock)
    if message_type == "text" and (raw_text.startswith("/") or session_data.get("pending_command")):
        handled, override_text = await handle_slash_command(raw_text, message_id, chat_id, session_data, running_processes, chat_queues)
        if handled:
            return
        if override_text:
            raw_text = override_text

    # QUEUEING SYSTEM
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        
    task_payload = {
        "message_id": message_id,
        "message_type": message_type,
        "content_json": content_json,
        "content_raw": content_raw,
        "raw_text": raw_text
    }
    
    if chat_id in chat_workers and not chat_workers[chat_id].done():
        qsize = chat_queues[chat_id].qsize()
        warning_msg = f"⏳ 收到！当前有任务正在执行，该请求已加入队列排队处理 (前方还有 {qsize + 1} 个任务)..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, warning_msg))
        await chat_queues[chat_id].put(task_payload)
        return
    else:
        # No worker running, put in queue and start worker
        await chat_queues[chat_id].put(task_payload)
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
        return


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), main_loop)
    else:
        log.error("main_loop is not running!")

def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    log.info(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id
    
    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        sessions = load_sessions()
        if chat_id not in sessions:
            sessions[chat_id] = {"model": new_model}
        else:
            sessions[chat_id]["model"] = new_model
        save_sessions(sessions)

        log.info(f"Switched model to {new_model} in chat {chat_id}")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已成功切换为 {new_model}！"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if main_loop and main_loop.is_running():
            async def notify_and_process():
                # Notify the user visually in the chat
                user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已发送给 AI 进行下一步处理...)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                
                # Send the choice to the LLM backend
                if choice.startswith("/"):
                    simulated_content = json.dumps({"text": choice})
                else:
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
    
    return P2CardActionTriggerResponse()

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")
    
    # Start background GC task
    gc_task = asyncio.create_task(garbage_collector())
    
    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .register_p2_card_action_trigger(do_p2_card_action_trigger) \
        .build()

    cli = lark.ws.Client(
        APP_ID, 
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG
    )
    
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)

def cleanup(signum, frame):
    log.warning("Gracefully shutting down... killing zombie processes")
    for process in running_processes.values():
        try:
            process.kill()
        except:
            pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    asyncio.run(main())
