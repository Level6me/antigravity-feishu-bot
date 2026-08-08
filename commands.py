
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
            reply_text = f"✅ Git 仓库克隆成功！\n📂 已将当前项目切换为：`{dir_name}`"
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
            reply_text = f"✅ 新项目创建成功！\n📂 已将当前项目切换为：`{dir_name}`"
        except Exception as e:
            reply_text = f"❌ 创建目录失败：{str(e)}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, input_text

    session_data["project"] = new_project_path
    session_data.pop("pending_command", None)
    from database import save_session_async
    await save_session_async(chat_id, session_data)
    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
    return True, input_text

async def _handle_create_project(user_text, message_id, chat_id, session_data, resolution=None):
    from config import WORKSPACE_ROOT, get_global_memory_path, get_oauth_token_path
    import re, os, asyncio, shutil
    from lark_client import send_interactive_card_sdk
    
    input_text = user_text.strip()
    ws_root = session_data.get("workspace_root")
    parent_path = ws_root if ws_root and os.path.exists(ws_root) else WORKSPACE_ROOT
    
    # Check if the input is an existing local directory
    expanded_path = os.path.abspath(os.path.expanduser(input_text))
    if os.path.isdir(expanded_path):
        session_data["project"] = expanded_path
        
        # Add to recent projects
        recent_projects = session_data.get("recent_projects", [])
        if expanded_path not in recent_projects:
            recent_projects.append(expanded_path)
            session_data["recent_projects"] = recent_projects
            
        session_data.pop("pending_command", None)
        from database import save_session_async
        await save_session_async(chat_id, session_data)
        
        dir_name = os.path.basename(expanded_path) or expanded_path
        reply_text = f"📂 已检测到现有项目目录，直接为您切换至该工作区：`{dir_name}`"
        from lark_client import send_reply_sdk
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text

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
        session_data.pop("pending_command", None)
        from database import save_session_async
        await save_session_async(chat_id, session_data)
        reply_text = f"📂 已取消新建，直接为您切换至现有同名项目：`{clean_dir_name}`"
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
from config import ANTIGRAVITY_BIN, BASE_VERSION_PREFIX, VERSION_START_COMMIT, WORKSPACE_ROOT, BASE_DIR, GITEE_MIRROR_URL
from database import list_auth_sessions
from utils.auth import SCOPE_TIERS, get_admin_chat_id, has_scope, is_admin, set_session_role

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
        
        # 脱敏：过滤敏感字段与绝对路径，仅保留最近 3 条摘要，避免向飞书暴露
        # token / 密钥 / 路径等内部信息。
        sensitive = re.compile(r'(?i)(token|secret|password|api[_-]?key|authorization|Bearer\s)')
        err_lines = []
        for l in err_out.split('\n'):
            line = l.strip()
            if not line or line.startswith('[TAILING]'):
                continue
            if sensitive.search(line) or '.env' in line or re.match(r'^/[^\s]+', line):
                continue
            line = re.sub(r'/Users/[^\s:]+', '<path>', line)
            err_lines.append(line[:160])
        err_logs = '\n'.join(err_lines[-3:]).strip()
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
    PROJECT = "project"
    CREATE_PROJECT = "create_project"
    CUSTOM_PROJECT_PATH = "custom_project_path"
    CUSTOM_WORKSPACE_ROOT = "custom_workspace_root"
    NOTE_ADD = "note_add"
    MEMORY_ADD = "memory_add"
    CRON_ADD = "cron_add"
    PLUGIN_INSTALL_GITHUB = "plugin_install_github"
    PLUGIN_ADD_SOURCE = "plugin_add_source"

async def handle_slash_command(user_text, message_id, chat_id, session_data, running_processes, chat_queues, chat_workers=None):
    log.info(f"handle_slash_command call: user_text='{user_text}', pending_command='{session_data.get('pending_command')}'")
    """
    Parses and handles slash commands. Returns True if a command was handled, False otherwise.
    Returns (handled: bool, override_user_text: str)
    """
    
    pending_command = session_data.get("pending_command")

    # Permission gate for privileged slash commands (guests never reach here;
    # this protects admin-only and scoped commands from regular users).
    if not is_admin(chat_id):
        if user_text.startswith("/update") or user_text.startswith("/user") or user_text.strip() == "/status":
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, "🔒 该命令仅管理员可用。"),
            )
            return True, user_text
        if user_text.strip() == "/quota" and not has_scope(chat_id, "quota"):
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, "🔒 当前会话未获得查看额度的权限。"),
            )
            return True, user_text
    
    # If the user typed a new slash command, clear any pending state
    first_word = user_text.split()[0] if user_text.strip() else ""
    is_slash_cmd = first_word in {
        "/help", "/model", "/card", "/menu", "/project", "/note", "/notes",
        "/status", "/context", "/quota", "/clear", "/stop", "/update", "/ping",
        "/newproj_resolve", "/cron", "/schedule", "/plugin", "/plugins"
    }
    
    if is_slash_cmd and pending_command:
        session_data.pop("pending_command", None)
        await save_session_async(chat_id, session_data)
        pending_command = None
        
    if not is_slash_cmd and pending_command:
        if pending_command == PendingCommand.CUSTOM_WORKSPACE_ROOT.value:
            target_path = user_text.strip()
            if target_path.startswith("~"):
                target_path = os.path.expanduser(target_path)
            target_path = os.path.abspath(target_path)
            
            if not os.path.exists(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的物理路径在系统上不存在，请核对拼写：\n`{target_path}`"
            elif not os.path.isdir(target_path):
                reply_text = f"❌ **路径设定失败！**\n\n您输入的路径不是一个合法的目录/文件夹：\n`{target_path}`"
            else:
                session_data["workspace_root"] = target_path
                reply_text = f"⚙️ **公共项目根目录设定成功！**\n\n- 当前公共项目根目录已设定为：`{target_path}`\n- 后续所有新建项目都将**默认创建在此目录下**，列表面板也将绑定至此。\n*(当前活跃开发工作区保持不变)*"
                
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command == PendingCommand.CUSTOM_PROJECT_PATH.value:
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
                reply_text = f"📂 **开发工作区设置成功！**\n\n- 当前活跃工作区：`{target_path}`"
                
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command == PendingCommand.NOTE_ADD.value:
            note_content = user_text.strip()
            notes = session_data.get("notes", [])
            notes.append(note_content)
            session_data["notes"] = notes
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            reply_text = f"✅ **已保存笔记：**\n\n{note_content}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command == PendingCommand.MEMORY_ADD.value:
            memory_text = user_text.strip()
            memories = await get_profile_async(chat_id)
            memories.append(memory_text)
            await save_profile_async(chat_id, memories)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            reply_text = f"🧠 **已为您保存个人偏好：**\n- {memory_text}"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            return True, user_text

        elif pending_command == PendingCommand.CRON_ADD.value:
            raw_input = user_text.strip()
            import re, time
            parts = [p.strip() for p in re.split(r'[|｜]', raw_input) if p.strip()]
            if len(parts) < 3:
                reply_text = "❌ **格式无效！**\n\n请按规范发送 3 段数据，用竖线 `|` 隔开：\n`任务名称 | 触发规则(如 0 9 * * * 或 600s) | 执行 Prompt`\n\n例如：`定时检查存储 | 0 9 * * * | 检查树莓派 iSCSI 运行状态`"
            else:
                name, expr, prompt = parts[0], parts[1], parts[2]
                task_type = 'delay' if re.match(r'^\d+\s*[s|m|h|d]?$', expr.lower()) else 'cron'
                
                from cron_engine import compute_next_run
                now_ts = int(time.time())
                next_run = compute_next_run(expr, task_type, now_ts)
                
                task_id = f"task_usr_{now_ts}"
                task_data = {
                    'id': task_id,
                    'chat_id': chat_id,
                    'category': 'user',
                    'name': name,
                    'task_type': task_type,
                    'cron_expr': expr,
                    'prompt': prompt,
                    'project_path': session_data.get('project', ''),
                    'is_active': True,
                    'created_by': chat_id,
                    'created_at': now_ts,
                    'next_run_at': next_run
                }
                
                from database import save_cron_task
                save_cron_task(task_data)
                created_card = CardBuilder.build_cron_created_card(task_data)
                session_data.pop("pending_command", None)
                await save_session_async(chat_id, session_data)
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, created_card))
                return True, user_text

        elif pending_command == PendingCommand.PLUGIN_INSTALL_GITHUB.value:
            repo_url = user_text.strip()
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)

            await asyncio.get_running_loop().run_in_executor(
                None, lambda: send_reply_sdk(message_id, f"⬇️ 正在从 GitHub 克隆安装插件 `{repo_url}`，请稍候...")
            )

            from plugin_store import install_plugin_from_github
            ok, msg = await asyncio.get_running_loop().run_in_executor(
                None, lambda: install_plugin_from_github(repo_url)
            )

            if ok:
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed")
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: send_interactive_card_sdk(message_id, new_card)
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: send_reply_sdk(message_id, f"❌ **插件安装失败：**\n{msg}")
                )
            return True, user_text

        elif pending_command == PendingCommand.PLUGIN_ADD_SOURCE.value:
            raw_input = user_text.strip()
            import re
            parts = [p.strip() for p in re.split(r'[|｜]', raw_input) if p.strip()]
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)

            if len(parts) < 2:
                reply_text = "❌ **格式错误！**\n请发送格式为 `源名称 | GitHub仓库URL [| 描述]` 的文本。"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
            else:
                name, url = parts[0], parts[1]
                desc = parts[2] if len(parts) > 2 else ""
                from plugin_store import add_plugin_source
                add_plugin_source(name, url, desc)
                from plugin_manager import plugin_manager
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="sources")
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: send_interactive_card_sdk(message_id, new_card)
                )
            return True, user_text
            
        elif pending_command == PendingCommand.PROJECT.value:
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
            
        elif pending_command == PendingCommand.CREATE_PROJECT.value:
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

    elif user_text.strip() == "/auth":
        reply_text = "✅ 当前会话已授权，无需申请。"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return True, user_text

    elif user_text.startswith("/user"):
        args = user_text[len("/user"):].strip()
        if not args:
            from utils.auth import start_display_name_refresh
            sessions = list_auth_sessions()
            task = start_display_name_refresh(sessions)
            try:
                # 面板最多等 3 秒；姓名解析放后台继续，下次打开即完整
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning("[/user] display-name resolution still running in background")
            panel = CardBuilder.build_user_panel_card(sessions)
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, panel))
            return True, user_text

        parts = args.split()
        op = parts[0].lower()

        if op == "grant" and len(parts) >= 2:
            target = parts[1]
            tier = parts[2] if len(parts) >= 3 and parts[2] in SCOPE_TIERS else "basic"
            set_session_role(target, "user", list(SCOPE_TIERS[tier]), operator=chat_id)
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, f"✅ 已授权 {target}（{tier}）。"),
            )
            return True, user_text

        if op in ("revoke", "ban", "unban") and len(parts) >= 2:
            target = parts[1]
            if op == "revoke":
                set_session_role(target, "guest", [], operator=chat_id)
                msg = f"✅ 已撤销 {target} 的授权。"
            elif op == "ban":
                set_session_role(target, "banned", [], operator=chat_id)
                msg = f"🚫 已拉黑 {target}。"
            else:
                set_session_role(target, "guest", [], operator=chat_id)
                msg = f"✅ 已解除 {target} 的拉黑。"
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, msg))
            return True, user_text

        if op == "promote" and len(parts) >= 2:
            target = parts[1]
            set_session_role(target, "admin", [], operator=chat_id)
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, f"👑 已提升 {target} 为管理员。"),
            )
            return True, user_text

        if op == "demote" and len(parts) >= 2:
            target = parts[1]
            set_session_role(target, "user", list(SCOPE_TIERS["dev"]), operator=chat_id)
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, f"✅ 已将 {target} 降为普通用户（开发权限）。"),
            )
            return True, user_text

        if op == "reset-admin":
            if args.strip() == "reset-admin confirm":
                admin_id = get_admin_chat_id()
                if admin_id:
                    set_session_role(admin_id, "user", list(SCOPE_TIERS["dev"]), operator=chat_id)
                set_session_role(chat_id, "admin", [], operator=chat_id)
                from database import set_bot_meta
                set_bot_meta("admin_chat_id", chat_id)
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: send_reply_sdk(message_id, "✅ 管理员已重新绑定到当前会话。"),
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: send_reply_sdk(
                        message_id,
                        "⚠️ 重新绑定管理员将把当前会话设为新的管理员。\n确认请发送：`/user reset-admin confirm`",
                    ),
                )
            return True, user_text

        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: send_reply_sdk(
                message_id,
                "用法：\n`/user` 打开管理面板\n`/user grant <chat_id> [basic|dev|full]`\n"
                "`/user revoke|ban|unban <chat_id>`\n`/user promote|demote <chat_id>`\n`/user reset-admin`",
            ),
        )
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
                if not GITEE_MIRROR_URL:
                    raise
                subprocess.run(["git", "fetch", GITEE_MIRROR_URL, "main"], capture_output=True, text=True, check=True, timeout=15, env=custom_env, cwd=BASE_DIR)
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
                if not GITEE_MIRROR_URL:
                    raise
                subprocess.run(["git", "pull", "--rebase", GITEE_MIRROR_URL, "main"], capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR)
                
            pop_res = subprocess.run(["git", "stash", "pop"], capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR)
            conflict_hint = ""
            if pop_res.returncode != 0:
                log.warning(f"git stash pop encountered conflicts or failed; reverting conflict markers: {pop_res.stderr}")
                # 冲突发生时，执行 git checkout -- . 清理冲突标记，防止 Python SyntaxError 无法启动
                subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, check=False, timeout=10, env=custom_env, cwd=BASE_DIR)
                conflict_note = os.path.join(BASE_DIR, ".update_conflict.txt")
                try:
                    with open(conflict_note, "w", encoding="utf-8") as nf:
                        nf.write(
                            "升级时 git stash pop 发生冲突，为保证系统正常启动已自动清理冲突标记。\n"
                            "之前的本地未提交改动仍保存在 git stash 中（可通过 `git stash list` 查看/恢复）。\n\n"
                            f"stash pop stderr:\n{pop_res.stderr[:2000]}\n"
                        )
                    conflict_hint = "\n\n⚠️ 本地改动与更新存在冲突，已清理冲突标记以保证正常启动（未提交改动存入 `git stash`）。"
                except Exception as nf_e:
                    log.error(f"Failed to write conflict note: {nf_e}")
            
            # Install new requirements if any
            pip_cmd = ["venv/bin/pip", "install", "-r", "requirements.txt"]
            subprocess.run(pip_cmd, capture_output=True, text=True, timeout=60, cwd=BASE_DIR)
            
            reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..." + conflict_hint
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
        from utils.quota import fetch_quota
        try:
            quota_data = await asyncio.get_running_loop().run_in_executor(None, fetch_quota)
        except Exception as e:
            log.error(f"[/quota] fetch_quota failed: {e}")
            quota_data = None
        quota_card = CardBuilder.build_quota_card(quota_data)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, quota_card))
        return True, user_text

    elif user_text.strip() == "/brain":
        memories = []
        memory_file = get_global_memory_path()
        try:
            if os.path.exists(memory_file):
                with open(memory_file, "r", encoding="utf-8") as f:
                    memories = json.load(f)
        except Exception as e:
            log.error(f"Error reading memory file: {e}")
            
        memory_card = CardBuilder.build_global_memory_card(memories)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, memory_card))
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
        try:
            stdout, _ = await asyncio.wait_for(fetch_proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            try:
                fetch_proc.kill()
            except Exception:
                pass
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: send_reply_sdk(message_id, "⏳ 模型列表获取超时，请稍后重试。"),
            )
            return True, user_text
        models_output = stdout.decode().strip()
        
        available_models = [line.strip() for line in models_output.split('\n') if line.strip()]
        if not available_models:
            available_models = ["Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)", "GPT-OSS 120B (Medium)"]
            
        card_content = CardBuilder.build_model_panel(available_models, session_data.get('model', 'Default'))
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))
        return True, user_text
        
    elif user_text.startswith("/cron") or user_text.startswith("/schedule"):
        from database import get_all_cron_tasks
        tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
        cron_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, cron_card))
        return True, user_text

    elif first_word in ["/plugin", "/plugins"]:
        from plugin_manager import plugin_manager
        args = user_text[len(first_word):].strip()
        if args == "reload":
            plugin_manager.reload_plugins()
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: send_reply_sdk(message_id, "✅ 已成功热重载插件中心！")
            )
        else:
            p_list = plugin_manager.get_plugin_list()
            card = CardBuilder.build_plugin_panel_card(p_list)
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: send_interactive_card_sdk(message_id, card)
            )
        return True, user_text

    # Dispatch command to plugin manager
    from plugin_manager import plugin_manager
    plugin_handled, p_res = await plugin_manager.dispatch_command(user_text, message_id, chat_id, session_data)
    if plugin_handled:
        return True, user_text

    return False, user_text
