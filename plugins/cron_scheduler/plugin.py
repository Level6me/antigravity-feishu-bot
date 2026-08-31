"""Cron Scheduler Plugin (v3.0.0 Remake) for antigravity-feishu-bot.
Provides:
- Instant Natural Language Schedule parsing & message interception
- Full-fledged slash command suite (/cron, /schedule)
- High-interactivity Feishu cards with user/system tabs, logs, and one-click actions
- Seamless integration with the independent CronDaemon background service
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from plugin_base import BasePlugin
from lark_client import patch_interactive_card_sdk, send_reply_sdk, send_card_to_chat_sdk
from logger import log

from scheduler_db import (
    init_db,
    save_task,
    get_task,
    get_all_tasks,
    delete_task,
    update_task_status,
    get_recent_logs
)
from scheduler import compute_next_run, parse_schedule_intent
from executors import execute_task


def format_ts(ts: Optional[int]) -> str:
    if not ts or ts <= 0:
        return "尚未运行"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def build_cron_panel_card(tasks: List[Dict[str, Any]], active_tab: str = "user", session_data: dict = None) -> dict:
    """构建计划任务总控中心交互卡片"""
    user_tasks = [t for t in tasks if t.get("category") == "user"]
    sys_tasks = [t for t in tasks if t.get("category") in ["system", "maintenance"]]
    displayed_tasks = user_tasks if active_tab == "user" else sys_tasks

    elements = []

    # 顶部标签页切换栏
    elements.append({
        "tag": "markdown",
        "content": f"**⏱️ 计划任务管理中心 (Cron Center v3.0)**\n由独立 PM2 守护引擎驱动，精准倒计时与周期巡检调度。\n当前选中的分类：**{'👤 用户主动任务' if active_tab == 'user' else '⚙️ 系统后台任务'}**"
    })

    header_actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "👤 用户任务" if active_tab != "user" else "🔵 👤 用户任务"},
            "type": "primary" if active_tab == "user" else "default",
            "value": {"action": "switch_cron_tab", "tab": "user"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⚙️ 系统任务" if active_tab != "system" else "🔵 ⚙️ 系统任务"},
            "type": "primary" if active_tab == "system" else "default",
            "value": {"action": "switch_cron_tab", "tab": "system"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "➕ 快捷新建"},
            "type": "primary",
            "value": {"action": "open_cron_create"}
        }
    ]
    elements.append({
        "tag": "action",
        "layout": "bisect",
        "actions": header_actions
    })
    elements.append({"tag": "hr"})

    if not displayed_tasks:
        elements.append({
            "tag": "markdown",
            "content": f"*(暂无{'用户主动' if active_tab == 'user' else '系统后台'}计划任务)*\n可以直接在聊天中发送：`一分钟后提醒我喝水` 或点击上方 **[ ➕ 快捷新建 ]** 快速添加！"
        })
    else:
        for t in displayed_tasks:
            t_id = t.get("id")
            is_active = bool(t.get("is_active", 1))
            status_icon = "🟢 启用中" if is_active else "🔴 已暂停"
            cron_expr = t.get("cron_expr", "")
            task_type_str = "标准 Cron" if t.get("task_type") == "cron" else "延迟倒计时"
            
            action_type_map = {
                "reminder": "💬 消息提醒",
                "shell": "🖥️ Shell 脚本",
                "ai_agent": "🧠 AI 巡检",
                "hardware_led": "💡 硬件联动"
            }
            act_str = action_type_map.get(t.get("action_type"), "通用任务")

            last_run = format_ts(t.get("last_run_at"))
            next_run = format_ts(t.get("next_run_at"))
            prompt_preview = t.get("prompt", "")
            if len(prompt_preview) > 50:
                prompt_preview = prompt_preview[:50] + "..."

            task_md = f"**{t.get('name', '未命名任务')}** (`{t_id}`) | **{status_icon}**\n" \
                      f"• **任务类型**：{act_str} | **触发规则**：`{cron_expr}` ({task_type_str})\n" \
                      f"• **执行内容**：`{prompt_preview}`\n" \
                      f"• **累计运行**：{t.get('run_count', 0)} 次 | **上次触发**：{last_run}\n" \
                      f"• **下次预计触发**：`{next_run}`"

            elements.append({"tag": "markdown", "content": task_md})

            # 操作按钮行
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚡ 立即触发"},
                        "type": "default",
                        "value": {"action": "run_cron_now", "task_id": t_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏸️ 暂停" if is_active else "▶️ 启用"},
                        "type": "default",
                        "value": {"action": "toggle_cron_active", "task_id": t_id, "is_active": not is_active}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                        "type": "danger",
                        "value": {"action": "delete_cron_task", "task_id": t_id}
                    }
                ]
            })
            elements.append({"tag": "hr"})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⏱️ 计划任务管理中心 (Cron Center v3.0)"},
            "template": "blue"
        },
        "elements": elements
    }


def build_cron_created_card(task_data: dict) -> dict:
    """构建任务创建成功的富交互确认卡片"""
    t_name = task_data.get("name", "计划任务")
    t_id = task_data.get("id", "")
    expr = task_data.get("cron_expr", "")
    task_type = "标准 Cron 周期" if task_data.get("task_type") == "cron" else "秒级倒计时"
    prompt = task_data.get("prompt", "")
    next_ts = task_data.get("next_run_at", 0)
    next_str = format_ts(next_ts)

    elements = [
        {
            "tag": "markdown",
            "content": f"**✨ 计划任务已成功创建并加入调度引擎！**\n\n"
                       f"• **任务名称**：**{t_name}** (`{t_id}`)\n"
                       f"• **调度规则**：`{expr}` ({task_type})\n"
                       f"• **下次预计触发**：`{next_str}`"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**📝 预设执行内容 / Prompt**：\n`{prompt}`\n\n🛡️ *任务已由后台守护服务持久化，断电或重启均自动恢复调度。*"
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⚡ 立即触发一次"},
                    "type": "primary",
                    "value": {"action": "run_cron_now", "task_id": t_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📋 管理任务面板"},
                    "type": "default",
                    "value": {"action": "open_cron_panel"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🗑️ 撤销任务"},
                    "type": "danger",
                    "value": {"action": "delete_cron_task", "task_id": t_id}
                }
            ]
        }
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"✅ 计划任务已就绪: {t_name}"},
            "template": "green"
        },
        "elements": elements
    }


def build_quick_create_guide_card() -> dict:
    """构建快捷创建任务指导与预设卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "➕ 快捷创建计划任务"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "**🎯 支持直接发送自然语言（最简便）：**\n"
                           "• `一分钟后提醒我喝水`\n"
                           "• `10分钟后检查服务器状态`\n"
                           "• `半小时后提醒我站会`\n"
                           "• `每天早上9点提醒站会`\n"
                           "• `每天23:30检查数据备份`\n"
                           "• `每隔10分钟巡检一次服务器`"
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": "**⚡ 或使用标准 3 段式指令：**\n`/cron add 任务名称 | 触发规则(如 60s 或 0 9 * * *) | 执行内容`"
            }
        ]
    }


class CronSchedulerPlugin(BasePlugin):

    def initialize(self):
        init_db()
        log.info(f"[Plugin:{self.plugin_id}] Cron Scheduler Plugin v3.0 initialized with standalone daemon engine.")

    async def on_message(self, chat_id: str, user_text: str, message_id: str, session_data: dict) -> bool:
        """自然语言消息拦截：毫秒级识别时间意图并注册任务"""
        intent = parse_schedule_intent(user_text)
        if not intent:
            return False

        log.info(f"[Plugin:{self.plugin_id}] Natural Language Schedule Intent matched: {intent}")
        now_ts = int(time.time())
        next_run = compute_next_run(intent["cron_expr"], intent["task_type"], now_ts)

        task_id = f"task_usr_{now_ts}"
        task_data = {
            "id": task_id,
            "chat_id": chat_id,
            "category": "user",
            "name": intent["name"],
            "task_type": intent["task_type"],
            "action_type": intent.get("action_type", "reminder"),
            "cron_expr": intent["cron_expr"],
            "prompt": intent["prompt"],
            "project_path": session_data.get("project", ""),
            "is_active": True,
            "created_by": chat_id,
            "created_at": now_ts,
            "next_run_at": next_run,
            "run_count": 0
        }

        save_task(task_data)
        created_card = build_cron_created_card(task_data)
        self.send_reply_card(message_id, created_card)
        return True

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        """处理 /cron 与 /schedule 命令"""
        if command.lower() not in ["/cron", "/schedule"]:
            return False

        args_clean = args.strip()

        # 1. 查看任务总面板
        if not args_clean or args_clean in ["list", "panel", "ls"]:
            tasks = get_all_tasks(chat_id)
            card = build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
            self.send_reply_card(message_id, card)
            return True

        # 2. 删除任务: /cron del <task_id>
        if args_clean.startswith(("del ", "rm ", "delete ")):
            task_id = args_clean.split(None, 1)[1].strip()
            delete_task(task_id)
            self.send_reply_text(message_id, f"🗑️ 计划任务 `{task_id}` 已成功删除。")
            return True

        # 3. 立即运行: /cron run <task_id>
        if args_clean.startswith("run "):
            task_id = args_clean.split(None, 1)[1].strip()
            task = get_task(task_id)
            if task:
                asyncio.create_task(execute_task(task, lambda c_id, c_data: send_card_to_chat_sdk(c_id, c_data)))
                self.send_reply_text(message_id, f"⚡ 正在立即触发任务 `{task.get('name')}`...")
            else:
                self.send_reply_text(message_id, f"❌ 未找到 ID 为 `{task_id}` 的计划任务。")
            return True

        # 4. 启用 / 暂停: /cron on/off <task_id>
        if args_clean.startswith("on "):
            task_id = args_clean.split(None, 1)[1].strip()
            update_task_status(task_id, True)
            self.send_reply_text(message_id, f"▶️ 计划任务 `{task_id}` 已启用。")
            return True
        elif args_clean.startswith("off "):
            task_id = args_clean.split(None, 1)[1].strip()
            update_task_status(task_id, False)
            self.send_reply_text(message_id, f"⏸️ 计划任务 `{task_id}` 已暂停。")
            return True

        # 5. 添加任务
        target_str = args_clean[4:].strip() if args_clean.startswith(("add ", "new ")) else args_clean

        # 5.1 竖线分隔 3 段数据: 名称 | 规则 | Prompt
        if "|" in target_str or "｜" in target_str:
            parts = [p.strip() for p in re.split(r"[|｜]", target_str) if p.strip()]
            if len(parts) >= 3:
                name, expr, prompt = parts[0], parts[1], parts[2]
                task_type = "delay" if re.match(r"^\d+\s*[s|m|h|d]?$", expr.lower()) else "cron"
                now_ts = int(time.time())
                next_run = compute_next_run(expr, task_type, now_ts)
                task_id = f"task_usr_{now_ts}"
                task_data = {
                    "id": task_id,
                    "chat_id": chat_id,
                    "category": "user",
                    "name": name,
                    "task_type": task_type,
                    "action_type": "reminder",
                    "cron_expr": expr,
                    "prompt": prompt,
                    "project_path": session_data.get("project", ""),
                    "is_active": True,
                    "created_by": chat_id,
                    "created_at": now_ts,
                    "next_run_at": next_run,
                    "run_count": 0
                }
                save_task(task_data)
                created_card = build_cron_created_card(task_data)
                self.send_reply_card(message_id, created_card)
                return True

        # 5.2 自然语言指令解析
        intent = parse_schedule_intent(target_str)
        if intent:
            now_ts = int(time.time())
            next_run = compute_next_run(intent["cron_expr"], intent["task_type"], now_ts)
            task_id = f"task_usr_{now_ts}"
            task_data = {
                "id": task_id,
                "chat_id": chat_id,
                "category": "user",
                "name": intent["name"],
                "task_type": intent["task_type"],
                "action_type": intent.get("action_type", "reminder"),
                "cron_expr": intent["cron_expr"],
                "prompt": intent["prompt"],
                "project_path": session_data.get("project", ""),
                "is_active": True,
                "created_by": chat_id,
                "created_at": now_ts,
                "next_run_at": next_run,
                "run_count": 0
            }
            save_task(task_data)
            created_card = build_cron_created_card(task_data)
            self.send_reply_card(message_id, created_card)
            return True

        guide_card = build_quick_create_guide_card()
        self.send_reply_card(message_id, guide_card)
        return True

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """处理卡片交互按钮回调"""
        if action == "switch_cron_tab":
            tab = value.get("tab", "user")
            tasks = get_all_tasks(chat_id)
            new_card = build_cron_panel_card(tasks, active_tab=tab, session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "open_cron_panel":
            tasks = get_all_tasks(chat_id)
            new_card = build_cron_panel_card(tasks, active_tab="user", session_data={})
            send_card_to_chat_sdk(chat_id, new_card)
            return True

        elif action == "open_cron_create":
            guide_card = build_quick_create_guide_card()
            send_card_to_chat_sdk(chat_id, guide_card)
            return True

        elif action == "toggle_cron_active":
            task_id = value.get("task_id")
            is_active = bool(value.get("is_active", True))
            update_task_status(task_id, is_active)
            tasks = get_all_tasks(chat_id)
            new_card = build_cron_panel_card(tasks, active_tab="user", session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "delete_cron_task":
            task_id = value.get("task_id")
            delete_task(task_id)
            tasks = get_all_tasks(chat_id)
            new_card = build_cron_panel_card(tasks, active_tab="user", session_data={})
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        elif action == "run_cron_now":
            task_id = value.get("task_id")
            task = get_task(task_id)
            if task:
                asyncio.create_task(execute_task(task, lambda c_id, c_data: send_card_to_chat_sdk(c_id, c_data)))
            return True

        return False
