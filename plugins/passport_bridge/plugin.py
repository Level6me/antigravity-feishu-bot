"""
FoloToy AI Passport 飞书机器人桥接插件 (PassportBridgePlugin)
全功能卡片式按钮交互控制面板版
"""

import os
import sys
import json
import time
import asyncio
from typing import Optional, Dict, Any

from plugin_base import BasePlugin
from logger import log
from lark_client import send_interactive_card_sdk, patch_interactive_card_sdk, send_reply_sdk

_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from ws_server import PassportServer

TTS_VOICE_LIST = [
    ("zh-CN-XiaoxiaoNeural", "👧 晓晓 (自然女声)"),
    ("zh-CN-YunxiNeural", "👦 云希 (活力男声)"),
    ("zh-CN-YunjianNeural", "👨 云健 (稳重男声)"),
    ("zh-CN-XiaoyiNeural", "👩 晓伊 (温和女声)")
]


class PassportBridgePlugin(BasePlugin):

    def initialize(self):
        """插件初始化，加载配置并启动局域网异步服务端"""
        self.config_data = self.get_config() or {}
        self.enabled = self.config_data.get("enabled", True)
        if not self.enabled:
            log.info(f"[Plugin:{self.plugin_id}] 插件已在配置中禁用。")
            return

        host = self.config_data.get("server_host", "0.0.0.0")
        port = int(self.config_data.get("server_port", 8765))
        udp_port = int(self.config_data.get("udp_discovery_port", 8765))

        self.server = PassportServer(host=host, port=port, udp_port=udp_port, plugin=self)
        
        # 将服务端挂载到当前主 asyncio 事件循环中
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.server.start())
            log.info(f"[Plugin:{self.plugin_id}] 局域网服务初始化成功，监听端口: {port}")
        except RuntimeError:
            log.warning(f"[Plugin:{self.plugin_id}] 当前未在异步主事件循环中，延后启动。")

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

    # ==================== 卡片构建器 ====================
    def build_control_card(self, chat_id: str, view_mode: str = "control", banner: Optional[str] = None) -> dict:
        """构建全交互式卡片控制中心"""
        cfg = self.get_config()
        port = cfg.get("server_port", 8765)
        bound_chat = cfg.get("bound_chat_id", "")
        curr_voice = cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural")
        
        voice_label = curr_voice
        for v_code, v_name in TTS_VOICE_LIST:
            if v_code == curr_voice:
                voice_label = v_name
                break

        clients_count = len(self.server.active_websockets) if hasattr(self, "server") and self.server else 0
        is_bound = (bound_chat == chat_id)
        
        if is_bound:
            bound_desc = "✅ **已绑定当前会话** (硬件语音在此接收)"
        elif bound_chat:
            bound_desc = "📌 **已绑定其他会话**"
        else:
            bound_desc = "⚠️ **未绑定** (默认广播至首个活跃会话)"

        header_color = "blue" if clients_count > 0 else "orange"
        elements = []

        # 1. 顶部操作反馈横幅
        if banner:
            elements.append({
                "tag": "markdown",
                "content": f"> {banner}"
            })
            elements.append({"tag": "hr"})

        # 2. 视图：控制中心
        if view_mode == "control":
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**📟 FoloToy AI Passport 实时网关状态**：\n"
                    f"• 🌐 **内网监听端口**：`0.0.0.0:{port}` (WebSocket & UDP 发现)\n"
                    f"• 📡 **在线硬件设备**：**`{clients_count}`** 台在线\n"
                    f"• 💬 **会话接收绑定**：{bound_desc}\n"
                    f"• 🔊 **TTS 播报音色**：`{voice_label}`\n"
                    f"• 🎙️ **语音 ASR 引擎**：本地 Whisper 极速语音转文字"
                )
            })
            elements.append({"tag": "hr"})

            # 第一排按钮：基础会话与看板控制
            bind_btn_text = "🔓 解绑本会话" if is_bound else "📌 绑定到本会话"
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
                        "text": {"tag": "plain_text", "content": "🔄 刷新状态"},
                        "type": "default",
                        "value": {"action": "refresh_status", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📡 同步看板"},
                        "type": "default",
                        "value": {"action": "push_dashboard", "chat_id": chat_id}
                    }
                ]
            })

            # 第二排按钮：硬件屏幕告警与通知测试
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔔 提示弹窗"},
                        "type": "default",
                        "value": {"action": "send_alert", "level": "info", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚠️ 警告弹窗"},
                        "type": "default",
                        "value": {"action": "send_alert", "level": "warning", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🚨 紧急告警"},
                        "type": "danger",
                        "value": {"action": "send_alert", "level": "danger", "chat_id": chat_id}
                    }
                ]
            })

            # 第三排按钮：系统偏好、熔断与指引切换
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔊 轮换音色"},
                        "type": "default",
                        "value": {"action": "cycle_voice", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🛑 紧急熔断"},
                        "type": "danger",
                        "value": {"action": "trigger_stop", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📖 固件与刷机指引"},
                        "type": "primary",
                        "value": {"action": "switch_view", "view": "firmware", "chat_id": chat_id}
                    }
                ]
            })

        # 3. 视图：固件与开发指引
        elif view_mode == "firmware":
            elements.append({
                "tag": "markdown",
                "content": (
                    "### 🛠️ FoloToy AI Passport (ESP32-C3) 固件指南\n\n"
                    "本插件已内置完整的 PlatformIO 固件源码，位于 `firmware/` 目录：\n\n"
                    "**1. 硬件外设与引脚分布**：\n"
                    "• **240×320 LCD (ST7789)**：MOSI:`7`, SCLK:`6`, CS:`10`, DC:`2`, RST:`3`, BL:`1`\n"
                    "• **ES8311 音频 I2C**：SDA:`8`, SCL:`0` (地址 `0x18`)\n"
                    "• **数字音频 I2S**：BCLK:`18`, WS:`19`, DOUT:`21`, DIN:`10`\n"
                    "• **3 颗实体按键**：上键:`4`, 下键:`5`, 中键(OK):`9`\n\n"
                    "**2. 树莓派一键烧录**：\n"
                    "使用 Type-C 数据线将 AI Passport 插入树莓派 USB 口，执行：\n"
                    "```bash\n"
                    "cd plugins/passport_bridge/firmware\n"
                    "./flash_firmware.sh\n"
                    "```\n"
                    "脚本将全自动检测端口、编译源码并完成固件烧录！\n\n"
                    "**3. 随身对讲操作**：\n"
                    "• 长按 **OK 键** 录音说话，松手立即发送，AI 回答将在扬声器播报并同步推送到飞书卡片！"
                )
            })
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⬅️ 返回控制面板"},
                        "type": "primary",
                        "value": {"action": "switch_view", "view": "control", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 刷新设备状态"},
                        "type": "default",
                        "value": {"action": "refresh_status", "chat_id": chat_id, "view": "firmware"}
                    }
                ]
            })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"content": "📟 FoloToy AI Passport 硬件交互控制中心", "tag": "plain_text"}
            },
            "elements": elements
        }
        return card

    # ==================== 斜杠指令入口 ====================
    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        """响应 /passport 与 /hardware 指令，统一直接返回交互式控制卡片"""
        if command not in ["/passport", "/hardware"]:
            return False

        sub_cmd = args.strip().split()[0] if args.strip() else ""
        sub_args = args.strip()[len(sub_cmd):].strip() if args.strip() else ""

        banner = None
        if sub_cmd == "bind":
            cfg = self.get_config()
            cfg["bound_chat_id"] = chat_id
            self.save_config(cfg)
            banner = "✅ 成功绑定当前会话！后续硬件语音提问将实时推送到本会话。"
        elif sub_cmd == "alert":
            alert_text = sub_args or "来自飞书指令的手动告警测试！"
            if hasattr(self, "server") and self.server:
                await self.server.broadcast_alert("飞书通知", alert_text, level="warning", duration_sec=5)
                banner = f"🚨 已向所有在线硬件推送告警：{alert_text}"
            else:
                banner = "⚠️ 硬件网关服务未就绪，无法下发告警。"

        card = self.build_control_card(chat_id, view_mode="control", banner=banner)
        send_interactive_card_sdk(message_id, card)
        return True

    # ==================== 飞书卡片按钮点击回调 ====================
    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """处理卡片按钮所有交互式点击事件"""
        if not getattr(self, "enabled", True):
            return False

        act = action or (value.get("action") if isinstance(value, dict) else "")
        if not act:
            return False

        banner = None
        view_mode = value.get("view", "control")

        # 1. 绑定会话
        if act == "bind_chat":
            cfg = self.get_config()
            cfg["bound_chat_id"] = chat_id
            self.save_config(cfg)
            banner = "✅ 已成功将当前会话绑定为 AI Passport 消息专属通道！"

        # 2. 解除绑定
        elif act == "unbind_chat":
            cfg = self.get_config()
            cfg["bound_chat_id"] = ""
            self.save_config(cfg)
            banner = "🔓 已解除会话专属绑定，后续消息将使用全局广播通道。"

        # 3. 刷新状态
        elif act == "refresh_status":
            clients_count = len(self.server.active_websockets) if hasattr(self, "server") and self.server else 0
            banner = f"🔄 状态已刷新：当前有 {clients_count} 台 AI Passport 在线连接。"

        # 4. 强制同步看板数据到硬件屏幕
        elif act == "push_dashboard":
            if hasattr(self, "server") and self.server:
                for ws in list(self.server.active_websockets):
                    await self.server.send_dashboard_to_client(ws)
                banner = f"📡 已成功向所有硬件客户端下发最新看板状态心跳！"
            else:
                banner = "⚠️ 服务端未运行，无法下发同步数据。"

        # 5. 发送硬件弹窗测试
        elif act == "send_alert":
            level = value.get("level", "warning")
            title_map = {"info": "系统通知", "warning": "重要警告", "danger": "紧急告警"}
            content_map = {
                "info": "飞书控制台下发的常规状态同步提醒。",
                "warning": "检测到待处理事项，请在飞书中查看卡片详情！",
                "danger": "系统触发高危事件拦截，请注意防范！"
            }
            title = title_map.get(level, "通知")
            content = content_map.get(level, "测试弹窗")
            if hasattr(self, "server") and self.server:
                await self.server.broadcast_alert(title, content, level=level, duration_sec=5)
                banner = f"🚨 已向硬件下发【{title}】弹窗提醒！"
            else:
                banner = "⚠️ 硬件网关未就绪，下发失败。"

        # 6. 轮换 TTS 音色
        elif act == "cycle_voice":
            cfg = self.get_config()
            curr = cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural")
            idx = 0
            for i, (v_code, _) in enumerate(TTS_VOICE_LIST):
                if v_code == curr:
                    idx = (i + 1) % len(TTS_VOICE_LIST)
                    break
            new_voice, new_name = TTS_VOICE_LIST[idx]
            cfg["tts_voice"] = new_voice
            self.save_config(cfg)
            banner = f"🔊 已将 TTS 播报音色切换为：**{new_name}**"

        # 7. 触发紧急安全熔断
        elif act == "trigger_stop":
            if hasattr(self, "server") and self.server:
                await self.server._trigger_emergency_stop()
                await self.server.broadcast_alert("紧急安全熔断", "飞书端已触发 Physical Stop！", level="danger", duration_sec=5)
            banner = "🛑 **紧急熔断已触发**：已终止后台所有正在运行的 Agent 任务！"

        # 8. 切换视图 (控制面板 ⇄ 固件说明)
        elif act == "switch_view":
            view_mode = value.get("view", "control")
            banner = "📖 已切换至固件开发与刷机指引" if view_mode == "firmware" else "📟 已返回硬件控制中心"

        # 生成新卡片并在飞书内就地局部更新 (Patch)
        new_card = self.build_control_card(chat_id, view_mode=view_mode, banner=banner)
        patch_interactive_card_sdk(card_message_id, new_card)
        return True

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        """AI 思考前钩子：向硬件广播思考动画状态"""
        if hasattr(self, "server") and self.server and self.server.active_websockets:
            asyncio.create_task(self.server.broadcast_ai_state("thinking", "思考中..."))
        return user_text, session_data

    async def on_tool_call(self, tool_name: str, tool_args: dict):
        """工具调用钩子：向硬件广播当前正在执行的工具"""
        if hasattr(self, "server") and self.server and self.server.active_websockets:
            asyncio.create_task(self.server.broadcast_ai_state("tool_running", f"工具: {tool_name}"))

    async def on_after_ai(self, ai_response_text: str, chat_id: str, session_data: dict) -> str:
        """AI 答复后钩子：更新硬件状态"""
        if hasattr(self, "server") and self.server and self.server.active_websockets:
            asyncio.create_task(self.server.broadcast_ai_state("idle", "已完成"))
        return ai_response_text

    def on_service_restarting(self):
        """服务停止/重启时释放网络资源"""
        if hasattr(self, "server") and self.server:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.server.stop())
            except Exception:
                pass
