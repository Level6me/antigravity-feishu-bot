import asyncio
import os
import time
import json
import uuid
import re
import subprocess
from config import ANTIGRAVITY_BIN
from logger import log
from card_builder import CardBuilder
from lark_client import patch_interactive_card_sdk, send_interactive_card_sdk, api_client
from multimodal import extract_and_upload_resources
from database import save_session_async

async def execute_antigravity(
    chat_id, user_text, message_id, bot_reply_msg_id, session_data, 
    is_new_conversation, system_instruction, final_prompt, downloaded_file_name, 
    download_success, running_processes
):
    loop = asyncio.get_running_loop()
    
    os.makedirs("logs", exist_ok=True)
    log_file_path = f"logs/agy_log_{uuid.uuid4().hex}.txt"
    cmd_args = [
        ANTIGRAVITY_BIN, 
        "-p", system_instruction + final_prompt, 
        "--dangerously-skip-permissions", 
        "--model", session_data["model"],
        "--print-timeout", "60m",
        "--log-file", log_file_path
    ]
    if not is_new_conversation:
        cmd_args.extend(["--conversation", session_data["conversation"]])
        
    target_transcript_path = None
    initial_transcript_size = 0
    if not is_new_conversation:
        conv_id = session_data["conversation"]
        path = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
        if os.path.exists(path):
            target_transcript_path = path
            initial_transcript_size = os.path.getsize(path)

    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    running_processes[chat_id] = process
    
    init_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text)
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

    def get_latest_transcript_file():
        if session_data.get("conversation"):
            conv_id = session_data["conversation"]
            path = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
            if os.path.exists(path):
                return path
        
        base_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain/")
        if not os.path.exists(base_dir):
            return None
        try:
            dirs = [os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
            if not dirs:
                return None
            newest_dir = max(dirs, key=os.path.getmtime)
            path = os.path.join(newest_dir, ".system_generated/logs/transcript.jsonl")
            if os.path.exists(path):
                return path
        except Exception:
            pass
        return None

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    
    last_update_text = ""
    last_tool_action = ""
    last_patch_time = time.time()
    process_start_time = time.time()
    
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
        else:
            if not accumulated_text.strip() and time.time() - last_patch_time >= 1.0:
                transcript_path = target_transcript_path or await loop.run_in_executor(None, get_latest_transcript_file)
                action = ""
                if transcript_path and os.path.exists(transcript_path):
                    try:
                        with open(transcript_path, 'r', encoding='utf-8') as f:
                            if transcript_path == target_transcript_path:
                                f.seek(initial_transcript_size)
                            lines = f.readlines()
                            if lines:
                                for line in reversed(lines):
                                    data = json.loads(line)
                                    if data.get("type") == "USER_INPUT":
                                        break
                                    if "tool_calls" in data and len(data["tool_calls"]) > 0:
                                        action = data["tool_calls"][-1].get("args", {}).get("toolAction", "").replace('"', '').strip()
                                        break
                    except Exception:
                        pass
                
                think_seconds = int(time.time() - process_start_time)
                if action:
                    last_tool_action = action
                    indicator_card = CardBuilder.build_tool_indicator(action, user_text, downloaded_file_name, download_success, think_seconds)
                else:
                    indicator_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text, think_seconds)
                
                last_patch_time = time.time()
                if bot_reply_msg_id:
                    await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, indicator_card))
                        
        if stdout_task.done() and stderr_task.done():
            break

    await process.wait()
    if chat_id in running_processes:
        del running_processes[chat_id]
    await stdout_task
    await stderr_task
    
    reply_text = accumulated_text.strip()
    reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
    reply_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', reply_text, flags=re.DOTALL).strip()
    
    await loop.run_in_executor(None, lambda: extract_and_upload_resources(reply_text, message_id, api_client))
    
    if os.path.exists(log_file_path):
        with open(log_file_path, "r") as f:
            log_content = f.read()
        match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
        if match:
            new_conv_id = match.group(1)
            if session_data.get("conversation") != new_conv_id:
                session_data["conversation"] = new_conv_id
                await save_session_async(chat_id, session_data)
        os.remove(log_file_path)
    
    is_error = False
    if not reply_text:
        reply_text = stderr_text.strip() or "Sorry, I couldn't generate a response."
        is_error = True

    choice_card_data = None
    if not is_error:
        choice_pattern = re.compile(r'\[CHOICE_CARD\]\s*Q:\s*(.*?)\n(.*?)\s*\[/CHOICE_CARD\]', re.DOTALL | re.IGNORECASE)
        match = choice_pattern.search(reply_text)
        if match:
            question = match.group(1).strip()
            options_text = match.group(2).strip()
            options = [opt.strip()[1:].strip() if opt.strip().startswith('-') else opt.strip() for opt in options_text.split('\n') if opt.strip()]
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
        
    return is_error
