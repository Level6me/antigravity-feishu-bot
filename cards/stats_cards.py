"""Card builders: stats_cards."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def build_quota_card(quota_data):
    elements = [
        {
            "tag": "markdown",
            "content": "⚡ **Google AI Pro 额度大盘** (实时同步自本地 LSP)"
        },
        {
            "tag": "hr"
        }
    ]
    
    if not quota_data or "response" not in quota_data or "groups" not in quota_data["response"]:
        elements.append({
            "tag": "markdown",
            "content": "❌ **无法获取配额数据**\n请检查宿主机上的 Antigravity 登录状态与网络连接。"
        })
    else:
        groups = quota_data["response"]["groups"]
        for g_idx, group in enumerate(groups):
            group_name = group.get("displayName", "未知模型组")
            group_content = f"📁 **{group_name}**\n"
            
            buckets = group.get("buckets", [])
            for bucket in buckets:
                bucket_name = bucket.get("displayName", "未知配额项")
                remaining = bucket.get("remainingFraction", 0.0)
                reset_time = bucket.get("resetTime", "")
                
                percentage = round(remaining * 100, 1)
                progress_emoji = "🟢" if percentage > 50 else ("🟡" if percentage > 20 else "🔴")
                
                filled_blocks = int(percentage / 10)
                bar_str = "■" * filled_blocks + "□" * (10 - filled_blocks)
                
                percentage_str = f"{percentage:.1f}%"
                group_content += f"• **{bucket_name}**:\n`[{bar_str}] {percentage_str:>6}` {progress_emoji}\n"
                if reset_time:
                    try:
                        time_part = reset_time.replace("Z", "")
                        dt = datetime.fromisoformat(time_part.split(".")[0])
                        friendly_reset = dt.strftime('%m-%d %H:%M')
                        group_content += f"(🕒 {friendly_reset})\n"
                    except Exception:
                        group_content += f"(🕒 {reset_time})\n"
                
            elements.append({
                "tag": "markdown",
                "content": group_content.strip()
            })
            
            if g_idx < len(groups) - 1:
                elements.append({
                    "tag": "hr"
                })
            
    elements.append({
        "tag": "hr"
    })
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 刷新额度配额"},
                "type": "primary",
                "value": {"action": "refresh_quota"}
            }
        ]
    })
    
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📊 Google AI Pro 套餐额度查询", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_context_card(stats):
    """
    构建飞书卡片消息：上下文使用率看板 (Context Usage Card)
    """
    model = stats.get("model", "Gemini 3.6 Flash (High)")
    total_tokens = stats.get("total_tokens", 0)
    max_tokens = stats.get("max_tokens", 1000000)
    total_pct = stats.get("total_pct", 0.0)
    
    user_tokens = stats.get("user_tokens", 0)
    user_pct = stats.get("user_pct", 0.0)
    agent_tokens = stats.get("agent_tokens", 0)
    agent_pct = stats.get("agent_pct", 0.0)
    tool_tokens = stats.get("tool_tokens", 0)
    tool_pct = stats.get("tool_pct", 0.0)
    free_tokens = stats.get("free_tokens", max_tokens)
    free_pct = stats.get("free_pct", 100.0)
    conv_id = stats.get("conv_id", "N/A")
    steps_count = stats.get("steps_count", 0)

    def format_k(num):
        if num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}K"
        return str(num)

    def make_progress_bar(pct, length=12):
        filled = int(round((pct / 100.0) * length))
        filled = max(0, min(length, filled))
        return "🟩" * filled + "⬜" * (length - filled)

    bar_str = make_progress_bar(total_pct)

    header_template = "blue"
    if total_pct > 80:
        header_template = "red"
    elif total_pct > 50:
        header_template = "orange"

    elements = [
        {
            "tag": "markdown",
            "content": f"🤖 **当前模型**：`{model}`\n💬 **会话 ID**：`{conv_id}` (`{steps_count}` 步历史步骤)"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"⚡ **总体上下文用量 (Total Usage)**\n`{format_k(total_tokens)} / {format_k(max_tokens)} tokens` (**{total_pct}%**)\n{bar_str}"
        },
        {"tag": "hr"},
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"👤 **User Messages**\n`{format_k(user_tokens)}` tokens (**{user_pct}%**)"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🤖 **Agent Responses**\n`{format_k(agent_tokens)}` tokens (**{agent_pct}%**)"
                        }
                    ]
                }
            ]
        },
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🛠️ **Tool Calls**\n`{format_k(tool_tokens)}` tokens (**{tool_pct}%**)"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🟩 **Free Space**\n`{format_k(free_tokens)}` tokens (**{free_pct}%**)"
                        }
                    ]
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 刷新数据"},
                    "type": "primary",
                    "value": {"action": "user_choice", "choice": "/context", "label": "刷新数据"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧹 清空上下文"},
                    "type": "danger",
                    "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🎛️ 切换模型"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
                }
            ]
        },
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"content": "🧠 上下文容量看板 (Context Usage)", "tag": "plain_text"}
        },
        "elements": elements
    }
