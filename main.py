import asyncio
import json
import subprocess
import os
import uuid
import re
import sys
import signal

# Add local bin paths to PATH
home = os.path.expanduser("~")
os.environ["PATH"] += os.pathsep + os.path.join(home, ".npm-global/bin") + os.pathsep + os.path.join(home, ".local/bin")


import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse

from config import APP_ID, APP_SECRET, SESSION_FILE, PROFILE_FILE, ANTIGRAVITY_BIN, ALLOWED_USERS, ALLOWED_CHATS, BASE_DIR
from database import get_session_async, get_profile_async, save_session_async, get_session_sync, save_session_sync, save_profile_async
from multimodal import extract_and_upload_resources
from lark_client import api_client, send_reply_sdk, send_interactive_card_sdk, patch_interactive_card_sdk, download_message_resource_sdk, set_emoji_sdk, delete_emoji_sdk
from commands import handle_slash_command
from logger import log
from card_builder import CardBuilder
import stats
from executor import execute_antigravity
from garbage_collection import garbage_collector
import time

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}

async def process_chat_queue(chat_id):
    queue = chat_queues[chat_id]
    try:
        while not queue.empty():
            task = await queue.get()
            try:
                await _process_single_task(chat_id, task)
                stats.record_success()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                stats.record_failure()
                log.error(f"Error processing queued task for {chat_id}: {e}")
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        log.info(f"Chat worker for {chat_id} was cancelled by /stop")
    finally:
        chat_workers.pop(chat_id, None)

async def _process_image_message(loop, message_id, content_json, content_raw):
    image_key = content_json.get("image_key", "")
    if not image_key:
        match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
        if match:
            image_key = match.group(0)

    if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
        image_key = content_raw[8:-1]
    
    bot_reply_msg_id = None
    if image_key:
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator(os.path.basename(output_filename), "图片")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        return f"请查看这张图片并做出回应。图片路径: {output_path}", os.path.basename(output_filename), success, bot_reply_msg_id
    else:
        return "[未获取到图片]", None, True, None

async def _process_post_message(loop, message_id, content_json):
    texts = []
    image_keys = []
    for line in content_json.get("content", []):
        for elem in line:
            if elem.get("tag") == "text":
                texts.append(elem.get("text", ""))
            elif elem.get("tag") == "img":
                image_keys.append(elem.get("image_key", ""))
    
    user_text = " ".join(texts)
    bot_reply_msg_id = None
    downloaded_file_name = None
    download_success = True
    
    if image_keys:
        image_key = image_keys[0]
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator("图片内容")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        downloaded_file_name = os.path.basename(output_filename)
        user_text += f"\n[附加图片路径: {output_path}]"
        
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

async def _process_link_message(content_json):
    if isinstance(content_json, dict):
        user_text = content_json.get("url", content_json.get("href", ""))
    else:
        user_text = str(content_json)
    return user_text, None, True, None

async def _process_file_audio_media_message(loop, message_id, message_type, content_json):
    file_key = content_json.get("file_key", "") or content_json.get("media_key", "") or content_json.get("audio_key", "")
    file_name = content_json.get("file_name", "")
    bot_reply_msg_id = None
    download_success = True
    downloaded_file_name = None
    user_text = ""
    
    if file_key:
        if not file_name:
            if message_type == "audio":
                file_name = f"audio_{file_key}.ogg"
            elif message_type == "media":
                file_name = f"video_{file_key}.mp4"
            else:
                file_name = f"file_{file_key}"
        
        if message_type == "media" and not any(file_name.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v"]):
            file_name = f"{file_name}.mp4"
        if message_type == "audio" and "." not in file_name:
            file_name = f"{file_name}.ogg"
        
        # Purify file_name to prevent directory traversal
        file_name = os.path.basename(file_name)
        
        os.makedirs("downloads", exist_ok=True)
        output_filename = os.path.join("downloads", file_name)
        dl_card = CardBuilder.build_download_indicator(file_name, message_type)
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, file_key, "file", output_path))
        downloaded_file_name = file_name
        
        if message_type == "file":
            user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
        elif message_type == "audio":
            user_text = f"请仔细听这段语音内容（语音文件路径: {output_path}），并做出响应。"
        elif message_type == "media":
            user_text = f"请仔细观看这段视频内容（视频文件路径: {output_path}），并做出响应。"
            
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

async def _process_batch_media_message(loop, message_id, content_json):
    items = content_json.get("items", [])
    media_hints = []
    download_success = True
    
    # 批量下发资源加载指示器
    dl_card = CardBuilder.build_download_indicator(f"合并批处理 ({len(items)} 个文件)", "多媒体组")
    bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
    
    os.makedirs("downloads", exist_ok=True)
    
    for idx, item in enumerate(items):
        m_type = item["message_type"]
        c_json = item["content_json"]
        c_raw = item["content_raw"]
        
        file_key = ""
        file_name = ""
        if m_type == "image":
            file_key = c_json.get("image_key", "")
            if not file_key:
                match = re.search(r'img_[a-zA-Z0-9_\-]+', c_raw)
                if match:
                    file_key = match.group(0)
            file_name = f"batch_img_{idx}_{file_key}.jpg"
        else:
            file_key = c_json.get("file_key", "") or c_json.get("media_key", "") or c_json.get("audio_key", "")
            file_name = c_json.get("file_name", f"batch_file_{idx}_{file_key}")
            file_name = os.path.basename(file_name)
            
        if file_key:
            output_path = os.path.abspath(os.path.join("downloads", file_name))
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(item["message_id"], file_key, "image" if m_type == "image" else "file", output_path))
            if success:
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件路径: `{output_path}`")
            else:
                download_success = False
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件 `{file_name}` (下载失败)")
                
    user_text = f"请查看以下 {len(items)} 个关联多模态文件并做出综合关联回应：\n\n" + "\n".join(media_hints)
    downloaded_file_name = f"合并批处理 ({len(items)} 个文件)"
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

async def _process_single_task(chat_id, task):
    message_id = task["message_id"]
    message_type = task["message_type"]
    content_json = task["content_json"]
    content_raw = task["content_raw"]
    raw_text = task["raw_text"]
    
    loop = asyncio.get_running_loop()
    session_data = await get_session_async(chat_id)
    
    # 首次部署成功后的欢迎引导消息推送
    if not session_data.get("welcome_sent"):
        session_data["welcome_sent"] = True
        await save_session_async(chat_id, session_data)
        welcome_card = CardBuilder.build_welcome_card()
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, welcome_card))
        
    downloaded_file_name = None
    download_success = True
    bot_reply_msg_id = None

    if message_type == "text":
        user_text = raw_text
    elif message_type == "image":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_image_message(loop, message_id, content_json, content_raw)
    elif message_type == "post":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_post_message(loop, message_id, content_json)
    elif message_type == "link":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_link_message(content_json)
    elif message_type in ["file", "audio", "media"]:
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_file_audio_media_message(loop, message_id, message_type, content_json)
    elif message_type == "batch_media":
        user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_batch_media_message(loop, message_id, content_json)
    else:
        user_text = f"[暂不支持的消息类型: {message_type}]"

    if not user_text:
        return

    # 方案二：安全沙箱前置命令高危扫描过滤
    dangerous_patterns = [
        r"\brm\s+-rf\b",
        r"\bchmod\s+-(R\s+)?777\b",
        r"\bdd\s+if=\b",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r":\(\){\s*:\s*\|\s*:\s*&\s*}\s*;\s*:"
    ]
    is_dangerous = False
    for pattern in dangerous_patterns:
        if re.search(pattern, user_text, re.IGNORECASE):
            is_dangerous = True
            break
            
    if is_dangerous:
        warn_card = CardBuilder.build_security_warning(user_text)
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, warn_card))
        return

    # Inject protocol into prompt
    current_proj = session_data.get("project", "默认")
    system_instruction = (
        "[System Rule: MUST ALWAYS communicate, reply, explain, and write responses in Simplified Chinese (简体中文). "
        "Any English text in the response must be limited to code syntax or technical names only. "
        "Absolute directive: NEVER output internal chain-of-thought, reasoning steps, planning commentary, or English preambles (such as 'I will...', 'Let me...'). "
        "Output ONLY your final answer directly in Simplified Chinese. "
        "If you need the user to make a choice, format your options inside [CHOICE_CARD] Q: <Question> \n - <Option1> \n - <Option2> [/CHOICE_CARD] tags. "
        "NEVER ask normal text multi-choice questions. ONLY output plain text choices, avoid complex formatting inside choices.]\n\n"
    )


    # 注入当前活跃项目环境参数
    system_instruction += f"[System Active Project Context]\n- Current active project workspace path is: {current_proj}\n- All file reads, writes, and analysis commands you execute should target this active workspace directory.\n\n"
    
    # 注入系统级防死锁与防护策略 (System Protection Guardrails)
    system_instruction += (
        "[System Execution & Safety Guardrails]\n"
        "1. 【全指令强制超时保护】：使用 `run_command` 工具执行任何 Shell 命令行（如 find, grep, pip, git, npm, python 等）时，必须在前缀显式包裹 `timeout <秒数>` 超时保护（例如：`timeout 30s find . -name '*.py'` 或 `timeout 60s pip install ...`），严禁执行任何未加 `timeout` 限制的命令。\n"
        "2. 【受限递归与大目录避让】：严禁在系统全盘（`/`）、根目录、用户主目录（`~`）或依赖目录（`venv`, `.venv`, `node_modules`, `.git`）中执行无限制的大范围递归搜索。搜索文件时必须使用精确路径、限定搜索深度（如 `find . -maxdepth 3`）并排除第三方依赖包目录。\n\n"
    )
    
    # 注入该项目专属 Prompt
    project_prompts = session_data.get("project_prompts", {})
    if current_proj in project_prompts and project_prompts[current_proj]:
        proj_prompt_text = project_prompts[current_proj]
        system_instruction += f"[Active Project Specific Rules & Description]\n{proj_prompt_text}\n\n"

    # 自动注入项目隐秘追踪与凭据档案 (.project_track.secret.md)
    if current_proj and current_proj not in ["默认", "Default"]:
        from project_tracker import ensure_and_read_project_tracker
        tracker_text = ensure_and_read_project_tracker(current_proj)
        if tracker_text:
            system_instruction += (
                f"[Project Track & Confidential Credentials Archive / 本项目隐秘追踪与凭据档案]\n"
                f"{tracker_text}\n\n"
                f"【项目防护与防泄露安全准则】：\n"
                f"1. 上述凭据（服务器 IP、密码、Token、AppID、密钥等）与项目记录仅限在当前项目中隐秘使用，绝不可跨项目泄露。\n"
                f"2. 当你在项目中完成改动或解决问题时，可以主动维护更新当前项目的 `.project_track.secret.md` 文件（更新 TodoList、改动履历、废弃/保留的方案）。\n"
                f"3. 严禁使用 git 将 `.project_track.secret.md` 推送到远程 GitHub！该文件已被自动写入 .gitignore 防护。\n\n"
            )

    # 注入用户备忘录 Notes
    notes = session_data.get("notes", [])
    if notes:
        notes_block = "\n".join([f"- {note}" for note in notes])
        system_instruction += f"[User's Permanent Notes / 备忘录]\n{notes_block}\n\n"
    
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
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
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
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    await reply_proc.communicate()

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
        
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), main_loop)
    else:
        log.error("main_loop is not running!")

def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    log.info(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id
    
    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        sender_id = data.event.operator.operator_id.open_id if data.event.operator and data.event.operator.operator_id else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
            
    if not is_allowed:
        log.warning(f"Unauthorized card action ignored. chat_id: {chat_id}, operator_id: {sender_id if 'sender_id' in locals() else None}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "您无权操作此卡片！"}})
        
    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        if main_loop and main_loop.is_running():
            async def do_switch():
                session_data = await get_session_async(chat_id)
                old_model = session_data.get("model", "Default")
                session_data["model"] = new_model
                await save_session_async(chat_id, session_data)
                log.info(f"Switched model to {new_model} in chat {chat_id}")
                result_card = CardBuilder.build_model_switch_result_card(new_model, old_model)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, result_card))
            asyncio.run_coroutine_threadsafe(do_switch(), main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已切换为 {new_model}"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if main_loop and main_loop.is_running():
            async def notify_and_process():
                if choice.startswith("/"):
                    # For slash commands, directly call the command handler
                    # Use card_message_id as the reply target
                    session_data = await get_session_async(chat_id)
                    await handle_slash_command(choice, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                else:
                    # For regular choices, notify and send to LLM
                    user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已发送给 AI 进行下一步处理...)*"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
        
    elif action_value.get("action") == "browse_dir":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
            async def do_browse_dir():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_dir(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在载入目录..."}})
        
    elif action_value.get("action") == "select_project":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
            async def do_select_project():
                from project_tracker import ensure_and_read_project_tracker
                session_data = await get_session_async(chat_id)
                session_data["project"] = target_path
                
                # 1. 彻底清空上个项目的 LLM 会话内存上下文，实现跨项目记忆隔离
                session_data["conversation"] = ""
                
                # 2. 确保目标项目的 .gitignore 及隐秘追踪档案 (.project_track.secret.md) 初始化就绪
                ensure_and_read_project_tracker(target_path)
                
                # 记录最近使用的项目
                recent = session_data.get("recent_projects", [])
                if target_path in recent:
                    recent.remove(target_path)
                recent.insert(0, target_path)
                session_data["recent_projects"] = recent[:5]
                
                await save_session_async(chat_id, session_data)
                
                success_text = (
                    f"📂 **工作区项目切换成功！**\n\n"
                    f"当前已将活跃目录设定为：\n`{target_path}`\n\n"
                    f"✨ **已成功清空当前上下文信息。**"
                )
                success_card = CardBuilder.build_ai_response(
                    success_text,
                    current_model=session_data.get('model', 'Default'),
                    current_role=session_data.get('role', '无'),
                    current_project=target_path,
                    session_data=session_data
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(card_message_id, success_card))
            asyncio.run_coroutine_threadsafe(do_select_project(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目切换成功，已清空上下文信息，档案已载入！"}})

    elif action_value.get("action") == "remove_project_from_list":
        target_path = action_value.get("path")
        
        if main_loop and main_loop.is_running():
            async def do_remove_project():
                session_data = await get_session_async(chat_id)
                ignored = session_data.get("ignored_projects", [])
                if target_path not in ignored:
                    ignored.append(target_path)
                session_data["ignored_projects"] = ignored
                
                recent = session_data.get("recent_projects", [])
                if target_path in recent:
                    recent.remove(target_path)
                session_data["recent_projects"] = recent
                
                await save_session_async(chat_id, session_data)
                
                active_project = session_data.get("project", "默认")
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(
                    active_project, 
                    recent, 
                    recent_page=1, 
                    workspace_root=ws_root, 
                    ignored_projects=ignored
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_remove_project(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目已成功从列表中移出！"}})
    elif action_value.get("action") == "view_note_detail":
        idx = int(action_value.get("index"))
        if main_loop and main_loop.is_running():
            async def do_view_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    note_content = notes[idx]
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"📝 **笔记详情**:\\n{note_content}"))
            asyncio.run_coroutine_threadsafe(do_view_note(), main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "详情已发送到当前会话！"}})
        
    elif action_value.get("action") == "delete_note":
        idx = int(action_value.get("index"))
        
        if main_loop and main_loop.is_running():
            async def do_delete_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    removed = notes.pop(idx)
                    session_data["notes"] = notes
                    await save_session_async(chat_id, session_data)
                    log.info(f"Removed note: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_note_list_card(notes)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_delete_note(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功删除该条笔记！"}})
        
    elif action_value.get("action") == "clear_notes":
        if main_loop and main_loop.is_running():
            async def do_clear_notes():
                session_data = await get_session_async(chat_id)
                session_data["notes"] = []
                await save_session_async(chat_id, session_data)
                
                new_card = CardBuilder.build_note_list_card([])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_clear_notes(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "您的记事本已被全部清空！"}})
        
    elif action_value.get("action") == "refresh_status":
        if main_loop and main_loop.is_running():
            async def do_refresh_status():
                from commands import get_system_status_card_data
                cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = get_system_status_card_data()
                new_card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_status(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "状态已刷新！"}})

    elif action_value.get("action") == "refresh_quota":
        if main_loop and main_loop.is_running():
            async def do_refresh_quota():
                import re
                import urllib.request
                import ssl
                
                lsp_port = None
                quota_data = None
                try:
                    candidate_ports = set()
                    for pid_dir in os.listdir("/proc"):
                        if not pid_dir.isdigit():
                            continue
                        try:
                            with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                                cmdline = f.read().decode("utf-8", errors="ignore")
                            if "agy" not in cmdline and "antigravity" not in cmdline:
                                continue
                            log.info(f"[/refresh_quota] Found agy process pid={pid_dir}")
                            fd_dir = f"/proc/{pid_dir}/fd"
                            if not os.path.isdir(fd_dir):
                                log.info(f"[/refresh_quota] fd_dir {fd_dir} is not a dir")
                                continue
                                
                            found_any_socket = False
                            for fd in os.listdir(fd_dir):
                                try:
                                    link = os.readlink(f"{fd_dir}/{fd}")
                                    if "socket:" in link:
                                        found_any_socket = True
                                        inode = link.split("[")[1].rstrip("]")
                                        with open("/proc/net/tcp", "r") as tcp_f:
                                            for tcp_line in tcp_f:
                                                parts = tcp_line.strip().split()
                                                if len(parts) >= 10 and parts[9] == inode and parts[3] == "0A":
                                                    hex_port = parts[1].split(":")[1]
                                                    port = int(hex_port, 16)
                                                    candidate_ports.add(port)
                                                    log.info(f"[/refresh_quota] Found listen port {port} for pid {pid_dir}")
                                except Exception as e:
                                    log.warning(f"[/refresh_quota] Error processing fd {fd} for pid {pid_dir}: {e}")
                        except Exception as e:
                            log.warning(f"[/refresh_quota] Error processing pid {pid_dir}: {e}")
                    
                    if not candidate_ports:
                        try:
                            out = subprocess.check_output("/usr/bin/ss -tlnp", shell=True, text=True, timeout=3)
                            for line in out.split("\n"):
                                if 'users:(("agy"' in line or 'users:(("antigravity"' in line:
                                    match = re.search(r"127\.0\.0\.1:(\d+)", line)
                                    if match:
                                        candidate_ports.add(int(match.group(1)))
                        except Exception as e:
                            log.warning(f"[/refresh_quota] fallback ss failed: {e}")
                    
                    log.info(f"[/refresh_quota] Found candidate ports: {candidate_ports}")
                    context = ssl._create_unverified_context()
                    metadata_payload = b'{"metadata": {"ideName": "antigravity", "extensionName": "antigravity"}}'
                    
                    def probe_port(port):
                        url = f"https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
                        req = urllib.request.Request(url, data=metadata_payload, headers={"Content-Type": "application/json"}, method="POST")
                        try:
                            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                                data = json.loads(response.read().decode())
                                if "response" in data and "groups" in data["response"]:
                                    return port, data
                        except Exception as e:
                            log.warning(f"[/refresh_quota] Port {port} failed: {e}")
                        return port, None

                    import concurrent.futures
                    if candidate_ports:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, len(candidate_ports))) as executor:
                            futures = [executor.submit(probe_port, p) for p in candidate_ports]
                            for future in concurrent.futures.as_completed(futures):
                                p, data = future.result()
                                if data:
                                    log.info(f"[/refresh_quota] Port {p} responded successfully")
                                    quota_data = data
                                    lsp_port = p
                                    log.info(f"[/refresh_quota] Selected port {p}")
                                    break
                except Exception as e:
                    log.error(f"Error discovering LSP port in refresh: {e}")
                        
                if not quota_data:
                    token_path = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
                    if os.path.exists(token_path):
                        try:
                            with open(token_path, "r") as f:
                                token_info = json.load(f)
                            access_token = token_info["token"]["access_token"]
                            url = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
                            req = urllib.request.Request(
                                url,
                                data=b'{"project":"high-battery-8d2jw"}',
                                headers={
                                    "Authorization": f"Bearer {access_token}",
                                    "Content-Type": "application/json",
                                    "User-Agent": "antigravity/cli/1.1.3"
                                },
                                method="POST"
                            )
                            context = ssl._create_unverified_context()
                            with urllib.request.urlopen(req, context=context) as response:
                                api_data = json.loads(response.read().decode())
                                quota_data = {"response": api_data}
                        except Exception as e:
                            log.error(f"Error fetching remote quota fallback in refresh: {e}")
                new_card = CardBuilder.build_quota_card(quota_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_quota(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "额度已刷新！"}})

    elif action_value.get("action") == "forget_single_memory":
        idx = int(action_value.get("index"))
        
        if main_loop and main_loop.is_running():
            async def do_forget():
                memories = await get_profile_async(chat_id)
                if 0 <= idx < len(memories):
                    removed = memories.pop(idx)
                    await save_profile_async(chat_id, memories)
                    log.info(f"Removed memory preference: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_memory_card(memories)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_forget(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功擦除该偏好记录！"}})

    elif action_value.get("action") == "create_project_prompt":
        parent_path = action_value.get("parent_path")
        
        if main_loop and main_loop.is_running():
            async def do_create_project_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "create_project"
                session_data["create_project_parent"] = parent_path
                await save_session_async(chat_id, session_data)
                
                prompt_msg = f"📂 **请输入新建项目的名称，或直接输入项目的 Git 仓库地址**：\n\n*(支持通过 Git URL 克隆；若输入项目名，将在公共根目录 `{parent_path}` 下新建并初始化)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))
            asyncio.run_coroutine_threadsafe(do_create_project_prompt(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请输入项目名或Git仓库地址！"}})

    elif action_value.get("action") == "prompt_custom_project_path":
        if main_loop and main_loop.is_running():
            async def do_prompt_custom():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "custom_project_path"
                await save_session_async(chat_id, session_data)
                
                msg = "⚙️ **设置开发工作区**\n\n请直接回复您想要设定的开发工作区绝对路径（例如：`/home/jiang/github/my-app`）：\n\n*(系统收到后将自动校验路径合法性并为您切换工作区且清空上下文)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_custom(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复开发工作区绝对路径！"}})

    elif action_value.get("action") == "prompt_add_note":
        if main_loop and main_loop.is_running():
            async def do_prompt_note():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "note_add"
                await save_session_async(chat_id, session_data)
                
                msg = "📝 **添加笔记**\n\n请直接在此回复您要添加的笔记内容：\n\n*(提示：随时也可发送 `/note add <内容>` 进行快速添加)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_note(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复您要添加的笔记内容！"}})

    elif action_value.get("action") == "prompt_add_memory":
        if main_loop and main_loop.is_running():
            async def do_prompt_memory():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = "memory_add"
                await save_session_async(chat_id, session_data)
                
                msg = "🧠 **新增个人偏好**\n\n请直接在此回复您希望 AI 记住的偏好或习惯设定（例如：`写代码只用 Python` 或 `用中文回答`）：\n\n*(系统收到后将永久保存至您的个人偏好记忆库)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_memory(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复您的个人偏好设定！"}})

    elif action_value.get("action") == "browse_recent_page":
        target_path = action_value.get("current_path")
        target_page = action_value.get("page", 1)
        
        if main_loop and main_loop.is_running():
            async def do_browse_recent_page():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, target_page, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_recent_page(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"正在载入第 {target_page} 页项目..."}})
    
    return P2CardActionTriggerResponse()

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")
    
    # Send post-update notification if applicable
    pending_file = os.path.join(BASE_DIR, ".update_pending.json")
    if os.path.exists(pending_file):
        try:
            with open(pending_file, "r") as f:
                data = json.load(f)
            os.remove(pending_file)
            msg_id = data.get("message_id")
            if msg_id:
                from commands import get_version_string
                v_str = get_version_string("HEAD")
                text = f"✨ 升级完毕！系统已成功重新上线。\n当前运行版本：{v_str}"
                # Send the notification in a background task so it doesn't block startup
                asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, text))
                log.info(f"Sent post-update notification to {msg_id}")
        except Exception as e:
            log.error(f"Failed to process post-update notification: {e}")
            
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
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.error(f"Failed to kill process group {process.pid}: {e}")
            try:
                process.kill()
            except Exception:
                pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    asyncio.run(main())
