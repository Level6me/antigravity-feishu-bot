"""Server Health Inspection Plugin for antigravity-feishu-bot."""

import os
import time
import subprocess
from datetime import datetime
from plugin_base import BasePlugin
from cards import CardBuilder
from logger import log


class ServerHealthPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] Server Health Inspection plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() in ["/sysinfo", "/health"]:
            card = self.build_health_card()
            self.send_reply_card(message_id, card)
            return True
        return False

    def build_health_card(self) -> dict:
        # Get Load Average
        load_1, load_5, load_15 = os.getloadavg() if hasattr(os, 'getloadavg') else (0.0, 0.0, 0.0)

        # Get Memory Stats
        mem_total_mb = 0
        mem_free_mb = 0
        mem_avail_mb = 0
        if os.path.exists("/proc/meminfo"):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            mem_total_mb = int(line.split()[1]) // 1024
                        elif line.startswith("MemAvailable:"):
                            mem_avail_mb = int(line.split()[1]) // 1024
            except Exception:
                pass
        
        mem_used_mb = mem_total_mb - mem_avail_mb if mem_total_mb > mem_avail_mb else 0
        mem_pct = (mem_used_mb / mem_total_mb * 100.0) if mem_total_mb > 0 else 0.0

        # Get Disk Stats (Root /)
        disk_total_gb = 0.0
        disk_used_gb = 0.0
        disk_pct = 0.0
        try:
            st = os.statvfs("/")
            disk_total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
            disk_free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
            disk_used_gb = disk_total_gb - disk_free_gb
            disk_pct = (disk_used_gb / disk_total_gb * 100.0) if disk_total_gb > 0 else 0.0
        except Exception:
            pass

        # Get Uptime
        uptime_str = "--"
        try:
            with open("/proc/uptime", "r") as f:
                up_sec = float(f.readline().split()[0])
                days = int(up_sec // 86400)
                hours = int((up_sec % 86400) // 3600)
                mins = int((up_sec % 3600) // 60)
                uptime_str = f"{days}天 {hours}小时 {mins}分"
        except Exception:
            pass

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        status_badge = "🟢 系统运行良好"
        header_color = "green"
        if mem_pct > 85.0 or disk_pct > 90.0:
            status_badge = "🟠 资源使用偏高"
            header_color = "orange"

        elements = [
            {
                "tag": "markdown",
                "content": f"**📊 实时系统性能状态 ({status_badge})**\n\n" \
                           f"• **系统 CPU 负载**：`1分: {load_1:.2f}` | `5分: {load_5:.2f}` | `15分: {load_15:.2f}`\n" \
                           f"• **内存使用率**：**{mem_pct:.1f}%** ({mem_used_mb} MB / {mem_total_mb} MB)\n" \
                           f"• **磁盘占用 (/)**：**{disk_pct:.1f}%** ({disk_used_gb:.1f} GB / {disk_total_gb:.1f} GB)\n" \
                           f"• **连续运行时间**：{uptime_str}\n" \
                           f"• **采样时间戳**：`{now_str}`"
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 刷新巡检状态"},
                        "type": "primary",
                        "value": {"action": "refresh_server_health"}
                    }
                ]
            }
        ]

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "🖥️ 服务器健康巡检报告 (Server Health)"
                },
                "template": header_color
            },
            "elements": elements
        }
        return card

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        if action == "refresh_server_health":
            card = self.build_health_card()
            from lark_client import patch_interactive_card_sdk
            patch_interactive_card_sdk(card_message_id, card)
            return True
        return False
