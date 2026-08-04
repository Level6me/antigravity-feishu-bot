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
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        log.info(f"User selected choice: {choice}")
        
        if app_state.main_loop and app_state.main_loop.is_running():
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

            asyncio.run_coroutine_threadsafe(notify_and_process(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
        
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
            asyncio.run_coroutine_threadsafe(do_create_project_prompt(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "请输入项目名或Git仓库地址！"}})

    elif action_value.get("action") == "prompt_custom_workspace_root":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_ws_root():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CUSTOM_WORKSPACE_ROOT.value
                await save_session_async(chat_id, session_data)
                
                msg = "⚙️ **设置公共项目根目录**\n\n请直接在此回复您想要设定的公共项目根目录绝对路径（例如：`/vol1/1000/github`）：\n\n*(系统收到后将自动校验路径合法性，并将后续所有新建项目与列表面板绑定至该根目录，当前活跃工作区保持不变)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_ws_root(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复公共项目根目录绝对路径！"}})

    elif action_value.get("action") == "prompt_custom_project_path":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_custom():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.CUSTOM_PROJECT_PATH.value
                await save_session_async(chat_id, session_data)
                
                msg = "⚙️ **设置开发工作区**\n\n请直接回复您想要设定的开发工作区绝对路径（例如：`/vol1/1000/github/my-app`）：\n\n*(系统收到后将自动校验路径合法性并为您切换工作区)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_custom(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复开发工作区绝对路径！"}})

    elif action_value.get("action") == "prompt_add_note":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_note():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.NOTE_ADD.value
                await save_session_async(chat_id, session_data)
                
                msg = "📝 **添加笔记**\n\n请直接在此回复您要添加的笔记内容：\n\n*(提示：随时也可发送 `/note add <内容>` 进行快速添加)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_note(), app_state.main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "请回复您要添加的笔记内容！"}})

    elif action_value.get("action") == "prompt_add_memory":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_prompt_memory():
                session_data = await get_session_async(chat_id)
                session_data["pending_command"] = PendingCommand.MEMORY_ADD.value
                await save_session_async(chat_id, session_data)
                
                msg = "🧠 **新增个人偏好**\n\n请直接在此回复您希望 AI 记住的偏好或习惯设定（例如：`写代码只用 Python` 或 `用中文回答`）：\n\n*(系统收到后将永久保存至您的个人偏好记忆库)*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, msg))
            asyncio.run_coroutine_threadsafe(do_prompt_memory(), app_state.main_loop)
            
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

    resp = handle_auth_card_action(action_value, chat_id, card_message_id)
    if resp is not None:
        return resp

    return P2CardActionTriggerResponse()
