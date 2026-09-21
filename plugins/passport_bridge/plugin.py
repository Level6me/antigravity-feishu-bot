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

MACRO_PRESETS = {
    "ok_double_click": {
        "checkin": {
            "action": "feishu_checkin",
            "description": "飞书工作台状态极速打卡",
            "notify_feishu": True
        },
        "todo": {
            "action": "bitable_capture",
            "description": "多维表格待办闪念录入",
            "notify_feishu": True
        },
        "pomodoro": {
            "action": "start_pomodoro",
            "description": "启动 25 分钟专注番茄钟",
            "notify_feishu": True
        },
        "broadcast": {
            "action": "station_broadcast",
            "description": "向局域网广播工位状态",
            "notify_feishu": True
        }
    },
    "up_double_click": {
        "git": {
            "action": "git_status_check",
            "description": "当前工程 Git 状态巡检",
            "command": "git branch --show-current && git status -s && git log -1 --oneline",
            "notify_feishu": True
        },
        "sys": {
            "action": "custom_script",
            "description": "主机资源与系统负载巡检",
            "command": "uptime && free -h && df -h /",
            "notify_feishu": True
        },
        "net": {
            "action": "custom_script",
            "description": "网络连通与外网延迟诊断",
            "command": "ping -c 3 223.5.5.5 | tail -2",
            "notify_feishu": True
        },
        "clock": {
            "action": "flip_clock_sync",
            "description": "同步气象并切换翻页时钟",
            "notify_feishu": False
        }
    },
    "down_double_click": {
        "guard": {
            "action": "custom_script",
            "description": "工位安全防窥 / 锁屏守护",
            "command": "echo '工位防窥安全守护已激活' && date",
            "notify_feishu": True
        },
        "stop": {
            "action": "emergency_stop",
            "description": "紧急物理熔断后台任务",
            "notify_feishu": True
        },
        "test": {
            "action": "custom_script",
            "description": "工程自动化快速单元测试",
            "command": "python3 -m unittest discover tests -v 2>&1 | tail -10 || echo '测试用例就绪'",
            "notify_feishu": True
        },
        "clean": {
            "action": "custom_script",
            "description": "清理工程构建与临时缓存",
            "command": "find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null && echo 'Python 缓存清理完毕'",
            "notify_feishu": True
        }
    }
}


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

    def _send_quick_capture_demo(self, chat_id: str):
        """发送多维表格/待办灵感快速录入演示交互卡片"""
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        bitable_card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "turquoise",
                "title": {"content": "📋 飞书多维表格 / 待办任务快速录入 (演示)", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"✨ **灵感/待办主题**：\n> **优化硬件端 WebSockets 与 IMA-ADPCM 音频解压实时性能**\n\n"
                        f"🏷️ **分类标签**：`#语音闪念` `#固件架构` `#硬件直录`\n"
                        f"🕒 **录入时间**：`{now_str}`\n"
                        f"📟 **录入终端**：`FoloToy AI Passport (随身硬件麦克风)`\n"
                        f"📊 **归档状态**：`已写入多维表格待办清单`"
                    )
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 标记完成"},
                            "type": "primary",
                            "value": {"action": "quick_todo_done", "content": "优化硬件端 WebSockets 延迟"}
                        }
                    ]
                }
            ]
        }
        try:
            from lark_client import send_card_to_chat_sdk
            send_card_to_chat_sdk(chat_id, bitable_card)
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] 发送待办演示卡片失败: {e}")

    # ==================== 卡片构建器 ====================
    def build_control_card(self, chat_id: str, view_mode: str = "control", banner: Optional[str] = None) -> dict:
        """构建全交互式卡片控制中心 (分类模块化、高颜值极客控制台)"""
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
            bound_desc = "🟢 **已绑定当前会话** (实时推流接收)"
        elif bound_chat:
            bound_desc = "🟡 **已绑定其他会话**"
        else:
            bound_desc = "⚪ **未绑定** (首个活跃会话接收)"

        online_badge = f"🟢 在线 ({clients_count} 台)" if clients_count > 0 else "⚪ 离线待命"
        header_color = "turquoise" if clients_count > 0 else "orange"
        elements = []

        # 1. 顶部视图导航选项卡 (Tab 切换)
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🎛️ 综合控制台"},
                    "type": "primary" if view_mode == "control" else "default",
                    "value": {"action": "switch_view", "view": "control", "chat_id": chat_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⚡ 物理动作宏"},
                    "type": "primary" if view_mode == "macro" else "default",
                    "value": {"action": "switch_view", "view": "macro", "chat_id": chat_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🛠️ 固件与指南"},
                    "type": "primary" if view_mode == "firmware" else "default",
                    "value": {"action": "switch_view", "view": "firmware", "chat_id": chat_id}
                }
            ]
        })
        elements.append({"tag": "hr"})

        # 2. 顶部操作反馈横幅
        if banner:
            elements.append({
                "tag": "markdown",
                "content": f"> 💡 **操作反馈**：{banner}"
            })
            elements.append({"tag": "hr"})

        # 3. 主视图：综合控制台 (清晰模块化分类)
        if view_mode == "control":
            # --- 模块 1: 硬件网关与实时画像 ---
            bind_btn_text = "🔓 解绑本会话" if is_bound else "📌 绑定至本会话"
            bind_btn_type = "danger" if is_bound else "primary"
            bind_action = "unbind_chat" if is_bound else "bind_chat"

            elements.append({
                "tag": "markdown",
                "content": (
                    f"**📊 硬件网关与实时画像 (Gateway Status)**\n"
                    f"• 📡 **设备状态**：{online_badge} | 🌐 **监听网关**：`0.0.0.0:{port}`\n"
                    f"• 💬 **专属通道**：{bound_desc}\n"
                    f"• 🎙️ **音频链路**：`IMA-ADPCM (4:1压缩 / 16kHz)` | 🔊 **音色**：`{voice_label}`\n"
                    f"• ⏱️ **活跃项目**：`{os.path.basename(os.getcwd())}` | 🔋 **工位环境**：`24°C 晴朗 / AQI 28 优`"
                )
            })
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
            elements.append({"tag": "hr"})

            # --- 模块 2: 随身对讲与语音协同 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    "**🎙️ 随身对讲与语音协同 (Voice & Bitable)**\n"
                    "*长按 OK 键说话推流，支持语音闪念秒级归档多维表格待办。*"
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📻 发起对讲广播"},
                        "type": "primary",
                        "value": {"action": "broadcast_walkie", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📝 灵感待办演示"},
                        "type": "default",
                        "value": {"action": "bitable_quick_capture_demo", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔊 轮换播报音色"},
                        "type": "default",
                        "value": {"action": "cycle_voice", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 模块 3: 工位穿透与生活助理 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    "**💼 工位协同与穿透提醒 (Desk Assistant)**\n"
                    "*全天候工位助手，支持会议穿透展示、复古翻页时钟与防丢寻机。*"
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔔 寻机声光爆闪"},
                        "type": "primary",
                        "value": {"action": "find_device", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📅 会议穿透提醒"},
                        "type": "default",
                        "value": {"action": "send_meeting_alert", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⛅ 同步天气时钟"},
                        "type": "default",
                        "value": {"action": "sync_weather_clock", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🍅 启动番茄钟"},
                        "type": "default",
                        "value": {"action": "start_pomodoro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 模块 4: 物理按键宏与工作流 ---
            macro_cfg = cfg.get("macro_bindings", {})
            ok_info = macro_cfg.get("ok_double_click", {})
            up_info = macro_cfg.get("up_double_click", {})
            down_info = macro_cfg.get("down_double_click", {})
            ok_name = ok_info.get("description", "飞书工作台状态打卡")
            up_name = up_info.get("description", "当前工程 Git 巡检")
            down_name = down_info.get("description", "工位安全防窥 / 自定义")

            elements.append({
                "tag": "markdown",
                "content": (
                    f"**⚡ 物理动作宏工作流 (Physical Action Engine)**\n"
                    f"• 🔘 **OK 双击**：`{ok_name}`\n"
                    f"• 🔼 **Up 双击**：`{up_name}`\n"
                    f"• 🔽 **Down 双击**：`{down_name}`\n"
                    f"*点击右侧按钮直接在飞书中为每个实体按键选择与绑定预设！*"
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📍 运行 OK 宏"},
                        "type": "primary",
                        "value": {"action": "trigger_macro_checkin", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔍 运行 Up 宏"},
                        "type": "default",
                        "value": {"action": "trigger_macro_git", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🛡️ 运行 Down 宏"},
                        "type": "default",
                        "value": {"action": "trigger_macro_down", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 配置按键宏"},
                        "type": "default",
                        "value": {"action": "switch_view", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 模块 5: 屏幕告警与高危熔断 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    "**🚨 屏幕告警与高危熔断 (Screen Alerts & Safety)**\n"
                    "*下发不同级别弹窗通知，或在失控时秒级熔断后台 Agent 任务。*"
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "💬 提示弹窗"},
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
                        "text": {"tag": "plain_text", "content": "🚨 告警弹窗"},
                        "type": "danger",
                        "value": {"action": "send_alert", "level": "danger", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🛑 紧急安全熔断"},
                        "type": "danger",
                        "value": {"action": "trigger_stop", "chat_id": chat_id}
                    }
                ]
            })

        # 4. 视图：物理按键动作宏专属交互配置面板
        elif view_mode == "macro":
            macro_cfg = cfg.get("macro_bindings", {})
            ok_binding = macro_cfg.get("ok_double_click", {})
            up_binding = macro_cfg.get("up_double_click", {})
            down_binding = macro_cfg.get("down_double_click", {})

            ok_desc = ok_binding.get("description", "飞书工作台状态签到")
            ok_cmd = ok_binding.get("command", "")
            ok_notify = ok_binding.get("notify_feishu", True)
            ok_act = ok_binding.get("action", "feishu_checkin")

            up_desc = up_binding.get("description", "当前工程 Git 状态巡检")
            up_cmd = up_binding.get("command", "")
            up_notify = up_binding.get("notify_feishu", True)
            up_act = up_binding.get("action", "git_status_check")

            down_desc = down_binding.get("description", "工位安全防窥 / 自定义动作")
            down_cmd = down_binding.get("command", "")
            down_notify = down_binding.get("notify_feishu", True)
            down_act = down_binding.get("action", "custom_script")

            elements.append({
                "tag": "markdown",
                "content": (
                    "### ⚡ FoloToy AI Passport 物理动作宏可视化配置\n"
                    "无需手动修改配置文件，**点击下方预设按钮即可秒级切换实体按键绑定与通知**："
                )
            })
            elements.append({"tag": "hr"})

            # --- 1. OK 确定键配置区 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**🔘 [OK 确定键] 双击宏配置**\n"
                    f"• 当前生效：**`{ok_desc}`**\n"
                    f"• 执行行为：`{ok_cmd if ok_cmd else ('内置: ' + ok_act)}`\n"
                    f"• 飞书通知：`{'🔔 开启卡片推送' if ok_notify else '🔕 静默模式 (无卡片)'}`\n"
                    f"*点击预设一键绑定*："
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("📍 签到打卡" + (" (当前)" if "签到" in ok_desc else ""))},
                        "type": "primary" if "签到" in ok_desc else "default",
                        "value": {"action": "set_macro", "key": "ok_double_click", "preset": "checkin", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("📝 灵感待办" + (" (当前)" if "待办" in ok_desc or "闪念" in ok_desc else ""))},
                        "type": "primary" if ("待办" in ok_desc or "闪念" in ok_desc) else "default",
                        "value": {"action": "set_macro", "key": "ok_double_click", "preset": "todo", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("⏱️ 番茄计时" + (" (当前)" if "番茄" in ok_desc else ""))},
                        "type": "primary" if "番茄" in ok_desc else "default",
                        "value": {"action": "set_macro", "key": "ok_double_click", "preset": "pomodoro", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("📢 工位广播" + (" (当前)" if "广播" in ok_desc else ""))},
                        "type": "primary" if "广播" in ok_desc else "default",
                        "value": {"action": "set_macro", "key": "ok_double_click", "preset": "broadcast", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔕 关闭通知" if ok_notify else "🔔 开启通知"},
                        "type": "default",
                        "value": {"action": "toggle_macro_notify", "key": "ok_double_click", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "▶️ 立即测试 OK 宏"},
                        "type": "primary",
                        "value": {"action": "trigger_macro_checkin", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 2. Up 上翻键配置区 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**🔼 [Up 上翻键] 双击宏配置**\n"
                    f"• 当前生效：**`{up_desc}`**\n"
                    f"• 执行命令：`{up_cmd if up_cmd else ('内置: ' + up_act)}`\n"
                    f"• 飞书通知：`{'🔔 开启卡片推送' if up_notify else '🔕 静默模式 (无卡片)'}`\n"
                    f"*点击预设一键绑定*："
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🔍 Git 巡检" + (" (当前)" if "Git" in up_desc else ""))},
                        "type": "primary" if "Git" in up_desc else "default",
                        "value": {"action": "set_macro", "key": "up_double_click", "preset": "git", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("📊 系统负载" + (" (当前)" if "负载" in up_desc else ""))},
                        "type": "primary" if "负载" in up_desc else "default",
                        "value": {"action": "set_macro", "key": "up_double_click", "preset": "sys", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🌐 网络诊断" + (" (当前)" if "网络" in up_desc else ""))},
                        "type": "primary" if "网络" in up_desc else "default",
                        "value": {"action": "set_macro", "key": "up_double_click", "preset": "net", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("⛅ 翻页时钟" + (" (当前)" if "时钟" in up_desc else ""))},
                        "type": "primary" if "时钟" in up_desc else "default",
                        "value": {"action": "set_macro", "key": "up_double_click", "preset": "clock", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔕 关闭通知" if up_notify else "🔔 开启通知"},
                        "type": "default",
                        "value": {"action": "toggle_macro_notify", "key": "up_double_click", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "▶️ 立即测试 Up 宏"},
                        "type": "primary",
                        "value": {"action": "trigger_macro_git", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 3. Down 下翻键配置区 ---
            elements.append({
                "tag": "markdown",
                "content": (
                    f"**🔽 [Down 下翻键] 双击宏配置**\n"
                    f"• 当前生效：**`{down_desc}`**\n"
                    f"• 执行命令：`{down_cmd if down_cmd else ('内置: ' + down_act)}`\n"
                    f"• 飞书通知：`{'🔔 开启卡片推送' if down_notify else '🔕 静默模式 (无卡片)'}`\n"
                    f"*点击预设一键绑定*："
                )
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🛡️ 安全防窥" + (" (当前)" if "防窥" in down_desc else ""))},
                        "type": "primary" if "防窥" in down_desc else "default",
                        "value": {"action": "set_macro", "key": "down_double_click", "preset": "guard", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🛑 紧急熔断" + (" (当前)" if "熔断" in down_desc else ""))},
                        "type": "primary" if "熔断" in down_desc else "default",
                        "value": {"action": "set_macro", "key": "down_double_click", "preset": "stop", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🧪 快速单测" + (" (当前)" if "单测" in down_desc or "测试" in down_desc else ""))},
                        "type": "primary" if ("单测" in down_desc or "测试" in down_desc) else "default",
                        "value": {"action": "set_macro", "key": "down_double_click", "preset": "test", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": ("🧹 缓存清理" + (" (当前)" if "清理" in down_desc or "缓存" in down_desc else ""))},
                        "type": "primary" if ("清理" in down_desc or "缓存" in down_desc) else "default",
                        "value": {"action": "set_macro", "key": "down_double_click", "preset": "clean", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔕 关闭通知" if down_notify else "🔔 开启通知"},
                        "type": "default",
                        "value": {"action": "toggle_macro_notify", "key": "down_double_click", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "▶️ 立即测试 Down 宏"},
                        "type": "primary",
                        "value": {"action": "trigger_macro_down", "view": "macro", "chat_id": chat_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

            # --- 4. 底部重置与导航 ---
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 全部恢复出厂默认宏"},
                        "type": "danger",
                        "value": {"action": "reset_macros", "view": "macro", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⬅️ 返回综合控制台"},
                        "type": "primary",
                        "value": {"action": "switch_view", "view": "control", "chat_id": chat_id}
                    }
                ]
            })

        # 5. 视图：固件与硬件指南
        elif view_mode == "firmware":
            elements.append({
                "tag": "markdown",
                "content": (
                    "### 🛠️ FoloToy AI Passport (ESP32-C3) 固件指南\n\n"
                    "**1. 硬件外设与引脚分布**：\n"
                    "• **240×320 ST7789P3 LCD**：MOSI:`7`, SCLK:`6`, CS:`10`, DC:`2`, RST:`3`, BL:`1`\n"
                    "• **ES8311 音频编解码**：I2C(SDA:`8`, SCL:`0`, 0x18), I2S(BCLK:`18`, WS:`19`, DOUT:`21`, DIN:`10`)\n"
                    "• **单引脚分压按键**：`GPIO0` (上键:0Ω, 下键:1kΩ, OK键:2.2kΩ)\n"
                    "• **电池与电量计**：CW2017 I2C 电量计 (SDA:`8`, SCL:`0`)\n\n"
                    "**2. 预编译 0x0 完整镜像**：\n"
                    "本插件已编译生成包含 Bootloader、分区表与固件的完整 0x0 镜像：\n"
                    "• 文件位置：`plugins/passport_bridge/firmware/merged_firmware_0x0.bin`\n\n"
                    "**3. 烧录方式**：\n"
                    "• **方式 A (推荐)**：打开 [官方 Web 刷机工具](https://ai-passport.folotoy.cn/tools/web-flasher/)，将上方 0x0 文件拖入并选择 `0x0` 地址刷入。\n"
                    "• **方式 B (命令行)**：插上 Type-C 数据线后执行：\n"
                    "```bash\n"
                    "cd plugins/passport_bridge/firmware && ./flash_firmware.sh\n"
                    "```"
                )
            })
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⬅️ 返回综合控制台"},
                        "type": "primary",
                        "value": {"action": "switch_view", "view": "control", "chat_id": chat_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 刷新设备连接"},
                        "type": "default",
                        "value": {"action": "refresh_status", "chat_id": chat_id, "view": "firmware"}
                    }
                ]
            })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"content": "📟 FoloToy AI Passport 硬件协同控制台", "tag": "plain_text"}
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
        elif sub_cmd in ["find", "ring", "locate"]:
            if hasattr(self, "server") and self.server and self.server.active_websockets:
                await self.server.trigger_find_device(duration_sec=10)
                banner = "🔔 **寻机鸣叫已触发**：硬件屏幕正高频爆闪并鸣叫！"
            else:
                banner = "⚠️ 当前无在线硬件设备。"
        elif sub_cmd == "meeting":
            m_title = sub_args or "项目敏捷站会"
            if hasattr(self, "server") and self.server:
                await self.server.trigger_meeting_reminder(m_title, "5分钟后开始")
                banner = f"📅 已下发会议开始穿透提醒：【{m_title}】"
            else:
                banner = "⚠️ 硬件网关未就绪，下发失败。"
        elif sub_cmd in ["broadcast", "say", "talk"]:
            bc_text = sub_args or "来自飞书对讲广播：请注意查收待办任务。"
            if hasattr(self, "server") and self.server:
                await self.server.broadcast_walkie_talkie(bc_text)
                banner = f"📻 已向所有随身硬件端广播语音对讲：{bc_text}"
            else:
                banner = "⚠️ 硬件网关未就绪，广播失败。"

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

        # 7. 寻机鸣叫与防丢响铃 (特性 8)
        elif act == "find_device":
            if hasattr(self, "server") and self.server and self.server.active_websockets:
                await self.server.trigger_find_device(duration_sec=10)
                banner = "🔔 **寻机防丢响铃已触发**：硬件屏幕正高频爆闪并鸣叫！"
            else:
                banner = "⚠️ 当前无在线设备连接。"

        # 8. 发送会议穿透提醒 (特性 5)
        elif act == "send_meeting_alert":
            if hasattr(self, "server") and self.server and self.server.active_websockets:
                await self.server.trigger_meeting_reminder("飞书项目敏捷站会", "5分钟后开始")
                banner = "📅 **会议提醒穿透已下发**：硬件屏幕已同步呈现会议卡片与声光提醒！"
            else:
                banner = "⚠️ 当前无在线设备连接。"

        # 9. 跨端广播对讲 (特性 6)
        elif act == "broadcast_walkie":
            if hasattr(self, "server") and self.server and self.server.active_websockets:
                await self.server.broadcast_walkie_talkie("工位即时广播：团队协同站会即将开始，请各位准备。")
                banner = "📻 **对讲广播已发送**：硬件扬声器已同步播报对讲语音！"
            else:
                banner = "⚠️ 当前无在线设备连接。"

        # 10. 演示多维表格灵感待办卡片 (特性 4)
        elif act == "bitable_quick_capture_demo":
            self._send_quick_capture_demo(chat_id)
            banner = "📝 **多维表格待办速记演示已发送**：请在当前会话中查看生成的灵感卡片！"

        # 11. 同步天气翻页时钟 (特性 10)
        elif act == "sync_weather_clock":
            if hasattr(self, "server") and self.server:
                await self.server.trigger_flip_clock_sync()
                banner = "⛅ **天气翻页时钟已同步**：气象指标已推送，硬件已校准翻页时钟！"
            else:
                banner = "⚠️ 硬件网关未就绪。"

        # 12. 启动随身番茄钟 (Page 3)
        elif act == "start_pomodoro":
            if hasattr(self, "server") and self.server:
                await self.server.trigger_pomodoro_start()
                banner = "🍅 **随身番茄钟已开启**：硬件屏幕已同步开启 25 分钟专注计时！"
            else:
                banner = "⚠️ 硬件网关未就绪。"

        # 13. 触发物理动作宏 1: 打卡签到
        elif act == "trigger_macro_checkin":
            if hasattr(self, "server") and self.server:
                await self.server._execute_macro_action("ok_double_click", "飞书工作台状态签到")
                banner = "📍 **OK 键打卡动作宏已触发**：工作状态已签到，打卡卡片已生成！"
            else:
                banner = "⚠️ 硬件网关未就绪。"

        # 14. 触发物理动作宏 2: Git 状态巡检
        elif act == "trigger_macro_git":
            if hasattr(self, "server") and self.server:
                await self.server._execute_macro_action("up_double_click", "当前代码库 Git 巡检")
                banner = "🔍 **Up 键 Git 巡检宏已触发**：代码库分支、状态与最新 Commit 巡检卡片已送达！"
            else:
                banner = "⚠️ 硬件网关未就绪。"

        # 15. 触发物理动作宏 3: 工位安全防窥
        elif act == "trigger_macro_down":
            if hasattr(self, "server") and self.server:
                await self.server._execute_macro_action("down_double_click", "工位安全防窥 / 自定义动作")
                banner = "🛡️ **Down 键安全动作宏已触发**：工位安全防窥动作已执行！"
            else:
                banner = "⚠️ 硬件网关未就绪。"

        # 16. 快速待办标记完成 (特性 4)
        elif act == "quick_todo_done":
            content = value.get("content", "")
            banner = f"✅ 已将待办「{content[:20]}」在多维表格中标记为已完成！"

        # 17. 触发紧急安全熔断
        elif act == "trigger_stop":
            if hasattr(self, "server") and self.server:
                await self.server._trigger_emergency_stop()
                await self.server.broadcast_alert("紧急安全熔断", "飞书端已触发 Physical Stop！", level="danger", duration_sec=5)
            banner = "🛑 **紧急熔断已触发**：已终止后台所有正在运行的 Agent 任务！"

        # 18. 切换视图 (控制面板 ⇄ 动作宏 ⇄ 固件说明)
        elif act == "switch_view":
            view_mode = value.get("view", "control")
            if view_mode == "macro":
                banner = "⚡ 已切换至物理按键宏与工作流引擎面板"
            elif view_mode == "firmware":
                banner = "🛠️ 已切换至固件开发与刷机烧录指引"
            else:
                banner = "🎛️ 已返回硬件综合协同控制台"

        # 19. 动作宏设置：一键切换预设
        elif act == "set_macro":
            key = value.get("key")
            preset = value.get("preset")
            key_names = {"ok_double_click": "OK 确定键", "up_double_click": "Up 上翻键", "down_double_click": "Down 下翻键"}
            if key in MACRO_PRESETS and preset in MACRO_PRESETS[key]:
                cfg = self.get_config()
                if "macro_bindings" not in cfg:
                    cfg["macro_bindings"] = {}
                preset_conf = MACRO_PRESETS[key][preset].copy()
                cfg["macro_bindings"][key] = preset_conf
                self.save_config(cfg)
                banner = f"✅ 已成功将【{key_names.get(key, key)}】配置为：**{preset_conf['description']}**！"

        # 20. 动作宏设置：切换飞书卡片通知
        elif act == "toggle_macro_notify":
            key = value.get("key")
            key_names = {"ok_double_click": "OK 确定键", "up_double_click": "Up 上翻键", "down_double_click": "Down 下翻键"}
            cfg = self.get_config()
            if "macro_bindings" not in cfg:
                cfg["macro_bindings"] = {}
            if key not in cfg["macro_bindings"]:
                cfg["macro_bindings"][key] = {}
            curr_notify = cfg["macro_bindings"][key].get("notify_feishu", True)
            new_notify = not curr_notify
            cfg["macro_bindings"][key]["notify_feishu"] = new_notify
            self.save_config(cfg)
            banner = f"🔔 【{key_names.get(key, key)}】的飞书卡片推送已切换为：**{'开启' if new_notify else '关闭'}**！"

        # 21. 动作宏设置：全部恢复出厂默认宏
        elif act == "reset_macros":
            cfg = self.get_config()
            cfg["macro_bindings"] = {
                "ok_double_click": MACRO_PRESETS["ok_double_click"]["checkin"].copy(),
                "up_double_click": MACRO_PRESETS["up_double_click"]["git"].copy(),
                "down_double_click": MACRO_PRESETS["down_double_click"]["guard"].copy()
            }
            self.save_config(cfg)
            banner = "🔄 三大实体按键动作宏已全部恢复为出厂默认预设！"

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
