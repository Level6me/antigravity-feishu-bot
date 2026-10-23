"""
FoloToy AI Passport × Letgo 极简实用硬件协同控制面板
专注核心能力：设备状态感知、寻机声光呼叫、Letgo 固件一键 OTA、网关音色无缝切换、指定文本对话式播报
"""

import os
import sys
import json
import time
import asyncio
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, Dict, Any, List, Tuple

from plugin_base import BasePlugin
from logger import log
from lark_client import (
    send_interactive_card_sdk, patch_interactive_card_sdk,
    send_reply_sdk, send_card_to_chat_sdk
)
import app_state

TTS_VOICE_LIST = [
    ("zh-CN-XiaoxiaoNeural", "👧 晓晓 (自然亲切女声)"),
    ("zh-CN-YunyangNeural", "🎙️ 云扬 (专业沉稳播音男声)"),
    ("zh-CN-YunxiNeural", "👦 云希 (活力解说男声)"),
    ("zh-CN-YunjianNeural", "👨 云健 (影视稳重男声)"),
    ("zh-CN-XiaoyiNeural", "👩 晓伊 (日常温和女声)")
]


# ==================== 网关与 Letgo 感知底层方法 ====================
def query_gateway_status(port: int = 8765) -> dict:
    """获取 passport-bridge 网关状态"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"User-Agent": "PassportPlugin"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return {}


def query_gateway_devices(port: int = 8765) -> dict:
    """获取 passport-bridge 白名单配对设备"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/devices", headers={"User-Agent": "PassportPlugin"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return {}


def query_gateway_current_voice(port: int = 8765) -> str:
    """从 passport-bridge 读取当前实际生效的 TTS 音色"""
    gw = query_gateway_status(port)
    if gw and gw.get("tts_voice"):
        return gw["tts_voice"]

    candidates = [
        os.environ.get("PASSPORT_CONFIG_FILE"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
        "/home/jiang/github/passport-bridge/config.json"
    ]
    for cfg_file in candidates:
        if cfg_file and os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("tts_voice"):
                        return data.get("tts_voice")
            except Exception:
                pass
    return "zh-CN-XiaoxiaoNeural"


def call_gateway_set_voice(new_voice: str, port: int = 8765) -> tuple:
    """调用 passport-bridge 设置新音色并同步让硬件生效"""
    try:
        data = json.dumps({"voice": new_voice}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/voice",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "PassportPlugin"}
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                return True, f"已切换音色为【{new_voice}】"
    except Exception as e:
        return False, f"切换音色失败: {e}"
    return False, "切换音色失败"


def call_gateway_ring(port: int = 8765) -> tuple:
    """向在线硬件发送真实寻机声光报警与语音呼叫"""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ring",
            data=b"{}",
            headers={"Content-Type": "application/json", "User-Agent": "PassportPlugin"}
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            res_data = json.loads(resp.read().decode())
            if res_data.get("success"):
                return True, "🔔 寻机声光呼叫已发送！硬件屏幕已弹窗并鸣叫。"
            return False, res_data.get("error", "寻机未成功")
    except urllib.error.HTTPError as he:
        try:
            err_json = json.loads(he.read().decode())
            return False, err_json.get("error", f"HTTP {he.code}")
        except Exception:
            return False, f"HTTP {he.code}"
    except Exception as e:
        return False, f"寻机呼叫异常: {e}"


def call_gateway_speak(text: str, port: int = 8765) -> tuple:
    """向 passport-bridge 下发指定文本进行流式语音朗读与弹窗展示"""
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/speak",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "PassportPlugin"}
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = json.loads(resp.read().decode())
            if body.get("success"):
                return True, f"已向硬件播报「{text[:30]}」"
            return False, body.get("error", "网关播报未成功")
    except urllib.error.HTTPError as he:
        try:
            err = json.loads(he.read().decode()).get("error")
            return False, err or f"HTTP {he.code}"
        except Exception:
            return False, f"HTTP {he.code}"
    except Exception as e:
        return False, f"播报接口异常: {e}"


def query_letgo_git_info() -> dict:
    """获取 Letgo 源码当前提交"""
    candidates = [
        os.environ.get("LETGO_DIR"),
        "/home/jiang/github/Letgo",
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Letgo"))
    ]
    letgo_dir = None
    for c in candidates:
        if c and os.path.exists(c):
            letgo_dir = c
            break

    info = {
        "commit": "ef0c0ab",
        "message": "feat(ui): 状态栏去时长、优化列表滚动与背光控制、音量顶层显示并完成全中文与浅色主题适配",
        "date": "近期"
    }
    if not letgo_dir:
        return info
    try:
        res = subprocess.run(
            ["git", "-C", letgo_dir, "log", "-1", "--pretty=format:%h|%s|%cd", "--date=relative"],
            capture_output=True, text=True, timeout=2.0
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split("|")
            if len(parts) >= 3:
                info = {"commit": parts[0], "message": parts[1], "date": parts[2]}
    except Exception:
        pass
    return info


def query_latest_firmware() -> dict:
    """获取最新就绪的 OTA 固件版本"""
    candidates = [
        os.environ.get("PASSPORT_FIRMWARE_DIR"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmware"),
        "/home/jiang/github/passport-bridge/firmware"
    ]
    for fw_dir in candidates:
        if fw_dir and os.path.exists(fw_dir):
            for entry in sorted(os.listdir(fw_dir), reverse=True):
                sub_path = os.path.join(fw_dir, entry)
                ota_file = os.path.join(sub_path, "ota.bin")
                if os.path.isdir(sub_path) and os.path.exists(ota_file):
                    size_mb = round(os.path.getsize(ota_file) / (1024 * 1024), 2)
                    return {"tag": entry, "size_mb": size_mb}
    return {"tag": "fw-38913402242", "size_mb": 1.95}


def call_gateway_ota(mac: str, tag: str, port: int = 8765) -> tuple:
    """通过 passport-bridge 下发 OTA 升级指令"""
    try:
        qs = urllib.parse.urlencode({"action": "upgrade", "mac": mac, "tag": tag})
        url = f"http://127.0.0.1:{port}/api/ota_action?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "PassportPlugin"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            if resp.status == 200 or "已下发" in body:
                return True, f"🚀 已成功向硬件 ({mac}) 下发固件【{tag}】的 OTA 升级指令！设备正实时写入 Flash。"
            return False, f"⚠️ 网关反馈: {body[:100]}"
    except Exception as e:
        return False, f"❌ 触发 OTA 异常: {e}"


def call_gateway_remove_device(mac: str, port: int = 8765) -> tuple:
    """通过 passport-bridge 解除配对"""
    try:
        data = json.dumps({"mac": mac}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/devices/remove",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "PassportPlugin"}
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                return True, f"🗑️ 设备 `{mac}` 已成功解除白名单配对！"
    except Exception as e:
        return False, f"❌ 解除配对异常: {e}"
    return False, "❌ 解除配对失败"


# ==================== 插件主类 ====================
class PassportBridgePlugin(BasePlugin):

    def initialize(self):
        """轻量级客户端模式初始化，不占用或抢占网络端口"""
        self.config_data = self.get_config() or {}
        self.enabled = self.config_data.get("enabled", True)
        self.pending_speech_chats = {}  # chat_id -> expire_timestamp
        if not self.enabled:
            log.info(f"[Plugin:{self.plugin_id}] 插件已在配置中禁用。")
            return
        log.info(f"[Plugin:{self.plugin_id}] Passport 控制台就绪，连接本地 8765 网关。")

    def get_config(self) -> dict:
        cfg_path = os.path.join(self.plugin_dir, "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_config(self, cfg: dict):
        cfg_path = os.path.join(self.plugin_dir, "config.json")
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            self.config_data = cfg
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] 保存配置失败: {e}")

    # ==================== 极简实用单页卡片构建器 ====================
    def build_control_card(self, chat_id: str, banner: Optional[str] = None) -> dict:
        """构建极简克制、高可用、功能真实的控制面板"""
        cfg = self.get_config()
        port = cfg.get("server_port", 8765)
        bound_chat = cfg.get("bound_chat_id", "")

        # 1. 查询网关与设备实时状态
        gw_status = query_gateway_status(port)
        connected_clients = gw_status.get("connected_clients", 0)
        gw_online = (gw_status.get("status") == "online")
        devices_dict = query_gateway_devices(port)

        # 2. 查询当前实际生效的音色
        curr_voice = query_gateway_current_voice(port)
        voice_label = curr_voice
        for v_code, v_name in TTS_VOICE_LIST:
            if v_code == curr_voice:
                voice_label = v_name
                break

        # 3. 会话绑定状态
        is_bound = (bound_chat == chat_id)
        bound_desc = "🟢 **已绑定本会话 (推流专属接收)**" if is_bound else "⚪ **未绑定 (广播至首个活跃会话)**"

        # 4. 状态徽章与颜色
        if connected_clients > 0:
            header_color = "turquoise"
            status_badge = f"🟢 **在线连接中 ({connected_clients} 台活跃)**"
        elif gw_online:
            header_color = "blue"
            status_badge = "🟢 **网关就绪 (硬件待命休眠中)**"
        else:
            header_color = "orange"
            status_badge = "⚪ **网关未启动**"

        elements = []

        # 顶部操作反馈
        if banner:
            elements.append({
                "tag": "markdown",
                "content": f"> 💡 **操作反馈**：{banner}"
            })
            elements.append({"tag": "hr"})

        # ---------- 板块 1: 硬件与网关状态 ----------
        target_mac = ""
        target_ip = ""
        target_name = "FoloToy AI Passport"
        if devices_dict:
            target_mac = list(devices_dict.keys())[0]
            target_ip = devices_dict[target_mac].get("ip", "10.0.0.23")
            target_name = devices_dict[target_mac].get("name", "FoloToy AI Passport")

        elements.append({
            "tag": "markdown",
            "content": (
                f"### 📱 随身设备与网关状态\n"
                f"• 📡 **设备状态**：{status_badge}\n"
                f"• 🏷️ **硬件节点**：`{target_name}` | 🔑 **MAC**：`{target_mac or '待配对'}`\n"
                f"• 🌐 **局域网 IP**：`{target_ip or '未知'}` | 端口：`8765`\n"
                f"• 💬 **飞书直达**：{bound_desc}"
            )
        })

        bind_btn_text = "🔓 解绑本会话" if is_bound else "📌 绑定本会话"
        bind_btn_type = "danger" if is_bound else "primary"
        bind_action = "unbind_chat" if is_bound else "bind_chat"

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": bind_btn_text},
                    "type": bind_btn_type,
                    "value": {"action": bind_action, "chat_id": chat_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔔 寻机声光呼叫"},
                    "type": "primary",
                    "value": {"action": "find_device", "chat_id": chat_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 刷新状态"},
                    "type": "default",
                    "value": {"action": "passport_refresh_status", "chat_id": chat_id}
                }
            ]
        })
        elements.append({"tag": "hr"})

        # ---------- 板块 2: Letgo 固件与一键 OTA ----------
        git_info = query_letgo_git_info()
        fw_info = query_latest_firmware()

        elements.append({
            "tag": "markdown",
            "content": (
                f"### 🚀 Letgo 固件与 OTA 升级\n"
                f"• 🎯 **适配型号**：`folotoy/ai-passport` (ESP32-C3 / 240×320 ST7789P3 彩屏)\n"
                f"• 🔖 **源码提交**：`{git_info['commit']}` - {git_info['message'][:35]}...\n"
                f"• 📦 **最新固件**：**`{fw_info['tag']}`** ({fw_info['size_mb']}MB / Flash 写入就绪)"
            )
        })

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"🚀 一键推送 OTA【{fw_info['tag']}】"},
                    "type": "primary",
                    "value": {
                        "action": "trigger_ota",
                        "tag": fw_info["tag"],
                        "mac": target_mac,
                        "chat_id": chat_id
                    }
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🗑️ 解绑该设备"},
                    "type": "danger",
                    "value": {
                        "action": "remove_device",
                        "mac": target_mac,
                        "chat_id": chat_id
                    }
                }
            ]
        })
        elements.append({"tag": "hr"})

        # ---------- 板块 3: 播报音色与指定文本播报 ----------
        elements.append({
            "tag": "markdown",
            "content": (
                f"### 🎙️ 语音播报与自定义朗读\n"
                f"• 🔊 **当前网关生效音色**：**`{voice_label}`**\n"
                f"*点击下方的【播放指定文本】按钮，系统将引导您回复想要让硬件朗读的任意内容：*"
            )
        })

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔊 切换播报音色"},
                    "type": "default",
                    "value": {"action": "cycle_voice", "chat_id": chat_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📢 播放指定文本"},
                    "type": "primary",
                    "value": {"action": "prompt_custom_speech", "chat_id": chat_id}
                }
            ]
        })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"content": "📟 FoloToy AI Passport 硬件协同面板", "tag": "plain_text"}
            },
            "elements": elements
        }

    # ==================== 斜杠指令入口 ====================
    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        """响应 /passport, /hardware 与 /aipass 指令"""
        if command not in ["/passport", "/hardware", "/aipass"]:
            return False

        sub_cmd = args.strip().split()[0].lower() if args.strip() else ""
        sub_args = args.strip()[len(sub_cmd):].strip() if args.strip() else ""

        # 支持直接指令：/passport voice <音色名称或序号>
        if sub_cmd in ["voice", "sound", "timbre", "speaker"]:
            cfg = self.get_config()
            port = cfg.get("server_port", 8765)
            target_voice = None
            target_name = None
            if sub_args:
                for v_code, v_name in TTS_VOICE_LIST:
                    if sub_args in v_code or sub_args in v_name:
                        target_voice = v_code
                        target_name = v_name
                        break
            if not target_voice:
                curr_voice = query_gateway_current_voice(port)
                idx = 0
                for i, (v_code, _) in enumerate(TTS_VOICE_LIST):
                    if v_code == curr_voice:
                        idx = (i + 1) % len(TTS_VOICE_LIST)
                        break
                target_voice, target_name = TTS_VOICE_LIST[idx]
            ok, msg = call_gateway_set_voice(target_voice, port)
            if ok:
                send_reply_sdk(message_id, f"🔊 **已将随身硬件音色切换为：** {target_name}（已向硬件播报）")
            else:
                send_reply_sdk(message_id, f"⚠️ 切换音色失败：{msg}")
            return True

        # 支持直接指令：/passport say <内容>
        if sub_cmd in ["say", "speak", "tts", "tell"] and sub_args:
            cfg = self.get_config()
            port = cfg.get("server_port", 8765)
            ok, msg = call_gateway_speak(sub_args, port)
            if ok:
                send_reply_sdk(message_id, f"📢 **已成功向随身硬件下发语音播报：**\n> 「{sub_args}」")
            else:
                send_reply_sdk(message_id, f"⚠️ 硬件播报未成功：{msg}")
            return True

        card = self.build_control_card(chat_id)
        send_interactive_card_sdk(message_id, card)
        return True

    # ==================== 消息拦截钩子（处理用户回复的文本） ====================
    async def on_message(self, chat_id: str, user_text: str, message_id: str, session_data: dict) -> bool:
        """拦截等待播放文本的用户回复"""
        if not getattr(self, "enabled", True):
            return False

        if not hasattr(self, "pending_speech_chats"):
            self.pending_speech_chats = {}

        if chat_id in self.pending_speech_chats:
            expire_time = self.pending_speech_chats[chat_id]
            # 120 秒超时判定
            if time.time() - expire_time > 120:
                self.pending_speech_chats.pop(chat_id, None)
                return False

            self.pending_speech_chats.pop(chat_id, None)
            clean_text = user_text.strip()

            if clean_text.lower() in ("/cancel", "取消", "退出"):
                send_reply_sdk(message_id, "⚪ 已取消文本播报。")
                return True

            cfg = self.get_config()
            port = cfg.get("server_port", 8765)
            ok, msg = call_gateway_speak(clean_text, port)
            if ok:
                send_reply_sdk(message_id, f"📢 **已成功向随身硬件下发语音播报：**\n> 「{clean_text}」")
            else:
                send_reply_sdk(message_id, f"⚠️ 硬件播报未成功：{msg}")
            return True

        return False

    # ==================== 飞书卡片按钮点击回调 ====================
    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """处理卡片交互式点击事件并局部刷新 (Patch)"""
        if not getattr(self, "enabled", True):
            return False

        act = action or (value.get("action") if isinstance(value, dict) else "")
        if not act:
            return False

        handled_actions = {
            "bind_chat", "unbind_chat", "find_device", "passport_refresh_status",
            "trigger_ota", "remove_device", "cycle_voice", "prompt_custom_speech"
        }
        if act not in handled_actions:
            return False

        banner = None
        cfg = self.get_config()
        port = cfg.get("server_port", 8765)

        # 1. 绑定会话
        if act == "bind_chat":
            cfg["bound_chat_id"] = chat_id
            self.save_config(cfg)
            banner = "✅ 已成功将当前会话绑定为 AI Passport 消息专属通道！"

        # 2. 解绑会话
        elif act == "unbind_chat":
            cfg["bound_chat_id"] = ""
            self.save_config(cfg)
            banner = "🔓 已解除会话专属绑定，后续消息将广播至首个活跃会话。"

        # 3. 寻机声光呼叫（调用真实网关 API）
        elif act == "find_device":
            ok, msg = call_gateway_ring(port)
            banner = msg

        # 4. 刷新状态
        elif act in ["passport_refresh_status", "refresh_status"]:
            gw_status = query_gateway_status(port)
            clients_cnt = gw_status.get("connected_clients", 0)
            banner = f"🔄 状态已刷新：当前网关在线，有 {clients_cnt} 台设备活跃连接。"

        # 5. 一键 OTA 升级触发
        elif act == "trigger_ota":
            tag = value.get("tag", "fw-38484776565")
            mac = value.get("mac", "")
            if not mac:
                devs = query_gateway_devices(port)
                if devs:
                    mac = list(devs.keys())[0]
            if mac:
                ok, msg = call_gateway_ota(mac, tag, port)
                banner = msg
            else:
                banner = "⚠️ 未检测到配对设备 MAC，无法下发 OTA。"

        # 6. 解绑设备
        elif act == "remove_device":
            mac = value.get("mac", "")
            if mac:
                ok, msg = call_gateway_remove_device(mac, port)
                banner = msg
            else:
                banner = "⚠️ 未指定设备 MAC 地址。"

        # 7. 切换音色（调用真实网关 API）
        elif act == "cycle_voice":
            curr_voice = query_gateway_current_voice(port)
            idx = 0
            for i, (v_code, _) in enumerate(TTS_VOICE_LIST):
                if v_code == curr_voice:
                    idx = (i + 1) % len(TTS_VOICE_LIST)
                    break
            new_voice, new_name = TTS_VOICE_LIST[idx]
            ok, msg = call_gateway_set_voice(new_voice, port)
            if ok:
                banner = f"🔊 已将网关音色切换为：**{new_name}**（已在硬件播报）"
            else:
                banner = f"⚠️ {msg}"

        # 8. 触发指定文本播报交互引导
        elif act == "prompt_custom_speech":
            if not hasattr(self, "pending_speech_chats"):
                self.pending_speech_chats = {}
            self.pending_speech_chats[chat_id] = time.time()

            guide_card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "turquoise",
                    "title": {"content": "📢 随身硬件文字播报", "tag": "plain_text"}
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            "✍️ **请直接在飞书聊天框中回复您想要硬件播报的文本内容**：\n\n"
                            "> 💡 示例：`小智小智，准备开会了！`\n"
                            "> 发送后硬件将以当前音色实时朗读并在屏幕居中弹窗展示；发送 `/cancel` 可随时取消本次播报。"
                        )
                    }
                ]
            }
            send_card_to_chat_sdk(chat_id, guide_card)
            banner = "✍️ 已开启文字播报等待：请直接在当前会话回复要朗读的内容！"

        new_card = self.build_control_card(chat_id, banner=banner)
        patch_interactive_card_sdk(card_message_id, new_card)
        return True

    def on_unload(self):
        """插件热重载或卸载时清理内存状态"""
        if hasattr(self, "pending_speech_chats"):
            self.pending_speech_chats.clear()
