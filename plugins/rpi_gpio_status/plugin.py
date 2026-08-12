"""Raspberry Pi GPIO LED Status Indicator Plugin for antigravity-feishu-bot.

Rules:
1. 开始思考时常亮黄灯; 使用工具时亮呼吸黄灯; feishu-bot重启服务时闪烁黄灯; feishu-bot启动完成时闪烁绿灯5次然后灭掉
2. 出现错误 / 被/stop强制停止 时常亮红灯
3. 任务执行完成 亮绿灯300秒然后灭掉
"""

import os
import time
import asyncio
import threading
from plugin_base import BasePlugin
from logger import log

# Multi-backend GPIO import (gpiozero with LGPIOFactory as primary)
GPIO_AVAILABLE = False
gpio_mode = "NONE"

try:
    from gpiozero import LED, PWMLED
    from gpiozero.pins.lgpio import LGPIOFactory
    import gpiozero
    gpiozero.Device.pin_factory = LGPIOFactory()
    GPIO_AVAILABLE = True
    gpio_mode = "GPIOZERO_LGPIO"
except Exception:
    try:
        import RPi.GPIO as GPIO
        GPIO_AVAILABLE = True
        gpio_mode = "RPI_GPIO"
    except Exception:
        try:
            import rpi_lgpio as GPIO
            GPIO_AVAILABLE = True
            gpio_mode = "RPI_LGPIO"
        except Exception:
            GPIO = None


class RpiGpioStatusPlugin(BasePlugin):

    def initialize(self):
        cfg = self.get_config()
        self.pins = cfg.get("gpio_pins", {"red": 22, "yellow": 27, "green": 17})
        self.led_objects = {}
        self.current_state = "off"
        
        self._anim_thread = None
        self._stop_anim = False
        self._timer_thread = None

        if GPIO_AVAILABLE:
            try:
                if gpio_mode == "GPIOZERO_LGPIO":
                    for name, p in self.pins.items():
                        if name == "yellow":
                            try:
                                self.led_objects[name] = PWMLED(p)
                            except Exception:
                                self.led_objects[name] = LED(p)
                        else:
                            self.led_objects[name] = LED(p)
                    log.info(f"[Plugin:{self.plugin_id}] Physical Raspberry Pi GPIO initialized via LGPIOFactory (Pins: {self.pins}).")
                elif gpio_mode in ("RPI_GPIO", "RPI_LGPIO") and globals().get('GPIO') is not None:
                    GPIO.setmode(GPIO.BCM)
                    GPIO.setwarnings(False)
                    for pin in self.pins.values():
                        GPIO.setup(pin, GPIO.OUT)
                    log.info(f"[Plugin:{self.plugin_id}] Physical Raspberry Pi GPIO initialized via {gpio_mode} (BCM Pins: {self.pins}).")
            except Exception as e:
                log.error(f"[Plugin:{self.plugin_id}] Failed to init GPIO pins: {e}")
        else:
            log.info(f"[Plugin:{self.plugin_id}] Running in Mock/Simulation mode.")

        # 1. 启动完成时闪烁绿灯 5 次，然后全灭
        self.on_startup_complete()

    def _stop_background_effects(self):
        """Stops active breathing/blinking threads safely."""
        self._stop_anim = True
        if self._anim_thread and self._anim_thread.is_alive() and threading.current_thread() != self._anim_thread:
            self._anim_thread.join(timeout=1.0)
        self._stop_anim = False

    def turn_all_off(self):
        """Turns all 3 LEDs off."""
        self._stop_background_effects()
        self.current_state = "off"
        self._set_raw_pin("red", 0)
        self._set_raw_pin("yellow", 0)
        self._set_raw_pin("green", 0)

    def _set_raw_pin(self, pin_name: str, level: float):
        """Sets pin level (0.0 to 1.0)."""
        pin = self.pins.get(pin_name)
        if pin is None:
            return
        if GPIO_AVAILABLE:
            try:
                if gpio_mode == "GPIOZERO_LGPIO" and pin_name in self.led_objects:
                    obj = self.led_objects[pin_name]
                    if hasattr(obj, 'value'):
                        obj.value = max(0.0, min(1.0, level))
                    else:
                        if level > 0: obj.on()
                        else: obj.off()
                elif globals().get('GPIO') is not None:
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.HIGH if level > 0 else GPIO.LOW)
            except Exception as e:
                log.error(f"[Plugin:{self.plugin_id}] GPIO output error on pin {pin}: {e}")
        else:
            log.debug(f"[Plugin:{self.plugin_id}] [MOCK-GPIO] Pin {pin} ({pin_name.upper()}) -> Level {level:.2f}")

    # ==================== 核心状态触发逻辑 ====================

    def on_startup_complete(self):
        """规则 1d: feishu-bot 启动完成时闪烁绿灯 5 次，然后灭掉"""
        self.turn_all_off()
        self.current_state = "startup_complete"

        def _startup_worker():
            for _ in range(5):
                if self._stop_anim:
                    break
                self._set_raw_pin("green", 1.0)
                time.sleep(0.2)
                self._set_raw_pin("green", 0.0)
                time.sleep(0.2)
            self.turn_all_off()

        self._anim_thread = threading.Thread(target=_startup_worker, daemon=True)
        self._anim_thread.start()

    def set_state_thinking(self):
        """规则 1a: 开始思考时常亮黄灯"""
        self.turn_all_off()
        self.current_state = "thinking_solid_yellow"
        self._set_raw_pin("yellow", 1.0)

    def set_state_breathing_yellow(self):
        """规则 1b: 使用工具时亮呼吸黄灯"""
        self.turn_all_off()
        self.current_state = "breathing_yellow"

        def _breathing_worker():
            step = 0.05
            val = 0.1
            direction = 1
            while not self._stop_anim:
                self._set_raw_pin("yellow", val)
                val += step * direction
                if val >= 1.0:
                    val = 1.0
                    direction = -1
                elif val <= 0.05:
                    val = 0.05
                    direction = 1
                time.sleep(0.04)
            self._set_raw_pin("yellow", 0.0)

        self._anim_thread = threading.Thread(target=_breathing_worker, daemon=True)
        self._anim_thread.start()

    def set_state_error(self):
        """规则 2: 出现错误 / 被/stop强制停止 常亮红灯"""
        self.turn_all_off()
        self.current_state = "solid_red_error"
        self._set_raw_pin("red", 1.0)

    def set_state_success(self):
        """规则 3: 任务执行完成 亮绿灯 300 秒然后灭掉"""
        self.turn_all_off()
        self.current_state = "solid_green_success_300s"
        self._set_raw_pin("green", 1.0)

        def _timer_worker():
            for _ in range(300):
                if self._stop_anim or self.current_state != "solid_green_success_300s":
                    return
                time.sleep(1.0)
            if self.current_state == "solid_green_success_300s":
                self.turn_all_off()

        self._timer_thread = threading.Thread(target=_timer_worker, daemon=True)
        self._timer_thread.start()

    # ==================== HOOK 事件绑定 ====================

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        """Hook: 开始 AI 思考 -> 规则 1a 常亮黄灯"""
        self.set_state_thinking()
        return user_text, session_data

    async def on_tool_call(self, tool_name: str, tool_args: dict):
        """Hook: 调用/使用工具 -> 规则 1b 呼吸黄灯"""
        self.set_state_breathing_yellow()

    def on_service_restarting(self):
        """Hook: feishu-bot 重启服务时 -> 规则 1c 闪烁黄灯"""
        self.turn_all_off()
        self.current_state = "restarting_yellow_blink"

        def _restarting_worker():
            state = False
            while not self._stop_anim:
                state = not state
                self._set_raw_pin("yellow", 1.0 if state else 0.0)
                time.sleep(0.25)

        self._anim_thread = threading.Thread(target=_restarting_worker, daemon=True)
        self._anim_thread.start()

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """Hook: AI 任务响应分析 -> 规则 2 / 规则 3"""
        # 避让正文中普通的"错误/失败"词汇解释，只有真正发生任务失败、超时、卡死强杀时才亮红灯
        explicit_error_markers = [
            "⚠️ 任务已检测到卡死",
            "⚠️ 任务已被终止",
            "❌ 执行失败",
            "❌ 运行超时",
            "⚠️ 任务超时",
            "❌ 任务出错"
        ]
        if any(marker in ai_response_text for marker in explicit_error_markers):
            self.set_state_error()
        else:
            self.set_state_success()
        return ai_response_text

    def build_control_card(self) -> dict:
        mode_desc = f"硬件 物理接口 ({gpio_mode})" if GPIO_AVAILABLE else "模拟日志模式 (Non-RPi)"
        
        status_map = {
            "thinking_solid_yellow": ("🟡 开始思考中 (常亮黄灯)", "yellow"),
            "breathing_yellow": ("⚡ 使用工具中 (呼吸黄灯)", "orange"),
            "solid_red_error": ("🔴 出现错误 / 被/stop强制停止 (常亮红灯)", "red"),
            "solid_green_success_300s": ("🟢 任务完成 (常亮绿灯 300s 后自动灭掉)", "green"),
            "startup_complete": ("✨ 启动完成 (绿灯闪烁 5 次自检)", "purple"),
            "off": ("⚪ 指示灯已关闭 (全灭)", "wathet")
        }
        status_badge, header_template = status_map.get(self.current_state, ("⚪ 已关灯", "wathet"))

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🍓 树莓派 GPIO 状态灯控制台"},
                "template": header_template
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前设备灯光状态**：`{status_badge}`\n"
                               f"**硬件工作模式**：`{mode_desc}`\n\n"
                               f"**精准规则引脚映射 (BCM 编码)**：\n"
                               f"• 🔴 **红灯 (Error / /stop)**：GPIO `{self.pins.get('red')}`\n"
                               f"• 🟡 **黄灯 (Thinking / Tool)**：GPIO `{self.pins.get('yellow')}`\n"
                               f"• 🟢 **绿灯 (Success / Startup)**：GPIO `{self.pins.get('green')}`"
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "**🎛️ 快捷逻辑调试测试组：**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟡 思考中 (常亮黄灯)"},
                            "type": "warning",
                            "value": {"action": "set_rpi_light", "state": "thinking"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚡ 使用工具中 (呼吸黄灯)"},
                            "type": "warning",
                            "value": {"action": "set_rpi_light", "state": "breathing"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔴 错误/强停 (常亮红灯)"},
                            "type": "danger",
                            "value": {"action": "set_rpi_light", "state": "error"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟢 任务完成 (绿灯300s)"},
                            "type": "primary",
                            "value": {"action": "set_rpi_light", "state": "success"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✨ 启动自检 (绿灯闪5次)"},
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
        return card

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        cmd_lower = command.lower()
        if cmd_lower in ["/stop", "/cancel"]:
            # 规则 2: 被/stop强制停止 常亮红灯
            self.set_state_error()
            return False

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
