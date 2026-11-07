"""Card builders: response."""

import os
import re
from datetime import datetime
from utils import get_context_usage_stats
from utils.quota import is_quota_error

from cards.common import create_footer

# 飞书卡片/消息体有 ~30KB 上限，markdown 字段预留安全余量
MAX_MARKDOWN_CHARS = 25000
TRUNCATION_NOTICE = "\n\n_(回复过长已截断，完整内容请在工作区查看)_"

def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_project="默认", is_error=False, is_streaming=False, session_data=None, is_quota=None):
    elements = []
    
    # 1. Main Text
    if reply_text:
        content = reply_text
        if len(content) > MAX_MARKDOWN_CHARS:
            content = content[:MAX_MARKDOWN_CHARS] + TRUNCATION_NOTICE
        if is_streaming:
            content += " ⏳" # Blinking cursor effect
        elements.append({
            "tag": "markdown",
            "content": content
        })
        
    # 2. Interactive Options
    if choice_card_data and choice_card_data.get("options"):
        if reply_text:
            elements.append({"tag": "hr"})
            
        actions = []
        markdown_options = []
        is_long_options = any(len(opt) > 6 for opt in choice_card_data["options"])
        
        for i, opt in enumerate(choice_card_data["options"][:10]):
            prefix_match = re.match(r'^([a-zA-Z0-9\u4e00-\u9fa5\s]+)[:：.、]\s*(.*)$', opt)
            
            if prefix_match:
                prefix = prefix_match.group(1).strip()
                rest_text = prefix_match.group(2).strip()
                if len(prefix) == 1 and prefix.encode('utf-8').isalpha():
                    btn_label = f"选项 {prefix}"
                elif len(prefix) <= 6:
                    btn_label = prefix
                else:
                    btn_label = f"选项 {i+1}"
            else:
                btn_label = f"选项 {i+1}"
                rest_text = opt
            
            if not is_long_options:
                btn_label = opt[:50]
                
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn_label},
                "type": "default",
                "value": {"action": "user_choice", "choice": opt, "label": btn_label}
            })
            if is_long_options:
                markdown_options.append(f"- **{btn_label}**: {rest_text}")

        question_text = f"**{choice_card_data.get('question', '请选择：')}**"
        if is_long_options:
            question_text += "\n\n" + "\n".join(markdown_options)
            
        elements.append({
            "tag": "markdown",
            "content": question_text
        })
        
        # 根据选项数量动态设置布局，保证按钮等宽对齐
        layout_mode = "flow"
        if len(actions) == 2:
            layout_mode = "bisect"
        elif len(actions) == 3:
            layout_mode = "trisection"
            
        elements.append({
            "tag": "action",
            "layout": layout_mode,
            "actions": actions
        })

    # 3. Context Info Row
    if not is_error:
        project_name_only = "默认"
        if current_project and current_project not in ["默认", "Default"]:
            project_name_only = os.path.basename(current_project) or current_project
        else:
            project_name_only = current_project or "默认"
            
        from utils import get_context_usage_stats
        stats = get_context_usage_stats(session_data)
        
        def format_k(num):
            if num >= 1000000:
                return f"{num / 1000000:.1f}M"
            elif num >= 1000:
                return f"{num / 1000:.1f}K"
            return str(num)

        raw_free_pct = stats.get("free_pct", 100.0)
        total_tokens = stats.get("total_tokens", 0)
        free_tokens = stats.get("free_tokens", 1000000)
        max_tokens = stats.get("max_tokens", 1000000)

        if total_tokens == 0 or raw_free_pct >= 100.0:
            free_pct_str = "100.0"
        elif raw_free_pct >= 99.9:
            free_pct_str = f"{raw_free_pct:.2f}" if raw_free_pct < 99.99 else "99.99"
        else:
            free_pct_str = f"{raw_free_pct:.1f}"

        context_str = f"🧠 上下文剩余: {free_pct_str}% ({format_k(free_tokens)}/{format_k(max_tokens)})"

        ts_summary = session_data.get("typesafe_audit_summary") if session_data else None
        meta_parts = [f"🤖 模型: {current_model}", f"🗂️ 项目: {project_name_only}", context_str]
        if ts_summary:
            meta_parts.append(ts_summary)

        elements.append({
            "tag": "markdown",
            "content": f"<font color='grey'>{' | '.join(meta_parts)}</font>"
        })

    # 4. Quota Actions
    if is_quota is None:
        is_quota = is_error and is_quota_error(reply_text)
    else:
        is_quota = bool(is_quota)
    if is_quota:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📊 查看实时配额 (/quota)"},
                    "type": "primary",
                    "value": {"action": "user_choice", "choice": "/quota", "label": "查看实时配额"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 一键切换模型 (/model)"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/model", "label": "一键切换模型"}
                }
            ]
        })

    # 5. Standard Footer
    elements.append(create_footer())

    if is_quota:
        header_template = "orange"
        header_title = "⚠️ 模型额度已用尽"
    elif is_error:
        header_template = "red"
        header_title = "❌ 发生错误"
    elif is_streaming:
        header_template = "wathet"
        header_title = "✨ AI 回复中..."
    else:
        header_template = "blue"
        header_title = "✨ AI 回复"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"content": header_title, "tag": "plain_text"}
        },
        "elements": elements
    }


def build_budget_pause_card(
    user_text: str,
    reason: str = "",
    completed_steps: list = None,
    planned_steps: list = None,
    partial_reply: str = "",
    elapsed_seconds: int = 0,
    scope_label: str = "",
    current_model: str = "Default",
    current_project: str = "默认",
    resumed_status: str = "",
):
    """Build an interactive pause/checkpoint card when Attempt 2 also exceeds the step/time/stagnation budget."""
    completed_steps = completed_steps or []
    planned_steps = planned_steps or []
    mins = elapsed_seconds // 60
    secs = elapsed_seconds % 60
    time_str = f"{mins}分{secs}秒" if mins > 0 else f"{secs}秒"

    short_q = (user_text or "").strip().replace("\n", " ")
    if len(short_q) > 120:
        short_q = short_q[:120] + "..."

    total_plan = max(1, len(planned_steps)) if planned_steps else 4
    done_plan = max(1, min(total_plan - 1, len(completed_steps) // 3)) if completed_steps else 1
    pct = max(10, min(90, int((done_plan / total_plan) * 100)))
    filled = max(1, min(9, int(round(pct / 10.0))))
    block_bar = ("■" * filled) + ("□" * (10 - filled))

    elements = [
        {
            "tag": "markdown",
            "content": (
                f"⏸️ **任务阶段性截停保护（已自动重试 1 次仍超出预算）**\n"
                f"📊 **阶段进度**：`{block_bar}` **{pct}%** · 已执行 {len(completed_steps)} 步 · 耗时 {time_str}\n\n"
                f"- **当前指令**：`{short_q}`\n"
                f"- **截停原因**：{reason or '单轮工具调用步数或耗时达到预算上限'}"
                + (f"\n- **范围契约**：{scope_label}" if scope_label else "")
            ),
        }
    ]

    if planned_steps:
        plan_lines = ["**📋 任务规划进度**："]
        for i, st in enumerate(planned_steps[:6], 1):
            done_mark = "✅" if i <= max(1, min(len(planned_steps) - 1, len(completed_steps) // 3)) else "⏳"
            plan_lines.append(f"{done_mark} {i}. {st}")
        elements.append({"tag": "markdown", "content": "\n".join(plan_lines)})

    if completed_steps:
        from cards.indicators import _collapse_consecutive_steps, format_step_item
        collapsed_all = _collapse_consecutive_steps(completed_steps)
        recent_steps = collapsed_all[-6:]
        step_lines = [format_step_item(s, False) for s in recent_steps]
        if len(collapsed_all) > 6:
            step_lines.insert(0, f"*(前序已完成 {len(completed_steps)} 次工具调用已归档至项目记忆胶囊...)*")
        elements.append(
            {
                "tag": "markdown",
                "content": "**🔧 最近已完成操作轨迹**：\n" + "\n".join(step_lines),
            }
        )

    if partial_reply and partial_reply.strip():
        preview = partial_reply.strip()
        if len(preview) > 800:
            preview = preview[:800] + "..."
        elements.append(
            {
                "tag": "markdown",
                "content": f"**💡 阶段性输出摘要**：\n{preview}",
            }
        )

    elements.append({"tag": "hr"})

    if resumed_status == "continued":
        elements.append(
            {
                "tag": "markdown",
                "content": "🚀 **已确认继续执行！系统正在携项目记忆胶囊与已完成进度接力推进剩余步骤...**",
            }
        )
        header_template = "wathet"
        header_title = "🚀 正在接力继续执行..."
    elif resumed_status == "stopped":
        elements.append(
            {
                "tag": "markdown",
                "content": "🛑 **已结束本轮长任务追加执行，当前阶段性成果已归档至项目记忆胶囊。**",
            }
        )
        header_template = "grey"
        header_title = "✅ 阶段性任务已归档"
    else:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "💾 **当前阶段进展与环境事实已自动保存至机器人数据目录的项目记忆胶囊 (`PROJECT_STATE.md`)。**\n"
                    "请确认是否授权继续推进剩余步骤："
                ),
            }
        )
        elements.append(
            {
                "tag": "action",
                "layout": "bisect",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "▶️ 继续执行剩余步骤"},
                        "type": "primary",
                        "value": {"action": "continue_long_task"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🛑 结束并保留当前进度"},
                        "type": "default",
                        "value": {"action": "stop_paused_task"},
                    },
                ],
            }
        )
        header_template = "orange"
        header_title = "⏸️ 任务超预算截停 · 等待确认继续"

    project_name_only = os.path.basename(current_project) if (current_project and current_project not in ("默认", "Default")) else "默认"
    elements.append(
        {
            "tag": "markdown",
            "content": f"<font color='grey'>🤖 模型: {current_model} | 🗂️ 项目: {project_name_only} | 🛡️ 预算停滞看门狗</font>",
        }
    )
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"content": header_title, "tag": "plain_text"},
        },
        "elements": elements,
    }

