"""Plugin Manager for antigravity-feishu-bot."""

import os
import sys
import json
import importlib.util

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from logger import log
from plugin_base import BasePlugin

PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")


class PluginManager:
    """Singleton manager for bot plugins."""

    def __init__(self):
        self.plugins = {}         # plugin_id -> plugin_instance
        self.command_map = {}      # command (e.g. "/sysinfo") -> plugin_instance
        self.system_commands = set()  # set of registered system commands
        self.ensure_plugins_dir()

    def ensure_plugins_dir(self):
        if not os.path.exists(PLUGINS_DIR):
            os.makedirs(PLUGINS_DIR, exist_ok=True)

    def load_all_plugins(self):
        """Discover and load all valid plugins from plugins/ directory."""
        self.ensure_plugins_dir()
        self.plugins.clear()
        self.command_map.clear()

        log.info(f"[PluginManager] Scanning plugins directory: {PLUGINS_DIR}")
        for entry in os.listdir(PLUGINS_DIR):
            p_dir = os.path.join(PLUGINS_DIR, entry)
            if os.path.isdir(p_dir):
                manifest_path = os.path.join(p_dir, "manifest.json")
                py_path = os.path.join(p_dir, "plugin.py")

                if os.path.exists(manifest_path) and os.path.exists(py_path):
                    self.load_single_plugin(p_dir, manifest_path, py_path)

        log.info(f"[PluginManager] Total plugins loaded: {len(self.plugins)}, commands registered: {list(self.command_map.keys())}")

    def load_single_plugin(self, p_dir: str, manifest_path: str, py_path: str):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            plugin_id = manifest.get("id")
            if not plugin_id:
                log.warning(f"[PluginManager] Invalid manifest in {p_dir}: missing 'id'")
                return

            if not manifest.get("enabled", True):
                log.info(f"[PluginManager] Plugin '{plugin_id}' is disabled in manifest.")
                return

            # Dynamic import
            spec = importlib.util.spec_from_file_location(f"bot_plugin_{plugin_id}", py_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find class inheriting from BasePlugin
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BasePlugin) and attr is not BasePlugin:
                    plugin_class = attr
                    break

            if not plugin_class:
                log.warning(f"[PluginManager] No subclass of BasePlugin found in {py_path}")
                return

            instance = plugin_class(p_dir, manifest)
            instance.initialize()

            self.plugins[plugin_id] = instance

            for cmd in instance.commands:
                cmd_norm = cmd.lower().strip()
                self.command_map[cmd_norm] = instance
                log.info(f"[PluginManager] Registered command '{cmd_norm}' -> Plugin '{plugin_id}'")

        except Exception as e:
            log.error(f"[PluginManager] Failed to load plugin from {p_dir}: {e}", exc_info=True)

    def reload_plugins(self):
        """Reload all plugins dynamically."""
        self.load_all_plugins()

    async def dispatch_command(self, user_text: str, message_id: str, chat_id: str, session_data: dict) -> tuple[bool, str]:
        """Check if user_text starts with any registered plugin command.
        Return (handled, response_text)."""
        first_word = user_text.split()[0].lower() if user_text.strip() else ""
        if first_word in self.command_map:
            plugin = self.command_map[first_word]
            args = user_text[len(first_word):].strip()
            try:
                handled = await plugin.on_command(first_word, args, chat_id, message_id, session_data)
                if handled:
                    return True, user_text
            except Exception as e:
                log.error(f"[PluginManager] Error executing command '{first_word}' in plugin '{plugin.plugin_id}': {e}", exc_info=True)
    async def dispatch_message(self, user_text: str, message_id: str, chat_id: str, session_data: dict) -> bool:
        """Pipe incoming user_text through on_message hooks of all active plugins.
        If any plugin returns True, the message is considered handled (intercepted)."""
        for pid, plugin in self.plugins.items():
            if plugin.enabled:
                try:
                    if await plugin.on_message(chat_id, user_text, message_id, session_data):
                        return True
                except Exception as e:
                    log.error(f"[PluginManager] Error in plugin '{pid}' on_message: {e}", exc_info=True)
        return False

    async def dispatch_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """Dispatch card action button events to all loaded plugins."""
        for plugin_id, plugin in self.plugins.items():
            try:
                if await plugin.on_card_action(action, value, chat_id, card_message_id):
                    return True
            except Exception as e:
                log.error(f"[PluginManager] Error in plugin '{plugin_id}' on_card_action: {e}")
        return False

    def get_plugin_list(self) -> list:
        """Return metadata list of all loaded plugins."""
        res = []
        for pid, instance in self.plugins.items():
            res.append({
                "id": pid,
                "name": instance.name,
                "version": instance.version,
                "commands": instance.commands,
                "enabled": instance.enabled,
                "dir": instance.plugin_dir
            })
        return res

    def is_slash_command(self, cmd_word: str) -> bool:
        """Check if cmd_word is registered by any plugin or system command."""
        cmd_norm = cmd_word.lower().strip()
        return cmd_norm in self.command_map or cmd_norm in self.system_commands

    def register_system_commands(self, commands: list[str]):
        """Register built-in system slash commands into dynamic registry."""
        for c in commands:
            self.system_commands.add(c.lower().strip())

    async def dispatch_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        """Pipe user_text through on_before_ai hooks of all active plugins."""
        curr_text = user_text
        curr_data = session_data
        for pid, plugin in self.plugins.items():
            if plugin.enabled:
                try:
                    curr_text, curr_data = await plugin.on_before_ai(curr_text, chat_id, curr_data)
                except Exception as e:
                    log.error(f"[PluginManager] Error in plugin '{pid}' on_before_ai: {e}")
        return curr_text, curr_data

    async def dispatch_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """Pipe ai_response_text through on_after_ai hooks of all active plugins."""
        curr_resp = ai_response_text
        for pid, plugin in self.plugins.items():
            if plugin.enabled:
                try:
                    curr_resp = await plugin.on_after_ai(curr_resp, chat_id, session_data)
                except Exception as e:
                    log.error(f"[PluginManager] Error in plugin '{pid}' on_after_ai: {e}")
        return curr_resp

    async def dispatch_tool_call(self, tool_name: str, tool_args: dict = None):
        """Dispatch tool call event to all active plugins."""
        for pid, plugin in self.plugins.items():
            if plugin.enabled:
                try:
                    await plugin.on_tool_call(tool_name, tool_args or {})
                except Exception as e:
                    log.error(f"[PluginManager] Error in plugin '{pid}' on_tool_call: {e}")

    def dispatch_service_restarting(self):
        """Sync dispatch service restarting event to all plugins."""
        for pid, plugin in self.plugins.items():
            if plugin.enabled:
                try:
                    plugin.on_service_restarting()
                except Exception as e:
                    log.error(f"[PluginManager] Error in plugin '{pid}' on_service_restarting: {e}")


# Global singleton instance
plugin_manager = PluginManager()
