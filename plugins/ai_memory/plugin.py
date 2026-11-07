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
            proj_summary = None
            try:
                from agent_memory import get_project_memory_summary, inspect_and_backfill_transcript
                import config
                proj_dir = (session_data or {}).get("project", "默认")
                conv_id = (session_data or {}).get("conversation", "")
                if conv_id:
                    tx_path = config.get_transcript_path(conv_id)
                    inspect_and_backfill_transcript(tx_path, project_dir=proj_dir, chat_id=chat_id, conv_id=conv_id)
                proj_summary = get_project_memory_summary(proj_dir, session_data=session_data)
            except Exception as e:
                log.warning(f"[Plugin:ai_memory] Failed to load project memory summary: {e}")
            card = CardBuilder.build_memory_card(memories, project_memory_summary=proj_summary)
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
