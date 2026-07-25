
async def _execute_project_creation(input_text, ideal_path, parent_path, is_git_url, message_id, chat_id, session_data):
    import os, asyncio, subprocess
    from lark_client import send_reply_sdk
    
    dir_name = os.path.basename(ideal_path)
    new_project_path = ideal_path
    
    if is_git_url:
        reply_text = f"🔄 正在为您克隆 Git 仓库 `{input_text}`，请稍候..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        try:
            subprocess.run(["git", "clone", input_text, new_project_path], capture_output=True, text=True, check=True, timeout=120)
            reply_text = f"✅ Git 仓库克隆成功！\n📂 已将当前项目切换为：`{dir_name}`\n\n✨ 已成功清空当前上下文信息。"
        except subprocess.CalledProcessError as e:
            reply_text = f"❌ 克隆失败：{e.stderr}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, input_text
    else:
        try:
            os.makedirs(new_project_path, exist_ok=True)
            prompt_path = os.path.join(new_project_path, "prompt.txt")
            with open(prompt_path, "w") as f:
                f.write(f"项目目标：{input_text}\n请在此基础上进行开发。")
            reply_text = f"✅ 新项目创建成功！\n📂 已将当前项目切换为：`{dir_name}`\n\n✨ 已成功清空当前上下文信息。"
        except Exception as e:
            reply_text = f"❌ 创建目录失败：{str(e)}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, input_text

    session_data["project"] = new_project_path
    session_data["conversation"] = ""  # 彻底清空上个项目的 LLM 会话上下文
    session_data.pop("pending_command", None)
    from database import save_session_async
    await save_session_async(chat_id, session_data)
    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
    return True, input_text

async def _handle_create_project(user_text, message_id, chat_id, session_data, resolution=None):
    from config import WORKSPACE_ROOT
    import re, os, asyncio, shutil
    from lark_client import send_interactive_card_sdk
    
    input_text = user_text.strip()
    ws_root = session_data.get("workspace_root")
    parent_path = ws_root if ws_root and os.path.exists(ws_root) else WORKSPACE_ROOT
    
    git_pattern = re.compile(r'^(https?://|git@|git://)[^\s]+$', re.IGNORECASE)
    is_git_url = bool(git_pattern.match(input_text)) or input_text.endswith(".git")
    
    if is_git_url:
        repo_name = input_text.split("/")[-1].replace(".git", "")
        if not repo_name:
            repo_name = "repo"
        clean_dir_name = repo_name
    else:
        clean_dir_name = re.sub(r'[^a-zA-Z0-9_一-龥]', '_', input_text)[:20]
        if not clean_dir_name:
            clean_dir_name = "project"
            
    ideal_path = os.path.join(parent_path, clean_dir_name)
    
    if os.path.exists(ideal_path) and resolution is None:
        conflict_card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "⚠️ 项目名称冲突"}, "template": "yellow"},
            "elements": [
                {"tag": "markdown", "content": f"目标路径下已存在同名项目：`{clean_dir_name}`\n请选择后续操作："},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "1. 增加后缀保留新项目"}, "type": "primary", "value": {"action": "user_choice", "choice": f"/newproj_resolve keep {input_text}", "label": "保留并增加后缀"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "2. 覆盖原有项目"}, "type": "danger", "value": {"action": "user_choice", "choice": f"/newproj_resolve replace {input_text}", "label": "覆盖原项目"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "3. 取消并切换至旧项目"}, "type": "default", "value": {"action": "user_choice", "choice": f"/newproj_resolve cancel {input_text}", "label": "取消并切换"}}
                ]}
            ]
        }
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, conflict_card))
        return True, user_text

    if resolution == "keep":
        import hashlib
        proj_hash = hashlib.md5(input_text.encode()).hexdigest()[:6]
        ideal_path = os.path.join(parent_path, f"{clean_dir_name}_{proj_hash}")
    elif resolution == "replace":
        if os.path.exists(ideal_path):
            shutil.rmtree(ideal_path, ignore_errors=True)
    elif resolution == "cancel":
        session_data["project"] = ideal_path
        session_data["conversation"] = ""  # 彻底清空上个项目的 LLM 会话上下文
        session_data.pop("pending_command", None)
        from database import save_session_async
        await save_session_async(chat_id, session_data)
        reply_text = f"📂 已取消新建，直接为您切换至现有同名项目：`{clean_dir_name}`\n\n✨ 已成功清空当前上下文信息。"
        from lark_client import send_reply_sdk
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text

    return await _execute_project_creation(input_text, ideal_path, parent_path, is_git_url, message_id, chat_id, session_data)

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
from config import ANTIGRAVITY_BIN, BASE_VERSION_PREFIX, VERSION_START_COMMIT, WORKSPACE_ROOT, BASE_DIR

def get_version_string(commit_ref="HEAD"):
    try:
        count_str = subprocess.run(["git", "rev-list", "--count", commit_ref], capture_output=True, text=True, timeout=5, cwd=BASE_DIR).stdout.strip()
        commit_count = int(count_str)
        patch = max(1, commit_count - VERSION_START_COMMIT)
        hash_str = subprocess.run(["git", "rev-parse", "--short", commit_ref], capture_output=True, text=True, timeout=5, cwd=BASE_DIR).stdout.strip()
        return f"{BASE_VERSION_PREFIX}{patch} (Build: {hash_str})"
    except Exception:
        return f"Unknown (Build: error)"

def get_system_status_card_data():
    try:
        out = subprocess.check_output(['pm2', 'jlist'], text=True, timeout=10)
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
        
        err_out = subprocess.check_output(['pm2', 'logs', 'feishu-bot', '--err', '--lines', '5', '--nostream'], text=True, timeout=10)
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
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, timeout=5).strip()
            commit_info = subprocess.check_output(["git", "log", "-1", "--format=%h - %s (%cr)"], text=True, timeout=5).strip()
            
            # try to fetch silently
            subprocess.run(["git", "fetch"], timeout=3, capture_output=True)
            status_out = subprocess.check_output(["git", "status", "-sb"], text=True, timeout=5).strip().split('\n')[0]
            
            update_hint = ""
            if "behind" in status_out:
                update_hint = " ⚠️ **(有新版本可更新)**"
                
            git_status = f"分支: `{branch}`\n最新: `{commit_info}`{update_hint}"
        except Exception:
            git_status = "无法获取 Git 状态"
            
        return cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats
    except Exception as e:
        return 0, 0, "Error", "error", 0, str(e), "Error", {}
from enum import Enum

class PendingCommand(str, Enum):
    ROLE = "role"
    PROJECT = "project"
    CREATE_PROJECT = "create_project"
    CUSTOM_PROJECT_PATH = "custom_project_path"
    NOTE_ADD = "note_add"
    MEMORY_ADD = "memory_add"

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
        if pending_command in [PendingCommand.CUSTOM_PROJECT_PATH, "custom_project_path"]:
            target_path = user_text.strip()
            if target_path.startswith("~"):
                target_path = os.path.expanduser(target_path)
            target_path = os.path.abspath(target_path)
            
            if not os.path.exists(target_path):
                reply_text = f"❌ **路径设置失败！**\n\n您输入的物理路径在系统上不存在，请核对拼写：\n`{target_path}`"
            elif not os.path.isdir(target_path):
                reply_text = f"❌ **路径设置失败！**\n\n您输入的路径不是有效目录：\n`{target_path}`"
            else:
                session_data["project"] = target_path
                session_data["conversation"] = ""
                from project_tracker import ensure_and_read_project_tracker
                ensure_and_read_project_tracker(target_path)
                reply_text = f"📂 **开发工作区设置成功！**\n\n- 当前活跃工作区：`{target_path}`\n\n✨ **已成功清空当前上下文信息，项目隐秘档案防护已开启！**"
                
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command in [PendingCommand.NOTE_ADD, "note_add"]:
            note_content = user_text.strip()
            notes = session_data.get("notes", [])
            notes.append(note_content)
            session_data["notes"] = notes
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            reply_text = f"✅ **已保存笔记：**\n\n{note_content}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command in [PendingCommand.MEMORY_ADD, "memory_add"]:
            memory_text = user_text.strip()
            memories = await get_profile_async(chat_id)
            memories.append(memory_text)
            await save_profile_async(chat_id, memories)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            reply_text = f"🧠 **已为您保存个人偏好：**\n- {memory_text}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif pending_command == PendingCommand.ROLE:
            new_role = user_text.strip()
            session_data["role"] = new_role
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            user_text = f"请记住以下设定，并在接下来的对话中始终扮演这个角色：{new_role}。收到请回复：'好的，角色设定已生效！'"
            return False, user_text
            
        elif pending_command == PendingCommand.PROJECT:
            new_project = user_text.strip()
            session_data["conversation"] = ""  # 彻底清空上个项目的 LLM 会话上下文
            if new_project.lower() in ["clear", "default", "默认", "reset"]:
                session_data["project"] = "默认"
                reply_text = "📂 已将项目重置为默认工作空间！\n\n✨ 已成功清空当前上下文信息。"
            else:
                session_data["project"] = new_project
                from project_tracker import ensure_and_read_project_tracker
                ensure_and_read_project_tracker(new_project)
                reply_text = f"📂 已成功将当前项目切换为：`{new_project}`\n\n✨ 已成功清空当前上下文信息，已成功载入该项目的隐秘追踪档案 (`.project_track.secret.md`)，防泄露防护已开启！"
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text
            
        elif pending_command == PendingCommand.CREATE_PROJECT:
            return await _handle_create_project(user_text, message_id, chat_id, session_data)

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
        
        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        
        try:
            # Try origin first, fallback to gitee
            try:
                subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=True, timeout=10, env=custom_env, cwd=BASE_DIR)
                remote_ref = "origin/main"
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                log.warning(f"Fetch from origin failed, trying Gitee: {e}")
                # We use the full URL with the user's gitee token to ensure it works even if the remote isn't explicitly configured on the server
                gitee_url = "https://singkong:8e73f3dd0f5dd66eb133988ba7a4c042@gitee.com/singkong/antigravity-feishu-bot.git"
                subprocess.run(["git", "fetch", gitee_url, "main"], capture_output=True, text=True, check=True, timeout=15, env=custom_env, cwd=BASE_DIR)
                remote_ref = "FETCH_HEAD"
            
            # Get hashes for comparison
            local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()
            remote_hash = subprocess.run(["git", "rev-parse", "--short", remote_ref], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()
            
            local_version_str = get_version_string("HEAD")
            remote_version_str = get_version_string(remote_ref)
            
            if local_hash == remote_hash:
                no_update_card = CardBuilder.build_no_update_card(local_version_str)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, no_update_card))
            else:
                # Get changelog
                changelog_cmd = ["git", "log", f"{local_hash}..{remote_ref}", "--pretty=format:- %s"]
                changelog = subprocess.run(changelog_cmd, capture_output=True, text=True, timeout=10, cwd=BASE_DIR).stdout.strip()
                if not changelog:
                    changelog = "- 未知更新"
                
                # Send update card
                update_card = CardBuilder.build_update_card(local_version_str, remote_version_str, changelog)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, update_card))
                
        except subprocess.TimeoutExpired:
            log.warning("Git fetch timed out")
            error_text = "❌ 检查更新超时 (15s): 网络连接 GitHub/Gitee 不佳，请稍后重试或检查服务器外网连通性。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
        except FileNotFoundError:
            error_text = "❌ 检查更新失败: 服务器上未安装 `git` 命令，无法获取云端代码库版本。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
        except subprocess.CalledProcessError as e:
            log.error(f"Git fetch error: {e.stderr}")
            error_text = f"❌ 拉取失败: \n`{e.stderr.strip()}`\n(请检查您的 git 远程凭证或鉴权设置)"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
        except Exception as e:
            log.error(f"Failed to check for updates: {e}")
            error_text = f"❌ 检查更新失败: {e}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
            
        return True, user_text
        
    elif user_text == "/update confirm":
        reply_text = "⬇️ 正在执行核心系统升级，请勿中断..."
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        
        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        
        try:
            # Safe update without losing local uncommitted changes
            subprocess.run(["git", "stash"], capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR)
            
            try:
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, check=True, timeout=15, env=custom_env, cwd=BASE_DIR)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                log.warning(f"Pull from origin failed, trying Gitee: {e}")
                gitee_url = "https://singkong:8e73f3dd0f5dd66eb133988ba7a4c042@gitee.com/singkong/antigravity-feishu-bot.git"
                subprocess.run(["git", "pull", "--rebase", gitee_url, "main"], capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR)
                
            subprocess.run(["git", "stash", "pop"], capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR)
            
            # Install new requirements if any
            pip_cmd = ["venv/bin/pip", "install", "-r", "requirements.txt"]
            subprocess.run(pip_cmd, capture_output=True, text=True, timeout=60, cwd=BASE_DIR)
            
            reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            
            # Save pending update state for post-reboot notification
            pending_file = os.path.join(BASE_DIR, ".update_pending.json")
            with open(pending_file, "w") as f:
                json.dump({"chat_id": chat_id, "message_id": message_id}, f)
            
            # Restart via pm2 in background without waiting, fully detached streams
            subprocess.Popen(
                ["pm2", "restart", "feishu-bot"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Update git error: {e.stderr}")
            error_text = f"❌ 升级执行失败: \n`{e.stderr.strip()}`"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
        except Exception as e:
            log.error(f"Failed to apply update: {e}")
            error_text = f"❌ 升级过程中出现错误: {e}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
            
        return True, user_text

        
    elif user_text.startswith("/newproj_resolve"):
        parts = user_text.split(" ", 2)
        if len(parts) >= 3:
            resolution = parts[1].strip()
            input_text = parts[2].strip()
            return await _handle_create_project(input_text, message_id, chat_id, session_data, resolution=resolution)
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

    elif user_text.strip() == "/context":
        from utils import get_context_usage_stats
        stats = get_context_usage_stats(session_data)
        context_card = CardBuilder.build_context_card(stats)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, context_card))
        return True, user_text

    elif user_text.strip() == "/quota":
        import re
        import urllib.request
        import ssl
        
        lsp_port = None
        quota_data = None
        try:
            candidate_ports = set()
            for pid_dir in os.listdir("/proc"):
                if not pid_dir.isdigit():
                    continue
                try:
                    cmdline_path = f"/proc/{pid_dir}/cmdline"
                    with open(cmdline_path, "rb") as f:
                        cmdline = f.read().decode("utf-8", errors="ignore")
                    if "agy" not in cmdline and "antigravity" not in cmdline:
                        continue
                    log.info(f"[/quota] Found agy process pid={pid_dir}")
                    fd_dir = f"/proc/{pid_dir}/fd"
                    if not os.path.isdir(fd_dir):
                        continue
                    
                    found_any_socket = False
                    for fd in os.listdir(fd_dir):
                        try:
                            link = os.readlink(f"{fd_dir}/{fd}")
                            if "socket:" in link:
                                found_any_socket = True
                                inode = link.split("[")[1].rstrip("]")
                                with open("/proc/net/tcp", "r") as tcp_f:
                                    for tcp_line in tcp_f:
                                        parts = tcp_line.strip().split()
                                        if len(parts) >= 10 and parts[9] == inode:
                                            if parts[3] == "0A":
                                                hex_port = parts[1].split(":")[1]
                                                port = int(hex_port, 16)
                                                candidate_ports.add(port)
                        except Exception as e:
                            log.warning(f"[/quota] Error processing fd {fd} for pid {pid_dir}: {e}")
                except Exception as e:
                    log.warning(f"[/quota] Error processing pid {pid_dir}: {e}")
            
            if not candidate_ports:
                try:
                    out = subprocess.check_output("/usr/bin/ss -tlnp", shell=True, text=True, timeout=3)
                    for line in out.split("\n"):
                        if 'users:(("agy"' in line or 'users:(("antigravity"' in line:
                            match = re.search(r"127\.0\.0\.1:(\d+)", line)
                            if match:
                                candidate_ports.add(int(match.group(1)))
                except Exception as e:
                    log.warning(f"[/quota] fallback ss failed: {e}")
            
            context = ssl._create_unverified_context()
            metadata_payload = b'{"metadata": {"ideName": "antigravity", "extensionName": "antigravity"}}'
            
            def probe_port(port):
                url = f"https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
                req = urllib.request.Request(url, data=metadata_payload, headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, context=context, timeout=5) as response:
                        data = json.loads(response.read().decode())
                        if "response" in data and "groups" in data["response"]:
                            return port, data
                except Exception as e:
                    log.warning(f"[/quota] Port {port} failed: {e}")
                return port, None

            import concurrent.futures
            if candidate_ports:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, len(candidate_ports))) as executor:
                    futures = [executor.submit(probe_port, p) for p in candidate_ports]
                    for future in concurrent.futures.as_completed(futures):
                        p, data = future.result()
                        if data:
                            quota_data = data
                            lsp_port = p
                            break
        except Exception as e:
            log.error(f"Error discovering LSP port: {e}")
                
        if not quota_data:
            token_path = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r") as f:
                        token_info = json.load(f)
                    access_token = token_info["token"]["access_token"]
                    url = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
                    req = urllib.request.Request(
                        url,
                        data=b'{"project":"high-battery-8d2jw"}',
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "User-Agent": "antigravity/cli/1.1.3"
                        },
                        method="POST"
                    )
                    context = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, context=context) as response:
                        api_data = json.loads(response.read().decode())
                        quota_data = {"response": api_data}
                except Exception as e:
                    log.error(f"Error fetching remote quota fallback: {e}")
                    
        quota_card = CardBuilder.build_quota_card(quota_data)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, quota_card))
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

    elif user_text.strip() == "/test_ss":
        try:
            out = subprocess.check_output("/usr/bin/ss -tlnp", shell=True, text=True, timeout=3)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, {
                "config": {"wide_screen_mode": True},
                "header": {"template": "blue", "title": {"content": "SS Output", "tag": "plain_text"}},
                "elements": [{"tag": "markdown", "content": f"```\n{out[:1000]}\n```"}]
            }))
        except Exception as e:
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, {
                "config": {"wide_screen_mode": True},
                "header": {"template": "red", "title": {"content": "SS Error", "tag": "plain_text"}},
                "elements": [{"tag": "markdown", "content": f"```\n{e}\n```"}]
            }))
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
            session_data["pending_command"] = PendingCommand.ROLE
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
            
            if not os.path.exists(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的物理路径在系统上不存在，请核对拼写：\n`{target_path}`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
                return True, user_text
                
            if not os.path.isdir(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的路径不是一个合法的目录/文件夹：\n`{target_path}`"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
                return True, user_text
            
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
        help_card = CardBuilder.build_help_card()
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, help_card))
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
