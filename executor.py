import asyncio
import os
import time
import json
import uuid
import re
import subprocess
from config import ANTIGRAVITY_BIN, DANGEROUSLY_SKIP_PERMISSIONS
from logger import log
from card_builder import CardBuilder
from lark_client import patch_interactive_card_sdk, send_interactive_card_sdk, api_client
from multimodal import extract_and_upload_resources
from database import save_session_async
import stats

def extract_final_response_from_transcript(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if not lines:
            return None
            
        responses = []
        # 从最后一行开始反向查找，收集所有的纯文字回复，直到遇到上一个用户的输入
        for line in reversed(lines):
            if not line.strip():
                continue
            data = json.loads(line)
            
            # 如果遇到用户输入，说明属于当前对话回合的日志结束了
            if data.get("type") == "USER_INPUT":
                break
                
            if data.get("source") == "MODEL" and data.get("type") == "PLANNER_RESPONSE":
                # 如果没有 tool_calls (为 None 或空列表)，说明是主模型的纯文字回复
                if not data.get("tool_calls"):
                    content = data.get("content", "")
                    if content.strip():
                        responses.append(content.strip())
                        
        if responses:
            # 因为是倒序收集的，所以拼接前需要反转顺序
            responses.reverse()
            return "\n\n".join(responses)
            
    except Exception as e:
        log.error(f"Failed to extract final response from transcript: {e}")
    return None

def extract_final_chinese_response(text):
    if not text:
        return ""
    
    # 移除常见的英文申明前缀
    text = re.sub(
        r'^(?:I\s+will|Sure,?\s+I\s+will|Let\s+me)\s+(?:report|summarize|explain|respond|write|reply|communicate|answer)\b.+?in\s+(?:Simplified\s+)?Chinese\.?\s*',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()
    
    # 移除被 XML 标签包裹的思考过程，如 <thought>...</thought> (防呆)
    text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    return text

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
        "--model", session_data["model"],
        "--print-timeout", "60m",
        "--log-file", log_file_path
    ]
    if DANGEROUSLY_SKIP_PERMISSIONS:
        cmd_args.append("--dangerously-skip-permissions")
        
    cwd_dir = None
    if session_data.get("project") and session_data["project"] not in ["默认", "Default"]:
        proj_val = session_data["project"]
        if os.path.isdir(proj_val):
            cwd_dir = proj_val
            cmd_args.extend(["--add-dir", proj_val])
        else:
            cmd_args.extend(["--project", proj_val])
        
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

    custom_env = os.environ.copy()
    custom_env["GIT_TERMINAL_PROMPT"] = "0"
    custom_env["DEBIAN_FRONTEND"] = "noninteractive"
    custom_env["GIT_ASKPASS"] = "echo"

    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        preexec_fn=os.setsid,
        cwd=cwd_dir,
        env=custom_env
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
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')()
        while True:
            chunk = await process.stdout.read(64)
            if not chunk:
                break
            accumulated_text += decoder.decode(chunk)
        accumulated_text += decoder.decode(b'', final=True)
            
    async def read_stderr():
        nonlocal stderr_text
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')()
        while True:
            chunk = await process.stderr.read(64)
            if not chunk:
                break
            stderr_text += decoder.decode(chunk)
        stderr_text += decoder.decode(b'', final=True)

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
    
    try:
        while process.returncode is None:
            await asyncio.sleep(0.5)
            
            # 尽早提取并保存新生成的会话 ID，防止因为执行过程中被 /stop 中断 (CancelledError) 导致新会话丢失
            if not session_data.get("conversation") and os.path.exists(log_file_path):
                try:
                    with open(log_file_path, "r") as f:
                        log_content = f.read()
                    match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
                    if match:
                        session_data["conversation"] = match.group(1)
                        await save_session_async(chat_id, session_data)
                except Exception:
                    pass
            
            # 实时从 transcript.jsonl 中获取最新的工具执行动作以更新状态指示器
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
            
            if time.time() - last_patch_time >= 1.0:
                last_patch_time = time.time()
                if bot_reply_msg_id:
                    await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, indicator_card))
                            
            if stdout_task.done() and stderr_task.done():
                break

        try:
            await process.wait()
        except Exception as e:
            log.error(f"Process wait error: {e}")
            import signal
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                log.warning(f"Process {process.pid} already exited when trying to terminate.")
            except Exception as e:
                log.error(f"Failed to kill process group {process.pid}: {e}")
                try:
                    process.kill()
                except Exception:
                    pass
        running_processes.pop(chat_id, None)
    await stdout_task
    await stderr_task
    
    # 优先从 transcript.jsonl 中提取干净的最终回复，过滤掉前面的中间思考过程与思维链
    transcript_path = target_transcript_path or await loop.run_in_executor(None, get_latest_transcript_file)
    final_reply = extract_final_response_from_transcript(transcript_path)
    
    if final_reply:
        reply_text = final_reply
    else:
        # 降级：如果提取失败，使用原本的 accumulated_text 逻辑进行兜底
        reply_text = accumulated_text.strip()
        reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
        reply_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', reply_text, flags=re.DOTALL).strip()
    
    # 彻底过滤多轮对话下的任何 English 规划段落，只留存最终中文回答
    reply_text = extract_final_chinese_response(reply_text)
    
    # Auto-inject images generated by generate_image tool during this run
    if target_transcript_path and os.path.exists(target_transcript_path):
        try:
            with open(target_transcript_path, 'r', encoding='utf-8') as f:
                f.seek(initial_transcript_size)
                for line in f.readlines():
                    data = json.loads(line)
                    if data.get("type") == "GENERATE_IMAGE" or "generate_image" in line:
                        match = re.search(r'Generated image is saved at (.*?\.(?:jpg|png|jpeg))', data.get("content", ""))
                        if match:
                            img_path = match.group(1)
                            if img_path not in reply_text:
                                reply_text += f"\n\n![Generated Image]({img_path})"
        except Exception as e:
            log.error(f"Failed to extract generated images from transcript: {e}")
    
        active_project = session_data.get("project")
        ws_root = session_data.get("workspace_root")
        allowed_dirs = [active_project, ws_root]
        
        await loop.run_in_executor(None, lambda: extract_and_upload_resources(reply_text, message_id, api_client, allowed_dirs))
        
        # 兜底：如果运行极快，提前读取了整个流程，在这里再次确保能够保存新生成的 conversation id
        if os.path.exists(log_file_path):
            with open(log_file_path, "r") as f:
                log_content = f.read()
            match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
            if match:
                new_conv_id = match.group(1)
                if session_data.get("conversation") != new_conv_id:
                    session_data["conversation"] = new_conv_id
                    await save_session_async(chat_id, session_data)
        
        is_error = False
    if not reply_text:
        reply_text = stderr_text.strip() or "Sorry, I couldn't generate a response."
        is_error = True
    else:
        # 统计整体 Agent 执行消耗：
        # 包含最终回复和用户输入
        approx_tokens = len(user_text) + len(reply_text)
        
        # 将 Agent 的底层思考和中间工具调用的过程一并计入耗损
        if transcript_path and os.path.exists(transcript_path):
            try:
                current_size = os.path.getsize(transcript_path)
                added_bytes = max(0, current_size - initial_transcript_size)
                # Better heuristic: 1 token ~ 1.5 chars for Chinese, JSON bytes inflated by ~2.5x vs actual tokens
                approx_tokens = int((len(user_text) + len(reply_text)) * 1.5 + added_bytes / 2.5)
            except Exception as e:
                log.error(f"Failed to calculate transcript token usage: {e}")
                
        stats.record_tokens(approx_tokens)

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
        current_project=session_data.get('project', '默认'),
        is_error=is_error,
        is_streaming=False
    )
        if bot_reply_msg_id:
            await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card))
            
        return is_error

    finally:
        if os.path.exists(log_file_path):
            try:
                # [Last Resort Defense]: Try to extract the conversation ID one last time 
                # in case the process was killed before the while loop could catch it (< 0.5s)
                with open(log_file_path, "r") as f:
                    log_content = f.read()
                match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
                if match:
                    new_conv_id = match.group(1)
                    if session_data.get("conversation") != new_conv_id:
                        session_data["conversation"] = new_conv_id
                        # Use create_task to schedule the DB save. 
                        # We specifically DO NOT use 'await' here to prevent asyncio.CancelledError 
                        # from aborting the execution of the subsequent os.remove cleanup.
                        asyncio.create_task(save_session_async(chat_id, session_data))
            except Exception as e:
                log.error(f"Failed to read/save conversation id in finally block: {e}")
            finally:
                try:
                    os.remove(log_file_path)
                except Exception:
                    pass
