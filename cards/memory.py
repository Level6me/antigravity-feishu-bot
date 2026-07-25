"""Card builders: memory."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def build_memory_card(memories):
    elements = []
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "➕ 新增偏好"},
                "type": "primary",
                "value": {"action": "prompt_add_memory"}
            }
        ]
    })
    elements.append({"tag": "hr"})

    if not memories:
        elements.append({
            "tag": "markdown",
            "content": "📭 **当前没有记录您的任何个人偏好。**\n\n点击上方「➕ 新增偏好」按钮即可快速记录您的个人习惯与偏好！"
        })
    else:
        elements.append({
            "tag": "markdown",
            "content": "🧠 **您的长时偏好与设定记录**：\n*(点击右侧「忘记」可立即擦除对应偏好)*"
        })
        elements.append({"tag": "hr"})
        
        for idx, m in enumerate(memories):
            columns = [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🔹 {m}"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "忘记"},
                            "type": "danger",
                            "value": {"action": "forget_single_memory", "index": idx}
                        }
                    ]
                }
            ]
            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "columns": columns
            })
            
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {"content": "🧠 偏好记忆管理器", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_note_list_card(notes):
    elements = []
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "➕ 添加笔记"},
                "type": "primary",
                "value": {"action": "prompt_add_note"}
            }
        ]
    })
    elements.append({"tag": "hr"})

    if not notes:
        elements.append({
            "tag": "markdown",
            "content": "📝 **您的记事本目前是空的。**\n\n点击上方「➕ 添加笔记」按钮，或发送 `/note add <内容>` 即可快速记录。"
        })
    else:
        elements.append({
            "tag": "markdown",
            "content": "📝 **您的记事本内容：**"
        })
        for i, note in enumerate(notes):
            parts = note.split(' ', 1)
            title = parts[0]
            preview = parts[1][:40].replace('\n', ' ') + ("..." if len(parts[1]) > 40 else "") if len(parts) > 1 else ""
            
            md_content = f"**{i+1}.** {title}"
            if preview:
                md_content += f"\n<font color='grey'>{preview}</font>"

            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "default",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": md_content
                            }
                        ]
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "vertical_align": "top",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🔍 详情"},
                                "type": "default",
                                "size": "small",
                                "value": {"action": "view_note_detail", "index": i}
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                                "type": "danger",
                                "size": "small",
                                "value": {"action": "delete_note", "index": i}
                            }
                        ]
                    }
                ]
            })
        
        elements.append({
            "tag": "hr"
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧹 清空全部记事本"},
                    "type": "danger",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认清空"},
                        "text": {"tag": "plain_text", "content": "您确定要清空所有笔记吗？此操作不可撤销。"}
                    },
                    "value": {"action": "clear_notes"}
                }
            ]
        })
        
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "📔 机器人记事本", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_global_memory_card(memories):
    elements = [
        {
            "tag": "markdown",
            "content": "**🧠 Antigravity 全局记忆核心看板**\n这是一个自动生成的专属看板，用于展示机器人的长期跨会话记忆。所有的内容都会被持久化存储在宿主机本地。"
        },
        {
            "tag": "hr"
        }
    ]
    
    if not memories:
        elements.append({
            "tag": "markdown",
            "content": "*目前记忆库为空。*"
        })
    else:
        # Reverse to show newest first, limit to last 10 for card size
        recent_memories = list(reversed(memories))[:10]
        for idx, mem in enumerate(recent_memories):
            time_str = mem.get("time", mem.get("timestamp", "未知时间"))
            content = mem.get("memory", mem.get("content", ""))
            elements.append({
                "tag": "markdown",
                "content": f"**🕒 {time_str}**\n{content}"
            })
            if idx < len(recent_memories) - 1:
                elements.append({
                    "tag": "hr"
                })
                
        if len(memories) > 10:
            elements.append({
                "tag": "hr"
            })
            elements.append({
                "tag": "markdown",
                "content": f"*(还有 {len(memories) - 10} 条较早的记忆被折叠...)*"
            })
            
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {"content": "🧠 机器人全局记忆库", "tag": "plain_text"}
        },
        "elements": elements
    }
