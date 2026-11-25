"""Card builders: memory."""

import os
import re
from datetime import datetime

from cards.common import create_footer, sanitize_feishu_markdown

def build_memory_card(memories, project_memory_summary=None, show_add_form: bool = False, page: int = 1, notice: str = ""):
    elements = []
    action_buttons = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔼 收起输入框" if show_add_form else "➕ 新增偏好"},
            "type": "primary" if not show_add_form else "default",
            "value": {"action": "toggle_memory_form", "show": not show_add_form, "page": page}
        }
    ]
    if project_memory_summary:
        action_buttons.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 同步当前会话记忆"},
                "type": "default",
                "value": {"action": "backfill_project_memory"}
            }
        )
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": action_buttons
    })

    if notice:
        elements.append({"tag": "markdown", "content": f"💡 {sanitize_feishu_markdown(notice)}"})

    if show_add_form:
        elements.append({
            "tag": "form",
            "name": "memory_add_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "memory_text",
                    "label": {"tag": "plain_text", "content": "新增个人长期偏好 / AI 行为设定"},
                    "placeholder": {"tag": "plain_text", "content": "例如：写代码默认用 Python 3.12 并附带类型注解"},
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
                                    "name": "mem_add_submit_btn",
                                    "text": {"tag": "plain_text", "content": "✅ 保存偏好"},
                                    "type": "primary",
                                    "action_type": "form_submit",
                                    "value": {"action": "memory_add_submit"},
                                }
                            ],
                        }
                    ],
                },
            ],
        })

    elements.append({"tag": "hr"})

    if project_memory_summary and isinstance(project_memory_summary, dict):
        proj_dir = project_memory_summary.get("project_dir", "默认")
        proj_slug = project_memory_summary.get("project_slug", "default_workspace")
        turns_cnt = project_memory_summary.get("episodic_turns_count", 0)
        facts_cnt = project_memory_summary.get("active_facts_count", 0)
        de_cnt = project_memory_summary.get("dead_ends_count", 0)
        cp_cnt = project_memory_summary.get("current_checkpoints", 0)
        relay_cnt = project_memory_summary.get("relay_lineage_count", 0)
        is_corrupted = project_memory_summary.get("is_corrupted", False)
        capsule_md = (project_memory_summary.get("capsule_markdown") or "").strip()

        status_badge = "⚠️ 摘要受损(待自动接力)" if is_corrupted else ("🟢 健康" if cp_cnt < 3 else "🟡 接近接力阈值")
        elements.append({
            "tag": "markdown",
            "content": (
                f"🗂️ **当前项目长效记忆引擎 (`data/agent_memory`)**\n"
                f"- **活跃项目**：`{proj_dir}` (`{proj_slug}`)\n"
                f"- **FTS5 历史轮次**：**{turns_cnt}** 轮 | **结构化事实**：**{facts_cnt}** 条 | **死胡同禁令**：**{de_cnt}** 条\n"
                f"- **当前会话压缩**：**{cp_cnt}** 次 ({status_badge}) | **跨会话无感接力**：**{relay_cnt}** 次"
            )
        })
        if capsule_md:
            preview_md = sanitize_feishu_markdown(capsule_md)
            if len(preview_md) > 1500:
                preview_md = preview_md[:1500] + "\n_(...更多内容保存在 PROJECT_STATE.md)_"
            elements.append({
                "tag": "markdown",
                "content": f"**📋 项目记忆胶囊 (`PROJECT_STATE.md`) 摘要**：\n{preview_md}"
            })
        elements.append({"tag": "hr"})

    if not memories:
        elements.append({
            "tag": "markdown",
            "content": "📭 **当前没有记录您的任何个人偏好。**\n\n点击上方「➕ 新增偏好」在卡片内直接输入保存即可！"
        })
    else:
        page_size = 10
        total_pages = max(1, (len(memories) + page_size - 1) // page_size)
        cur_page = max(1, min(int(page or 1), total_pages))
        start_idx = (cur_page - 1) * page_size
        page_items = list(enumerate(memories))[start_idx:start_idx + page_size]

        elements.append({
            "tag": "markdown",
            "content": f"🧠 **您的长时偏好与设定记录**（共 {len(memories)} 条）："
        })
        elements.append({"tag": "hr"})

        for idx, m in page_items:
            columns = [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🔹 {sanitize_feishu_markdown(str(m))}"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "size": "small",
                            "text": {"tag": "plain_text", "content": "忘记"},
                            "type": "danger",
                            "confirm": {
                                "title": {"tag": "plain_text", "content": "确认擦除偏好"},
                                "text": {"tag": "plain_text", "content": "确定擦除该条个人偏好设定吗？"}
                            },
                            "value": {"action": "forget_single_memory", "index": idx, "page": cur_page}
                        }
                    ]
                }
            ]
            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "columns": columns
            })

        if total_pages > 1:
            p_btns = []
            if cur_page > 1:
                p_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                    "type": "default",
                    "value": {"action": "memory_page", "page": cur_page - 1},
                })
            if cur_page < total_pages:
                p_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"下一页 ({cur_page}/{total_pages}) ▶️"},
                    "type": "default",
                    "value": {"action": "memory_page", "page": cur_page + 1},
                })
            elements.append({"tag": "action", "layout": "flow", "actions": p_btns})

    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {"content": "🧠 偏好记忆管理器", "tag": "plain_text"}
        },
        "elements": elements
    }


def _summarize_note_item(note_str: str) -> tuple[str, str]:
    """Safely extract short title + preview for both Chinese multi-line notes and space-separated notes."""
    text = (note_str or "").strip()
    if not text:
        return "(空笔记)", ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first_line = lines[0] if lines else text
    rest_lines = " ".join(lines[1:]).strip() if len(lines) > 1 else ""

    if not rest_lines and " " in first_line:
        p0, p1 = first_line.split(" ", 1)
        if len(p0) <= 28:
            title = p0
            rest_lines = p1.strip()
        else:
            title = first_line[:28] + "…"
            rest_lines = first_line[28:].strip()
    elif len(first_line) > 28:
        title = first_line[:28] + "…"
        rest_lines = (first_line[28:] + " " + rest_lines).strip()
    else:
        title = first_line

    preview = rest_lines[:45] + ("..." if len(rest_lines) > 45 else "")
    return title, preview


def build_note_list_card(notes, expanded_idx=None, show_add_form: bool = False, page: int = 1, notice: str = ""):
    elements = []
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔼 收起输入框" if show_add_form else "➕ 添加笔记"},
                "type": "primary" if not show_add_form else "default",
                "value": {"action": "toggle_note_form", "show": not show_add_form, "page": page}
            }
        ]
    })

    if notice:
        elements.append({"tag": "markdown", "content": f"💡 {sanitize_feishu_markdown(notice)}"})

    if show_add_form:
        elements.append({
            "tag": "form",
            "name": "note_add_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "note_content",
                    "label": {"tag": "plain_text", "content": "笔记内容（支持标题与正文）"},
                    "placeholder": {"tag": "plain_text", "content": "输入备忘内容，点击保存原地更新记事本"},
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
                                    "name": "note_add_submit_btn",
                                    "text": {"tag": "plain_text", "content": "✅ 保存笔记"},
                                    "type": "primary",
                                    "action_type": "form_submit",
                                    "value": {"action": "note_add_submit"},
                                }
                            ],
                        }
                    ],
                },
            ],
        })

    elements.append({"tag": "hr"})

    if not notes:
        elements.append({
            "tag": "markdown",
            "content": "📝 **您的记事本目前是空的。**\n\n点击上方「➕ 添加笔记」在卡片内直接输入，或发送 `/note add <内容>` 即可记录。"
        })
    else:
        page_size = 8
        total_pages = max(1, (len(notes) + page_size - 1) // page_size)
        cur_page = max(1, min(int(page or 1), total_pages))
        start_idx = (cur_page - 1) * page_size
        page_notes = list(enumerate(notes))[start_idx:start_idx + page_size]

        elements.append({
            "tag": "markdown",
            "content": f"📝 **您的记事本内容**（共 {len(notes)} 条，第 {cur_page}/{total_pages} 页）："
        })
        for i, note in page_notes:
            is_expanded = (expanded_idx is not None and int(expanded_idx) == i)
            title, preview = _summarize_note_item(str(note))

            md_content = f"**{i+1}.** {title}"
            if is_expanded:
                md_content += f"\n\n> {str(note).replace(chr(10), chr(10) + '> ')}"
            elif preview:
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
                                "content": sanitize_feishu_markdown(md_content)
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
                                "text": {"tag": "plain_text", "content": "🔼 收起" if is_expanded else "🔍 详情"},
                                "type": "primary" if is_expanded else "default",
                                "size": "small",
                                "value": {
                                    "action": "view_note_detail",
                                    "index": -1 if is_expanded else i,
                                    "page": cur_page,
                                }
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🗑️ 删除"},
                                "type": "danger",
                                "size": "small",
                                "confirm": {
                                    "title": {"tag": "plain_text", "content": "确认删除笔记"},
                                    "text": {"tag": "plain_text", "content": f"确定删除第 {i+1} 条笔记吗？"}
                                },
                                "value": {"action": "delete_note", "index": i, "page": cur_page}
                            }
                        ]
                    }
                ]
            })

        if total_pages > 1:
            p_btns = []
            if cur_page > 1:
                p_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                    "type": "default",
                    "value": {"action": "note_page", "page": cur_page - 1},
                })
            if cur_page < total_pages:
                p_btns.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"下一页 ({cur_page}/{total_pages}) ▶️"},
                    "type": "default",
                    "value": {"action": "note_page", "page": cur_page + 1},
                })
            elements.append({"tag": "action", "layout": "flow", "actions": p_btns})

        elements.append({"tag": "hr"})
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
            if isinstance(mem, dict):
                time_str = mem.get("time", mem.get("timestamp", "未知时间"))
                content = mem.get("memory", mem.get("content", ""))
            else:
                time_str = "偏好设定"
                content = str(mem)
            elements.append({
                "tag": "markdown",
                "content": sanitize_feishu_markdown(f"**🕒 {time_str}**\n{content}")
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
