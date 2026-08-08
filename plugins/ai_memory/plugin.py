"""AI Memory Plugin for antigravity-feishu-bot."""

import asyncio
from plugin_base import BasePlugin
from cards import CardBuilder
from database import get_profile_async
from logger import log


class AIMemoryPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] AI Memory plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() == "/memory":
            memories = await get_profile_async(chat_id)
            card = CardBuilder.build_memory_card(memories)
            self.send_reply_card(message_id, card)
            return True
        return False

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        memories = await get_profile_async(chat_id)
        if memories:
            mem_str = " | ".join(memories)
            log.info(f"[Plugin:ai_memory] Auto-injecting {len(memories)} user memories into prompt.")
            session_data["memory_context"] = mem_str
        return user_text, session_data
