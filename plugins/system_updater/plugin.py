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

                local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()
                remote_hash = subprocess.run(["git", "rev-parse", "--short", remote_ref], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()

                if local_hash == remote_hash:
                    card = CardBuilder.build_no_update_card(local_hash)
                else:
                    changelog_cmd = ["git", "log", f"{local_hash}..{remote_ref}", "--pretty=format:- %s"]
                    changelog = subprocess.run(changelog_cmd, capture_output=True, text=True, timeout=10, cwd=BASE_DIR).stdout.strip() or "- 未知更新"
                    card = CardBuilder.build_update_card(local_hash, remote_hash, changelog)

                await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card))
            except Exception as ex:
                log.error(f"System update error: {ex}")
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, f"❌ 检查更新异常: {ex}"))
            return True
        return False
