"""System Updater Plugin for antigravity-feishu-bot."""

import sys
import os
import asyncio
import subprocess

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from plugin_base import BasePlugin
from cards import CardBuilder
from config import BASE_DIR, GITEE_MIRROR_URL
from lark_client import send_reply_sdk, send_interactive_card_sdk
from logger import log


class SystemUpdaterPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] System Updater plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() == "/update":
            clean_args = (args or "").strip().lower()

            if clean_args == "confirm":
                from commands import get_version_string
                from database import save_pending_update_notice
                import json

                old_version = await asyncio.get_running_loop().run_in_executor(None, lambda: get_version_string("HEAD"))

                reply_text = "⬇️ 正在执行核心系统升级，请勿中断..."
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))

                custom_env = os.environ.copy()
                custom_env["GIT_TERMINAL_PROMPT"] = "0"
                custom_env["DEBIAN_FRONTEND"] = "noninteractive"
                custom_env["GIT_ASKPASS"] = "echo"

                try:
                    subprocess.run(["git", "stash"], capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR)

                    try:
                        subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR)
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                        log.warning(f"Pull from origin failed, trying Gitee fallback: {e}")
                        if not GITEE_MIRROR_URL:
                            raise
                        subprocess.run(["git", "pull", "--rebase", GITEE_MIRROR_URL, "main"], capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR)

                    pop_res = subprocess.run(["git", "stash", "pop"], capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR)
                    conflict_hint = ""
                    if pop_res.returncode != 0:
                        log.warning(f"git stash pop encountered conflicts: {pop_res.stderr}")
                        subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, check=False, timeout=10, env=custom_env, cwd=BASE_DIR)
                        conflict_hint = "\n\n⚠️ 本地改动与更新存在冲突，已自动清理冲突标记以保证正常启动。"

                    pip_bin = os.path.join(BASE_DIR, "venv", "bin", "pip")
                    if os.path.exists(pip_bin):
                        pip_cmd = [pip_bin, "install", "-r", os.path.join(BASE_DIR, "requirements.txt")]
                        subprocess.run(pip_cmd, capture_output=True, text=True, timeout=60, cwd=BASE_DIR)

                    # Save pending update notice before restarting
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: save_pending_update_notice(chat_id, message_id, old_version)
                    )
                    pending_file = os.path.join(BASE_DIR, ".update_pending.json")
                    try:
                        with open(pending_file, "w") as f:
                            json.dump({"chat_id": chat_id, "message_id": message_id, "old_version": old_version}, f)
                    except Exception as ex:
                        log.error(f"Failed to write .update_pending.json: {ex}")

                    reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..." + conflict_hint
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))

                    subprocess.Popen(
                        ["pm2", "restart", "feishu-bot"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL
                    )
                except Exception as e:
                    log.error(f"Failed to apply update: {e}")
                    error_text = f"❌ 升级执行失败: {e}"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, error_text))
                return True

            reply_text = "🔍 正在从云端拉取最新版本信息，请稍候..."
            await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))

            custom_env = os.environ.copy()
            custom_env["GIT_TERMINAL_PROMPT"] = "0"
            custom_env["DEBIAN_FRONTEND"] = "noninteractive"
            custom_env["GIT_ASKPASS"] = "echo"

            try:
                try:
                    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=True, timeout=10, env=custom_env, cwd=BASE_DIR)
                    remote_ref = "origin/main"
                except Exception as e:
                    if GITEE_MIRROR_URL:
                        subprocess.run(["git", "fetch", GITEE_MIRROR_URL, "main"], capture_output=True, text=True, check=True, timeout=15, env=custom_env, cwd=BASE_DIR)
                        remote_ref = "FETCH_HEAD"
                    else:
                        raise e

                from commands import get_version_string
                local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()
                remote_hash = subprocess.run(["git", "rev-parse", "--short", remote_ref], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()

                local_version_str = get_version_string("HEAD")
                remote_version_str = get_version_string(remote_ref)

                if local_hash == remote_hash:
                    card = CardBuilder.build_no_update_card(local_version_str)
                else:
                    changelog_cmd = ["git", "log", f"{local_hash}..{remote_ref}", "--pretty=format:- %s"]
                    changelog = subprocess.run(changelog_cmd, capture_output=True, text=True, timeout=10, cwd=BASE_DIR).stdout.strip() or "- 未知更新"
                    card = CardBuilder.build_update_card(local_version_str, remote_version_str, changelog)

                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card))
            except Exception as ex:
                log.error(f"System update error: {ex}")
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, f"❌ 检查更新异常: {ex}"))
            return True
        return False
