import asyncio
import subprocess
import uuid
import os
import signal
import json
import re
from database import get_profile_async, save_profile_async, save_session_async
from lark_client import send_reply_sdk, send_interactive_card_sdk
from logger import log
from card_builder import CardBuilder
from config import ANTIGRAVITY_BIN, BASE_VERSION_PREFIX, VERSION_START_COMMIT, WORKSPACE_ROOT

def get_version_string(commit_ref="HEAD"):
    try:
        count_str = subprocess.run(["git", "rev-list", "--count", commit_ref], capture_output=True, text=True).stdout.strip()
        commit_count = int(count_str)
        patch = max(1, commit_count - VERSION_START_COMMIT)
        hash_str = subprocess.run(["git", "rev-parse", "--short", commit_ref], capture_output=True, text=True).stdout.strip()
        return f"{BASE_VERSION_PREFIX}{patch} (Build: {hash_str})"
    except Exception:
        return f"Unknown (Build: error)"

def get_system_status_card_data():
    try:
        out = subprocess.check_output(['pm2', 'jlist'], text=True)
        pm2_list = json.loads(out)
        
        bot_info = next((item for item in pm2_list if item['name'] == 'feishu-bot'), None)
        if bot_info:
            status = bot_info['pm2_env']['status']
            uptime = bot_info['pm2_env']['pm_uptime']
            restarts = bot_info['pm2_env']['restart_time']
            cpu = bot_info['monit']['cpu']
            mem_mb = round(bot_info['monit']['memory'] / (1024 * 1024), 1)
        else:
            return 0, 0, "Unknown", "offline", 0, "No process found"
            
        import time
        now = time.time() * 1000
        uptime_ms = now - uptime
        minutes = int(uptime_ms / (1000 * 60)) % 60
        hours = int(uptime_ms / (1000 * 60 * 60)) % 24
        days = int(uptime_ms / (1000 * 60 * 60 * 24))
        
        uptime_parts = []
        if days > 0: uptime_parts.append(f"{days}天")
        if hours > 0: uptime_parts.append(f"{hours}小时")
        uptime_parts.append(f"{minutes}分钟")
        uptime_str = "".join(uptime_parts) if uptime_parts else "<1分钟"
        
        err_out = subprocess.check_output(['pm2', 'logs', 'feishu-bot', '--err', '--lines', '5', '--nostream'], text=True)
        # Strip ANSI escape codes
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        err_out = ansi_escape.sub('', err_out)
        
        err_lines = [l for l in err_out.split('\n') if not l.startswith('[TAILING]') and not l.startswith('/Users') and l.strip()]
        err_logs = '\n'.join(err_lines).strip()
        if not err_logs:
            err_logs = "无报错日志"
            
        import stats
        bot_stats = stats.get_stats()
        
        git_status = "未知"
        try:
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
            commit_info = subprocess.check_output(["git", "log", "-1", "--format=%h - %s (%cr)"], text=True).strip()
            
            # try to fetch silently
            subprocess.run(["git", "fetch"], timeout=3, capture_output=True)
            status_out = subprocess.check_output(["git", "status", "-sb"], text=True).strip().split('\n')[0]
            
            update_hint = ""
            if "behind" in status_out:
                update_hint = " ⚠️ **(有新版本可更新)**"
                
            git_status = f"分支: `{branch}`\n最新: `{commit_info}`{update_hint}"
        except Exception:
            git_status = "无法获取 Git 状态"
            
        return cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats
    except Exception as e:
        return 0, 0, "Error", "error", 0, str(e), "Error", {}


async def handle_slash_command(user_text, message_id, chat_id, session_data, running_processes, chat_queues, chat_workers=None):
    log.info(f"handle_slash_command call: user_text='{user_text}', pending_command='{session_data.get('pending_command')}'")
    """
    Parses and handles slash commands. Returns True if a command was handled, False otherwise.
    Returns (handled: bool, override_user_text: str)
    """
    
    pending_command = session_data.get("pending_command")
    
    # If the user typed a new slash command, clear any pending state
    if user_text.startswith("/") and pending_command:
        session_data.pop("pending_command", None)
        await save_session_async(chat_id, session_data)
        pending_command = None
        
    if not user_text.startswith("/") and pending_command:
        if pending_command == "remember":
            memory_text = user_text.strip()
            memories = await get_profile_async(chat_id)
            memories.append(memory_text)
            await save_profile_async(chat_id, memories)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            reply_text = f"🧠 已为您永久记录偏好：\n- {memory_text}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif pending_command == "role":
            new_role = user_text.strip()
            session_data["role"] = new_role
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            user_text = f"请记住以下设定，并在接下来的对话中始终扮演这个角色：{new_role}。收到请回复：'好的，角色设定已生效！'"
            return False, user_text
            
        elif pending_command == "project":
            new_project = user_text.strip()
            if new_project.lower() in ["clear", "default", "默认", "reset"]:
                session_data["project"] = "默认"
                reply_text = "📂 已将项目重置为默认工作空间！"
            else:
                session_data["project"] = new_project
                reply_text = f"📂 已成功将当前项目切换为：`{new_project}`"
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif pending_command == "create_project":
            input_text = user_text.strip()
            ws_root = session_data.get("workspace_root")
            parent_path = ws_root if ws_root and os.path.exists(ws_root) else WORKSPACE_ROOT
            
            # 正则嗅探是否是 Git 仓库 URL
            git_pattern = re.compile(r'^(https?://|git@|git://)[^\s]+$', re.IGNORECASE)
            is_git_url = bool(git_pattern.match(input_text)) or input_text.endswith(".git")
            
            # 初始化要保存的变量
            new_project_path = "默认"
            reply_text = ""
            
            if is_git_url:
                # 1. 尝试解析项目名称 (从 URL 提取末尾)
                url_path = input_text
                if url_path.endswith(".git"):
                    url_path = url_path[:-4]
                project_name = url_path.split("/")[-1].split(":")[-1].strip()
                if not project_name:
                    import uuid
                    project_name = f"git_project_{uuid.uuid4().hex[:6]}"
                
                new_project_path = os.path.join(parent_path, project_name)
                if os.path.exists(new_project_path):
                    reply_text = f"❌ **克隆失败**：目录 `{new_project_path}` 已经存在，无法重复创建。"
                else:
                    # 发送克隆中消息，避免挂机误解
                    await asyncio.get_running_loop().run_in_executor(
                        None, 
                        lambda: send_reply_sdk(message_id, f"📥 **正在从远程仓库克隆项目**...\n\n- 目标地址：`{input_text}`\n- 保存路径：`{new_project_path}`\n\n*(已禁用终端密码交互，若为私有仓库请确认已授权，请稍候...)*")
                    )
                    
                    # 2. 执行 git clone
                    # 设定环境变量 GIT_TERMINAL_PROMPT=0 强制禁用终端密码交互，遇到私有仓库立刻 fail-fast 退出
                    git_env = os.environ.copy()
                    git_env["GIT_TERMINAL_PROMPT"] = "0"
                    
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "git", "clone", input_text, new_project_path,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=git_env
                        )
                        # 设置 45 秒超时，双重防挂起
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                        
                        if proc.returncode == 0:
                            # 3. 克隆成功，切换活跃工作区并持久化
                            session_data["project"] = new_project_path
                            
                            recent = session_data.get("recent_projects", [])
                            if new_project_path in recent:
                                recent.remove(new_project_path)
                            recent.insert(0, new_project_path)
                            session_data["recent_projects"] = recent[:5]
                            
                            # 自动为克隆的项目注入专属 Prompt
                            prompt_text = f"当前已锁定活跃开发项目 '{project_name}'，其物理路径位于 '{new_project_path}'。您在分析、阅读、修改代码或运行命令等所有操作时，必须严格局限在此项目目录中执行。"
                            project_prompts = session_data.get("project_prompts", {})
                            project_prompts[new_project_path] = prompt_text
                            session_data["project_prompts"] = project_prompts
                            
                            reply_text = f"✅ **远程项目克隆并设定成功！**\n\n- 项目名称：`{project_name}`\n- 物理路径：`{new_project_path}`\n\n当前已将此项目设为您的活跃开发工作区。"
                        else:
                            err_msg = stderr.decode(errors='ignore').strip()
                            # 判定是否可能是私有仓库权限问题
                            auth_hint = ""
                            if any(phrase in err_msg for phrase in ["terminal prompts disabled", "Permission denied", "fatal: Authentication failed", "fatal: could not read Username"]):
                                auth_hint = "\n\n💡 *这可能是一个私有仓库。请先确保您的机器人宿主机配置了对应的 SSH 密钥（在 GitHub 绑定 id_rsa.pub），或者使用了带有 Access Token 凭证的 HTTPS URL 格式。*"
                            
                            reply_text = f"❌ **仓库克隆失败** (返回码 {proc.returncode})：\n```\n{err_msg}\n```{auth_hint}"
                            new_project_path = "默认"
                            
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except:
                            pass
                        reply_text = "❌ **克隆超时**：45 秒内未完成 Git 克隆，已强制终止进程。\n\n💡 *如果是私有仓库，可能会因为缺乏账户凭证或 SSH 密钥而无法访问，请验证克隆地址或本地权限。*"
                        new_project_path = "默认"
                    except Exception as e:
                        reply_text = f"❌ **执行 Git 克隆时发生意外错误**：\n`{str(e)}`"
                        new_project_path = "默认"
            else:
                # 走普通的本地项目创建逻辑
                project_name = input_text
                new_project_path = os.path.join(parent_path, project_name)
                try:
                    os.makedirs(new_project_path, exist_ok=True)
                    try:
                        subprocess.run(["git", "init"], cwd=new_project_path, capture_output=True)
                    except Exception as git_err:
                        log.warning(f"Failed to auto-init git in {new_project_path}: {git_err}")
                    
                    session_data["project"] = new_project_path
                    recent = session_data.get("recent_projects", [])
                    if new_project_path in recent:
                        recent.remove(new_project_path)
                    recent.insert(0, new_project_path)
                    session_data["recent_projects"] = recent[:5]
                    
                    # 自动设定项目的专属 Prompt 提示词（融合名称与目录绝对路径）
                    auto_prompt = f"当前已锁定活跃开发项目 '{project_name}'，其物理路径位于 '{new_project_path}'。您在分析、阅读、修改代码或运行命令等所有操作时，必须严格局限在此项目目录中执行。"
                    prompts = session_data.get("project_prompts", {})
                    prompts[new_project_path] = auto_prompt
                    session_data["project_prompts"] = prompts
                    
                    reply_text = f"✨ **新项目目录创建并切换成功！**\n\n📁 **物理路径**：`{new_project_path}`\n*(已自动在本地初始化 Git 仓库)*\n\n🎯 **项目专属 Prompt 已自动绑定**：\n> {auto_prompt}\n\n当前工作空间已成功锁定该项目，您可以开始发送开发指令了！"
                except Exception as e:
                    reply_text = f"❌ **新建项目失败**（无法创建该目录，可能是权限不足）:\n`{str(e)}`"
                    new_project_path = "默认"
            
            session_data.pop("pending_command", None)
            session_data.pop("create_project_parent", None)
            await save_session_async(chat_id, session_data)
            
            success_card = CardBuilder.build_ai_response(
                reply_text,
                current_model=session_data.get("model", "Default"),
                current_role=session_data.get("role", "无"),
                current_project=new_project_path if os.path.exists(new_project_path) else "默认"
            )
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, success_card))
            return True, user_text

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
                    
        has_running = chat_id in running_processes
        has_worker = chat_workers and chat_id in chat_workers and not chat_workers[chat_id].done()
        
        if has_running or has_worker or cleared:
            # Kill the subprocess
            try:
                if chat_id in running_processes:
                    process = running_processes.pop(chat_id, None)
                    if process:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except:
                            process.kill()
            except:
                pass
            
            # Cancel the worker task to fully release the queue lock
            if chat_workers and chat_id in chat_workers:
                chat_workers[chat_id].cancel()
                chat_workers.pop(chat_id, None)
                
            reply_text = "🛑 当前任务已被紧急叫停，排队中的任务也已清空！"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        else:
            reply_text = "ℹ️ 当前没有正在运行的任务。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/clear"):
        session_data["conversation"] = ""
        await save_session_async(chat_id, session_data)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text.startswith("/remember"):
        parts = user_text.split(" ", 1)
        if len(parts) > 1 and parts[1].strip():
            memory_text = parts[1].strip()
            memories = await get_profile_async(chat_id)
            memories.append(memory_text)
            await save_profile_async(chat_id, memories)
            reply_text = f"🧠 已为您永久记录偏好：\n- {memory_text}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
        else:
            session_data["pending_command"] = "remember"
            await save_session_async(chat_id, session_data)
            reply_text = "🧠 请直接输入您希望我永久记住的偏好或设定："
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
        
    elif user_text.startswith("/memory"):
        memories = await get_profile_async(chat_id)
        memory_card = CardBuilder.build_memory_card(memories)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, memory_card))
        return True, user_text
        
    elif user_text == "/ping":
        reply_text = "🏓 Pong! 核心系统运行正常，网络连接畅通。"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text
        
    elif user_text == "/update":
        reply_text = "🔍 正在从云端拉取最新版本信息，请稍候..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        
        try:
            # Fetch latest from origin
            subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=True)
            
            # Get hashes for comparison
            local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
            remote_hash = subprocess.run(["git", "rev-parse", "--short", "origin/main"], capture_output=True, text=True).stdout.strip()
            
            local_version_str = get_version_string("HEAD")
            remote_version_str = get_version_string("origin/main")
            
            if local_hash == remote_hash:
                no_update_card = CardBuilder.build_no_update_card(local_version_str)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, no_update_card))
            else:
                # Get changelog
                changelog_cmd = ["git", "log", f"{local_hash}..origin/main", "--pretty=format:- %s"]
                changelog = subprocess.run(changelog_cmd, capture_output=True, text=True).stdout.strip()
                if not changelog:
                    changelog = "- 未知更新"
                
                # Send update card
                update_card = CardBuilder.build_update_card(local_version_str, remote_version_str, changelog)
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
            # Hard reset to origin/main
            subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, text=True, check=True)
            
            # Install new requirements if any
            pip_cmd = ["venv/bin/pip", "install", "-r", "requirements.txt"]
            subprocess.run(pip_cmd, capture_output=True, text=True)
            
            reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            
            # Save pending update state for post-reboot notification
            from config import BASE_DIR
            pending_file = os.path.join(BASE_DIR, ".update_pending.json")
            with open(pending_file, "w") as f:
                json.dump({"chat_id": chat_id, "message_id": message_id}, f)
            
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

    elif user_text.startswith("/note"):
        parts = user_text.split(" ", 1)
        subcommand = parts[1].strip() if len(parts) > 1 else ""
        notes = session_data.get("notes", [])
        
        if not subcommand or subcommand == "list" or user_text.strip() == "/notes":
            note_card = CardBuilder.build_note_list_card(notes)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, note_card))
            return True, user_text
            
        elif subcommand.startswith("add "):
            note_content = subcommand[4:].strip()
            notes.append(note_content)
            session_data["notes"] = notes
            await save_session_async(chat_id, session_data)
            reply_text = f"✅ 已保存笔记：\n{note_content}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif subcommand.startswith("del "):
            try:
                idx = int(subcommand[4:].strip()) - 1
                if 0 <= idx < len(notes):
                    deleted = notes.pop(idx)
                    session_data["notes"] = notes
                    await save_session_async(chat_id, session_data)
                    reply_text = f"🗑️ 已删除笔记：\n{deleted}"
                else:
                    reply_text = "❌ 找不到指定编号的笔记，请使用 `/note list` 查看编号。"
            except ValueError:
                reply_text = "❌ 格式错误，正确用法：`/note del <编号>`"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif subcommand == "clear":
            session_data["notes"] = []
            await save_session_async(chat_id, session_data)
            reply_text = "🧹 您的记事本已清空！"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        else:
            # 默认直接作为添加
            notes.append(subcommand)
            session_data["notes"] = notes
            await save_session_async(chat_id, session_data)
            reply_text = f"✅ 已保存笔记：\n{subcommand}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

    elif user_text.strip() == "/status":
        cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = get_system_status_card_data()
        status_card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, status_card))
        return True, user_text

    elif user_text.strip() == "/brain":
        memories = []
        memory_file = os.path.expanduser("~/.gemini/antigravity-cli/global_memory.json")
        try:
            if os.path.exists(memory_file):
                with open(memory_file, "r", encoding="utf-8") as f:
                    memories = json.load(f)
        except Exception as e:
            log.error(f"Error reading memory file: {e}")
            
        memory_card = CardBuilder.build_global_memory_card(memories)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, memory_card))
        return True, user_text

    elif user_text.startswith("/role"):
        parts = user_text.split(" ", 1)
        if len(parts) > 1 and parts[1].strip():
            new_role = parts[1].strip()
            session_data["role"] = new_role
            await save_session_async(chat_id, session_data)
            user_text = f"请记住以下设定，并在接下来的对话中始终扮演这个角色：{new_role}。收到请回复：'好的，角色设定已生效！'"
            return False, user_text
        else:
            session_data["pending_command"] = "role"
            await save_session_async(chat_id, session_data)
            reply_text = "🎭 请直接输入您希望我扮演的角色（例如：资深Python工程师）："
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
    elif user_text.startswith("/project"):
        args = user_text[len("/project"):].strip()
        if args:
            target_path = args
            if target_path.startswith("~"):
                target_path = os.path.expanduser(target_path)
            target_path = os.path.abspath(target_path)
            
            # 检验路径是否存在以及是否为文件夹
            if not os.path.exists(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的物理路径在系统上不存在，请核对拼写：\n`{target_path}`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
                return True, user_text
                
            if not os.path.isdir(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的路径不是一个合法的目录/文件夹：\n`{target_path}`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
                return True, user_text
            
            # 只设定公共项目根目录，不作为当前项目，也不进行物理创建
            session_data["workspace_root"] = target_path
            await save_session_async(chat_id, session_data)
            
            reply_text = f"⚙️ **公共项目根目录设定成功！**\n\n- 当前公共项目根目录已设定为：`{target_path}`\n- 后续所有新建项目都将**默认创建在此目录下**，列表面板也将绑定至此。\n*(当前活跃开发工作区保持不变)*"
                
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
        else:
            start_path = session_data.get("project", "默认")
            ws_root = session_data.get("workspace_root")
            proj_root = ws_root if ws_root and os.path.exists(ws_root) else WORKSPACE_ROOT
            
            if start_path in ["默认", "Default"] or not os.path.exists(start_path):
                start_path = proj_root
            
            recent_projects = session_data.get("recent_projects", [])
            ignored_projects = session_data.get("ignored_projects", [])
            browser_card = CardBuilder.build_dir_browser_card(start_path, recent_projects, workspace_root=proj_root, ignored_projects=ignored_projects)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, browser_card))
            return True, user_text
        
    elif user_text.startswith("/help"):
        reply_text = """💡 **Antigravity 机器人高级操作指南**

🔹 `/model` : 弹出交互式控制面板，自由切换大模型
🔹 `/role <设定>` : 让机器人扮演特定角色 (例如: `/role 资深Python工程师`)
🔹 `/project [路径]` : 管理及切换工作区项目 (不带参发送可视化项目管理器，支持翻页选择与新建；带参直接精准切换至指定路径)
🔹 `/remember <设定>` : 让机器人永久记住你的偏好 (例如: `/remember 我写代码只用 Python`)
🔹 `/memory` : 查看机器人当前记住的所有偏好
🔹 `/forget` : 清除机器人的长时记忆偏好
🔹 `/note [内容]` : 添加或管理备忘录 (支持 add/list/del/clear)
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
