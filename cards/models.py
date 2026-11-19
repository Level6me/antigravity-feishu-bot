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


def build_model_panel(available_models, current_model, health_statuses=None):
    if health_statuses is None:
        try:
            from utils.model_health import get_all_health_statuses
            health_statuses = get_all_health_statuses()
        except Exception:
            health_statuses = {}

    parsed_models = []
    for item in available_models:
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

    has_congested = any(
        isinstance(v, dict) and v.get("status") == "unhealthy"
        for v in (health_statuses or {}).values()
    )
    congested_tip = (
        "\n⚠️ 标有 `🔴 (503拥堵)` 的模型刚检测到服务端算力不足，建议优先选其他模型。"
        if has_congested
        else ""
    )
    
    elements = [
        {
            "tag": "markdown",
            "content": f"🎯 **当前活跃模型**：`{current_model}`\n\n从下方选择一个模型，即可一键热切换：{congested_tip}"
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
            h_info = (health_statuses or {}).get(m_id.lower(), {})
            h_status = h_info.get("status", "unknown") if isinstance(h_info, dict) else "unknown"
            if h_status == "unhealthy":
                display = f"{'✅ ' if is_current else ''}🔴 {m_label} (503拥堵)"
                btn_type = "danger"
            elif h_status == "healthy":
                display = f"{'✅ ' if is_current else ''}🟢 {m_label}"
                btn_type = "primary" if is_current else "default"
            else:
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


def build_model_switch_result_card(
    new_model,
    old_model,
    auto_relayed=False,
    probe_status=None,
    probe_latency_ms=0.0,
    suggested_fallback=None,
):
    model_lower = new_model.lower()
    if "gemini" in model_lower:
        icon, color = "💎", "blue"
    elif "claude" in model_lower:
        icon, color = "🧠", "purple"
    elif "gpt" in model_lower:
        icon, color = "⚡", "green"
    else:
        icon, color = "🔮", "grey"

    title_text = f"{icon} 模型已切换"
    relay_note = (
        "\n\n♻️ **已自动隔离此前 503 异常轨迹并无感接力历史上下文**（无需手动 `/clear`，直接继续对话即可）。"
        if auto_relayed
        else ""
    )

    probe_note = ""
    fallback_actions = []
    if probe_status == "probing":
        probe_note = "\n\n🔍 *正在探活新模型服务端算力状态，稍后自动更新...*"
    elif probe_status == "healthy":
        sec = max(0.1, float(probe_latency_ms or 0.0) / 1000.0)
        probe_note = f"\n\n🟢 **算力探活正常**（首字延迟约 {sec:.1f}s），当前可用。"
    elif probe_status == "unhealthy":
        color = "red"
        title_text = "🔴 模型已切换（检测到 503 无容量）"
        probe_note = (
            f"\n\n🔴 **服务端算力告警**：实测 **`{new_model}`** 此刻返回 **HTTP 503 无容量**！"
            "\n建议立即改选其他可用模型（无需 `/clear`）。"
        )
        if suggested_fallback:
            fallback_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"⚡ 一键改切至 {suggested_fallback}"},
                "type": "primary",
                "value": {"action": "switch_model", "model": suggested_fallback},
            })
        fallback_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🎛️ 打开模型列表 (/model)"},
            "type": "default",
            "value": {"action": "user_choice", "choice": "/model", "label": "打开模型列表"},
        })
    elif probe_status == "timeout":
        color = "orange"
        title_text = "🟠 模型已切换（探活超时预警）"
        probe_note = (
            f"\n\n🟠 **探活超时预警**：**`{new_model}`** 探活请求超过 18s 无首字响应，服务端此刻可能严重拥堵。"
        )
        if suggested_fallback:
            fallback_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"⚡ 一键改切至 {suggested_fallback}"},
                "type": "primary",
                "value": {"action": "switch_model", "model": suggested_fallback},
            })
        fallback_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🎛️ 打开模型列表 (/model)"},
            "type": "default",
            "value": {"action": "user_choice", "choice": "/model", "label": "打开模型列表"},
        })

    elements = [
        {
            "tag": "markdown",
            "content": (
                f"🎉 **模型切换成功！**\n\n{icon} 当前活跃模型已变更为：\n\n"
                f"> **`{new_model}`**\n\n🔄 上一个模型：~~{old_model}~~"
                f"{relay_note}{probe_note}\n\n*接下来的所有对话都将使用新模型进行响应。*"
            ),
        },
    ]
    if fallback_actions:
        elements.append({
            "tag": "action",
            "layout": "bisect" if len(fallback_actions) == 2 else "flow",
            "actions": fallback_actions,
        })
    elements.append({"tag": "hr"})
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"content": title_text, "tag": "plain_text"}
        },
        "elements": elements
    }
