"""Per-chat async queue and single-task execution pipeline."""
import asyncio
import re

from database import delete_pending_task, get_session_async, get_profile_async, save_session_async
from card_builder import CardBuilder
from lark_client import send_interactive_card_sdk, set_emoji_sdk, delete_emoji_sdk
from executor import execute_antigravity
from logger import log
import stats
import app_state
from handlers.media import (
    _process_image_message,
    _process_post_message,
    _process_link_message,
    _process_file_audio_media_message,
    _process_batch_media_message,
)

# Local aliases so extracted code that referenced globals still works after light rewrite
running_processes = app_state.running_processes
chat_queues = app_state.chat_queues
chat_workers = app_state.chat_workers


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
                created_at = task.get("created_at")
                if created_at:
                    delete_pending_task(chat_id, created_at)
    except asyncio.CancelledError:
        log.info(f"Chat worker for {chat_id} was cancelled by /stop")
    finally:
        chat_workers.pop(chat_id, None)
        # 回收空闲的队列条目，防止 chat_queues 随 chat_id 数量单调增长
        # 仅在队列为空时 pop，避免与并发入队产生竞态
        q = chat_queues.get(chat_id)
        if q is not None and q.empty():
            chat_queues.pop(chat_id, None)


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
    bot_reply_msg_id = task.get("bot_reply_msg_id")
    is_resumed = bool(task.get("resumed"))

    if message_type == "text":
        user_text = raw_text
    elif message_type == "image":
        if is_resumed:
            user_text = raw_text
        else:
            user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_image_message(loop, message_id, content_json, content_raw)
    elif message_type == "post":
        if is_resumed:
            user_text = raw_text
        else:
            user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_post_message(loop, message_id, content_json)
    elif message_type == "link":
        if is_resumed:
            user_text = raw_text
        else:
            user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_link_message(content_json)
    elif message_type in ["file", "audio", "media"]:
        if is_resumed:
            user_text = raw_text
        else:
            user_text, downloaded_file_name, download_success, bot_reply_msg_id = await _process_file_audio_media_message(loop, message_id, message_type, content_json)
    elif message_type == "batch_media":
        if is_resumed:
            user_text = raw_text
        else:
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

    # Run plugin on_before_ai hooks
    from plugin_manager import plugin_manager
    user_text, session_data = await plugin_manager.dispatch_before_ai(user_text, chat_id, session_data)
    if not user_text or not user_text.strip():
        log.info(f"Message in chat {chat_id} was intercepted and consumed by plugin hook. Skipping AI execution.")
        return

    # Inject protocol into prompt
    current_proj = session_data.get("project", "默认")
    system_instruction = (
        "[System Rule: MUST ALWAYS communicate, reply, explain, and write responses in Simplified Chinese (简体中文). "
        "Any English text in the response must be limited to code syntax or technical names only. "
        "Absolute directive: NEVER output internal chain-of-thought, reasoning steps, planning commentary, or English preambles (such as 'I will...', 'Let me...'). "
        "Output ONLY your final answer directly in Simplified Chinese.]\n\n"
    )


    # 注入当前活跃项目环境参数
    system_instruction += f"[System Active Project Context]\n- Current active project workspace path is: {current_proj}\n- All file reads, writes, and analysis commands you execute should target this active workspace directory.\n\n"
    
    # 注入系统级防死锁与防护策略 (System Protection Guardrails)
    system_instruction += (
        "[System Execution & Safety Guardrails]\n"
        "1. 【全指令强制超时保护】：使用 `run_command` 工具执行任何 Shell 命令行（如 find, grep, pip, git, npm, python 等）时，必须在前缀显式包裹 `timeout <秒数>` 超时保护（例如：`timeout 30s find . -name '*.py'` 或 `timeout 60s pip install ...`），严禁执行任何未加 `timeout` 限制的命令。\n"
        "2. 【受限递归与大目录避让】：严禁在系统全盘（`/`）、根目录、用户主目录（`~`）或依赖目录（`venv`, `.venv`, `node_modules`, `.git`）中执行无限制的大范围递归搜索。搜索文件时必须使用精确路径、限定搜索深度（如 `find . -maxdepth 3`）并排除第三方依赖包目录。\n"
        "3. 【严禁自杀式重启自身服务】：严禁通过命令执行 `pm2 restart feishu-bot`、`pkill -f feishu_bot` 或任何重启/杀死当前机器人自身进程的操作！如修改了插件代码，插件支持热重载（通过 `/plugin reload`），严禁中断当前会话进程。\n"
        "4. 【交互式选项卡片规范】：当你的回答需要向用户提供多个方案/选项供选择、确认操作或询问用户意图时，必须在回答的末尾附带 `[CHOICE_CARD]` 标签生成可点击按钮，格式如下：\n"
        "[CHOICE_CARD]\n"
        "Q: 请选择您希望采用的方案：\n"
        "- 方案 1: 简短描述\n"
        "- 方案 2: 简短描述\n"
        "[/CHOICE_CARD]\n\n"
    )
    
    # 注入该项目专属 Prompt
    project_prompts = session_data.get("project_prompts", {})
    if current_proj in project_prompts and project_prompts[current_proj]:
        proj_prompt_text = project_prompts[current_proj]
        system_instruction += f"[Active Project Specific Rules & Description]\n{proj_prompt_text}\n\n"



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
    # 43200s (12小时) 总超时兜底：支持长达数小时至十几小时的超大型自动化工程任务
    # 超时时 CancelledError 会进入 execute_antigravity，其 finally 块仍会执行清理
    is_error = False
    try:
        is_error = await asyncio.wait_for(
            execute_antigravity(
                chat_id, user_text, message_id, bot_reply_msg_id, session_data, 
                is_new_conversation, system_instruction, final_prompt, downloaded_file_name, 
                download_success, running_processes, is_resumed=is_resumed, task_meta=task
            ),
            timeout=43200.0
        )
    except asyncio.TimeoutError:
        from logger import log
        log.error(f"[Pipeline] execute_antigravity hard timeout (43200s / 12h) for chat {chat_id}")
        is_error = True
    except Exception as e:
        from logger import log
        log.error(f"[Pipeline] execute_antigravity raised for chat {chat_id}: {e}")
        is_error = True
    
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
        reaction_id = await app_state.run_feishu_sync(loop, lambda: set_emoji_sdk(message_id, mapped_type))
        return reaction_id
    except Exception as e:
        log.error(f"Failed to set emoji reaction {emoji_type}: {e}")
        return None


async def delete_emoji(message_id, reaction_id):
    if not reaction_id:
        return
    loop = asyncio.get_running_loop()
    try:
        await app_state.run_feishu_sync(loop, lambda: delete_emoji_sdk(message_id, reaction_id))
    except Exception as e:
        log.error(f"Failed to delete emoji reaction: {e}")

# emoji_spinner removed
