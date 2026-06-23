import asyncio
import subprocess
import uuid
from database import load_profiles, save_profiles, save_sessions
from lark_client import send_reply_sdk, send_interactive_card_sdk
from logger import log
from card_builder import CardBuilder
from config import ANTIGRAVITY_BIN

async def handle_slash_command(user_text, message_id, chat_id, sessions, running_processes):
    """
    Parses and handles slash commands. Returns True if a command was handled, False otherwise.
    Returns (handled: bool, override_user_text: str)
    """
    session_data = sessions.get(chat_id, {})
    
    if user_text == "/stop":
        if chat_id in running_processes:
            try:
                running_processes[chat_id].kill()
            except:
                pass
            reply_text = "🛑 当前任务已被紧急叫停！"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        else:
            reply_text = "ℹ️ 当前没有正在运行的任务。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/clear"):
        sessions[chat_id]["conversation"] = uuid.uuid4().hex
        save_sessions(sessions)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/remember "):
        memory_text = user_text[len("/remember "):].strip()
        profiles = load_profiles()
        if chat_id not in profiles:
            profiles[chat_id] = []
        profiles[chat_id].append(memory_text)
        save_profiles(profiles)
        reply_text = f"🧠 已为您永久记录偏好：\n- {memory_text}"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/memory"):
        profiles = load_profiles()
        memories = profiles.get(chat_id, [])
        if not memories:
            reply_text = "📭 当前没有记录您的任何长时偏好。您可以通过 `/remember <偏好>` 来添加。"
        else:
            reply_text = "🧠 **当前已永久记住您的以下偏好**：\n" + "\n".join([f"- {m}" for m in memories])
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/forget"):
        profiles = load_profiles()
        if chat_id in profiles:
            del profiles[chat_id]
            save_profiles(profiles)
        reply_text = "🗑️ 您的所有长时记忆偏好已被彻底清空！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text

    elif user_text.startswith("/role "):
        new_role = user_text.split(" ", 1)[1].strip()
        sessions[chat_id]["role"] = new_role
        save_sessions(sessions)
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
