"""Plugin Manager for antigravity-feishu-bot."""

import os
import json
import importlib.util
from logger import log
from plugin_base import BasePlugin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")


class PluginManager:
    """Singleton manager for bot plugins."""

    def __init__(self):
        self.plugins = {}         # plugin_id -> plugin_instance
        self.command_map = {}      # command (e.g. "/sysinfo") -> plugin_instance
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
        return False, user_text

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


# Global singleton instance
plugin_manager = PluginManager()
