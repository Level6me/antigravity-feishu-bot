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

from config import APP_ID, APP_SECRET, SESSION_FILE, PROFILE_FILE
from database import load_sessions, save_sessions, load_profiles, save_profiles
from multimodal import extract_and_upload_resources
from lark_client import api_client, send_reply_sdk, send_interactive_card_sdk, patch_interactive_card_sdk, download_message_resource_sdk
from commands import handle_slash_command
from logger import log
from card_builder import CardBuilder
import time

main_loop = None
running_processes = {}






async def set_emoji(message_id, emoji_type):
    process = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "reactions", "create", 
        "--message-id", message_id,
        "--data", f'{{"reaction_type":{{"emoji_type":"{emoji_type}"}}}}',
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    try:
        data = json.loads(stdout.decode())
        return data.get("data", {}).get("reaction_id")
    except:
        return None

async def delete_emoji(message_id, reaction_id):
    if not reaction_id: return
    process = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "reactions", "delete", 
        "--message-id", message_id,
        "--reaction-id", reaction_id,
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

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

    user_text = ""
    downloaded_file_name = None
    download_success = True

    if message_type == "text":
        user_text = content_json.get("text", "") if content_json.get("text") else content_raw
        user_text = user_text.strip()
    elif message_type == "image":
        image_key = content_json.get("image_key", "")
        if not image_key:
            match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
            if match:
                image_key = match.group(0)

        if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
            image_key = content_raw[8:-1]
        
        if image_key:
            output_filename = f"img_{image_key}.jpg"
            
            dl_card = CardBuilder.build_download_indicator(output_filename, "图片")
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
            
            output_path = os.path.abspath(output_filename)
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
            
            downloaded_file_name = output_filename
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
            output_filename = f"img_{image_key}.jpg"
            
            dl_card = CardBuilder.build_download_indicator("图片内容")
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
            
            output_path = os.path.abspath(output_filename)
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
            
            downloaded_file_name = output_filename
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
            
            # Force .mp4 for media to fix Gemini API unsupported mime type
            if message_type == "media" and not file_name.lower().endswith(".mp4"):
                file_name = file_key + ".mp4"
            if message_type == "audio" and "." not in file_name:
                file_name = file_key + ".ogg"
            
            dl_card = CardBuilder.build_download_indicator(file_name, message_type)
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

            output_path = os.path.abspath(file_name)
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

    sessions = load_sessions()
    if chat_id not in sessions:
        sessions[chat_id] = {"conversation": "", "model": "Gemini 3.5 Flash"}
    
    session_data = sessions[chat_id]

    handled, user_text = await handle_slash_command(user_text, message_id, chat_id, sessions, running_processes)
    if handled:
        return

    log.info(f"[User {chat_id}]: {user_text}")

    save_sessions(sessions)

    r_id = await set_emoji(message_id, "StatusReading")
    await asyncio.sleep(1)
    await delete_emoji(message_id, r_id)

    # Spinner removed; rely on typing indicator stream

    # Inject protocol into prompt
    system_instruction = "[System Rule: If you need the user to make a choice, format your options inside [CHOICE_CARD] Q: <Question> \n - <Option1> \n - <Option2> [/CHOICE_CARD] tags. NEVER ask normal text multi-choice questions. ONLY output plain text choices, avoid complex formatting inside choices.]\n\n"
    
    # Load long-term memory if this is a new conversation
    final_prompt = user_text
    is_new_conversation = not session_data.get("conversation")
    if is_new_conversation:
        profiles = load_profiles()
        memories = profiles.get(chat_id, [])
        if memories:
            memory_block = "\n".join([f"- {m}" for m in memories])
            final_prompt = f"[System Context: Please strictly follow the user's permanent preferences below:]\n{memory_block}\n\n[User's Message:]\n{user_text}"
            
    log_file_path = f"agy_log_{uuid.uuid4().hex}.txt"
    cmd_args = [
        "/Users/YOUR_USERNAME/.local/bin/antigravity", 
        "-p", system_instruction + final_prompt, 
        "--dangerously-skip-permissions", 
        "--model", session_data["model"],
        "--log-file", log_file_path
    ]
    if not is_new_conversation:
        cmd_args.extend(["--conversation", session_data["conversation"]])
    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    running_processes[chat_id] = process
    
    init_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success)
    if bot_reply_msg_id:
        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, init_card))
    else:
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, init_card))
    
    accumulated_text = ""
    stderr_text = ""
    
    async def read_stdout():
        nonlocal accumulated_text
        while True:
            chunk = await process.stdout.read(64)
            if not chunk:
                break
            accumulated_text += chunk.decode(errors='ignore')
            
    async def read_stderr():
        nonlocal stderr_text
        while True:
            chunk = await process.stderr.read(64)
            if not chunk:
                break
            stderr_text += chunk.decode(errors='ignore')

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    
    last_update_text = ""
    last_patch_time = time.time()
    
    while process.returncode is None:
        await asyncio.sleep(0.5)
        if accumulated_text != last_update_text:
            if time.time() - last_patch_time >= 1.0:
                last_update_text = accumulated_text
                last_patch_time = time.time()
                clean_text = re.sub(r'\[CHOICE_CARD\].*', '', accumulated_text, flags=re.DOTALL)
                clean_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', clean_text, flags=re.DOTALL)
                clean_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', clean_text)
                if clean_text.strip():
                    patch_card = CardBuilder.build_ai_response(
                        clean_text.strip(),
                        current_model=session_data.get('model', 'Default'),
                        current_role=session_data.get('role', '无'),
                        is_streaming=True
                    )
                    if bot_reply_msg_id:
                        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, patch_card))
        if stdout_task.done() and stderr_task.done():
            break

    await process.wait()
    if chat_id in running_processes:
        del running_processes[chat_id]
    await stdout_task
    await stderr_task
    
    # Spinner cancellation removed
    
    reply_text = accumulated_text.strip()
    reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
    reply_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', reply_text, flags=re.DOTALL).strip()
    
    # Auto-upload extracted files and images
    await asyncio.get_running_loop().run_in_executor(None, lambda: extract_and_upload_resources(reply_text, message_id, api_client))
    
    # Parse log file for conversation ID
    if os.path.exists(log_file_path):
        with open(log_file_path, "r") as f:
            log_content = f.read()
        match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
        if match:
            new_conv_id = match.group(1)
            if session_data.get("conversation") != new_conv_id:
                sessions[chat_id]["conversation"] = new_conv_id
                save_sessions(sessions)
        os.remove(log_file_path)

    
    is_error = False
    if not reply_text:
        reply_text = stderr_text.strip() or "Sorry, I couldn't generate a response."
        is_error = True

    # Parse for [CHOICE_CARD]
    choice_card_data = None
    if not is_error:
        choice_pattern = re.compile(r'\[CHOICE_CARD\]\s*Q:\s*(.*?)\n(.*?)\s*\[/CHOICE_CARD\]', re.DOTALL | re.IGNORECASE)
        match = choice_pattern.search(reply_text)
        if match:
            question = match.group(1).strip()
            options_text = match.group(2).strip()
            options = [opt.strip()[1:].strip() if opt.strip().startswith('-') else opt.strip() for opt in options_text.split('\n') if opt.strip()]
            
            # Remove the block from the text reply
            reply_text = choice_pattern.sub('', reply_text).strip()
            choice_card_data = {
                "question": question,
                "options": options
            }

    if reply_text:
        log.info(f"[Agent text]: {reply_text[:100]}...")

    final_card = CardBuilder.build_ai_response(
        reply_text, 
        choice_card_data=choice_card_data,
        current_model=session_data.get('model', 'Default'),
        current_role=session_data.get('role', '无'),
        is_error=is_error,
        is_streaming=False
    )
    
    if bot_reply_msg_id:
        await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card))
    else:
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, final_card))

    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")

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
                simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
    
    return P2CardActionTriggerResponse()

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")
    
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
