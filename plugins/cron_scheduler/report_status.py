#!/usr/bin/env python3
"""System status reporter script for cron_scheduler."""

import os
import subprocess
import time
from datetime import datetime

def get_system_status():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. 负载与运行时间
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    up_str = "--"
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                up_sec = float(f.readline().split()[0])
                days = int(up_sec // 86400)
                hours = int((up_sec % 86400) // 3600)
                mins = int((up_sec % 3600) // 60)
                up_str = f"{days}天 {hours}小时 {mins}分"
        except Exception:
            pass

    # 2. 内存使用
    mem_total_mb = 0
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
    mem_used_mb = max(0, mem_total_mb - mem_avail_mb)
    mem_pct = (mem_used_mb / mem_total_mb * 100.0) if mem_total_mb > 0 else 0.0

    # 3. 磁盘使用 (/)
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

    # 4. PM2 核心服务状态
    pm2_services = []
    try:
        out = subprocess.check_output(["pm2", "jlist"], text=True, timeout=5)
        import json
        pms = json.loads(out)
        for p in pms:
            p_name = p.get("name", "")
            p_status = p.get("pm2_env", {}).get("status", "unknown")
            p_cpu = p.get("monit", {}).get("cpu", 0)
            p_mem = p.get("monit", {}).get("memory", 0) // (1024 * 1024)
            if p_name in ["feishu-bot", "feishu-cron-daemon", "pi-led-api", "cyberpi-gpio"]:
                st_icon = "🟢" if p_status == "online" else "🔴"
                pm2_services.append(f"{st_icon} `{p_name}`: {p_status} (CPU {p_cpu}%, RAM {p_mem}MB)")
    except Exception:
        pass

    # 5. 综合评级
    level = "🟢 运行良好"
    if mem_pct > 85.0 or disk_pct > 90.0 or load[0] > 4.0:
        level = "🟠 资源偏高"

    lines = [
        f"**🖥️ 系统健康状态巡检报告**",
        f"• **巡检时间**：`{now_str}`",
        f"• **综合评级**：{level}",
        f"• **持续运行**：`{up_str}`",
        f"",
        f"**📊 资源指标**：",
        f"• **CPU 平均负载**：`{load[0]:.2f}` (1m), `{load[1]:.2f}` (5m), `{load[2]:.2f}` (15m)",
        f"• **内存占用**：`{mem_used_mb} MB` / `{mem_total_mb} MB` (**{mem_pct:.1f}%**)",
        f"• **根磁盘空间**：`{disk_used_gb:.1f} GB` / `{disk_total_gb:.1f} GB` (**{disk_pct:.1f}%**)",
    ]

    if pm2_services:
        lines.append("")
        lines.append("**🤖 核心进程运行状态**：")
        lines.extend([f"• {s}" for s in pm2_services])

    return "\n".join(lines)

if __name__ == "__main__":
    print(get_system_status())
