"""Card builders: models."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def _parse_model_entry(entry_str):
    entry_str = entry_str.strip()
    if not entry_str:
        return "", ""
        
    parts = entry_str.split(maxsplit=1)
    if len(parts) == 2:
        model_id, label = parts[0].strip(), parts[1].strip()
        if model_id.islower() or "-" in model_id:
            return model_id, label
            
    match = re.match(r'^([a-z0-9\.\_\-]+?)([A-Z].*)$', entry_str)
    if match:
        return match.group(1).strip(), match.group(2).strip()
        
    return entry_str, entry_str


def build_model_panel(available_models, current_model):
    parsed_models = []
    for item in available_models[:12]:
        m_id, m_label = _parse_model_entry(item)
        if m_id:
            parsed_models.append((m_id, m_label))

    model_groups = {}
    for m_id, m_label in parsed_models:
        lower = m_id.lower()
        if "gemini" in lower:
            group = "gemini"
        elif "claude" in lower:
            group = "claude"
        elif "gpt" in lower:
            group = "gpt"
        else:
            group = "other"
        model_groups.setdefault(group, []).append((m_id, m_label))
    
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
        for m_id, m_label in models:
            is_current = (m_id == current_model or m_label == current_model or m_id.lower() == current_model.lower())
            display = f"✅ {m_label}" if is_current else m_label
            btn_type = "primary" if is_current else "default"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": display},
                "type": btn_type,
                "value": {"action": "switch_model", "model": m_id}
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
