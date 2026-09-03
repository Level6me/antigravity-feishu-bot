"""BasePlugin class for antigravity-feishu-bot plugin system."""

import os
import json
import asyncio
from logger import log
from lark_client import send_card_to_chat_sdk, send_text_to_chat_sdk, send_reply_sdk, send_interactive_card_sdk


class BasePlugin:
    """Base class that all bot plugins must inherit from."""

    def __init__(self, plugin_dir: str, manifest: dict):
        self.plugin_dir = plugin_dir
        self.manifest = manifest
        self.plugin_id = manifest.get("id", "")
        self.name = manifest.get("name", self.plugin_id)
        self.version = manifest.get("version", "1.0.0")
        self.commands = manifest.get("commands", [])
        self.enabled = manifest.get("enabled", True)

    def initialize(self):
        """Called once when plugin is loaded into system."""
        log.info(f"[Plugin:{self.plugin_id}] Initialized successfully.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        """Handle slash command registered by plugin.
        Return True if handled, False otherwise."""
        return False

    async def on_message(self, chat_id: str, user_text: str, message_id: str, session_data: dict) -> bool:
        """Hook for processing incoming user messages before AI pipeline.
        Return True to intercept and stop further processing."""
        return False

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """Hook for processing interactive card button clicks.
        Return True if handled."""
        return False

    async def on_cron(self):
        """Hook called periodically by CronEngine if scheduled."""
        pass

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        """Hook called right before user_text is passed to AI model.
        Can modify user_text or session_data. Return (user_text, session_data)."""
        return user_text, session_data

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """Hook called right after AI returns response text before sending to Feishu.
        Can post-process or modify ai_response_text."""
        return ai_response_text

    async def on_tool_call(self, tool_name: str, tool_args: dict):
        """Hook called when AI executes a tool action during pipeline."""
        pass

    def on_service_restarting(self):
        """Hook called when feishu-bot service is shutting down / restarting."""
        pass

    def send_card(self, chat_id: str, card_content: dict):
        """向指定 chat 发送交互卡片（非阻塞提交，返回 Future）。
        
        注意：此方法在 async 上下文中返回 asyncio.Future，消息在后台线程池中异步发送，
        调用方不应依赖此方法的返回值来确认发送成功。
        若需确认发送结果，请使用 await self.send_card_async(...) 代替。
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, card_content))
        except RuntimeError:
            return send_card_to_chat_sdk(chat_id, card_content)

    def send_reply_card(self, message_id: str, card_content: dict):
        """以回复方式发送交互卡片（非阻塞提交，返回 Future）。
        
        注意：此方法在 async 上下文中返回 asyncio.Future，若需等待结果请使用 await self.send_reply_card_async(...)。
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))
        except RuntimeError:
            return send_interactive_card_sdk(message_id, card_content)

    def send_text(self, chat_id: str, text: str):
        """向指定 chat 发送纯文本消息（非阻塞提交，返回 Future）。
        
        注意：此方法在 async 上下文中返回 asyncio.Future，若需等待结果请使用 await self.send_text_async(...)。
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, lambda: send_text_to_chat_sdk(chat_id, text))
        except RuntimeError:
            return send_text_to_chat_sdk(chat_id, text)

    def send_reply_text(self, message_id: str, text: str):
        """以回复方式发送纯文本消息（非阻塞提交，返回 Future）。
        
        注意：此方法在 async 上下文中返回 asyncio.Future，若需等待结果请使用 await self.send_reply_text_async(...)。
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, lambda: send_reply_sdk(message_id, text))
        except RuntimeError:
            return send_reply_sdk(message_id, text)

    async def send_card_async(self, chat_id: str, card_content: dict):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, card_content))

    async def send_reply_card_async(self, message_id: str, card_content: dict):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))

    async def send_text_async(self, chat_id: str, text: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: send_text_to_chat_sdk(chat_id, text))

    async def send_reply_text_async(self, message_id: str, text: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: send_reply_sdk(message_id, text))

    def get_config(self) -> dict:
        """Load plugin config.json if present."""
        cfg_path = os.path.join(self.plugin_dir, "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.error(f"[Plugin:{self.plugin_id}] Failed to load config: {e}")
        return {}

    def get_data_dir(self) -> str:
        """Return isolated private directory for storing plugin data files."""
        data_dir = os.path.abspath(os.path.join(self.plugin_dir, "..", "..", "plugin_data", self.plugin_id))
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    def get_data_file(self, filename: str) -> str:
        """Return absolute path for a private file inside plugin isolated data directory."""
        return os.path.join(self.get_data_dir(), filename)
