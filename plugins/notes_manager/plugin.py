"""Notes Manager Plugin for antigravity-feishu-bot."""

import asyncio
from plugin_base import BasePlugin
from cards import CardBuilder
from database import save_session_async
from lark_client import patch_interactive_card_sdk
from logger import log


class NotesManagerPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] Notes Manager plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        cmd = command.lower()
        if cmd == "/note" and args and args.startswith("add"):
            content = args[3:].strip()
            notes = session_data.get("notes", [])
            notes.append(content)
            session_data["notes"] = notes
            await save_session_async(chat_id, session_data)
            self.send_reply_text(message_id, f"✅ 已成功为您添加一条笔记：\n\"{content}\"")
            return True
        elif cmd in ["/notes", "/note"]:
            notes = session_data.get("notes", [])
            card = CardBuilder.build_note_list_card(notes)
            self.send_reply_card(message_id, card)
            return True
        return False

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        if action == "delete_note":
            idx = value.get("index")
            if idx is not None:
                from database import get_session_async
                session_data = await get_session_async(chat_id)
                notes = session_data.get("notes", [])
                if 0 <= idx < len(notes):
                    notes.pop(idx)
                    session_data["notes"] = notes
                    await save_session_async(chat_id, session_data)
                    new_card = CardBuilder.build_note_list_card(notes)
                    patch_interactive_card_sdk(card_message_id, new_card)
            return True
        return False
