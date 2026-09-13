"""
🍓 Raspberry Pi GPIO LED & Hardware PWM Buzzer Status Plugin (API Gateway Edition v2.2)
for antigravity-feishu-bot.

Features:
- Dual Gateway Integration:
  • pi_led_api: central HTTP RESTful & WebSocket gateway controlling Red/Yellow/Green LEDs
  • buzzer-api: hardware PWM buzzer API controlling sound effects & melodies
- Full AI dialogue lifecycle audio-visual indicators:
  • Thinking (思考中): Solid Yellow LED + Repeating Beep (单哔确认) every 10 seconds
  • Tool Call (使用工具): Breathing Yellow LED + Repeating Two Beeps (双哔确认) every 10 seconds
  • Task Success (任务成功): Solid Green LED + Success Melody (操作成功)
  • Task Error (任务失败): Solid Red LED + Error Melody (操作失败)
  • /update Success (更新重启成功): Level Up Melody (角色升级)
- Reconstructed /led & /light Control Panel:
  • Interactive LED controls, sound effect previews, and dual-gateway config
- Comprehensive /stat Command Card:
  • Real-time CPU temp, memory, load avg, LED pins, buzzer status & audio metrics
"""

import os
import sys
import time
import json
import requests
import threading
from typing import Optional, Dict, Any, Tuple

from plugin_base import BasePlugin
from logger import log
from lark_client import patch_interactive_card_sdk, send_reply_sdk, send_text_to_chat_sdk

DEFAULT_API_URL = "http://127.0.0.1:8080"
DEFAULT_API_TOKEN = "tok_98456ea9826d816b7a8198303823aa2f"
DEFAULT_BUZZER_URL = "http://127.0.0.1:8001"
DEFAULT_BUZZER_TOKEN = "bz_live_7546eb23c0725692a2fa8cc3"

def _is_valid_url(url: str) -> bool:
    """Validate if the string is likely a valid HTTP(S) URL or IP endpoint."""
    u = url.strip().lower()
    if u.startswith(("http://", "https://", "ws://", "wss://", "127.0.0.1", "localhost")):
        return True
    if "." in u and (":" in u or "/" in u):
        return True
    return False


class RpiGpioApiStatusPlugin(BasePlugin):

    def __init__(self, plugin_dir: str, manifest: dict):
        super().__init__(plugin_dir, manifest)
        self.config_data = {}
        self.api_url = DEFAULT_API_URL
        self.api_token = DEFAULT_API_TOKEN
        self.buzzer_api_url = DEFAULT_BUZZER_URL
        self.buzzer_api_token = DEFAULT_BUZZER_TOKEN
        self.buzzer_enabled = True
        self.buzzer_volume = 10
        self.buzzer_interval = 15
        self.auto_indicator = True
        self.success_duration = 300
        self.current_state = "off"
        self.last_test_result = None
        self.pending_input = {}
        self.active_timer_thread = None
        self.stop_timer_event = threading.Event()
        self.current_buzzer_mode = "idle"

    def initialize(self):
        self.config_data = self.get_config() or {}
        self.enabled = self.config_data.get("enabled", True)
        if not self.enabled:
            log.info(f"[Plugin:{self.plugin_id}] Disabled via config.json.")
            return

        # LED 网关参数
        raw_url = self.config_data.get("api_url", DEFAULT_API_URL).rstrip("/")
        if not _is_valid_url(raw_url):
            raw_url = DEFAULT_API_URL
        self.api_url = raw_url
        self.api_token = self.config_data.get("api_token", DEFAULT_API_TOKEN)

        # 蜂鸣器网关参数
        raw_buzzer_url = self.config_data.get("buzzer_api_url", DEFAULT_BUZZER_URL).rstrip("/")
        if not _is_valid_url(raw_buzzer_url):
            raw_buzzer_url = DEFAULT_BUZZER_URL
        self.buzzer_api_url = raw_buzzer_url
        self.buzzer_api_token = self.config_data.get("buzzer_api_token", DEFAULT_BUZZER_TOKEN)
        self.buzzer_enabled = self.config_data.get("buzzer_enabled", True)
        self.buzzer_volume = int(self.config_data.get("buzzer_volume", 10))
        self.buzzer_interval = int(self.config_data.get("buzzer_interval", 15))

        # 自动联动与时长参数
        self.auto_indicator = self.config_data.get("auto_indicator_enabled", True)
        self.success_duration = int(self.config_data.get("success_duration_sec", 300))

        self.current_state = "off"
        self.last_test_result: Optional[dict] = None
        self.pending_input: Dict[str, Tuple[str, float]] = {}  # chat_id -> (mode, timestamp)

        # 循环提示音线程管理
        self.active_timer_thread: Optional[threading.Thread] = None
        self.stop_timer_event = threading.Event()
        self.current_buzzer_mode = "idle"  # "idle", "thinking", "tool"

        # 初始化与重载时首先彻底清理历史残留的循环定时器线程
        cleanup_orphan_timer_threads()

        log.info(f"[Plugin:{self.plugin_id}] Initialized: LED={self.api_url}, Buzzer={self.buzzer_api_url}")

        # 检查是否刚完成 OTA /update 升级重启成功
        self._check_and_trigger_update_success_sound()

        # 开机自检完成指示灯
        self.on_startup_complete()

    def _check_and_trigger_update_success_sound(self):
        """检查是否存在更新成功待播放标记，触发角色升级提示音 (level_up)"""
        base_dir = os.path.abspath(os.path.join(self.plugin_dir, "..", ".."))
        flag_file = os.path.join(base_dir, ".update_buzzer_pending")
        if os.path.exists(flag_file):
            try:
                os.remove(flag_file)
            except Exception:
                pass
            log.info(f"[Plugin:{self.plugin_id}] 🔔 检测到核心系统 /update 升级成功重启，触发角色升级提示音 (level_up)！")
            # 延时 1 秒等待系统与网络就绪后播放
            def _play_delay():
                time.sleep(1.0)
                self.play_melody("level_up")
            threading.Thread(target=_play_delay, daemon=True).start()

    def save_config_file(self, new_configs: dict):
        """保存配置到插件本地 config.json 并实时热更新内存属性"""
        self.config_data.update(new_configs)
        if "api_url" in new_configs:
            u = new_configs["api_url"].rstrip("/")
            if _is_valid_url(u):
                if not u.startswith("http://") and not u.startswith("https://"):
                    u = f"http://{u}"
                self.api_url = u
                self.config_data["api_url"] = u
        if "api_token" in new_configs:
            self.api_token = new_configs["api_token"]
        if "buzzer_api_url" in new_configs:
            bu = new_configs["buzzer_api_url"].rstrip("/")
            if _is_valid_url(bu):
                if not bu.startswith("http://") and not bu.startswith("https://"):
                    bu = f"http://{bu}"
                self.buzzer_api_url = bu
                self.config_data["buzzer_api_url"] = bu
        if "buzzer_api_token" in new_configs:
            self.buzzer_api_token = new_configs["buzzer_api_token"]
        if "buzzer_enabled" in new_configs:
            self.buzzer_enabled = bool(new_configs["buzzer_enabled"])
        if "buzzer_volume" in new_configs:
            self.buzzer_volume = max(0, min(100, int(new_configs["buzzer_volume"])))
        if "buzzer_interval" in new_configs:
            self.buzzer_interval = max(3, int(new_configs["buzzer_interval"]))
        if "auto_indicator_enabled" in new_configs:
            self.auto_indicator = bool(new_configs["auto_indicator_enabled"])
        if "success_duration_sec" in new_configs:
            self.success_duration = int(new_configs["success_duration_sec"])

        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=2)
            log.info(f"[Plugin:{self.plugin_id}] 配置已成功保存至 {config_path}")
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] 保存配置失败: {e}")

    def _get_led_headers(self, custom_token: Optional[str] = None) -> dict:
        tok = custom_token if custom_token is not None else self.api_token
        headers = {"Content-Type": "application/json"}
        if tok:
            if tok.startswith("ey"):
                headers["Authorization"] = f"Bearer {tok}"
            else:
                headers["X-API-Key"] = tok
        return headers

    def _get_buzzer_headers(self, custom_token: Optional[str] = None) -> dict:
        tok = custom_token if custom_token is not None else self.buzzer_api_token
        headers = {"Content-Type": "application/json"}
        if tok:
            headers["X-API-Token"] = tok
            headers["Authorization"] = f"Bearer {tok}"
        return headers

    # ==================== 异步调用网关 ====================

    def _call_api_async(self, endpoint: str, method: str = "POST", json_data: Optional[dict] = None):
        """Asynchronous HTTP request to LED gateway without blocking AI/bot thread."""
        def _worker():
            try:
                url = f"{self.api_url}{endpoint}"
                headers = self._get_led_headers()
                if method.upper() == "POST":
                    requests.post(url, json=json_data or {}, headers=headers, timeout=2.5)
                elif method.upper() == "DELETE":
                    requests.delete(url, headers=headers, timeout=2.5)
                elif method.upper() == "GET":
                    requests.get(url, headers=headers, timeout=2.5)
            except Exception as e:
                log.debug(f"[Plugin:{self.plugin_id}] Call LED {endpoint} error: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def _fetch_snapshot_sync(self) -> Optional[dict]:
        """Synchronously fetch current LED snapshot from gateway."""
        try:
            url = f"{self.api_url}/api/status"
            res = requests.get(url, headers=self._get_led_headers(), timeout=2.0)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            log.debug(f"[Plugin:{self.plugin_id}] Fetch LED status error: {e}")
        return None

    def _call_buzzer_api_async(self, endpoint: str, json_data: Optional[dict] = None):
        """Asynchronously call buzzer-api without blocking AI thread."""
        if not getattr(self, "buzzer_enabled", True):
            return

        def _worker():
            try:
                url = f"{self.buzzer_api_url}{endpoint}"
                headers = self._get_buzzer_headers()
                requests.post(url, json=json_data or {}, headers=headers, timeout=2.5)
            except Exception as e:
                log.debug(f"[Plugin:{self.plugin_id}] Call Buzzer {endpoint} error: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def _fetch_buzzer_status_sync(self) -> Optional[dict]:
        """Synchronously fetch current Buzzer status from gateway."""
        try:
            url = f"{self.buzzer_api_url}/api/status"
            res = requests.get(url, headers=self._get_buzzer_headers(), timeout=2.0)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            log.debug(f"[Plugin:{self.plugin_id}] Fetch Buzzer status error: {e}")
        return None

    # ==================== 蜂鸣器发声快捷函数 ====================

    def play_melody(self, melody_name: str, volume: Optional[int] = None):
        """播放预设旋律或提示音 (level_up, beep, two_beeps, success, error 等)"""
        vol = volume if volume is not None else self.buzzer_volume
        self._call_buzzer_api_async("/api/play/melody", {"name": melody_name, "volume": vol})

    def play_tone(self, frequency: float, duration: float = 0.15, volume: Optional[int] = None):
        """播放指定频率的单音"""
        vol = volume if volume is not None else self.buzzer_volume
        self._call_buzzer_api_async("/api/play/tone", {"frequency": frequency, "duration": duration, "volume": vol})

    def stop_buzzer(self):
        """紧急停止蜂鸣器当前发声"""
        self._call_buzzer_api_async("/api/stop", {})

def cleanup_orphan_timer_threads() -> int:
    """扫描当前 Python 进程所有活动线程的调用栈，强制终结所有失控或历史残留的 _timer_worker 循环线程"""
    import sys
    killed = 0
    try:
        for tid, top_frame in list(sys._current_frames().items()):
            f = top_frame
            while f:
                if f.f_code.co_name == "_timer_worker":
                    evt = f.f_locals.get("evt")
                    if isinstance(evt, threading.Event):
                        try:
                            evt.set()
                            killed += 1
                        except Exception:
                            pass
                    obj = f.f_locals.get("self")
                    if obj and hasattr(obj, "stop_timer_event"):
                        try:
                            obj.stop_timer_event.set()
                            obj.current_buzzer_mode = "idle"
                        except Exception:
                            pass
                f = f.f_back
    except Exception as e:
        log.error(f"[BuzzerGuard] Error cleaning orphan timer threads: {e}")
    if killed > 0:
        log.info(f"[BuzzerGuard] 🛡️ 成功扫描并终止 {killed} 个失控的 _timer_worker 历史循环线程")
    return killed


    # ==================== 循环提示音定时器引擎 ====================

    def _stop_recurring_sound(self):
        """停止当前正在进行的循环提示音计时线程并立即关停蜂鸣器"""
        self.stop_timer_event.set()
        self.current_buzzer_mode = "idle"
        self.active_timer_thread = None
        cleanup_orphan_timer_threads()
        self.stop_buzzer()

    def _start_recurring_sound(self, mode: str):
        """启动或切换指定模式的循环提示音 (thinking: 单哔, tool: 双哔)"""
        if not getattr(self, "buzzer_enabled", True) or not getattr(self, "auto_indicator", True):
            return

        # 若已处于该模式且线程处于活跃状态，无需重复打断启动
        if self.current_buzzer_mode == mode and self.active_timer_thread and self.active_timer_thread.is_alive():
            return

        self._stop_recurring_sound()
        self.stop_timer_event = threading.Event()
        self.current_buzzer_mode = mode

        def _timer_worker(evt: threading.Event, m: str):
            # 1. 刚进入该状态时立即播放首次提示音
            if m == "thinking":
                self.play_melody("beep")
            elif m == "tool":
                self.play_melody("two_beeps")

            # 2. 依据配置的间隔秒数循环提示，直到被中断或任务结束
            interval = float(getattr(self, "buzzer_interval", 15))
            while not evt.wait(interval):
                if evt.is_set():
                    break
                if self.current_buzzer_mode != m or self.current_buzzer_mode == "idle":
                    break
                if not getattr(self, "buzzer_enabled", True) or not getattr(self, "auto_indicator", True):
                    break
                if m == "thinking":
                    self.play_melody("beep")
                elif m == "tool":
                    self.play_melody("two_beeps")

        t = threading.Thread(target=_timer_worker, args=(self.stop_timer_event, mode), daemon=True)
        self.active_timer_thread = t
        t.start()

    # ==================== 连通性测试 (支持双网关) ====================

    def test_gateway_connection(self, custom_url: Optional[str] = None, custom_token: Optional[str] = None) -> dict:
        """测试目标网关连通性并测量网络 RTT 延迟 (同时测试 LED 网关与蜂鸣器 API)"""
        # 1. 测试 LED 网关
        target_led_url = (custom_url or self.api_url).rstrip("/")
        if not target_led_url.startswith("http://") and not target_led_url.startswith("https://"):
            target_led_url = f"http://{target_led_url}"

        led_headers = self._get_led_headers(custom_token)
        start_t = time.perf_counter()
        led_res_dict = {}
        try:
            res = requests.get(f"{target_led_url}/api/status", headers=led_headers, timeout=3.0)
            led_latency = round((time.perf_counter() - start_t) * 1000, 1)
            if res.status_code == 200:
                data = res.json()
                led_res_dict = {
                    "success": True,
                    "url": target_led_url,
                    "latency_ms": led_latency,
                    "driver_mode": data.get("hardware", {}).get("mode", "UNKNOWN"),
                    "pins": data.get("hardware", {}).get("pins", {}),
                    "token_required": data.get("auth", {}).get("token_required", False)
                }
            else:
                led_res_dict = {
                    "success": False,
                    "url": target_led_url,
                    "latency_ms": led_latency,
                    "error": f"HTTP {res.status_code}: {res.text[:80]}"
                }
        except Exception as e:
            led_res_dict = {
                "success": False,
                "url": target_led_url,
                "latency_ms": round((time.perf_counter() - start_t) * 1000, 1),
                "error": str(e)
            }

        # 2. 测试蜂鸣器网关
        target_buzzer_url = self.buzzer_api_url.rstrip("/")
        buzzer_headers = self._get_buzzer_headers()
        start_b = time.perf_counter()
        buzzer_res_dict = {}
        try:
            res_b = requests.get(f"{target_buzzer_url}/api/status", headers=buzzer_headers, timeout=3.0)
            buzzer_latency = round((time.perf_counter() - start_b) * 1000, 1)
            if res_b.status_code == 200:
                data_b = res_b.json()
                buzzer_res_dict = {
                    "success": True,
                    "url": target_buzzer_url,
                    "latency_ms": buzzer_latency,
                    "pwm_channel": data_b.get("pwm_channel", "pwm0"),
                    "token_required": data_b.get("token_required", False),
                    "system": data_b.get("system", {})
                }
            else:
                buzzer_res_dict = {
                    "success": False,
                    "url": target_buzzer_url,
                    "latency_ms": buzzer_latency,
                    "error": f"HTTP {res_b.status_code}: {res_b.text[:80]}"
                }
        except Exception as e:
            buzzer_res_dict = {
                "success": False,
                "url": target_buzzer_url,
                "latency_ms": round((time.perf_counter() - start_b) * 1000, 1),
                "error": str(e)
            }

        result = {
            "success": led_res_dict.get("success", False) and buzzer_res_dict.get("success", False),
            "led": led_res_dict,
            "buzzer": buzzer_res_dict,
            "timestamp": time.strftime("%H:%M:%S", time.localtime())
        }
        self.last_test_result = result
        return result

    # ==================== 状态切换快捷函数 ====================

    def on_startup_complete(self):
        self.current_state = "startup_flashing_green"
        self._call_api_async("/api/state", "POST", {"state": "startup"})

    def set_state_thinking(self):
        if not self.auto_indicator: return
        self.current_state = "thinking_solid_yellow"
        self._call_api_async("/api/state", "POST", {"state": "thinking", "duration": 300})

    def set_state_breathing_yellow(self):
        if not self.auto_indicator: return
        self.current_state = "breathing_yellow"
        self._call_api_async("/api/state", "POST", {"state": "breathing"})

    def set_state_error(self):
        if not self.auto_indicator: return
        self.current_state = "solid_red_error"
        self._call_api_async("/api/state", "POST", {"state": "error"})

    def set_state_success(self):
        if not self.auto_indicator: return
        self.current_state = "success_solid_green"
        self._call_api_async("/api/state", "POST", {"state": "success", "duration": self.success_duration})

    def turn_all_off(self):
        self._stop_recurring_sound()
        self.current_state = "off"
        self._call_api_async("/api/off", "POST")
        self.stop_buzzer()

    def on_service_restarting(self):
        self._stop_recurring_sound()
        self.stop_buzzer()
        if not getattr(self, "enabled", True): return
        self.current_state = "restarting_yellow_blink"
        self._call_api_async("/api/state", "POST", {"state": "restarting"})

    # ==================== AI 对话生命周期拦截与交互式输入捕获 ====================

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        if not getattr(self, "enabled", True): return user_text, session_data

        # 检查是否处于等待用户输入配置的状态 (带 120 秒超时机制)
        pending_item = self.pending_input.get(chat_id)
        if pending_item:
            pending_mode, ts = pending_item
            if time.time() - ts > 120:
                self.pending_input.pop(chat_id, None)
                pending_mode = None

            if pending_mode:
                text_val = user_text.strip()
                if text_val.lower() in ["取消", "cancel", "退出", "q"]:
                    self.pending_input.pop(chat_id, None)
                    send_text_to_chat_sdk(chat_id, "⚪ 已取消配置修改。")
                    return "", session_data

                if pending_mode == "waiting_url":
                    self.pending_input.pop(chat_id, None)
                    self.save_config_file({"api_url": text_val})
                    test_res = self.test_gateway_connection()
                    res_badge = f"🟢 **LED 测试通过** (`{test_res.get('led', {}).get('latency_ms')} ms`)" if test_res.get("led", {}).get("success") else f"🔴 **LED 测试未通过** (`{test_res.get('led', {}).get('error')}`)"
                    reply_msg = (
                        f"✅ **LED 网关地址已成功修改并保存！**\n\n"
                        f"• **当前网关地址**：`{self.api_url}`\n"
                        f"• **实时连通性测试**：{res_badge}\n\n"
                        f"💡 发送 `/led` 可重新打开主控制台卡片。"
                    )
                    send_text_to_chat_sdk(chat_id, reply_msg)
                    return "", session_data

                elif pending_mode == "waiting_token":
                    self.pending_input.pop(chat_id, None)
                    new_tok = "" if text_val.lower() in ("0", "none", "null", "空", "清空", "无") else text_val
                    self.save_config_file({"api_token": new_tok})
                    test_res = self.test_gateway_connection()
                    res_badge = f"🟢 **测试通过**" if test_res.get("led", {}).get("success") else f"🔴 **测试未通过**"
                    tok_desc = f"`{new_tok[:3]}****{new_tok[-3:]}`" if len(new_tok) > 6 else (new_tok or "无 (免密模式)")
                    reply_msg = (
                        f"✅ **LED API Token 密钥已成功更新！**\n\n"
                        f"• **当前 Token 状态**：`{tok_desc}`\n"
                        f"• **实时连通性测试**：{res_badge}\n\n"
                        f"💡 发送 `/led` 可重新打开主控制台卡片。"
                    )
                    send_text_to_chat_sdk(chat_id, reply_msg)
                    return "", session_data

                elif pending_mode == "waiting_buzzer_url":
                    self.pending_input.pop(chat_id, None)
                    self.save_config_file({"buzzer_api_url": text_val})
                    test_res = self.test_gateway_connection()
                    res_badge = f"🟢 **蜂鸣器测试通过** (`{test_res.get('buzzer', {}).get('latency_ms')} ms`)" if test_res.get("buzzer", {}).get("success") else f"🔴 **蜂鸣器测试未通过** (`{test_res.get('buzzer', {}).get('error')}`)"
                    reply_msg = (
                        f"✅ **蜂鸣器 API 网关地址已成功修改！**\n\n"
                        f"• **当前网关地址**：`{self.buzzer_api_url}`\n"
                        f"• **实时连通性测试**：{res_badge}\n\n"
                        f"💡 发送 `/led` 或 `/stat` 可查看状态。"
                    )
                    send_text_to_chat_sdk(chat_id, reply_msg)
                    return "", session_data

                elif pending_mode == "waiting_buzzer_token":
                    self.pending_input.pop(chat_id, None)
                    new_tok = "" if text_val.lower() in ("0", "none", "null", "空", "清空", "无") else text_val
                    self.save_config_file({"buzzer_api_token": new_tok})
                    test_res = self.test_gateway_connection()
                    res_badge = f"🟢 **测试通过**" if test_res.get("buzzer", {}).get("success") else f"🔴 **测试未通过**"
                    tok_desc = f"`{new_tok[:3]}****{new_tok[-3:]}`" if len(new_tok) > 6 else (new_tok or "无 (免密模式)")
                    reply_msg = (
                        f"✅ **蜂鸣器 API Token 密钥已成功更新！**\n\n"
                        f"• **当前 Token 状态**：`{tok_desc}`\n"
                        f"• **实时连通性测试**：{res_badge}\n\n"
                        f"💡 发送 `/led` 可重新打开主控制台卡片。"
                    )
                    send_text_to_chat_sdk(chat_id, reply_msg)
                    return "", session_data

        # 正常进入 AI 思考状态：黄灯常亮 + 每 10 秒单哔提示音
        self.set_state_thinking()
        self._start_recurring_sound("thinking")
        return user_text, session_data

    async def on_tool_call(self, tool_name: str, tool_args: dict):
        """当 AI 调用工具时：黄灯呼吸 + 每 10 秒双哔确认提示音"""
        if not getattr(self, "enabled", True): return
        self.set_state_breathing_yellow()
        self._start_recurring_sound("tool")

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """当 AI 回复生成完毕后：停止循环计时音，播放成功或失败提示音"""
        if not getattr(self, "enabled", True): return ai_response_text
        if not ai_response_text: return ai_response_text

        # 立即停止思考/工具调用的循环提示音
        self._stop_recurring_sound()

        is_err = session_data.get("last_execution_error", False)
        if not is_err:
            stripped = ai_response_text.strip()
            if stripped.startswith(("❌", "⚠️")) or "traceback (most recent call last):" in stripped.lower():
                is_err = True

        if is_err:
            self.set_state_error()
            self.play_melody("error")
        else:
            self.set_state_success()
            self.play_melody("success")

        return ai_response_text

    async def on_task_stop(self, chat_id: str):
        """当任务被 /stop 叫停、取消或发生致命异常时触发"""
        log.info(f"[Plugin:{self.plugin_id}] 🛑 监听到任务中止信号 (chat: {chat_id})，立即停止循环提示音、静音并切换红灯告警")
        self._stop_recurring_sound()
        self.stop_buzzer()
        self.set_state_error()
        self.play_melody("error")

    async def on_message(self, chat_id: str, user_text: str, message_id: str, session_data: dict) -> bool:
        """前置消息拦截：若用户输入 /stop 时毫秒级停止发声与指示"""
        txt = (user_text or "").strip().lower()
        if txt in ["/stop", "/cancel", "叫停任务", "停止"]:
            log.info(f"[Plugin:{self.plugin_id}] 🛑 前置拦截到中止命令 '{txt}'，快速终止蜂鸣器循环与发声")
            self._stop_recurring_sound()
            self.stop_buzzer()
            self.set_state_error()
            self.play_melody("error")
            return False
        return False

    # ==================== 飞书命令处理 (/led, /light, /stat) ====================

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if not getattr(self, "enabled", True):
            return False

        cmd_lower = command.lower()
        if cmd_lower in ["/stop", "/cancel"]:
            log.info(f"[Plugin:{self.plugin_id}] on_command 捕获到 {cmd_lower}，终止蜂鸣器发声")
            self._stop_recurring_sound()
            self.stop_buzzer()
            self.set_state_error()
            self.play_melody("error")
            return False

        if cmd_lower not in ["/led", "/light", "/stat"]:
            return False

        args_parts = args.strip().split() if args else []
        subcmd = args_parts[0].lower() if args_parts else ""

        # 1. /stat 命令处理：综合系统与硬件看板
        if cmd_lower == "/stat" or subcmd == "stat":
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_stat_card(snapshot, buzzer_snapshot)
            self.send_reply_card(message_id, card)
            return True

        # 2. /led 默认或 panel / status 视图：重构控制中心面板
        if not subcmd or subcmd in ["status", "panel"]:
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="control")
            self.send_reply_card(message_id, card)
            return True

        # 3. 连通性测试
        elif subcmd in ["test", "ping"]:
            res = self.test_gateway_connection()
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="control", test_banner=res)
            self.send_reply_card(message_id, card)
            return True

        # 4. 配置视图
        elif subcmd == "config":
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config")
            self.send_reply_card(message_id, card)
            return True

        # 5. 提示音试听 (/led sound <name>)
        elif subcmd in ["sound", "play"] and len(args_parts) >= 2:
            m_name = args_parts[1].lower()
            self.play_melody(m_name)
            self.send_reply_text(message_id, f"🔊 正在播放蜂鸣器提示音：`{m_name}`")
            return True

        # 6. 一键关闭 / 停止
        elif subcmd in ["off", "stop"]:
            self.turn_all_off()
            self.send_reply_text(message_id, "⏹️ 所有物理指示灯与蜂鸣器发声已全部停止。")
            return True

        elif subcmd in ["thinking", "think"]:
            self.set_state_thinking()
            self.play_melody("beep")
            self.send_reply_text(message_id, "🟡 已切换至【思考中】(黄灯常亮 + 单哔提示)")
            return True

        elif subcmd in ["breathing", "breath", "run"]:
            self.set_state_breathing_yellow()
            self.play_melody("two_beeps")
            self.send_reply_text(message_id, "✨ 已切换至【任务执行中】(平滑正弦呼吸黄灯 + 双哔确认)")
            return True

        elif subcmd in ["success", "ok"]:
            self.set_state_success()
            self.play_melody("success")
            self.send_reply_text(message_id, f"🟢 已切换至【任务完成】(绿灯常亮 {self.success_duration}s + 成功音)")
            return True

        elif subcmd in ["error", "err", "fail"]:
            self.set_state_error()
            self.play_melody("error")
            self.send_reply_text(message_id, "🔴 已切换至【系统异常】(红灯常亮 + 失败告警音)")
            return True

        elif subcmd in ["upgrade", "levelup"]:
            self.play_melody("level_up")
            self.send_reply_text(message_id, "🆙 正在播放【角色升级 (Level Up)】提示音！")
            return True

        elif subcmd in ["startup", "check"]:
            self.on_startup_complete()
            self.play_melody("level_up")
            self.send_reply_text(message_id, "🔄 已触发【开机自检】(绿灯连闪 5 次 + 角色升级音)")
            return True

        elif subcmd == "timer" and len(args_parts) >= 3:
            color = args_parts[1].lower()
            try:
                secs = int(args_parts[2])
                self._call_api_async("/api/timer", "POST", {"color": color, "duration_sec": secs, "fade_out_sec": 5})
                self.send_reply_text(message_id, f"⏱️ 已为 {color.upper()} 启动 {secs} 秒智能倒计时 (结束前渐暗关灯)")
            except ValueError:
                self.send_reply_text(message_id, "⚠️ 请输入正确的秒数，格式：`/led timer green 60`")
            return True

        elif subcmd == "pattern" and len(args_parts) >= 2:
            p_name = args_parts[1].lower()
            self._call_api_async("/api/pattern", "POST", {"name": p_name, "repeat": 5})
            self.send_reply_text(message_id, f"🎭 正在播放动效序列：`{p_name}`")
            return True

        else:
            help_text = (
                "🍓 **树莓派 GPIO 状态灯与蜂鸣器指示指令**：\n\n"
                "• `/stat`：查看树莓派系统负载、温度、LED 与蜂鸣器全景状态\n"
                "• `/led` 或 `/light`：弹出重构后的交互式综合控制面板卡片\n"
                "• `/led test`：测试 LED 网关与蜂鸣器双网关连通性与响应延迟\n"
                "• `/led config`：进入交互式网关地址、Token 与音量配置面板\n"
                "• `/led sound <name>`：试听提示音 (`level_up` / `beep` / `two_beeps` / `success` / `error`)\n"
                "• `/led thinking`：测试思考状态 (黄灯常亮 + 单哔)\n"
                "• `/led breathing`：测试任务执行状态 (正弦呼吸 + 双哔)\n"
                "• `/led success`：测试成功状态 (绿灯 + 成功音)\n"
                "• `/led error`：测试失败状态 (红灯 + 错误音)\n"
                "• `/led off`：一键熄灭全灯并紧急静音"
            )
            self.send_reply_text(message_id, help_text)
            return True

    # ==================== 飞书交互式卡片事件响应 ====================

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        if not getattr(self, "enabled", True):
            return False

        act = action or (value.get("action") if isinstance(value, dict) else "")

        # 1. 播放蜂鸣器提示音
        if act == "play_buzzer_melody":
            melody = value.get("melody", "beep")
            if melody == "stop":
                self.stop_buzzer()
            else:
                self.play_melody(melody)
            return True

        # 2. 连通性测试 (双网关)
        elif act == "test_gateway_connection":
            test_res = self.test_gateway_connection()
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            current_view = value.get("view", "control")
            if current_view == "stat":
                card = self.build_stat_card(snapshot, buzzer_snapshot, test_banner=test_res)
            else:
                card = self.build_control_card(snapshot, buzzer_snapshot, view_mode=current_view, test_banner=test_res)
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 3. 视图切换 (control / config / stat)
        elif act == "switch_led_view":
            target_view = value.get("view", "control")
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            if target_view == "stat":
                card = self.build_stat_card(snapshot, buzzer_snapshot)
            else:
                card = self.build_control_card(snapshot, buzzer_snapshot, view_mode=target_view)
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 4. 交互式按钮：提示输入 LED 网关 URL
        elif act == "prompt_input_url":
            self.pending_input[chat_id] = ("waiting_url", time.time())
            prompt_text = (
                "🌐 **【修改 LED 网关服务地址】**\n\n"
                "请直接在聊天框中**回复新的网关服务 URL**（例如：`http://127.0.0.1:8080`）：\n\n"
                "*(若需退出修改，请回复「取消」)*"
            )
            send_reply_sdk(card_message_id, prompt_text)
            return True

        # 5. 交互式按钮：提示输入 LED Token
        elif act == "prompt_input_token":
            self.pending_input[chat_id] = ("waiting_token", time.time())
            prompt_text = (
                "🔑 **【修改 LED API Token 密钥】**\n\n"
                "请直接在聊天框中**回复新的 API Token 密钥**（若免密请回复「空」或「0」）：\n\n"
                "*(若需退出修改，请回复「取消」)*"
            )
            send_reply_sdk(card_message_id, prompt_text)
            return True

        # 6. 交互式按钮：提示输入 Buzzer 网关 URL
        elif act == "prompt_input_buzzer_url":
            self.pending_input[chat_id] = ("waiting_buzzer_url", time.time())
            prompt_text = (
                "🔊 **【修改蜂鸣器 API 网关地址】**\n\n"
                "请直接在聊天框中**回复新的蜂鸣器服务 URL**（例如：`http://127.0.0.1:8001`）：\n\n"
                "*(若需退出修改，请回复「取消」)*"
            )
            send_reply_sdk(card_message_id, prompt_text)
            return True

        # 7. 交互式按钮：提示输入 Buzzer Token
        elif act == "prompt_input_buzzer_token":
            self.pending_input[chat_id] = ("waiting_buzzer_token", time.time())
            prompt_text = (
                "🔑 **【修改蜂鸣器 API Token 密钥】**\n\n"
                "请直接在聊天框中**回复新的蜂鸣器专属 Token**（例如 `bz_live_...`）：\n\n"
                "*(若需退出修改，请回复「取消」)*"
            )
            send_reply_sdk(card_message_id, prompt_text)
            return True

        # 8. 交互式按钮：重置为本地默认
        elif act == "reset_default_local":
            self.save_config_file({
                "api_url": DEFAULT_API_URL,
                "api_token": DEFAULT_API_TOKEN,
                "buzzer_api_url": DEFAULT_BUZZER_URL,
                "buzzer_api_token": DEFAULT_BUZZER_TOKEN,
                "buzzer_volume": 10,
                "buzzer_interval": 15
            })
            test_res = self.test_gateway_connection()
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config", test_banner=test_res, info_banner="✅ 已重置为本地双网关默认配置 (LED: 8080, Buzzer: 8001)")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 9. 切换自动 LED 状态指示联动开关
        elif act == "toggle_auto_indicator":
            new_auto = not self.auto_indicator
            self.save_config_file({"auto_indicator_enabled": new_auto})
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 10. 切换蜂鸣器提示音自动联动开关
        elif act == "toggle_buzzer_enabled":
            new_bz = not self.buzzer_enabled
            self.save_config_file({"buzzer_enabled": new_bz})
            if not new_bz:
                self._stop_recurring_sound()
                self.stop_buzzer()
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 11. 调整蜂鸣器音量
        elif act == "set_buzzer_volume":
            vol = int(value.get("volume", 10))
            self.save_config_file({"buzzer_volume": vol})
            self.play_melody("beep", volume=vol)
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 11.1 调整思考与工具调用提示音循环间隔时间
        elif act == "set_buzzer_interval":
            inv = int(value.get("interval", 15))
            self.save_config_file({"buzzer_interval": inv})
            self.play_melody("beep")
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config", info_banner=f"✅ 提示音循环间隔已设为 {inv} 秒")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 12. 设置成功保持秒数
        elif act == "set_success_duration":
            dur = int(value.get("duration", 300))
            self.save_config_file({"success_duration_sec": dur})
            snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(snapshot, buzzer_snapshot, view_mode="config")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 13. 控制面板：切换系统预设状态
        elif act == "set_led_state":
            target_state = value.get("state", "off")
            dur = int(value.get("duration", 300))
            self._call_api_async("/api/state", "POST", {"state": target_state, "duration": dur})
            if target_state == "thinking":
                self.play_melody("beep")
            elif target_state == "breathing":
                self.play_melody("two_beeps")
            elif target_state == "success":
                self.play_melody("success")
            elif target_state == "error":
                self.play_melody("error")
            elif target_state == "off":
                self.turn_all_off()

            time.sleep(0.3)
            new_snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(new_snapshot, buzzer_snapshot, view_mode="control")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 14. 控制面板：一键熄灭 & 静音
        elif act == "turn_off_all":
            self.turn_all_off()
            time.sleep(0.3)
            new_snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            card = self.build_control_card(new_snapshot, buzzer_snapshot, view_mode="control")
            patch_interactive_card_sdk(card_message_id, card)
            return True

        # 15. 控制面板：刷新卡片
        elif act == "refresh_led_card":
            target_view = value.get("view", "control")
            new_snapshot = self._fetch_snapshot_sync()
            buzzer_snapshot = self._fetch_buzzer_status_sync()
            if target_view == "stat":
                card = self.build_stat_card(new_snapshot, buzzer_snapshot)
            else:
                card = self.build_control_card(new_snapshot, buzzer_snapshot, view_mode=target_view)
            patch_interactive_card_sdk(card_message_id, card)
            return True

        return False

    # ==================== 构建 /led 综合控制卡片 ====================

    def build_control_card(self, snapshot: Optional[dict] = None, buzzer_snapshot: Optional[dict] = None,
                           view_mode: str = "control", test_banner: Optional[dict] = None,
                           info_banner: Optional[str] = None) -> dict:
        raw_state = "unknown"
        mode_str = "HTTP 网关连接"
        pins_info = "🔴 22 | 🟡 27 | 🟢 17"
        header_color = "blue"

        if snapshot:
            raw_state = snapshot.get("current_state", "off")
            hw = snapshot.get("hardware", {})
            mode_str = f"{hw.get('mode', 'MOCK')} {'(Gamma 2.2)' if hw.get('gamma_correction') else ''}"
            pins = hw.get("pins", {})
            if pins:
                pins_info = f"🔴 GPIO {pins.get('red', 22)} | 🟡 GPIO {pins.get('yellow', 27)} | 🟢 GPIO {pins.get('green', 17)}"

        state_map = {
            "thinking_solid_yellow": ("🟡 思考中 (Solid Yellow)", "orange"),
            "thinking": ("🟡 思考中 (Solid Yellow)", "orange"),
            "breathing_yellow": ("✨ 任务执行中 (Breathing Yellow)", "orange"),
            "breathing": ("✨ 任务执行中 (Breathing Yellow)", "orange"),
            "restarting_yellow_blink": ("⚡ 重启中 (Blink Yellow)", "orange"),
            "success_solid_green": ("🟢 任务成功完成 (Solid Green)", "green"),
            "solid_green_success_300s": ("🟢 任务成功完成 (Solid Green)", "green"),
            "solid_red_error": ("🔴 系统异常告警 (Solid Red)", "red"),
            "error": ("🔴 系统异常告警 (Solid Red)", "red"),
            "startup_flashing_green": ("🔄 开机自检中 (Startup)", "blue"),
            "off": ("⏹️ 全部熄灭 (Off)", "grey"),
        }

        display_name, header_color = state_map.get(raw_state, (f"💡 运行中 ({raw_state})", "blue"))

        # 蜂鸣器状态摘要
        buzzer_playing = False
        buzzer_freq = 0
        if buzzer_snapshot:
            buzzer_playing = bool(buzzer_snapshot.get("is_playing"))
            buzzer_freq = buzzer_snapshot.get("frequency", 0)

        buzzer_desc = f"🎵 正在发声 ({buzzer_freq} Hz)" if buzzer_playing else "⏹️ 静音待命"
        buzzer_switch_text = "🟢 开启联动" if self.buzzer_enabled else "⚪ 已停用"

        masked_led_token = f"{self.api_token[:3]}****{self.api_token[-3:]}" if len(self.api_token) > 6 else (self.api_token or "无")
        masked_buzzer_token = f"{self.buzzer_api_token[:4]}****{self.buzzer_api_token[-4:]}" if len(self.buzzer_api_token) > 8 else (self.buzzer_api_token or "无")

        elements = []

        # 1. Info / Prompt Banner
        if info_banner:
            elements.append({"tag": "markdown", "content": info_banner})
            elements.append({"tag": "hr"})

        # 2. Test Result Banner (双网关)
        if test_banner or self.last_test_result:
            tb = test_banner or self.last_test_result
            led_t = tb.get("led", {})
            buz_t = tb.get("buzzer", {})

            led_badge = f"✅ **LED 网关**：`{led_t.get('latency_ms')} ms` (驱动: `{led_t.get('driver_mode', 'OK')}`)" if led_t.get("success") else f"❌ **LED 网关失败**：`{led_t.get('error')}`"
            buz_badge = f"✅ **蜂鸣器 API**：`{buz_t.get('latency_ms')} ms` (通道: `{buz_t.get('pwm_channel', 'pwm0')}`)" if buz_t.get("success") else f"❌ **蜂鸣器 API 失败**：`{buz_t.get('error')}`"

            elements.append({
                "tag": "markdown",
                "content": f"🔌 **双网关连通性测试报告** (`{tb.get('timestamp')}`)\n\n• {led_badge}\n• {buz_badge}"
            })
            elements.append({"tag": "hr"})

        if view_mode == "config":
            # ==================== 参数配置视图 ====================
            elements.extend([
                {
                    "tag": "markdown",
                    "content": (
                        "**⚙️ 硬件与网关参数配置中心**\n\n"
                        f"• **LED 网关服务**：`{self.api_url}` (Token: `{masked_led_token}`)\n"
                        f"• **蜂鸣器网关服务**：`{self.buzzer_api_url}` (Token: `{masked_buzzer_token}`)\n"
                        f"• **指示灯自动联动**：`{'🟢 开启中' if self.auto_indicator else '⚪ 已停用'}` | 成功常亮：`{self.success_duration}s`\n"
                        f"• **蜂鸣器提示音联动**：`{buzzer_switch_text}` | 默认音量：`{self.buzzer_volume}%` | 循环间隔：`{self.buzzer_interval}s`"
                    )
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "**🎛️ 1. 修改网关与安全 Token（机器人将发送回复提示）：**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🌐 修改 LED 网关"}, "type": "primary", "value": {"action": "prompt_input_url"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔑 修改 LED Token"}, "type": "default", "value": {"action": "prompt_input_token"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔊 修改蜂鸣器网关"}, "type": "primary", "value": {"action": "prompt_input_buzzer_url"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔑 修改蜂鸣器 Token"}, "type": "default", "value": {"action": "prompt_input_buzzer_token"}}
                    ]
                },
                {
                    "tag": "markdown",
                    "content": "**⏱️ 2. 联动开关与参数微调：**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"LED联动: {'🟢 开启' if self.auto_indicator else '⚪ 停用'}"}, "type": "primary" if self.auto_indicator else "default", "value": {"action": "toggle_auto_indicator"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"提示音: {buzzer_switch_text}"}, "type": "primary" if self.buzzer_enabled else "default", "value": {"action": "toggle_buzzer_enabled"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_volume == 1 else ''}音量 1%"}, "type": "primary" if self.buzzer_volume == 1 else "default", "value": {"action": "set_buzzer_volume", "volume": 1}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_volume == 5 else ''}音量 5%"}, "type": "primary" if self.buzzer_volume == 5 else "default", "value": {"action": "set_buzzer_volume", "volume": 5}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_volume == 10 else ''}音量 10%"}, "type": "primary" if self.buzzer_volume == 10 else "default", "value": {"action": "set_buzzer_volume", "volume": 10}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_volume == 15 else ''}音量 15%"}, "type": "primary" if self.buzzer_volume == 15 else "default", "value": {"action": "set_buzzer_volume", "volume": 15}}
                    ]
                },
                {
                    "tag": "markdown",
                    "content": f"**🔔 3. 思考/工具调用循环提示音间隔：** (当前: `{self.buzzer_interval}秒`)"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_interval == 15 else ''}间隔 15秒"}, "type": "primary" if self.buzzer_interval == 15 else "default", "value": {"action": "set_buzzer_interval", "interval": 15}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_interval == 20 else ''}间隔 20秒"}, "type": "primary" if self.buzzer_interval == 20 else "default", "value": {"action": "set_buzzer_interval", "interval": 20}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": f"{'✓ ' if self.buzzer_interval == 30 else ''}间隔 30秒"}, "type": "primary" if self.buzzer_interval == 30 else "default", "value": {"action": "set_buzzer_interval", "interval": 30}}
                    ]
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔌 测试双网关连通性"}, "type": "primary", "value": {"action": "test_gateway_connection", "view": "config"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🏠 重置为本地默认"}, "type": "danger", "value": {"action": "reset_default_local"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔙 返回控制面板"}, "type": "default", "value": {"action": "switch_led_view", "view": "control"}}
                    ]
                }
            ])
            header_title = "⚙️ 树莓派 LED & 蜂鸣器参数配置中心"
            header_color = "blue"

        else:
            # ==================== 标准控制看板视图 ====================
            elements.extend([
                {
                    "tag": "markdown",
                    "content": (
                        f"**💡 当前 LED 状态**：**{display_name}**\n"
                        f"• 引脚：`{pins_info}` | 驱动：`{mode_str}`\n\n"
                        f"**🔊 当前蜂鸣器状态**：**{buzzer_desc}**\n"
                        f"• 硬件通道：`GPIO 18 (PWM0)` | 音量：`{self.buzzer_volume}%` | 间隔：`{self.buzzer_interval}s` | 联动：`{buzzer_switch_text}`"
                    )
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "**🎯 1. LED 物理状态切换**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🟡 思考中"}, "type": "default", "value": {"action": "set_led_state", "state": "thinking", "duration": 300}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "✨ 正弦呼吸"}, "type": "primary", "value": {"action": "set_led_state", "state": "breathing"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🟢 成功完成"}, "type": "primary", "value": {"action": "set_led_state", "state": "success", "duration": self.success_duration}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔴 异常报错"}, "type": "danger", "value": {"action": "set_led_state", "state": "error"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "⏹️ 熄灭全灯"}, "type": "default", "value": {"action": "turn_off_all"}}
                    ]
                },
                {
                    "tag": "markdown",
                    "content": "**🔔 2. 蜂鸣器提示音快速试听**"
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🆙 角色升级"}, "type": "primary", "value": {"action": "play_buzzer_melody", "melody": "level_up"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔔 单哔确认"}, "type": "default", "value": {"action": "play_buzzer_melody", "melody": "beep"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "✌️ 双哔确认"}, "type": "default", "value": {"action": "play_buzzer_melody", "melody": "two_beeps"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 操作成功"}, "type": "primary", "value": {"action": "play_buzzer_melody", "melody": "success"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 操作失败"}, "type": "danger", "value": {"action": "play_buzzer_melody", "melody": "error"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🛑 静音"}, "type": "default", "value": {"action": "play_buzzer_melody", "melody": "stop"}}
                    ]
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔌 测双网关连通性"}, "type": "primary", "value": {"action": "test_gateway_connection", "view": "control"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "⚙️ 参数配置"}, "type": "default", "value": {"action": "switch_led_view", "view": "config"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "📊 系统综合状态 (/stat)"}, "type": "default", "value": {"action": "switch_led_view", "view": "stat"}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "🔄 刷新卡片"}, "type": "default", "value": {"action": "refresh_led_card", "view": "control"}}
                    ]
                }
            ])
            header_title = "🍓 树莓派 GPIO 状态灯 & 蜂鸣器控制中心"

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": header_color
            },
            "elements": elements
        }

    # ==================== 构建 /stat 综合系统与硬件状态卡片 ====================

    def build_stat_card(self, snapshot: Optional[dict] = None, buzzer_snapshot: Optional[dict] = None,
                        test_banner: Optional[dict] = None) -> dict:
        """构建全景系统监控与 GPIO / 蜂鸣器硬件运行状态卡片"""
        sys_metrics = {}
        if buzzer_snapshot and "system" in buzzer_snapshot:
            sys_metrics = dict(buzzer_snapshot["system"])
        elif snapshot and "system" in snapshot:
            sys_metrics = dict(snapshot["system"])

        # 本地底层指标后备保底 (若 API 网关暂未返回或不可达)
        if not sys_metrics.get("temperature"):
            try:
                if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                        sys_metrics['temperature'] = round(int(f.read().strip()) / 1000.0, 1)
            except Exception:
                pass
        if not sys_metrics.get("load_avg") or sys_metrics.get("load_avg") == [0.0, 0.0, 0.0]:
            try:
                sys_metrics['load_avg'] = [round(x, 2) for x in os.getloadavg()]
            except Exception:
                pass
        if not sys_metrics.get("memory_total_mb"):
            try:
                with open('/proc/meminfo', 'r') as f:
                    mem_dict = {}
                    for line in f:
                        parts = line.split(':')
                        if len(parts) == 2:
                            mem_dict[parts[0].strip()] = int(parts[1].split()[0].strip())
                    total_kb = mem_dict.get('MemTotal', 0)
                    avail_kb = mem_dict.get('MemAvailable', 0)
                    sys_metrics['memory_total_mb'] = int(total_kb / 1024)
                    sys_metrics['memory_used_mb'] = int((total_kb - avail_kb) / 1024)
            except Exception:
                pass
        if not sys_metrics.get("uptime_hours"):
            try:
                with open('/proc/uptime', 'r') as f:
                    sys_metrics['uptime_hours'] = round(float(f.read().split()[0]) / 3600.0, 1)
            except Exception:
                pass

        # CPU 温度
        temp = sys_metrics.get("temperature", 0.0)
        temp_color = "red" if temp >= 70 else ("orange" if temp >= 60 else "green")

        # 内存
        mem_used = sys_metrics.get("memory_used_mb", 0)
        mem_total = sys_metrics.get("memory_total_mb", 1)
        mem_percent = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0

        # 系统负载
        load_avg = sys_metrics.get("load_avg", [0.0, 0.0, 0.0])
        load_str = f"{load_avg[0]} / {load_avg[1]} / {load_avg[2]}" if isinstance(load_avg, list) and len(load_avg) >= 3 else str(load_avg)

        # 运行时长
        uptime_h = sys_metrics.get("uptime_hours", 0.0)

        # LED 状态
        raw_state = snapshot.get("current_state", "off") if snapshot else "unknown"
        hw = snapshot.get("hardware", {}) if snapshot else {}
        pins = hw.get("pins", {}) if hw else {}
        pins_str = f"🔴 {pins.get('red', 22)} | 🟡 {pins.get('yellow', 27)} | 🟢 {pins.get('green', 17)}" if pins else "🔴 22 | 🟡 27 | 🟢 17"
        led_mode = hw.get("mode", "GPIOZERO_LGPIO") if hw else "未知"

        # 蜂鸣器状态
        is_playing = buzzer_snapshot.get("is_playing", False) if buzzer_snapshot else False
        buzzer_freq = buzzer_snapshot.get("frequency", 0) if buzzer_snapshot else 0
        buzzer_note = buzzer_snapshot.get("note", "--") if buzzer_snapshot else "--"

        elements = []

        # 连通性测试报告横幅 (若有)
        if test_banner or self.last_test_result:
            tb = test_banner or self.last_test_result
            led_t = tb.get("led", {})
            buz_t = tb.get("buzzer", {})
            led_badge = f"🟢 `{led_t.get('latency_ms')} ms`" if led_t.get("success") else "🔴 离线"
            buz_badge = f"🟢 `{buz_t.get('latency_ms')} ms`" if buz_t.get("success") else "🔴 离线"
            elements.append({
                "tag": "markdown",
                "content": f"🔌 **实时双网关 RTT 测速** (`{tb.get('timestamp')}`)\n• **LED 网关**：{led_badge} | **蜂鸣器 API**：{buz_badge}"
            })
            elements.append({"tag": "hr"})

        elements.extend([
            {
                "tag": "markdown",
                "content": (
                    "**🖥️ 树莓派底层核心硬件指标**\n\n"
                    f"• 🌡️ **CPU 运行温度**：<font color='{temp_color}'>**{temp} °C**</font>\n"
                    f"• 🧠 **物理内存负载**：**{mem_used} MB** / {mem_total} MB (`{mem_percent}%`)\n"
                    f"• 📈 **系统负载均值**：`{load_str}` (1/5/15 min)\n"
                    f"• ⏱️ **连续开机时长**：**{uptime_h} 小时**"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**💡 GPIO 三色 LED 状态 (pi_led_api:8080)**\n\n"
                    f"• **当前模式**：`{raw_state}`\n"
                    f"• **硬件引脚**：`{pins_str}` (驱动: `{led_mode}`)\n"
                    f"• **网关地址**：`{self.api_url}`"
                )
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**🔊 硬件 PWM 蜂鸣器状态 (buzzer-api:8001)**\n\n"
                    f"• **通道输出**：`GPIO 18 (PWM0)`\n"
                    f"• **发声状态**：`{'正在发声 🎵 (' + str(buzzer_freq) + ' Hz / ' + str(buzzer_note) + ')' if is_playing else '静音待命 ⏹️'}`\n"
                    f"• **提示音联动**：`{'🟢 开启中' if self.buzzer_enabled else '⚪ 已停用'}` (音量: `{self.buzzer_volume}%`, 循环: `{self.buzzer_interval}s`)\n"
                    f"• **服务地址**：`{self.buzzer_api_url}`"
                )
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "🔄 刷新状态"}, "type": "primary", "value": {"action": "refresh_led_card", "view": "stat"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "🔌 测双网关延迟"}, "type": "default", "value": {"action": "test_gateway_connection", "view": "stat"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "🎛️ 打开控制面板 (/led)"}, "type": "default", "value": {"action": "switch_led_view", "view": "control"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "🆙 试听升级音"}, "type": "default", "value": {"action": "play_buzzer_melody", "melody": "level_up"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 试听成功音"}, "type": "default", "value": {"action": "play_buzzer_melody", "melody": "success"}}
                ]
            }
        ])

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📊 树莓派硬件与服务全景状态看板 (/stat)"},
                "template": "blue"
            },
            "elements": elements
        }
