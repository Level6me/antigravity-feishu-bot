"""Raspberry Pi GPIO LED Status Indicator Plugin for antigravity-feishu-bot."""

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
    from gpiozero import LED
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
        self.pins = cfg.get("gpio_pins", {"red": 17, "yellow": 27, "green": 22})
        self.blink_interval = cfg.get("blink_interval_sec", 0.3)
        self._blinking_thread = None
        self._stop_blink = False
        self.current_color = "green"
        self.led_objects = {}

        if GPIO_AVAILABLE:
            try:
                if gpio_mode == "GPIOZERO_LGPIO":
                    for name, p in self.pins.items():
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
            log.info(f"[Plugin:{self.plugin_id}] RPi.GPIO/lgpio module not detected. Running in Mock/Simulation mode.")

        # Default Standby State (Green LED ON)
        self.set_state_idle()

    def _set_pin(self, pin_name: str, state: bool):
        pin = self.pins.get(pin_name)
        if pin is None:
            return
        if GPIO_AVAILABLE:
            try:
                if gpio_mode == "GPIOZERO_LGPIO" and pin_name in self.led_objects:
                    if state:
                        self.led_objects[pin_name].on()
                    else:
                        self.led_objects[pin_name].off()
                elif globals().get('GPIO') is not None:
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            except Exception as e:
                log.error(f"[Plugin:{self.plugin_id}] GPIO output error on pin {pin}: {e}")
        else:
            status_str = "HIGH ON" if state else "LOW OFF"
            log.debug(f"[Plugin:{self.plugin_id}] [MOCK-GPIO] Pin {pin} ({pin_name.upper()}) -> {status_str}")

    def _stop_active_blinking(self):
        self._stop_blink = True
        if self._blinking_thread and self._blinking_thread.is_alive():
            self._blinking_thread.join(timeout=1.0)

    def set_state_idle(self):
        """State: Standby / Ready -> Green ON, Yellow OFF, Red OFF."""
        self._stop_active_blinking()
        self.current_color = "green"
        self._set_pin("green", True)
        self._set_pin("yellow", False)
        self._set_pin("red", False)

    def set_state_running(self):
        """State: AI Executing / Thinking -> Yellow ON, Green OFF, Red OFF."""
        self._stop_active_blinking()
        self.current_color = "yellow"
        self._set_pin("green", False)
        self._set_pin("red", False)
        self._set_pin("yellow", True)

    def set_state_success(self):
        """State: Execution Finished Successfully -> Green ON, Yellow OFF, Red OFF."""
        self._stop_active_blinking()
        self.current_color = "green"
        self._set_pin("yellow", False)
        self._set_pin("red", False)
        self._set_pin("green", True)

    def set_state_error(self):
        """State: Execution Failed / Exception -> Red ON, Yellow OFF, Green OFF."""
        self._stop_active_blinking()
        self.current_color = "red"
        self._set_pin("green", False)
        self._set_pin("yellow", False)
        self._set_pin("red", True)

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        """Hook called before AI pipeline execution -> Turn on Yellow LED (Running)."""
        self.set_state_running()
        return user_text, session_data

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """Hook called after AI pipeline completion -> Update LED based on response status."""
        if any(err_kw in ai_response_text.lower() for err_kw in ["⚠️", "❌", "错误", "失败", "超时"]):
            self.set_state_error()
        else:
            self.set_state_success()
        return ai_response_text

    def build_control_card(self, active_color: str = None) -> dict:
        if active_color is None:
            active_color = self.current_color

        mode_desc = f"硬件 物理接口 ({gpio_mode})" if GPIO_AVAILABLE else "模拟日志模式 (Non-RPi)"
        
        status_badge = "🟢 系统就绪 (Green ON)"
        header_template = "green"
        if active_color == "yellow":
            status_badge = "🟡 任务运行中 (Yellow ON)"
            header_template = "yellow"
        elif active_color == "red":
            status_badge = "🔴 异常告警 (Red ON)"
            header_template = "red"
        elif active_color == "off":
            status_badge = "⚪ 所有指示灯已关闭 (All OFF)"
            header_template = "wathet"

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "💡 树莓派 LED 状态指示灯控制台"},
                "template": header_template
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前设备灯光状态**：`{status_badge}`\n"
                               f"**硬件工作模式**：`{mode_desc}`\n\n"
                               f"**GPIO 引脚映射 (BCM 编码)**：\n"
                               f"• 🔴 **红灯 (Error)**：GPIO `{self.pins.get('red')}`\n"
                               f"• 🟡 **黄灯 (Running)**：GPIO `{self.pins.get('yellow')}`\n"
                               f"• 🟢 **绿灯 (Ready)**：GPIO `{self.pins.get('green')}`"
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "**🎛️ 快捷交互控制按钮组（点击即刻控制指示灯）：**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟢 点亮绿灯 (就绪)"},
                            "type": "primary",
                            "value": {"action": "set_rpi_light", "color": "green"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🟡 点亮黄灯 (运行)"},
                            "type": "warning",
                            "value": {"action": "set_rpi_light", "color": "yellow"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔴 点亮红灯 (错误)"},
                            "type": "danger",
                            "value": {"action": "set_rpi_light", "color": "red"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚪ 关闭所有灯"},
                            "type": "default",
                            "value": {"action": "set_rpi_light", "color": "off"}
                        }
                    ]
                }
            ]
        }
        return card

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() in ["/light", "/led"]:
            sub_cmd = args.strip().lower()
            if sub_cmd == "red":
                self.set_state_error()
            elif sub_cmd == "yellow":
                self.set_state_running()
            elif sub_cmd == "green":
                self.set_state_success()
            elif sub_cmd == "off":
                self._stop_active_blinking()
                self.current_color = "off"
                for p in ["red", "yellow", "green"]:
                    self._set_pin(p, False)

            card = self.build_control_card()
            self.send_reply_card(message_id, card)
            return True
        return False

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        act = action or (value.get("action") if isinstance(value, dict) else "")
        if act == "set_rpi_light":
            color = value.get("color", "") if isinstance(value, dict) else ""
            if color == "green":
                self.set_state_success()
            elif color == "yellow":
                self.set_state_running()
            elif color == "red":
                self.set_state_error()
            elif color == "off":
                self._stop_active_blinking()
                self.current_color = "off"
                for p in ["red", "yellow", "green"]:
                    self._set_pin(p, False)

            card = self.build_control_card(active_color=color)
            from lark_client import patch_interactive_card_sdk
            patch_interactive_card_sdk(card_message_id, card)
            return True
        return False
