import asyncio
import os
import time
import json
import uuid
import re
import subprocess
from config import ANTIGRAVITY_BIN, DANGEROUSLY_SKIP_PERMISSIONS, get_brain_dir, get_transcript_path
from logger import log
from card_builder import CardBuilder
from lark_client import patch_interactive_card_sdk, send_interactive_card_sdk, api_client
from multimodal import extract_and_upload_resources
from database import save_session_async
import stats

def extract_final_response_from_transcript(transcript_path, initial_size=0):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, 'r', encoding='utf-8', errors='ignore') as f:
            if initial_size > 0:
                try:
                    f.seek(initial_size)
                except Exception:
                    pass
            lines = f.readlines()
        if not lines:
            return None
            
        # 从本轮新增的行中反向查找，仅获取最后一个（即最新的）模型纯文字回复作为最终执行结果
        for line in reversed(lines):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
            except Exception:
                continue
            
            # 如果遇到用户输入，说明属于当前对话回合的日志结束了
            if data.get("type") == "USER_INPUT":
                break
                
            if data.get("source") == "MODEL" and data.get("type") == "PLANNER_RESPONSE":
                # 如果没有 tool_calls (为 None 或空列表)，说明是主模型的纯文字回复
                if not data.get("tool_calls"):
                    content = data.get("content", "")
                    if content and content.strip():
                        return content.strip()
                        
    except Exception as e:
        log.error(f"Failed to extract final response from transcript: {e}")
    return None

def extract_final_chinese_response(text):
    if not text:
        return ""
    
    # 1. 移除被 XML 标签包裹的思考过程与思维链，如 <thought>...</thought>, <thinking>...</thinking>, <think>...</think>
    text = re.sub(r'<(?:thought|thinking|think)>.*?</(?:thought|thinking|think)>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'^<(?:thought|thinking|think)>.*', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # 2. 移除开头的常见英文规划与描述前缀 (例如 "I will...", "Sure, I will...", "Let me...", "Here is...", "I need to...")
    text = re.sub(
        r'^(?:I\s+will|Sure,?\s+I\s+will|Let\s+me|Here\s+is|I\s+need\s+to|Based\s+on)\s+.*?\n\n',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

    # 3. 移除单行英文申明 (例如 "I will respond in Simplified Chinese.", "Sure, I will analyze the codebase in Chinese.")
    text = re.sub(
        r'^(?:I\s+will|Sure,?\s+I\s+will|Let\s+me)\s+(?:report|summarize|explain|respond|write|reply|communicate|answer|check|analyze)\b.+?(?:in\s+(?:Simplified\s+)?Chinese|below)\.?\s*',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

    # 4. 移除包含 Thinking Process, Thought:, Plan:, Thinking: 等小标题的说明段落
    text = re.sub(r'(?:\*\*|\#\#?\s*)?(?:Thinking Process|Thought|Thinking|Plan|Reasoning)(?:\*\*|:)?.*?(?=\n\n|\Z)', '', text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 5. 清理残留的动态思考占位符
    text = re.sub(r'\*\(\s*(?:🧠|🔍|⚙️|💡|🚀)?\s*正在.*?\)\*', '', text).strip()

    return text

async def execute_antigravity(
    chat_id, user_text, message_id, bot_reply_msg_id, session_data, 
    is_new_conversation, system_instruction, final_prompt, downloaded_file_name, 
    download_success, running_processes
):
    loop = asyncio.get_running_loop()
    
    async def _feishu_call(sync_fn, timeout=30.0, label="feishu_call"):
        """在线程池中执行同步飞书 SDK 调用，并加 30s 超时硬上限。
        防止网络黑洞（NAT 会话挂起 / 中间设备静默丢包）让主循环无限阻塞。
        超时或异常统一返回 None，调用方负责降级。"""
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, sync_fn),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            log.error(f"[Executor] {label} timed out after {timeout}s — network black-hole?")
            return None
        except Exception as e:
            log.error(f"[Executor] {label} failed: {e}")
            return None
    
    async def _sync_conversation_id_from_log(log_path):
        """从 antigravity 日志中解析 conversation ID 并同步落盘到 session_data。"""
        if not os.path.exists(log_path):
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                log_content = f.read()
            conv_not_found = "not found" in log_content and "conversation" in log_content
            match = re.search(r'(?:Created|found|Resuming|Loaded) conversation ([0-9a-fA-F-]+)', log_content)
            if match:
                new_conv_id = match.group(1)
                if session_data.get("conversation") != new_conv_id:
                    session_data["conversation"] = new_conv_id
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=3.0)
                    except (asyncio.TimeoutError, Exception) as e:
                        log.error(f"save_session_async timed out or failed: {e}")
            elif conv_not_found:
                if session_data.get("conversation") != "":
                    session_data["conversation"] = ""
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=3.0)
                    except (asyncio.TimeoutError, Exception) as e:
                        log.error(f"save_session_async timed out or failed: {e}")
        except Exception as e:
            log.error(f"Failed to sync conversation id from log: {e}")

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
        path = get_transcript_path(conv_id)
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
        await _feishu_call(
            lambda: patch_interactive_card_sdk(bot_reply_msg_id, init_card),
            label="init-card patch"
        )
    else:
        new_id = await _feishu_call(
            lambda: send_interactive_card_sdk(message_id, init_card),
            label="init-card send"
        )
        if new_id:
            bot_reply_msg_id = new_id
        else:
            log.warning(f"[Executor] Initial typing card send failed for chat {chat_id}; will fall back to sending final card as new message")
    
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
            path = get_transcript_path(conv_id)
            if os.path.exists(path):
                return path
        return None

    def fetch_current_action():
        t_path = target_transcript_path or get_latest_transcript_file()
        if not t_path or not os.path.exists(t_path):
            return ""
        try:
            with open(t_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            if not lines:
                return ""
            for line in reversed(lines[-50:]):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("type") == "USER_INPUT":
                        break
                    t_calls = data.get("tool_calls")
                    if t_calls and isinstance(t_calls, list) and len(t_calls) > 0:
                        last_call = t_calls[-1]
                        if isinstance(last_call, dict):
                            args = last_call.get("args", {})
                            name = last_call.get("name", "")
                            act = args.get("toolAction", "") or args.get("toolSummary", "")
                            if not act and name:
                                act = f"正在执行 {name}"
                            if isinstance(act, str) and act.strip():
                                clean_act = act.replace('"', '').strip()
                                if clean_act:
                                    return clean_act
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    
    last_update_text = ""
    last_tool_action = ""
    last_patch_time = time.time()
    current_patch_interval = 1.0
    process_start_time = time.time()
    # 活性检测：追踪 stdout 输出增长 + 工具动作变化
    # 如果两者都长时间无变化，说明子进程内部卡死（例如 antigravity 自身死锁），提前触发超时
    last_progress_time = process_start_time
    last_stdout_len = 0
    STALL_TIMEOUT = 300  # 5 分钟无进展视为卡死
    
    try:
        while process.returncode is None:
            await asyncio.sleep(0.5)
            
            # 尽早提取并同步主会话 ID (处理新创建会话、失效会话重建与 ID 变化)
            if os.path.exists(log_file_path):
                await _sync_conversation_id_from_log(log_file_path)
            
            # 实时从 transcript.jsonl 中获取最新的工具执行动作以更新状态指示卡片
            action = await loop.run_in_executor(None, fetch_current_action)
            
            # 活性检测：stdout 有新增数据 或 工具动作发生变化，都算"进展"
            current_stdout_len = len(accumulated_text)
            if current_stdout_len > last_stdout_len or (action and action != last_tool_action):
                last_progress_time = time.time()
                last_stdout_len = current_stdout_len
                if action:
                    last_tool_action = action
            
            think_seconds = int(time.time() - process_start_time)
            stall_seconds = int(time.time() - last_progress_time)
            
            # 活性超时：进程已运行至少 STALL_TIMEOUT 秒 且 连续 STALL_TIMEOUT 秒无进展
            # 第一个条件避免误杀刚启动、正在做长规划的任务
            if think_seconds >= STALL_TIMEOUT and stall_seconds >= STALL_TIMEOUT:
                log.error(f"[Executor] Process stalled (no stdout/tool progress for {stall_seconds}s) for chat {chat_id}, killing process group...")
                import signal
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception as ex:
                    log.error(f"Failed to kill stalled process: {ex}")
                stderr_text = (
                    f"⚠️ 任务无响应已被强制终止（已运行 {think_seconds // 60} 分钟，"
                    f"最后 {STALL_TIMEOUT // 60} 分钟无任何输出或工具动作变化）。\n"
                    "可能原因：Agent 内部死锁、底层命令静默挂起、或模型推理长时间无输出。"
                )
                break
            
            # 全局超时强杀防护 (30分钟超长无响应防挂死)
            if think_seconds > 1800:
                log.error(f"[Executor] Process timeout reached (1800s) for chat {chat_id}, killing process group...")
                import signal
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception as ex:
                    log.error(f"Failed to kill timed out process: {ex}")
                stderr_text = "⚠️ 执行超时 (30分钟)：后台任务运行时间过长，已被系统超时机制自动强行中断。"
                break

            display_action = action or last_tool_action
            if display_action:
                indicator_card = CardBuilder.build_tool_indicator(display_action, user_text, downloaded_file_name, download_success, think_seconds)
            else:
                indicator_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text, think_seconds)
            
            if time.time() - last_patch_time >= current_patch_interval:
                last_patch_time = time.time()
                if bot_reply_msg_id:
                    await _feishu_call(
                        lambda: patch_interactive_card_sdk(bot_reply_msg_id, indicator_card),
                        label="indicator patch"
                    )
                # 保持 1.0s 高刷新率，实时更新思考计时器与工具动作
                current_patch_interval = 1.0
                            
            if stdout_task.done() and stderr_task.done():
                break

        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning(f"[Executor] process.wait() timed out for chat {chat_id} (pid {process.pid}) — likely grandchild inherited pipe fd; force-killing process group")
            import signal
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as e:
                log.error(f"Failed to kill process group on wait timeout: {e}")
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                log.error(f"[Executor] process.wait() still hung after SIGKILL for chat {chat_id}; abandoning")
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
        finally:
            running_processes.pop(chat_id, None)
            # 子进程退出后显式关闭管道 transport，防止孙进程继承 fd 导致 read_stdout 永远不返回 EOF
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        transport = pipe._transport if hasattr(pipe, "_transport") else None
                        if transport is not None:
                            transport.close()
                    except Exception:
                        pass
        try:
            await asyncio.wait_for(stdout_task, timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("stdout_task timed out (possible pipe leak)")
        try:
            await asyncio.wait_for(stderr_task, timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("stderr_task timed out (possible pipe leak)")
        
        is_error = False

        # 优先从 transcript.jsonl 中提取干净的最终回复，过滤掉前面的中间思考过程与思维链
        transcript_path = target_transcript_path or await loop.run_in_executor(None, get_latest_transcript_file)
        final_reply = extract_final_response_from_transcript(transcript_path, initial_transcript_size)
        
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
        if transcript_path and os.path.exists(transcript_path):
            try:
                with open(transcript_path, 'r', encoding='utf-8') as f:
                    f.seek(initial_transcript_size)
                    for line in f.readlines():
                        try:
                            data = json.loads(line)
                            if data.get("type") == "GENERATE_IMAGE" or "generate_image" in line:
                                match = re.search(r'Generated image is saved at (.*?\.(?:jpg|png|jpeg))', data.get("content", ""))
                                if match:
                                    img_path = match.group(1)
                                    if img_path not in reply_text:
                                        reply_text += f"\n\n![Generated Image]({img_path})"
                        except Exception:
                            pass
            except Exception as e:
                log.error(f"Failed to extract generated images from transcript: {e}")
        
            active_project = session_data.get("project")
            ws_root = session_data.get("workspace_root")
            allowed_dirs = [active_project, ws_root]
            
            await _feishu_call(
                lambda: extract_and_upload_resources(reply_text, message_id, api_client, allowed_dirs),
                timeout=120.0,
                label="extract_and_upload_resources"
            )
            
            # 兜底：确保最新 conversation ID 正确写入 session_data
            if os.path.exists(log_file_path):
                await _sync_conversation_id_from_log(log_file_path)
            
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
            
        # 再次确保生成卡片前更新 session_data
        if os.path.exists(log_file_path):
            await _sync_conversation_id_from_log(log_file_path)
    
        final_card = CardBuilder.build_ai_response(
            reply_text, 
            choice_card_data=choice_card_data,
            current_model=session_data.get('model', 'Default'),
            current_project=session_data.get('project', '默认'),
            is_error=is_error,
            is_streaming=False,
            session_data=session_data
        )
        if bot_reply_msg_id:
            patch_ok = await _feishu_call(
                lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card),
                label="final-card patch"
            )
            if patch_ok is None:
                # patch 失败（超时/网络异常）→ 降级为发送纯文本回复，保证用户能看到结果
                from lark_client import send_reply_sdk
                fallback_text = reply_text[:2000] if reply_text else "(AI 回复卡片发送失败，请查看工作区获取完整内容)"
                await _feishu_call(
                    lambda: send_reply_sdk(message_id, fallback_text),
                    label="final-card text fallback"
                )
        else:
            await _feishu_call(
                lambda: send_interactive_card_sdk(message_id, final_card),
                label="final-card send"
            )
            
        return is_error
    
    finally:
        if os.path.exists(log_file_path):
            try:
                # [Last Resort Defense]: 彻底确认主会话 ID 或清除已失效 ID 并同步落盘
                await _sync_conversation_id_from_log(log_file_path)
            finally:
                try:
                    os.remove(log_file_path)
                except Exception:
                    pass

