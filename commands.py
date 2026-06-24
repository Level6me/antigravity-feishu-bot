import asyncio
import subprocess
import uuid
from database import get_profile_async, save_profile_async, save_session_async
from lark_client import send_reply_sdk, send_interactive_card_sdk
from logger import log
from card_builder import CardBuilder
from config import ANTIGRAVITY_BIN

async def handle_slash_command(user_text, message_id, chat_id, session_data, running_processes, chat_queues):
    """
    Parses and handles slash commands. Returns True if a command was handled, False otherwise.
    Returns (handled: bool, override_user_text: str)
    """
    
    if user_text == "/stop":
        cleared = False
        if chat_id in chat_queues:
            while not chat_queues[chat_id].empty():
                try:
                    chat_queues[chat_id].get_nowait()
                    chat_queues[chat_id].task_done()
                    cleared = True
                except asyncio.QueueEmpty:
                    break
                    
        if chat_id in running_processes or cleared:
            try:
                if chat_id in running_processes:
                    running_processes[chat_id].kill()
            except:
                pass
            reply_text = "🛑 当前任务已被紧急叫停，排队中的任务也已清空！"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        else:
            reply_text = "ℹ️ 当前没有正在运行的任务。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/clear"):
        session_data["conversation"] = uuid.uuid4().hex
        await save_session_async(chat_id, session_data)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/remember "):
        memory_text = user_text[len("/remember "):].strip()
        memories = await get_profile_async(chat_id)
        memories.append(memory_text)
        await save_profile_async(chat_id, memories)
        reply_text = f"🧠 已为您永久记录偏好：\n- {memory_text}"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/memory"):
        memories = await get_profile_async(chat_id)
        if not memories:
            reply_text = "📭 当前没有记录您的任何长时偏好。您可以通过 `/remember <偏好>` 来添加。"
        else:
            memory_list = "\n".join([f"- {m}" for m in memories])
            reply_text = f"🧠 **您的长时偏好记录：**\n{memory_list}\n\n*(如需清空请发送 `/clear memory`)*"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text == "/update":
        reply_text = "🔍 正在从云端拉取最新版本信息，请稍候..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        
        try:
            # Fetch latest from github
            subprocess.run(["git", "fetch", "github", "main"], capture_output=True, text=True, check=True)
            
            # Get current version
            local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
            
            # Get remote version
            remote_hash = subprocess.run(["git", "rev-parse", "--short", "github/main"], capture_output=True, text=True).stdout.strip()
            
            if local_hash == remote_hash:
                no_update_card = CardBuilder.build_no_update_card(f"Build: {local_hash}")
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, no_update_card))
            else:
                # Get changelog
                changelog_cmd = ["git", "log", f"{local_hash}..github/main", "--pretty=format:- %s"]
                changelog = subprocess.run(changelog_cmd, capture_output=True, text=True).stdout.strip()
                if not changelog:
                    changelog = "- 未知更新"
                
                # Send update card
                update_card = CardBuilder.build_update_card(f"Build: {local_hash}", f"Build: {remote_hash}", changelog)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, update_card))
                
        except Exception as e:
            log.error(f"Failed to check for updates: {e}")
            error_text = f"❌ 检查更新失败: {e}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
            
        return True, user_text
        
    elif user_text == "/update confirm":
        reply_text = "⬇️ 正在执行核心系统升级，请勿中断..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        
        try:
            # Hard reset to github/main
            subprocess.run(["git", "reset", "--hard", "github/main"], capture_output=True, text=True, check=True)
            
            # Install new requirements if any
            pip_cmd = ["venv/bin/pip", "install", "-r", "requirements.txt"]
            subprocess.run(pip_cmd, capture_output=True, text=True)
            
            reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            
            # Restart via pm2 in background without waiting
            subprocess.Popen(["pm2", "restart", "feishu-bot"])
        except Exception as e:
            log.error(f"Failed to apply update: {e}")
            error_text = f"❌ 升级过程中出现错误: {e}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
            
        return True, user_text

        
    elif user_text.startswith("/forget"):
        await save_profile_async(chat_id, [])
        reply_text = "🗑️ 您的所有长时记忆偏好已被彻底清空！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text

    elif user_text.startswith("/role "):
        new_role = user_text.split(" ", 1)[1].strip()
        session_data["role"] = new_role
        await save_session_async(chat_id, session_data)
        user_text = f"请记住以下设定，并在接下来的对话中始终扮演这个角色：{new_role}。收到请回复：'好的，角色设定已生效！'"
        return False, user_text
        
    elif user_text.startswith("/help"):
        reply_text = """💡 **Antigravity 机器人高级操作指南**

🔹 `/model` : 弹出交互式控制面板，自由切换大模型
🔹 `/role <设定>` : 让机器人扮演特定角色 (例如: `/role 资深Python工程师`)
🔹 `/remember <设定>` : 让机器人永久记住你的偏好 (例如: `/remember 我写代码只用 Python`)
🔹 `/memory` : 查看机器人当前记住的所有偏好
🔹 `/forget` : 清除机器人的长时记忆偏好
🔹 `/clear` : 清空当前对话的上下文记忆，重新开始
🔹 `/stop` : 紧急刹车！强制中止正在后台生成的耗时任务
🔹 `/update` : 检查并获取云端最新版本的机器人引擎核心
🔹 `/help` : 显示此帮助菜单

*✨ 隐藏黑科技提示：*
* **多模态解析**：直接向我发送文档 (PDF/Word)、语音、视频或图片，我能直接阅读、倾听并分析！*
* **远程终端**：我可以读取你电脑上的文件，甚至直接执行如 `ls -al` 等终端命令！*
* **全网搜索**：发给我任意网页链接，我可以帮你提取摘要！*"""
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/model") or user_text.startswith("/card") or user_text.startswith("/menu"):
        fetch_proc = await asyncio.create_subprocess_exec(
            ANTIGRAVITY_BIN, "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        stdout, _ = await fetch_proc.communicate()
        models_output = stdout.decode().strip()
        
        available_models = [line.strip() for line in models_output.split('\n') if line.strip()]
        if not available_models:
            available_models = ["Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)", "GPT-OSS 120B (Medium)"]
            
        card_content = CardBuilder.build_model_panel(available_models, session_data.get('model', 'Default'))
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))
        return True, user_text
        
    return False, user_text
