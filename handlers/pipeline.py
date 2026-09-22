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


def _is_operational_command(text: str) -> bool:
    """检测用户输入是否属于需要自主执行的操作命令/执行诉求，而非纯文本提问."""
    if not text:
        return False
    clean = text.strip()

    # 1. 优先排除以疑问词开头的纯技术原理/科普咨询（除非包含强烈的委托执行动作如“帮我”、“执行”、“修改”）
    question_starters = ("什么是", "为什么", "为何", "怎么看", "如何理解", "有哪些区别", "区别是什么", "介绍一下", "讲解一下", "科普一下", "如何", "怎么", "怎样")
    if any(clean.startswith(qs) for qs in question_starters) and not any(kw in clean for kw in ("帮我", "请帮我", "麻烦帮我", "执行", "修改", "改一下", "启动", "排查")):
        return False

    # 2. 快捷确认与授权执行指令 (例如上下文讨论后的快速授权)
    confirm_patterns = [
        r"^(可以|行|好|好的|没问题|ok|yes)?[,，\s]*(改吧|更新吧|执行吧|执行|确认|就这样改|就按这样改|就这样做|这样改|帮我改|直接改|做吧|搞起|推吧|发吧|跑一下|开始吧|继续|搞一下|弄一下|好|好的|好啊|行|行的|没问题|ok|yes|go)[\s!！。.]*$",
        r"(就这样|就按这|按这|照这|按照你|按你|照你).*(改|做|办|执行|更新)",
        r"(按照|按|照)(你说的|建议|方案|思路)(改|执行|做|更新|处理)",
        r"(直接|帮我|请帮我)(改|更新|执行|跑|做|提交|推送|部署)",
    ]
    for pat in confirm_patterns:
        if re.search(pat, clean, re.IGNORECASE):
            return True

    # 3. 明确的操作动词与系统命令请求
    action_keywords = [
        "帮我", "请帮我", "麻烦帮我", "替我", "给我",
        "启动", "运行", "执行", "重启", "停止", "杀死", "关闭",
        "修改", "改写", "修复", "改一下", "重构", "优化代码",
        "排查", "排错", "查一下", "看一下", "看下", "看日志", "查看日志",
        "部署", "安装", "编译", "构建", "配置", "上线",
        "推送到", "提交代码", "git push", "git commit", "git pull",
        "创建文件", "写入", "生成", "导出", "下载", "删除",
        "测试", "压测", "跑测试"
    ]
    if any(kw in clean for kw in action_keywords):
        return True

    return False


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

    from system_one_gate import evaluate_adaptive_tier, resolve_adaptive_model
    adaptive_tier = evaluate_adaptive_tier(decision)
    if adaptive_tier:
        session_data["typesafe_adaptive_tier"] = adaptive_tier
        base_model = session_data.get("base_model") or session_data.get("model", "")
        session_data["base_model"] = base_model
        session_data["model"] = resolve_adaptive_model(base_model, adaptive_tier)
    else:
        session_data.pop("typesafe_adaptive_tier", None)
        if "base_model" in session_data:
            session_data["model"] = session_data["base_model"]

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

    # 4. 智能意图路由与人机交互规范 (严格贯彻：区分提问与命令)
    # 提问/咨询：纯文本高效解答，不无端调用工具
    # 命令/操作：自主决策并调用工具执行到底，绝对严禁推诿让用户手动在终端执行！
    is_complex_agent = False
    if session_data.get("mode") == "agent":
        is_complex_agent = True
    elif decision.needs_terminal:
        is_complex_agent = True
    elif decision.intent in ("code_agent", "server_health", "cron", "notes"):
        is_complex_agent = True
    elif decision.complexity_score >= 0.5:
        is_complex_agent = True
    elif _is_operational_command(user_text):
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

    # 注入当前工作空间上下文与安全防线（全模式常驻保障）
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

    if is_complex_agent:
        # 复杂工程任务与操作指令模式
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
            "[System Autonomous Execution Directive / 自主执行决策规范：区分提问与命令]\n"
            "用户核心准则：【严格区分提问与命令】。\n"
            "1. 【命令必须自主执行并交付】：对于用户的操作、修改、运行、测试、排查等执行诉求（包括“改吧”、“执行”等简短授权），你必须作为全功能 Autonomous Agent 自主调用相应工具（如 run_command、replace_file_content、view_file、write_to_file）直接执行并交付最终结果！\n"
            "2. 【绝对严禁推诿与二次确认】：严禁让用户在服务器或本地终端手动运行命令；严禁抛出多选方案让用户做选择题；严禁停下来等待用户确认；绝对严禁以“受安全策略限制”或“处于轻量模式”等借口拒绝执行命令！\n\n"
        )
    else:
        # 轻量问答与概念咨询模式：纯文本高效解答，但绝不推诿操作诉求
        system_instruction += (
            "[System Interaction Directive / 问答交互规范：区分提问与命令]\n"
            "用户核心准则：【严格区分提问与命令】。\n"
            "1. 【提问秒回纯文本】：当用户提出的是概念咨询、原理解释、方案探讨或日常闲聊时，无需调用工具，直接针对问题组织语言，用凝练、专业、清晰的语言进行解答。\n"
            "2. 【操作绝不推诿拒执】：若用户意图中含有明确的操作诉求（如查看代码、排查错误、修改配置、运行命令等），你依然拥有完整的全套 Agent 工具调用能力，必须自主调用工具完成任务，绝对严禁以“处于轻量纯文本交互模式”或“受安全策略限制”为由拒绝执行，绝对严禁推诿让用户去终端手动操作！\n\n"
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

    # 注入 System One 自适应思考模式指令 (Adaptive Reasoning Effort: Low / Medium / High)
    adaptive_effort = session_data.get("typesafe_adaptive_tier")
    if adaptive_effort == "Low":
        system_instruction += (
            "[System One Adaptive Reasoning: LOW]\n"
            "当前任务判定为轻量日常问答或闲聊，请以极简快速思考直接给出精炼回答，避免冗长思考与过度分析。\n\n"
        )
    elif adaptive_effort == "High":
        system_instruction += (
            "[System One Adaptive Reasoning: HIGH]\n"
            "当前任务判定为多步复杂工程或系统操作，请开启深度思考推理与严谨边界校验。\n\n"
        )
    elif adaptive_effort == "Medium":
        system_instruction += (
            "[System One Adaptive Reasoning: MEDIUM]\n"
            "当前任务判定为常规工程开发，兼顾思考深度与响应效率。\n\n"
        )
    
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
        log.error(f"[Pipeline] execute_antigravity hard timeout (43200s / 12h) for chat {chat_id}")
        is_error = True
    except Exception as e:
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
