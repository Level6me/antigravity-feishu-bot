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


def build_capacity_choice_card(
    failed_model: str,
    user_text: str = "",
    candidate_models=None,
    recommended_model: str = "",
    current_project: str = "默认",
    error_detail: str = "",
    resolved_status: str = "",
    resolved_model: str = "",
):
    """Build an interactive card when a model encounters HTTP 503 capacity exhaustion,
    allowing the user to choose whether to retry the current model, switch to a recommended
    model, or pick any specific target model to continue the task.
    """
    err_model = (failed_model or "当前模型").strip()
    if candidate_models is None:
        try:
            from utils.model_health import list_healthy_candidate_models
            candidate_models = list_healthy_candidate_models(err_model, limit=8)
        except Exception:
            candidate_models = []

    if not recommended_model and candidate_models:
        first_cand = candidate_models[0]
        if isinstance(first_cand, dict) and first_cand.get("health") != "unhealthy":
            recommended_model = first_cand.get("id", "")

    task_preview = (user_text or "").strip().replace("\n", " ")
    if len(task_preview) > 120:
        task_preview = task_preview[:117] + "..."

    if resolved_status == "retry_current":
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "🔄 已选择继续使用当前模型重试", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"▶️ **正在使用原模型 `{resolved_model or err_model}` 重新执行任务...**\n\n"
                        + (f"📌 **待执行任务**：`{task_preview}`\n" if task_preview else "")
                        + "♻️ 已自动清理此前 503 失败轨迹并保留对话上下文。"
                    ),
                },
                create_footer(),
            ],
        }

    if resolved_status == "switched":
        target_m = resolved_model or recommended_model or "新模型"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "green",
                "title": {"content": f"⚡ 已切换至 {target_m} 并继续执行", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"✅ **模型已从 ~~`{err_model}`~~ 切换为 `{target_m}`，正在自动继续执行当前任务！**\n\n"
                        + (f"📌 **继续任务**：`{task_preview}`\n" if task_preview else "")
                        + "♻️ 已无感接力近期对话上下文与项目记忆胶囊。"
                    ),
                },
                create_footer(),
            ],
        }

    if resolved_status == "cancelled":
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {"content": "⏹️ 已暂停当前任务重试", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"已取消本次 503 算力拥堵任务的自动续跑（当前模型仍为 `{err_model}`）。\n"
                        "您可以随时重新发送消息，或发送 `/model` 切换其他模型。"
                    ),
                },
                create_footer(),
            ],
        }

    elements = [
        {
            "tag": "markdown",
            "content": (
                f"🚦 **模型服务端算力不足 (HTTP 503 No Capacity)**\n\n"
                f"当前模型 **`{err_model}`** 在云端暂时没有空闲算力容量（**并非**您的个人套餐配额耗尽）。\n"
                + (f"📌 **待执行任务**：`{task_preview}`\n" if task_preview else "")
                + "\n请选择：**继续使用当前模型重试**，或**切换到指定模型立即继续执行任务**（系统将自动无感接力上下文，无需 `/clear`）："
            ),
        },
    ]

    # Primary quick decision buttons
    primary_actions = []
    if recommended_model and recommended_model.lower() != err_model.lower():
        rec_label = recommended_model
        for c in (candidate_models or []):
            if isinstance(c, dict) and c.get("id") == recommended_model:
                rec_label = c.get("label") or recommended_model
                break
        primary_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"⚡ 切换推荐模型 ({rec_label}) 并继续"},
            "type": "primary",
            "value": {
                "action": "capacity_choice_decision",
                "decision": "switch_and_continue",
                "target_model": recommended_model,
                "failed_model": err_model,
            },
        })

    primary_actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": f"🔄 继续当前模型 ({err_model}) 执行"},
        "type": "default" if primary_actions else "primary",
        "value": {
            "action": "capacity_choice_decision",
            "decision": "retry_current",
            "target_model": err_model,
            "failed_model": err_model,
        },
    })
    primary_actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "⏹️ 暂不继续"},
        "type": "default",
        "value": {
            "action": "capacity_choice_decision",
            "decision": "cancel",
            "failed_model": err_model,
        },
    })
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": primary_actions,
    })

    # Candidate model quick buttons & dropdown selector
    if candidate_models:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "🎛️ **或直接点击切换至以下模型并立即继续任务**：",
        })
        model_buttons = []
        select_options = []
        for item in candidate_models:
            if isinstance(item, dict):
                m_id = str(item.get("id") or "").strip()
                m_label = str(item.get("label") or m_id).strip()
                m_health = str(item.get("health") or "unknown")
            else:
                m_id, m_label = _parse_model_entry(str(item))
                m_health = "unknown"
            if not m_id or m_id.lower() == err_model.lower():
                continue

            if m_health == "healthy":
                btn_text = f"🟢 {m_label}"
            elif m_health == "unhealthy":
                btn_text = f"🔴 {m_label} (503拥堵)"
            else:
                btn_text = m_label

            is_rec = bool(recommended_model and m_id.lower() == recommended_model.lower())
            model_buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"⭐ {btn_text}" if is_rec else btn_text},
                "type": "primary" if is_rec else ("danger" if m_health == "unhealthy" else "default"),
                "value": {
                    "action": "capacity_choice_decision",
                    "decision": "switch_and_continue",
                    "target_model": m_id,
                    "failed_model": err_model,
                },
            })
            select_options.append({
                "text": {"tag": "plain_text", "content": f"{btn_text} ({m_id})"},
                "value": m_id,
            })

        if model_buttons:
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": model_buttons[:8],
            })

        if select_options:
            initial_opt = recommended_model if any(o["value"] == recommended_model for o in select_options) else select_options[0]["value"]
            elements.append({
                "tag": "form",
                "name": "capacity_model_select_form",
                "elements": [
                    {
                        "tag": "select_static",
                        "name": "capacity_target_model",
                        "initial_option": initial_opt,
                        "placeholder": {"tag": "plain_text", "content": "从下拉列表选择目标模型"},
                        "options": select_options[:20],
                    },
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "background_style": "default",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "auto",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "name": "capacity_form_submit_btn",
                                        "text": {"tag": "plain_text", "content": "✅ 切换所选模型并继续任务"},
                                        "type": "primary",
                                        "action_type": "form_submit",
                                        "value": {
                                            "action": "capacity_choice_decision",
                                            "decision": "switch_form_and_continue",
                                            "failed_model": err_model,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            })

    elements.append({"tag": "hr"})
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "🚦 模型算力不足 · 请选择切换模型或继续执行", "tag": "plain_text"},
        },
        "elements": elements,
    }
