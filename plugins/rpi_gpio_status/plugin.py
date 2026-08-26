"""Raspberry Pi GPIO LED Status Indicator Plugin for antigravity-feishu-bot.
Connects via pi-led-api Central Gateway (http://127.0.0.1:8080) for unified hardware control.
"""

import os
import time
import requests
import threading
from plugin_base import BasePlugin
from logger import log

API_BASE = "http://127.0.0.1:8080"
API_TOKEN = os.getenv("API_TOKEN", "default_feishu_bot_token")

class RpiGpioStatusPlugin(BasePlugin):

    def initialize(self):
        cfg = self.get_config()
        self.api_url = cfg.get("api_url", API_BASE)
        self.current_state = "off"
        log.info(f"[Plugin:{self.plugin_id}] Connected to central pi-led-api service at {self.api_url}")
        self.on_startup_complete()

    def _call_api(self, state: str, duration: int = 300):
        def _worker():
            try:
                headers = {"Content-Type": "application/json", "X-API-Key": API_TOKEN}
                requests.post(
                    f"{self.api_url}/api/state",
                    json={"state": state, "duration": duration},
                    headers=headers,
                    timeout=3
                )
            except Exception as e:
                log.debug(f"[Plugin:{self.plugin_id}] Call pi-led-api error: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def on_startup_complete(self):
        self.current_state = "startup_complete"
        self._call_api("startup")

    def set_state_thinking(self):
        self.current_state = "thinking_solid_yellow"
        self._call_api("thinking")

    def set_state_breathing_yellow(self):
        self.current_state = "breathing_yellow"
        self._call_api("breathing")

    def set_state_error(self):
        self.current_state = "solid_red_error"
        self._call_api("error")

    def set_state_success(self):
        self.current_state = "solid_green_success_300s"
        self._call_api("success", 300)

    def turn_all_off(self):
        self.current_state = "off"
        self._call_api("off")

    def on_service_restarting(self):
        self.current_state = "restarting_yellow_blink"
        self._call_api("restarting")

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        self.set_state_thinking()
        return user_text, session_data

    async def on_tool_call(self, tool_name: str, tool_args: dict):
        self.set_state_breathing_yellow()

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        is_err = session_data.get("last_execution_error", False)
        if not is_err:
            stripped = ai_response_text.strip()
            if stripped.startswith(("❌", "⚠️")) or "traceback (most recent call last):" in stripped.lower():
                is_err = True

        if is_err:
            self.set_state_error()
        else:
            self.set_state_success()
        return ai_response_text

    def build_control_card(self) -> dict:
        status_map = {
            "thinking_solid_yellow": ("🟡 开始思考中 (常亮黄灯)", "yellow"),
            "breathing_yellow": ("⚡ 使用工具中 (呼吸黄灯)", "orange"),
            "solid_red_error": ("🔴 出现错误 / 强停 (常亮红灯)", "red"),
            "solid_green_success_300s": ("🟢 任务完成 (常亮绿灯 300s)", "green"),
            "startup_complete": ("✨ 启动完成 (绿灯闪烁 5 次自检)", "purple"),
            "off": ("⚪ 指示灯已关闭 (全灭)", "wathet")
        }
        status_badge, header_template = status_map.get(self.current_state, (f"💡 状态: {self.current_state}", "blue"))

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🍓 树莓派 GPIO 状态灯控制台"},
                "template": header_template
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**网关服务**：`{self.api_url}`\n"
                               f"**当前状态**：`{status_badge}`\n\n"
                               f"**硬件引脚映射 (BCM)**：\n"
                               f"• 🔴 **红灯 (Error / /stop)**：GPIO `22`\n"
                               f"• 🟡 **黄灯 (Thinking / Tool)**：GPIO `27`\n"
                               f"• 🟢 **绿灯 (Success / Startup)**：GPIO `17`"
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "**🎛️ 快捷状态控制：**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟡 思考中 (黄灯)"},
                            "type": "warning",
                            "value": {"action": "set_rpi_light", "state": "thinking"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚡ 使用工具 (呼吸黄)"},
                            "type": "warning",
                            "value": {"action": "set_rpi_light", "state": "breathing"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔴 错误/强停 (红灯)"},
                            "type": "danger",
                            "value": {"action": "set_rpi_light", "state": "error"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟢 任务完成 (绿灯)"},
                            "type": "primary",
                            "value": {"action": "set_rpi_light", "state": "success"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✨ 启动自检 (闪绿)"},
                            "type": "primary",
                            "value": {"action": "set_rpi_light", "state": "startup"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚪ 关闭所有灯"},
                            "type": "default",
                            "value": {"action": "set_rpi_light", "state": "off"}
                        }
                    ]
                }
            ]
        }

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        cmd_lower = command.lower()
        if cmd_lower in ["/light", "/led"]:
            sub_cmd = args.strip().lower()
            if sub_cmd in ["thinking", "yellow", "思考"]:
                self.set_state_thinking()
            elif sub_cmd in ["breathing", "tool", "呼吸"]:
                self.set_state_breathing_yellow()
            elif sub_cmd in ["red", "error", "stop", "错误"]:
                self.set_state_error()
            elif sub_cmd in ["green", "success", "完成"]:
                self.set_state_success()
            elif sub_cmd in ["startup", "test", "自检"]:
                self.on_startup_complete()
            elif sub_cmd in ["off", "关灯"]:
                self.turn_all_off()

            card = self.build_control_card()
            self.send_reply_card(message_id, card)
            return True
        return False

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        act = action or (value.get("action") if isinstance(value, dict) else "")
        if act == "set_rpi_light":
            st = value.get("state", "") if isinstance(value, dict) else ""
            if st == "thinking":
                self.set_state_thinking()
            elif st == "breathing":
                self.set_state_breathing_yellow()
            elif st == "error":
                self.set_state_error()
            elif st == "success":
                self.set_state_success()
            elif st == "startup":
                self.on_startup_complete()
            elif st == "off":
                self.turn_all_off()

            card = self.build_control_card()
            from lark_client import patch_interactive_card_sdk
            patch_interactive_card_sdk(card_message_id, card)
            return True
        return False
