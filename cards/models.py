"""Card builders: models."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def build_model_panel(available_models, current_model):
    model_groups = {}
    for model_name in available_models[:12]:
        lower = model_name.lower()
        if "gemini" in lower:
            group = "gemini"
        elif "claude" in lower:
            group = "claude"
        elif "gpt" in lower:
            group = "gpt"
        else:
            group = "other"
        model_groups.setdefault(group, []).append(model_name)
    
    group_meta = {
        "gemini": {"icon": "💎", "title": "Gemini 系列", "color": "blue"},
        "claude": {"icon": "🧠", "title": "Claude 系列", "color": "purple"},
        "gpt":    {"icon": "⚡", "title": "GPT 系列", "color": "green"},
        "other":  {"icon": "🔮", "title": "其他模型", "color": "grey"},
    }
    
    elements = [
        {
            "tag": "markdown",
            "content": f"🎯 **当前活跃模型**：`{current_model}`\n\n从下方选择一个模型，即可一键热切换："
        }
    ]
    
    for group_key in ["gemini", "claude", "gpt", "other"]:
        models = model_groups.get(group_key, [])
        if not models:
            continue
        meta = group_meta[group_key]
        
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": f"{meta['icon']} **{meta['title']}**"
        })
        
        actions = []
        for model_name in models:
            is_current = (model_name == current_model)
            display = f"✅ {model_name}" if is_current else model_name
            btn_type = "primary" if is_current else "default"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": display},
                "type": btn_type,
                "value": {"action": "switch_model", "model": model_name}
            })
        
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": actions
        })
    
    elements.append({"tag": "hr"})
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "violet",
            "title": {"content": "🎛️ 大模型切换控制台", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_model_switch_result_card(new_model, old_model):
    model_lower = new_model.lower()
    if "gemini" in model_lower:
        icon, color = "💎", "blue"
    elif "claude" in model_lower:
        icon, color = "🧠", "purple"
    elif "gpt" in model_lower:
        icon, color = "⚡", "green"
    else:
        icon, color = "🔮", "grey"
        
    elements = [
        {
            "tag": "markdown",
            "content": f"🎉 **模型切换成功！**\n\n{icon} 当前活跃模型已变更为：\n\n> **`{new_model}`**\n\n🔄 上一个模型：~~{old_model}~~\n\n*接下来的所有对话都将使用新模型进行响应。*"
        },
        {"tag": "hr"},
        create_footer()
    ]
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"content": f"{icon} 模型已切换", "tag": "plain_text"}
        },
        "elements": elements
    }
