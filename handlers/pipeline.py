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
        try:
            from plugin_manager import plugin_manager
            await plugin_manager.dispatch_task_stop(chat_id)
        except Exception as e:
            log.error(f"Error dispatching task stop on worker cancellation: {e}")
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

    # === System One (Jev) Decision Gateway & Guardrail ===
    from system_one_gate import evaluate_message_gate
    from plugin_manager import plugin_manager

    decision = await evaluate_message_gate(user_text)
    if not decision.is_fallback:
        session_data["typesafe_decision_count"] = 1
    else:
        session_data["typesafe_decision_count"] = 0

    # 1. 安全沙箱门禁拦截（基于 TypeSafe Noul 概率评判与置信度兜底）
    if decision.is_dangerous:
        log.warning(f"[Security] Intercepted dangerous request: '{user_text}' (prob={decision.danger_prob:.2f})")
        warn_card = CardBuilder.build_security_warning(user_text)
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, warn_card))
        return

    # 2. 插件极速直达通道 (Fast-Path / 零模型冷启动延迟)
    if decision.intent == "typesafe_status" and decision.intent_confidence >= 0.65:
        log.info(f"[TypeSafe FastPath] Direct dispatch typesafe config inquiry for chat {chat_id}")
        from system_one_gate import get_typesafe_config_state, test_typesafe_connectivity
        cfg = get_typesafe_config_state()
        res = await test_typesafe_connectivity()
        ts_card = CardBuilder.build_typesafe_config_card(
            api_key=cfg["api_key"],
            enabled=cfg["enabled"],
            model=cfg["model"],
            base_url=cfg["base_url"],
            test_result=res,
            tier=cfg.get("tier")
        )
        await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, ts_card))
        return

    elif decision.intent == "server_health" and decision.intent_confidence >= 0.70:
        health_plugin = plugin_manager.plugins.get("server_health")
        if health_plugin and health_plugin.enabled:
            log.info(f"[TypeSafe FastPath] Direct dispatch to server_health plugin for chat {chat_id}")
            handled = await health_plugin.on_command("/health", "", chat_id, message_id, session_data)
            if handled:
                return

    elif decision.intent == "notes" and decision.intent_confidence >= 0.75:
        notes_plugin = plugin_manager.plugins.get("notes_manager")
        if notes_plugin and notes_plugin.enabled:
            # 判断是查看备忘还是新增备忘
            is_view_notes = any(kw in user_text for kw in ["查看", "列出", "显示", "所有备忘", "所有笔记", "查备忘", "查笔记"])
            if is_view_notes:
                log.info(f"[TypeSafe FastPath] Direct dispatch note listing to notes_manager for chat {chat_id}")
                handled = await notes_plugin.on_command("/notes", "", chat_id, message_id, session_data)
                if handled:
                    return
            else:
                # 提取笔记文本
                cleaned_note = re.sub(r'^(请帮我|帮我|请)?(记录|记一下|记下|添加|新增|写下)?(一条)?(备忘|笔记|待办)?[:：\s]*', '', user_text).strip()
                if cleaned_note:
                    log.info(f"[TypeSafe FastPath] Direct dispatch add note to notes_manager for chat {chat_id}")
                    handled = await notes_plugin.on_command("/note", f"add {cleaned_note}", chat_id, message_id, session_data)
                    if handled:
                        return

    elif decision.intent == "cron" and decision.intent_confidence >= 0.75:
        cron_plugin = plugin_manager.plugins.get("cron_scheduler")
        if cron_plugin and cron_plugin.enabled:
            try:
                from plugins.cron_scheduler.scheduler import parse_schedule_intent
                if parse_schedule_intent(user_text):
                    log.info(f"[TypeSafe FastPath] Direct dispatch schedule intent to cron_scheduler for chat {chat_id}")
                    handled = await cron_plugin.on_command("/cron", user_text, chat_id, message_id, session_data)
                    if handled:
                        return
            except Exception as e:
                log.warning(f"[TypeSafe FastPath] Failed to parse schedule intent: {e}")

    # 3. 运行插件 on_before_ai 钩子
    user_text, session_data = await plugin_manager.dispatch_before_ai(user_text, chat_id, session_data)
    if not user_text or not user_text.strip():
        log.info(f"Message in chat {chat_id} was intercepted and consumed by plugin hook. Skipping AI execution.")
        return

    # 4. 智能意图路由 (Fast Chat vs Deep Agent)
    # 严格置信度与终端需求门禁 (Confidence-Gated Routing)：
    # 只有当明确需要终端操作、或复杂度 >= 1.2、或高置信度 (>=0.85) 的代码工程任务时，才判定为 complex_agent
    is_complex_agent = False
    if session_data.get("mode") == "agent":
        is_complex_agent = True
    elif decision.needs_terminal or decision.complexity_score >= 1.2:
        is_complex_agent = True
    elif decision.intent == "code_agent" and decision.intent_confidence >= 0.85 and decision.complexity_score >= 0.8:
        is_complex_agent = True

    # Inject protocol into prompt
    current_proj = session_data.get("project", "默认")
    system_instruction = (
        "[System Rule: MUST ALWAYS communicate, reply, explain, and write responses in Simplified Chinese (简体中文). "
        "Any English text in the response must be limited to code syntax or technical names only. "
        "Absolute directive: NEVER output internal chain-of-thought, reasoning steps, planning commentary, or English preambles. "
        "Output ONLY your final answer directly in Simplified Chinese.]\n\n"
    )

    system_instruction += (
        "[System Feishu Resource Delivery / 飞书文件传送规范]\n"
        "1. 【严禁使用 antigravity 私自发文件】：严禁在 antigravity 中编写外部脚本、使用 curl 或 webhook 尝试私自向飞书发送文件。\n"
        "2. 【统一使用飞书 Lark API 推送】：向用户提供文件时，请在最终回复中直接以标准 Markdown 链接输出该文件的本地绝对路径（例如 `[导出报告.xlsx](/path/to/file.xlsx)`、`📄 [文档.md](/path/to/doc.md)` 或 `[发送文件: script.py](/path/to/script.py)`）。\n"
        "   请注意：用户在飞书客户端无法直接点击打开 Linux 本地磁盘路径，系统后台会自动拦截你链接的文件路径，统一使用飞书官方 Lark API（im.v1.file.create）将该文件原生推送到当前飞书会话中供用户点击预览与下载。\n"
        "3. 【命令行工具支持】：如需在终端脚本中主动传送文件，可直接运行 `python3 send_to_feishu.py <文件路径>`，该脚本使用官方 Lark API 发送文件到当前会话。\n\n"
    )

    if task.get("message_type") == "audio" or task.get("is_voice_message"):
        system_instruction += (
            "[Voice Interaction Directive / 语音交互规范]\n"
            "用户正在通过飞书原生语音与你对话。本系统会将你的文本回复自动合成为高拟真语音消息发送给用户。\n"
            "请遵循口语化表达：语言自然生动、亲切凝练、避免输出冗长代码块或复杂大表格，重点结论口语化输出。\n\n"
        )

    if is_complex_agent:
        # 复杂工程任务：注入步骤规划、自主执行、安全防护与项目上下文
        system_instruction += (
            "[System Mandatory Task Planning Directive / 任务规划强制规范]\n"
            "【必须执行】：只要你根据用户意图，判断当前任务需要调用工具执行操作（如读取或修改代码、执行终端命令、多步骤排错、数据查询分析、生成或导出文件等），你必须在调用任何工具之前，首先在回复的最开始输出一行结构化规划标签：\n"
            "[TASK_PLAN] 步骤1名称 | 步骤2名称 | 步骤3名称 | 步骤4名称 [/TASK_PLAN]\n"
            "规则要求：\n"
            "1. 规划必须由你基于对任务的真实理解量身定制（3~4步为宜），每一步需精炼并包含具体目标或涉及的关键文件/操作，切忌泛化套话；\n"
            "2. 输出该行标签后立即调用首个工具开始执行，不要输出任何多余过渡句；\n"
            "3. 前端界面会自动提取此标签并在飞书卡片中向用户展示步骤清单并逐项打勾 ✅。\n\n"
        )
        system_instruction += (
            "[System Autonomous Execution Directive / 自主执行决策规范]\n"
            "当遇到需要执行终端命令、修改文件或面对多种技术路径时，请始终自主评估并采用最稳妥、最高效的最优方案直接调用工具执行，严禁主动提出多选方案（如方案一/二/三/四）让用户做选择题，严禁停下来等待用户确认。始终直接自主推进并交付最终结果！\n\n"
        )
        system_instruction += f"[System Active Project Context]\n- Current active project workspace path is: {current_proj}\n\n"
        system_instruction += (
            "[System Execution & Safety Guardrails]\n"
            "1. 【全指令强制超时保护】：使用 `run_command` 工具执行命令时必须前缀 `timeout <秒数>`。\n"
            "2. 【受限递归与大目录避让】：严禁在系统全盘或依赖目录中执行无限制的大范围递归搜索。\n"
            "3. 【严禁自杀式重启自身服务】：严禁执行重启当前飞书机器人自身进程的操作。\n"
            "4. 【计划任务调度能力】：当用户有定时提醒或周期任务时，使用 run_command 执行 CLI 注册到 cron_scheduler 引擎中。\n\n"
        )
        import config
        if getattr(config, "TYPESAFE_TIER", "gateway") == "copilot":
            system_instruction += (
                "[System One Co-Pilot Safety & Reflection Directive / 全链路执行与自愈规则]\n"
                "当前系统处于 System One Level 3 (全链路保护) 模式。\n"
                "1. 【工具调用前审慎】：在调用任何终端工具前，自主核验命令参数的安全性与受控性，严禁执行超出项目目录的不可逆破坏操作。\n"
                "2. 【报错自愈反思】：当命令执行失败或退出码非 0 时，必须深入分析错误根因，自愈提出修正方案，严禁机械重复失败命令。\n\n"
            )
        project_prompts = session_data.get("project_prompts", {})
        if current_proj in project_prompts and project_prompts[current_proj]:
            proj_prompt_text = project_prompts[current_proj]
            system_instruction += f"[Active Project Specific Rules & Description]\n{proj_prompt_text}\n\n"
    else:
        # 轻量问答与概念咨询模式：严禁执行任何工具，纯文本秒级回答
        system_instruction += (
            "[System Mode Directive: Pure Conversation Mode / 轻量纯文本问答模式]\n"
            "当前任务经由 System One 决策网关评估属于常规技术咨询或日常对话，严禁调用任何终端命令工具（如 run_command）或文件读写工具！"
            "请直接针对用户问题组织语言，用凝练清晰的语言直接给出最终解答。\n\n"
        )

    # 注入长期记忆上下文（由 ai_memory 插件提取）
    mem_ctx = session_data.get("memory_context")
    if mem_ctx:
        system_instruction += f"[User Long-Term Memory / 用户长期偏好与记忆]\n{mem_ctx}\n\n"

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
            final_prompt = f"[System Context: User preferences:]\n{memory_block}\n\n[User's Message:]\n{user_text}"
            
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
