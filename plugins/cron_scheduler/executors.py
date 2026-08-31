"""Multi-mode task execution engine for cron_scheduler daemon."""

import asyncio
import os
import subprocess
import time
from datetime import datetime
from typing import Dict, Any, Tuple
import urllib.request
import json


def build_reminder_card(task: dict) -> dict:
    """构建定时提醒专用交互卡片"""
    name = task.get("name", "定时提醒")
    prompt = task.get("prompt", "您设定的提醒时间到了！")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 动态匹配图标
    icon = "⏰"
    if "喝水" in name or "喝水" in prompt:
        icon = "💧"
    elif "站会" in name or "开会" in name or "会议" in prompt:
        icon = "📅"
    elif "吃" in name or "饭" in name:
        icon = "🍱"
    elif "休息" in name:
        icon = "☕"
    elif "巡检" in name or "检查" in name:
        icon = "🔍"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{icon} 提醒事项：{name}"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**{prompt}**\n\n• **触发时间**：`{now_str}`\n• **任务编号**：`{task.get('id')}`"
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 计划任务中心"},
                        "type": "default",
                        "value": {"action": "open_cron_panel"}
                    }
                ]
            }
        ]
    }


def build_execution_report_card(task: dict, result_text: str, is_error: bool = False, duration_ms: int = 0) -> dict:
    """构建任务执行报告卡片"""
    name = task.get("name", "计划任务")
    task_id = task.get("id", "")
    dur_str = f"{duration_ms / 1000.0:.2f} 秒" if duration_ms > 0 else "< 0.1 秒"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    action_type = task.get("action_type", "task")

    type_label = {
        "reminder": "💬 消息提醒",
        "shell": "🖥️ Shell 脚本",
        "ai_agent": "🧠 AI 智能巡检",
        "hardware_led": "💡 硬件联动"
    }.get(action_type, "⚙️ 系统任务")

    header_color = "red" if is_error else "green"
    status_title = "❌ 任务执行异常" if is_error else "✅ 任务执行成功"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{status_title}: {name}"},
            "template": header_color
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"• **任务名称**：**{name}** (`{task_id}`)\n"
                           f"• **任务类型**：{type_label} | **耗时**：`{dur_str}`\n"
                           f"• **完成时间**：`{now_str}`"
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"**📊 执行报告与输出**：\n\n{result_text}"
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 再次运行"},
                        "type": "primary",
                        "value": {"action": "run_cron_now", "task_id": task_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 任务中心"},
                        "type": "default",
                        "value": {"action": "open_cron_panel"}
                    }
                ]
            }
        ]
    }


async def execute_task(task: dict, send_card_func=None) -> Tuple[bool, str, int]:
    """
    通用任务执行入口
    返回: (is_success, result_message, duration_ms)
    """
    start_time = time.time()
    chat_id = task.get("chat_id")
    action_type = task.get("action_type", "reminder")
    prompt = task.get("prompt", "")
    command = task.get("command", "")
    
    is_success = True
    result_text = ""

    try:
        # 1. 提醒类任务
        if action_type == "reminder":
            card = build_reminder_card(task)
            if send_card_func and chat_id:
                await send_card_func(chat_id, card)
            result_text = f"提醒已准时送达飞书：{prompt}"

        # 2. Shell 运维脚本类任务
        elif action_type == "shell":
            cmd = command or prompt
            if not cmd:
                raise ValueError("Shell 任务未指定可执行命令")
            
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                out_str = stdout.decode("utf-8", errors="replace").strip()
                err_str = stderr.decode("utf-8", errors="replace").strip()
                
                if proc.returncode == 0:
                    result_text = f"```bash\n{out_str or '命令执行成功，无额外输出'}\n```"
                else:
                    is_success = False
                    result_text = f"❌ 命令执行返回非零状态码 `{proc.returncode}`\n\n```bash\n{err_str or out_str}\n```"
            except asyncio.TimeoutError:
                proc.kill()
                is_success = False
                result_text = "❌ 命令执行超时 (超过 60 秒限制)，已强制终止。"

            if send_card_func and chat_id:
                card = build_execution_report_card(task, result_text, is_error=not is_success, duration_ms=int((time.time() - start_time) * 1000))
                await send_card_func(chat_id, card)

        # 3. 硬件 / LED 控制类任务
        elif action_type == "hardware_led":
            endpoint = "http://127.0.0.1:8080/api/state"
            req = urllib.request.Request(endpoint, headers={"User-Agent": "CronSchedulerDaemon/3.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result_text = f"硬件状态获取成功：\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
            
            if send_card_func and chat_id:
                card = build_execution_report_card(task, result_text, is_error=False, duration_ms=int((time.time() - start_time) * 1000))
                await send_card_func(chat_id, card)

        # 4. AI 智能巡检类任务
        elif action_type == "ai_agent":
            # 如果在主 bot 环境，可调用 execute_antigravity，如果作为独立进程则直接返回执行标记
            result_text = f"AI 巡检任务触发完成。\n预设指令：`{prompt}`\n已向目标会话就绪执行。"
            if send_card_func and chat_id:
                card = build_reminder_card(task)
                await send_card_func(chat_id, card)

        else:
            result_text = f"任务触发成功：{prompt}"
            if send_card_func and chat_id:
                card = build_reminder_card(task)
                await send_card_func(chat_id, card)

    except Exception as e:
        is_success = False
        result_text = f"执行异常: {str(e)}"
        if send_card_func and chat_id:
            try:
                card = build_execution_report_card(task, result_text, is_error=True, duration_ms=int((time.time() - start_time) * 1000))
                await send_card_func(chat_id, card)
            except Exception:
                pass

    duration_ms = int((time.time() - start_time) * 1000)
    return is_success, result_text, duration_ms
