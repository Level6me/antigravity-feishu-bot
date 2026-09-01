"""Card builders: cron scheduled tasks management and notifications."""

import os
import time
from datetime import datetime
from cards.common import create_footer

def _format_timestamp(ts):
    if not ts or ts <= 0:
        return "尚未运行"
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def _format_relative_time(ts):
    if not ts or ts <= 0:
        return "尚未设定"
    now_ts = int(time.time())
    diff = ts - now_ts
    readable_date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    
    if diff <= 0:
        rel = "即将触发"
    elif diff < 60:
        rel = f"{diff} 秒后"
    elif diff < 3600:
        rel = f"{diff // 60} 分钟后"
    elif diff < 86400:
        hours = diff // 3600
        mins = (diff % 3600) // 60
        rel = f"{hours} 小时 {mins} 分后" if mins > 0 else f"{hours} 小时后"
    else:
        days = diff // 86400
        hours = (diff % 86400) // 3600
        rel = f"{days} 天 {hours} 小时后" if hours > 0 else f"{days} 天后"
        
    return f"{rel} ({readable_date})"


BADGE_MAP = {
    "reminder": "💬【消息提醒】",
    "shell": "🖥️【Shell脚本】",
    "ai_agent": "🧠【AI巡检】",
    "hardware_led": "💡【硬件联动】"
}


def build_cron_panel_card(tasks, active_tab="user", session_data=None):
    user_tasks = [t for t in tasks if t.get('category') in ['user', None, '', 'default']]
    sys_tasks = [t for t in tasks if t.get('category') in ['system', 'maintenance']]
    
    displayed_tasks = user_tasks if active_tab == 'user' else sys_tasks
    
    elements = []
    
    # 顶部说明与快捷按钮栏
    elements.append({
        "tag": "markdown",
        "content": f"**⏱️ 计划任务管理中心 (Cron Center)**\n包含用户创建的周期指令与系统级后台任务。选中的分类：**{'👤 用户主动任务' if active_tab == 'user' else '⚙️ 系统后台任务'}**"
    })
    
    # 切换 Tab 按钮与新建任务按钮
    header_actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "👤 用户任务" if active_tab != 'user' else "🔵 👤 用户任务"},
            "type": "primary" if active_tab == 'user' else "default",
            "value": {"action": "switch_cron_tab", "tab": "user"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⚙️ 系统任务" if active_tab != 'system' else "🔵 ⚙️ 系统任务"},
            "type": "primary" if active_tab == 'system' else "default",
            "value": {"action": "switch_cron_tab", "tab": "system"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "➕ 新建任务"},
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
            "content": f"*(暂无{'用户主动' if active_tab == 'user' else '系统后台'}计划任务)*\n点击上方 **[ ➕ 新建任务 ]** 即可添加一个定时任务！"
        })
    else:
        for t in displayed_tasks:
            t_id = t.get('id')
            is_active = bool(t.get('is_active', 1))
            status_icon = "🟢 启用中" if is_active else "🔴 已暂停"
            action_type = t.get('action_type', 'reminder')
            badge = BADGE_MAP.get(action_type, "💬【消息提醒】")
            t_name = t.get('name', '未命名任务')
            
            last_run = _format_timestamp(t.get('last_run_at'))
            next_run_str = _format_relative_time(t.get('next_run_at'))
            
            prompt_preview = t.get('prompt', '') or t.get('command', '')
            if len(prompt_preview) > 75:
                prompt_preview = prompt_preview[:75] + "..."
                
            task_md = f"**{badge} {t_name}**  {status_icon}\n" \
                      f"⏰ **触发设定**：{next_run_str}\n" \
                      f"💡 **执行内容**：{prompt_preview}\n" \
                      f"📊 **任务审计**：累计运行 **{t.get('run_count', 0)}** 次 · 上次：{last_run} (`#{t_id}`)"

            elements.append({
                "tag": "markdown",
                "content": task_md
            })
            
            # 操作按钮行
            task_actions = [
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
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": task_actions
            })
            elements.append({"tag": "hr"})

    elements.append(create_footer())
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "⏱️ 计划任务管理中心 (Cron Center)"
            },
            "template": "blue"
        },
        "elements": elements
    }
    return card


def build_cron_start_card(task_data):
    """当计划任务触发启动时推送的交互卡片"""
    t_name = task_data.get('name', '计划任务')
    t_id = task_data.get('id', '')
    cat = "👤 用户任务" if task_data.get('category') == 'user' else "⚙️ 系统任务"
    expr = task_data.get('cron_expr', '')
    prompt = task_data.get('prompt', '')
    
    content = f"**▶️ 计划任务已触发，正在后台启动执行...**\n\n" \
              f"• **任务名称**：**{t_name}** (`{t_id}`)\n" \
              f"• **任务类别**：{cat} | **触发规则**：`{expr}`\n" \
              f"• **执行指令**：`{prompt}`\n\n" \
              f"⏳ *正在运行 Agent 分析并生成结果，请稍候...*"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"⏰ 触发提示: {t_name}"
            },
            "template": "orange"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content
            }
        ]
    }
    return card


def build_cron_execution_card(task_data, result_text, is_error=False, duration_ms=0):
    """当计划任务执行完毕后推送的最终结果报告卡片"""
    t_name = task_data.get('name', '计划任务')
    t_id = task_data.get('id', '')
    cat = "👤 用户任务" if task_data.get('category') == 'user' else "⚙️ 系统任务"
    dur_sec = f"{duration_ms / 1000.0:.1f} 秒" if duration_ms > 0 else "< 1 秒"
    
    header_color = "red" if is_error else "green"
    status_title = "❌ 计划任务执行异常" if is_error else "✅ 计划任务报告"
    
    elements = [
        {
            "tag": "markdown",
            "content": f"**📌 任务基础信息**\n" \
                       f"• **任务名称**：**{t_name}** (`{t_id}`) | **类别**：{cat}\n" \
                       f"• **完成耗时**：{dur_sec} | **完成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**📊 执行结果与报告**\n\n{result_text}"
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
                    "value": {"action": "run_cron_now", "task_id": t_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⚙️ 管理任务中心"},
                    "type": "default",
                    "value": {"action": "open_cron_panel"}
                }
            ]
        }
    ]

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{status_title}: {t_name}"
            },
            "template": header_color
        },
        "elements": elements
    }
    return card


def build_cron_created_card(task_data):
    """当新计划任务创建成功后推送的确认交互卡片"""
    t_name = task_data.get('name', '计划任务')
    t_id = task_data.get('id', '')
    cat = "👤 用户主动任务" if task_data.get('category') == 'user' else "⚙️ 系统后台任务"
    expr = task_data.get('cron_expr', '')
    task_type = "标准 Cron 表达式" if task_data.get('task_type') == 'cron' else "倒计时定时器"
    prompt = task_data.get('prompt', '')
    
    next_ts = task_data.get('next_run_at', 0)
    next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M:%S') if next_ts > 0 else "算中..."

    elements = [
        {
            "tag": "markdown",
            "content": f"**📌 任务基本信息**\n" \
                       f"• **任务名称**：**{t_name}** (`{t_id}`)\n" \
                       f"• **任务类别**：{cat} | **规则类型**：{task_type}\n" \
                       f"• **触发规则**：`{expr}` | **下次预计触发**：`{next_str}`"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**📝 预设执行 Prompt**\n`{prompt}`\n\n🛡️ *该任务已持久化存入数据库，中途发生重启亦可自动恢复倒计时与触发。*"
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
                    "text": {"tag": "plain_text", "content": "⚙️ 打开任务中心"},
                    "type": "default",
                    "value": {"action": "open_cron_panel"}
                }
            ]
        },
        create_footer()
    ]

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"✅ 计划任务创建成功: {t_name}"
            },
            "template": "green"
        },
        "elements": elements
    }
    return card
