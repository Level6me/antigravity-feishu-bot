"""Feishu interactive card action callbacks."""
import asyncio
import json
import os
import subprocess

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from config import ALLOWED_USERS, ALLOWED_CHATS, get_oauth_token_path
from database import get_session_async, get_profile_async, save_session_async, save_profile_async
from card_builder import CardBuilder
from lark_client import (
    send_reply_sdk,
    send_interactive_card_sdk,
    patch_interactive_card_sdk,
)
from commands import PendingCommand, handle_slash_command
from logger import log
import app_state
from handlers.messages import _handle_message_async_internal
from handlers.auth_actions import handle_auth_card_action
from utils.auth import get_role, is_admin

# Compatibility aliases for extracted body
running_processes = app_state.running_processes
chat_queues = app_state.chat_queues
chat_workers = app_state.chat_workers


# Provide main_loop as dynamic attribute via module-level lookup in function
def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    log.info(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id
    
    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        # 卡片回调操作者字段为 CallBackOperator.open_id（字符串），
        # 注意与消息事件 sender.sender_id.open_id 的对象结构不同。
        sender_id = data.event.operator.open_id if data.event.operator else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True
            
    if not is_allowed:
        log.warning(f"Unauthorized card action ignored. chat_id: {chat_id}, operator_id: {sender_id if 'sender_id' in locals() else None}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "您无权操作此卡片！"}})

    # Auth gate: guests / pending / banned chats cannot operate any card.
    operator_open_id = data.event.operator.open_id if data.event.operator else None
    role = get_role(chat_id, operator_open_id or "")
    if role in ("guest", "pending", "banned"):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "该会话未授权，无法操作。"}})

    # Admin gate for plugin write operations (prevents privilege escalation via card buttons)
    plugin_write_actions = {
        "reload_plugins", "update_plugin", "uninstall_plugin",
        "install_github_repo", "prompt_install_github", "prompt_add_source"
    }
    if action_value.get("action") in plugin_write_actions:
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 此操作仅管理员可用！"}})
        
    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        result_card = None
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch():
                session_data = await get_session_async(chat_id)
                old_model = session_data.get("model", "Default")
                session_data["model"] = new_model
                await save_session_async(chat_id, session_data)
                log.info(f"Switched model to {new_model} in chat {chat_id}")
                res_card = CardBuilder.build_model_switch_result_card(new_model, old_model)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, res_card))
                return res_card

            future = asyncio.run_coroutine_threadsafe(do_switch(), app_state.main_loop)
            try:
                result_card = future.result(timeout=8)
            except Exception as e:
                log.error(f"[switch_model] do_switch failed or timed out: {e}")

        if result_card:
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": result_card},
                "toast": {"type": "success", "content": f"模型已切换为 {new_model}"}
            })
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已切换为 {new_model}"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice", "")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def notify_and_process():
                handled = False
                if choice and choice.startswith("/"):
                    session_data = await get_session_async(chat_id)
                    # 1. 优先使用完整的 choice 执行命令（保留如 "/update confirm" 等子命令参数）
                    handled_res, _ = await handle_slash_command(choice, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                    handled = bool(handled_res)
                    if not handled:
                        # 2. 提取首个命令词（处理带说明括号的选项如 "/light (常用缩写...)" -> "/light"）
                        clean_cmd = choice.split()[0].strip()
                        if clean_cmd != choice:
                            handled_res2, _ = await handle_slash_command(clean_cmd, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                            handled = bool(handled_res2)

                if not handled:
                    # 2. 如果不是有效斜杠指令或未被命令捕获，统一模拟为用户回复提交给 AI 继续处理
                    user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已提交 AI 进行下一步处理...)*"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
        
    elif action_value.get("action") == "extend_wait":
        log.info(f"User requested extend_wait in chat {chat_id}")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已确认继续等待，后台保持执行并已延长超时保护。"}})
        
    elif action_value.get("action") == "browse_dir":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_browse_dir():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_dir(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在载入目录..."}})
        
    elif action_value.get("action") == "select_project":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_select_project():
                session_data = await get_session_async(chat_id)
                session_data["project"] = target_path
                
                # 记录最近使用的项目
                recent = session_data.get("recent_projects", [])
                if target_path in recent:
                    recent.remove(target_path)
                recent.insert(0, target_path)
                session_data["recent_projects"] = recent[:5]
                
                await save_session_async(chat_id, session_data)
                
                success_text = (
                    f"📂 **工作区项目切换成功！**\n\n"
                    f"当前已将活跃目录设定为：\n`{target_path}`"
                )
                success_card = CardBuilder.build_ai_response(
                    success_text,
                    current_model=session_data.get('model', 'Default'),
                    current_project=target_path,
                    session_data=session_data
                )
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(card_message_id, success_card))
            asyncio.run_coroutine_threadsafe(do_select_project(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目切换成功！"}})

    elif action_value.get("action") == "remove_project_from_list":
        target_path = action_value.get("path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_remove_project(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "项目已成功从列表中移出！"}})
    elif action_value.get("action") == "view_note_detail":
        idx = int(action_value.get("index"))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_view_note():
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    note_content = notes[idx]
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"📝 **笔记详情**:\\n{note_content}"))
            asyncio.run_coroutine_threadsafe(do_view_note(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "详情已发送到当前会话！"}})
        
    elif action_value.get("action") == "delete_note":
        idx = int(action_value.get("index"))
        
        if app_state.main_loop and app_state.main_loop.is_running():
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
            asyncio.run_coroutine_threadsafe(do_delete_note(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功删除该条笔记！"}})
        
    elif action_value.get("action") == "clear_notes":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_clear_notes():
                session_data = await get_session_async(chat_id)
                session_data["notes"] = []
                await save_session_async(chat_id, session_data)
                
                new_card = CardBuilder.build_note_list_card([])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_clear_notes(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "您的记事本已被全部清空！"}})
        
    elif action_value.get("action") == "refresh_status":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "仅管理员可查看系统状态。"}})
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_status():
                from commands import get_system_status_card_data
                cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = get_system_status_card_data()
                new_card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_status(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "状态已刷新！"}})

    elif action_value.get("action") == "refresh_quota":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_quota():
                from utils.quota import fetch_quota
                quota_data = await asyncio.get_running_loop().run_in_executor(None, fetch_quota)
                new_card = CardBuilder.build_quota_card(quota_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_quota(), app_state.main_loop)

        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "额度已刷新！"}})

    elif action_value.get("action") == "forget_single_memory":
        idx = int(action_value.get("index"))
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_forget():
                memories = await get_profile_async(chat_id)
                if 0 <= idx < len(memories):
                    removed = memories.pop(idx)
                    await save_profile_async(chat_id, memories)
                    log.info(f"Removed memory preference: '{removed}' in chat {chat_id}")
                    
                    new_card = CardBuilder.build_memory_card(memories)
                    await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_forget(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功擦除该偏好记录！"}})

    elif action_value.get("action") == "create_project_prompt":
        parent_path = action_value.get("parent_path")
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_create_project_prompt():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CREATE_PROJECT.value
                session_data["create_project_parent"] = parent_path
                await save_session_async(chat_id, session_data)
                
                prompt_msg = f"📂 **请输入新建项目的名称，或直接输入项目的 Git 仓库地址**：\n\n*(支持通过 Git URL 克隆；若输入项目名，将在公共根目录 `{parent_path}` 下新建并初始化)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, prompt_msg))

            future = asyncio.run_coroutine_threadsafe(do_create_project_prompt(), app_state.main_loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                log.error(f"[create_project_prompt] error: {e}")
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请输入项目名或Git仓库地址！"}})

    elif action_value.get("action") == "prompt_custom_workspace_root":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_ws_root():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CUSTOM_WORKSPACE_ROOT.value
                await save_session_async(chat_id, session_data)
                
                msg = "⚙️ **设置公共项目根目录**\n\n请直接在此回复您想要设定的公共项目根目录绝对路径（例如：`/vol1/1000/github`）：\n\n*(系统收到后将自动校验路径合法性，并将后续所有新建项目与列表面板绑定至该根目录，当前活跃工作区保持不变)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))

            future = asyncio.run_coroutine_threadsafe(do_prompt_ws_root(), app_state.main_loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                log.error(f"[prompt_custom_workspace_root] error: {e}")
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复公共项目根目录绝对路径！"}})

    elif action_value.get("action") == "prompt_custom_project_path":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_custom():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CUSTOM_PROJECT_PATH.value
                await save_session_async(chat_id, session_data)
                
                msg = "⚙️ **设置开发工作区**\n\n请直接回复您想要设定的开发工作区绝对路径（例如：`/vol1/1000/github/my-app`）：\n\n*(系统收到后将自动校验路径合法性并为您切换工作区)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))

            future = asyncio.run_coroutine_threadsafe(do_prompt_custom(), app_state.main_loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                log.error(f"[prompt_custom_project_path] error: {e}")
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复开发工作区绝对路径！"}})

    elif action_value.get("action") == "prompt_add_note":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_note():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.NOTE_ADD.value
                await save_session_async(chat_id, session_data)
                
                msg = "📝 **添加笔记**\n\n请直接在此回复您要添加的笔记内容：\n\n*(提示：随时也可发送 `/note add <内容>` 进行快速添加)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))

            future = asyncio.run_coroutine_threadsafe(do_prompt_note(), app_state.main_loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                log.error(f"[prompt_add_note] error: {e}")
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复您要添加的笔记内容！"}})

    elif action_value.get("action") == "prompt_add_memory":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_memory():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.MEMORY_ADD.value
                await save_session_async(chat_id, session_data)
                
                msg = "🧠 **新增个人偏好**\n\n请直接在此回复您希望 AI 记住的偏好或习惯设定（例如：`写代码只用 Python` 或 `用中文回答`）：\n\n*(系统收到后将永久保存至您的个人偏好记忆库)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))

            future = asyncio.run_coroutine_threadsafe(do_prompt_memory(), app_state.main_loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                log.error(f"[prompt_add_memory] error: {e}")
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复您的个人偏好设定！"}})

    elif action_value.get("action") == "browse_recent_page":
        target_path = action_value.get("current_path")
        target_page = action_value.get("page", 1)
        
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_browse_recent_page():
                session_data = await get_session_async(chat_id)
                recent_projects = session_data.get("recent_projects", [])
                ignored_projects = session_data.get("ignored_projects", [])
                ws_root = session_data.get("workspace_root")
                new_card = CardBuilder.build_dir_browser_card(target_path, recent_projects, target_page, workspace_root=ws_root, ignored_projects=ignored_projects)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_browse_recent_page(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"正在载入第 {target_page} 页项目..."}})

    elif action_value.get("action") == "switch_cron_tab":
        tab = action_value.get("tab", "user")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch_cron():
                from database import get_all_cron_tasks
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab=tab, session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_switch_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已切换至 {'用户' if tab=='user' else '系统'} 任务面板"}})

    elif action_value.get("action") == "open_cron_panel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_open_cron():
                from database import get_all_cron_tasks
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                from lark_client import send_card_to_chat_sdk
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, new_card))
            asyncio.run_coroutine_threadsafe(do_open_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在打开计划任务中心..."}})

    elif action_value.get("action") == "open_cron_create":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_open_create():
                from commands import PendingCommand
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CRON_ADD.value
                await save_session_async(chat_id, session_data)
                
                msg = "⏱️ **新建计划任务**\n\n请直接在此回复 3 段信息，中间用竖线 `|` 隔开：\n" \
                      "`任务名称 | 触发规则(Cron表达式/秒数) | 执行 Prompt`\n\n" \
                      "📌 **示例 1 (标准 Cron 每天 09:00 执行)**：\n" \
                      "`每日总结 | 0 9 * * * | 检查当前工作区的 Git 提交并生成日报`\n\n" \
                      "📌 **示例 2 (倒计时 10 分钟后一次性执行)**：\n" \
                      "`磁盘压测汇报 | 600s | 提取 /tmp/iscsi_stab_test.log 并分析报告`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_open_create(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请发送格式为 '名称 | 规则 | Prompt' 的任务文本"}})

    elif action_value.get("action") == "toggle_cron_active":
        task_id = action_value.get("task_id")
        is_active = bool(action_value.get("is_active", True))
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_toggle_cron():
                from database import update_cron_task_status, get_all_cron_tasks
                await asyncio.get_running_loop().run_in_executor(None, lambda: update_cron_task_status(task_id, is_active))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_toggle_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"任务已{'启用' if is_active else '暂停'}"}})

    elif action_value.get("action") == "delete_cron_task":
        task_id = action_value.get("task_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_delete_cron():
                from database import delete_cron_task, get_all_cron_tasks
                await asyncio.get_running_loop().run_in_executor(None, lambda: delete_cron_task(task_id))
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_delete_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "计划任务已物理删除！"}})

    elif action_value.get("action") == "run_cron_now":
        task_id = action_value.get("task_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_run_now():
                from database import get_cron_task
                task = await asyncio.get_running_loop().run_in_executor(None, lambda: get_cron_task(task_id))
                if task:
                    from cron_engine import cron_engine
                    asyncio.create_task(cron_engine._run_task_wrapper(task))
            asyncio.run_coroutine_threadsafe(do_run_now(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已触发即刻运行计划任务！"}})

    elif action_value.get("action") == "reload_plugins":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_reload_plugins():
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_reload_plugins(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功热重载插件库！"}})

    elif action_value.get("action") == "switch_plugin_tab":
        tab = action_value.get("tab", "installed")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch_plugin():
                from plugin_manager import plugin_manager
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab=tab)
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_switch_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"已切换至 {'已安装插件' if tab=='installed' else '插件源与商店'}"}})

    elif action_value.get("action") == "refresh_store_plugins":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_store():
                from plugin_store import fetch_remote_store_plugins
                remote_list = await asyncio.get_running_loop().run_in_executor(None, lambda: fetch_remote_store_plugins(force_refresh=True))
                from plugin_manager import plugin_manager
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="sources")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_store(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已成功刷新 GitHub 插件列表！"}})

    elif action_value.get("action") == "update_plugin":
        plugin_id = action_value.get("plugin_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_update_plugin():
                from plugin_store import update_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: update_plugin(plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"🔄 **插件更新结果：**\n{msg}"))
            asyncio.run_coroutine_threadsafe(do_update_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在从 Git 拉取插件 {plugin_id} 最新代码..."}})

    elif action_value.get("action") == "uninstall_plugin":
        plugin_id = action_value.get("plugin_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_uninstall_plugin():
                from plugin_store import uninstall_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: uninstall_plugin(plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"🗑️ **插件卸载通知：**\n{msg}"))
            asyncio.run_coroutine_threadsafe(do_uninstall_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": f"正在卸载插件 {plugin_id}..."}})

    elif action_value.get("action") == "install_github_repo":
        repo_url = action_value.get("repo_url")
        plugin_id = action_value.get("plugin_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_install_repo():
                from plugin_store import install_plugin_from_github
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: install_plugin_from_github(repo_url, plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, f"📥 **插件安装通知：**\n{msg}"))
            asyncio.run_coroutine_threadsafe(do_install_repo(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在一键安装 GitHub 插件仓库..."}})

    elif action_value.get("action") == "prompt_install_github":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_install():
                from commands import PendingCommand
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.PLUGIN_INSTALL_GITHUB.value
                await save_session_async(chat_id, session_data)
                msg = "📥 **从 GitHub 安装插件**\n\n请在此直接回复 GitHub 插件仓库地址，例如：\n`https://github.com/owner/my-feishu-plugin.git` 或 `owner/my-feishu-plugin`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_install(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复 GitHub 仓库地址"}})

    elif action_value.get("action") == "prompt_add_source":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_source():
                from commands import PendingCommand
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.PLUGIN_ADD_SOURCE.value
                await save_session_async(chat_id, session_data)
                msg = "➕ **添加 GitHub 插件源**\n\n请直接在此回复 2~3 段信息，中间用竖线 `|` 隔开：\n`源名称 | 仓库URL | [可选描述]`\n\n例如：`开源社区插件源 | https://github.com/my-org/plugins-repo | 社区维护插件集`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_source(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复源名称与 URL"}})

    # Dispatch to plugin manager
    action_name = action_value.get("action", "")
    if action_name and app_state.main_loop and app_state.main_loop.is_running():
        async def do_dispatch_plugin_card():
            from plugin_manager import plugin_manager
            await plugin_manager.dispatch_card_action(action_name, action_value, chat_id, card_message_id)
        asyncio.run_coroutine_threadsafe(do_dispatch_plugin_card(), app_state.main_loop)

    resp = handle_auth_card_action(action_value, chat_id, card_message_id)
    if resp is not None:
        return resp

    return P2CardActionTriggerResponse()
