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


MAX_DISPLAY_TASKS = 15


def _is_completed_delay_task(task: dict) -> bool:
    """判断是否为已执行完毕的历史一次性倒计时任务"""
    return (
        task.get('task_type') == 'delay'
        and not bool(task.get('is_active', 1))
        and (int(task.get('run_count', 0) or 0) > 0 or int(task.get('last_run_at', 0) or 0) > 0)
    )


def build_cron_panel_card(tasks, active_tab="user", session_data=None, logs=None, page=1, notice=""):
    valid_tasks = [t for t in (tasks or []) if not _is_completed_delay_task(t)]
    user_tasks = [t for t in valid_tasks if t.get('category') in ['user', None, '', 'default']]
    sys_tasks = [t for t in valid_tasks if t.get('category') in ['system', 'maintenance']]

    if active_tab == 'history' and logs is None:
        try:
            from database import get_cron_logs
            logs = get_cron_logs(limit=MAX_DISPLAY_TASKS)
        except Exception:
            logs = []
    history_logs = list(logs or [])

    displayed_tasks = user_tasks if active_tab == 'user' else sys_tasks
    has_inactive_user = any(not bool(t.get('is_active', 1)) for t in user_tasks)

    elements = []

    # 顶部说明与快捷按钮栏
    if active_tab == 'history':
        subtitle = (
            f"当前分类：**📜 历史执行记录**（共 **{len(history_logs)}** 条） · "
            f"*含已自动销毁的一次性任务与周期任务审计流水*"
        )
    elif active_tab == 'create':
        subtitle = "当前分类：**➕ 新建计划任务** · *表单一步创建提醒、AI 智能巡检或 Shell 脚本*"
    else:
        subtitle = (
            f"当前分类：**{'👤 用户主动任务' if active_tab == 'user' else '⚙️ 系统后台任务'}**（共 **{len(displayed_tasks)}** 项） · "
            f"*一次性任务触发后自动归档至历史记录*"
        )

    header_md = f"**⏱️ 计划任务管理中心 (Cron Center)**\n{subtitle}"
    if notice:
        header_md += f"\n\n💡 {notice}"
    elements.append({
        "tag": "markdown",
        "content": header_md,
    })

    # 切换 Tab 按钮与新建任务按钮（统一走内联 Tab，无需再发提示文本刷屏）
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
            "text": {"tag": "plain_text", "content": "📜 历史记录" if active_tab != 'history' else "🔵 📜 历史记录"},
            "type": "primary" if active_tab == 'history' else "default",
            "value": {"action": "switch_cron_tab", "tab": "history"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔵 ➕ 新建任务" if active_tab == 'create' else "➕ 新建任务"},
            "type": "primary" if active_tab == 'create' else "default",
            "value": {"action": "switch_cron_tab", "tab": "create"}
        }
    ]
    if active_tab == 'user' and has_inactive_user:
        header_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🧹 清理已暂停"},
            "type": "danger",
            "confirm": {
                "title": {"tag": "plain_text", "content": "确认清理已暂停任务"},
                "text": {"tag": "plain_text", "content": "将移除所有已暂停或已完成的一次性任务（历史日志保留），确定继续吗？"}
            },
            "value": {"action": "clear_completed_cron"}
        })
    elif active_tab == 'history' and history_logs:
        header_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🧹 清空记录"},
            "type": "danger",
            "confirm": {
                "title": {"tag": "plain_text", "content": "确认清空历史记录"},
                "text": {"tag": "plain_text", "content": "确定清空当前会话全部计划任务历史流水吗？"}
            },
            "value": {"action": "clear_cron_logs"}
        })
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": header_actions
    })
    elements.append({"tag": "hr"})

    if active_tab == 'create':
        elements.append({
            "tag": "form",
            "name": "cron_create_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "cron_name",
                    "label": {"tag": "plain_text", "content": "任务名称"},
                    "placeholder": {"tag": "plain_text", "content": "例如：每日代码巡检 / 10分钟后喝水提醒"},
                },
                {
                    "tag": "select_static",
                    "name": "cron_action_type",
                    "initial_option": "ai_agent",
                    "placeholder": {"tag": "plain_text", "content": "选择执行引擎类型"},
                    "options": [
                        {"text": {"tag": "plain_text", "content": "🧠 AI 智能体分析执行 (唤醒 Agent 执行指令并汇报)"}, "value": "ai_agent"},
                        {"text": {"tag": "plain_text", "content": "💬 纯文本消息提醒 (直接推送提醒文案)"}, "value": "reminder"},
                        {"text": {"tag": "plain_text", "content": "🖥️ Shell 脚本命令 (仅管理员可执行受控命令)"}, "value": "shell"},
                    ],
                },
                {
                    "tag": "select_static",
                    "name": "cron_preset",
                    "initial_option": "0 9 * * *",
                    "placeholder": {"tag": "plain_text", "content": "选择常用触发周期（或在下方自定义）"},
                    "options": [
                        {"text": {"tag": "plain_text", "content": "⏳ 5 分钟后执行一次 (300s)"}, "value": "300s"},
                        {"text": {"tag": "plain_text", "content": "⏳ 30 分钟后执行一次 (1800s)"}, "value": "1800s"},
                        {"text": {"tag": "plain_text", "content": "⏳ 1 小时后执行一次 (3600s)"}, "value": "3600s"},
                        {"text": {"tag": "plain_text", "content": "🔁 每天 09:00 定时执行 (0 9 * * *)"}, "value": "0 9 * * *"},
                        {"text": {"tag": "plain_text", "content": "🔁 工作日 09:30 执行 (30 9 * * 1-5)"}, "value": "30 9 * * 1-5"},
                        {"text": {"tag": "plain_text", "content": "🔁 每 2 小时巡检一次 (0 */2 * * *)"}, "value": "0 */2 * * *"},
                    ],
                },
                {
                    "tag": "input",
                    "name": "cron_custom_expr",
                    "label": {"tag": "plain_text", "content": "自定义 Cron / 秒数（选填，填写则优先覆盖上方预设）"},
                    "placeholder": {"tag": "plain_text", "content": "留空使用上方预设；或填如 600s / */15 * * * *"},
                },
                {
                    "tag": "input",
                    "name": "cron_prompt",
                    "label": {"tag": "plain_text", "content": "执行 Prompt / 提醒文本 / Shell 命令"},
                    "placeholder": {"tag": "plain_text", "content": "例如：检查当前工作区 Git 状态与最近报错日志并输出简报"},
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "auto",
                            "elements": [
                                {
                                    "tag": "button",
                                    "name": "cron_submit_btn",
                                    "text": {"tag": "plain_text", "content": "✅ 立即创建计划任务"},
                                    "type": "primary",
                                    "action_type": "form_submit",
                                    "value": {"action": "cron_create_submit"},
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "auto",
                            "elements": [
                                {
                                    "tag": "button",
                                    "name": "cron_cancel_btn",
                                    "text": {"tag": "plain_text", "content": "返回列表"},
                                    "type": "default",
                                    "value": {"action": "switch_cron_tab", "tab": "user"},
                                }
                            ],
                        },
                    ],
                },
            ],
        })
    elif active_tab == 'history':
        if not history_logs:
            elements.append({
                "tag": "markdown",
                "content": (
                    "*(暂无历史计划任务执行记录)*\n"
                    "当一次性倒计时任务或周期计划任务触发执行后，其执行时间、耗时与结果摘要会自动归档在此处供随时查阅。"
                )
            })
        else:
            for lg in history_logs[:MAX_DISPLAY_TASKS]:
                status = lg.get('status', 'success')
                status_badge = "✅ 执行成功" if status == 'success' else "❌ 执行失败"
                t_id = lg.get('task_id', '')
                t_name = lg.get('task_name') or lg.get('name') or t_id or '未命名任务'
                t_type = lg.get('task_type') or ('delay' if 'delay' in str(t_id) else 'cron')
                action_type = lg.get('action_type', 'reminder')
                badge = BADGE_MAP.get(action_type, "💬【消息提醒】")
                cron_expr = lg.get('cron_expr', '')
                if t_type == 'delay':
                    type_tag = f"⏳ 一次性 (`{cron_expr}`)" if cron_expr else "⏳ 一次性"
                else:
                    type_tag = f"🔁 周期 (`{cron_expr}`)" if cron_expr else "🔁 周期"

                exec_time = _format_timestamp(lg.get('executed_at'))
                dur_ms = int(lg.get('duration_ms', 0) or 0)
                dur_str = f"{dur_ms / 1000.0:.1f}s" if dur_ms >= 100 else (f"{dur_ms}ms" if dur_ms > 0 else "<0.1s")

                prompt_str = (lg.get('prompt') or '').strip()
                if len(prompt_str) > 70:
                    prompt_str = prompt_str[:70] + "..."
                out_str = (lg.get('output') or lg.get('error_msg') or '').strip().replace('\n', ' ')
                if len(out_str) > 90:
                    out_str = out_str[:90] + "..."

                lines_md = [
                    f"**{badge} {t_name}**  {status_badge}",
                    f"🕒 **执行时间**：{exec_time} · **耗时**：{dur_str} · **类型**：{type_tag} (`#{t_id}`)"
                ]
                if prompt_str:
                    lines_md.append(f"💡 **任务内容**：{prompt_str}")
                if out_str:
                    lines_md.append(f"📝 **结果摘要**：{out_str}")

                elements.append({
                    "tag": "markdown",
                    "content": "\n".join(lines_md)
                })
                elements.append({"tag": "hr"})
    elif not displayed_tasks:
        elements.append({
            "tag": "markdown",
            "content": f"*(暂无{'用户主动' if active_tab == 'user' else '系统后台'}计划任务)*\n可以直接在聊天中发送：`一分钟后提醒我喝水` 或点击上方 **[ ➕ 新建任务 ]** 在卡片表单中一步创建！"
        })
    else:
        page_size = 8
        total_pages = max(1, (len(displayed_tasks) + page_size - 1) // page_size)
        cur_page = max(1, min(int(page or 1), total_pages))
        start_idx = (cur_page - 1) * page_size
        visible_tasks = displayed_tasks[start_idx:start_idx + page_size]

        for t in visible_tasks:
            t_id = t.get('id')
            is_active = bool(t.get('is_active', 1))
            status_icon = "🟢 启用中" if is_active else "🔴 已暂停"
            action_type = t.get('action_type', 'reminder')
            badge = BADGE_MAP.get(action_type, "💬【消息提醒】")
            t_name = t.get('name', '未命名任务')
            t_type = t.get('task_type', 'cron')
            cron_expr = t.get('cron_expr', '')
            schedule_tag = f"⏳ 一次性 (`{cron_expr}`)" if t_type == 'delay' else f"🔁 周期 (`{cron_expr}`)"

            last_run = _format_timestamp(t.get('last_run_at'))
            next_run_str = _format_relative_time(t.get('next_run_at')) if is_active else "已暂停"

            prompt_preview = t.get('prompt', '') or t.get('command', '')
            if len(prompt_preview) > 75:
                prompt_preview = prompt_preview[:75] + "..."

            task_md = f"**{badge} {t_name}**  {status_icon}\n" \
                      f"⏰ **触发设定**：{schedule_tag} · {next_run_str}\n" \
                      f"💡 **执行内容**：{prompt_preview}\n" \
                      f"📊 **任务审计**：累计运行 **{t.get('run_count', 0)}** 次 · 上次：{last_run} (`#{t_id}`)"

            elements.append({
                "tag": "markdown",
                "content": task_md
            })

            # 操作按钮行（携带当前 active_tab 与 page，防止在系统任务页操作后跳回用户任务页）
            task_actions = [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⚡ 立即触发"},
                    "type": "default",
                    "value": {"action": "run_cron_now", "task_id": t_id, "tab": active_tab, "page": cur_page}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⏸️ 暂停" if is_active else "▶️ 启用"},
                    "type": "default",
                    "value": {"action": "toggle_cron_active", "task_id": t_id, "is_active": not is_active, "tab": active_tab, "page": cur_page}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                    "type": "danger",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认删除计划任务"},
                        "text": {"tag": "plain_text", "content": f"确定要删除任务「{t_name[:24]}」吗？此操作不可恢复。"}
                    },
                    "value": {"action": "delete_cron_task", "task_id": t_id, "tab": active_tab, "page": cur_page}
                }
            ]
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": task_actions
            })
            elements.append({"tag": "hr"})

        if total_pages > 1:
            page_btns = []
            if cur_page > 1:
                page_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                    "type": "default",
                    "value": {"action": "cron_page", "tab": active_tab, "page": cur_page - 1},
                })
            if cur_page < total_pages:
                page_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"下一页 ({cur_page}/{total_pages}) ▶️"},
                    "type": "default",
                    "value": {"action": "cron_page", "tab": active_tab, "page": cur_page + 1},
                })
            elements.append({"tag": "action", "layout": "flow", "actions": page_btns})

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
    
    action_buttons = []
    if task_data.get('task_type') != 'delay':
        action_buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔄 再次运行"},
            "type": "primary",
            "value": {"action": "run_cron_now", "task_id": t_id}
        })
    action_buttons.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "⚙️ 管理任务中心"},
        "type": "default",
        "value": {"action": "open_cron_panel"}
    })

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
            "actions": action_buttons
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
    is_delay = task_data.get('task_type') == 'delay'
    task_type = "一次性倒计时" if is_delay else "标准 Cron 表达式"
    prompt = task_data.get('prompt', '')
    
    next_ts = task_data.get('next_run_at', 0)
    next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M:%S') if next_ts > 0 else "计算中..."
    lifecycle_note = (
        "🛡️ *该一次性任务已持久化，触发完成后将自动从任务列表移除（执行记录保留于审计日志）。*"
        if is_delay else
        "🛡️ *该周期任务已持久化存入数据库，中途发生重启亦可自动恢复调度。*"
    )

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
            "content": f"**📝 预设执行 Prompt**\n`{prompt}`\n\n{lifecycle_note}"
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
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🗑️ 撤销任务"},
                    "type": "danger",
                    "value": {"action": "delete_cron_task", "task_id": t_id}
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
