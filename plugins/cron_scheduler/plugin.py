"""Cron Scheduler Plugin for antigravity-feishu-bot."""

import asyncio
from plugin_base import BasePlugin
from cards import CardBuilder
from database import get_all_cron_tasks, update_cron_task_status, delete_cron_task, get_cron_task
from lark_client import patch_interactive_card_sdk, send_reply_sdk
from logger import log


class CronSchedulerPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] Cron Scheduler plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() in ["/cron", "/schedule"]:
            tasks = get_all_cron_tasks(chat_id)
            card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
            self.send_reply_card(message_id, card)
            return True
        return False

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        if action == "switch_cron_tab":
            tab = value.get("tab", "user")
            tasks = get_all_cron_tasks(chat_id)
            new_card = CardBuilder.build_cron_panel_card(tasks, active_tab=tab, session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "toggle_cron_active":
            task_id = value.get("task_id")
            is_active = bool(value.get("is_active", True))
            update_cron_task_status(task_id, is_active)
            tasks = get_all_cron_tasks(chat_id)
            new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "delete_cron_task":
            task_id = value.get("task_id")
            delete_cron_task(task_id)
            tasks = get_all_cron_tasks(chat_id)
            new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "run_cron_now":
            task_id = value.get("task_id")
            task = get_cron_task(task_id)
            if task:
                from cron_engine import cron_engine
                asyncio.create_task(cron_engine._run_task_wrapper(task))
            return True

        return False
