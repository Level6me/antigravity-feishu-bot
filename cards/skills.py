"""Interactive Feishu card builder for Antigravity CLI Skills management (`/skills`)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from cards.common import create_footer

_PAGE_SIZE = 5


def _truncate(text: str, limit: int = 120) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _scope_badge(scope: str) -> str:
    s = (scope or "global").lower()
    if s == "workspace":
        return "📁 `工作区`"
    if s == "builtin":
        return "🏛️ `内置`"
    return "🌐 `全局`"


def build_skill_panel_card(
    skills: List[Dict[str, Any]],
    *,
    active_tab: str = "list",
    filter_scope: str = "all",
    page: int = 1,
    expanded_name: str = "",
    search_query: str = "",
    pinned_skills: Optional[List[str]] = None,
    notice: str = "",
    notice_level: str = "info",
    needs_session_reload: bool = False,
    is_admin_user: bool = True,
    workspace_dir: str = "",
) -> Dict[str, Any]:
    """Build an interactive multi-tab Feishu card for managing Antigravity CLI Skills (`/skills`).

    Tabs:
      - `list`: Browse/filter/search skills across workspace, global, and builtin scopes;
                pin/unpin skills to the current chat session; view details; enable/disable/remove.
      - `add`: Install skills from GitHub / local path OR author a new `SKILL.md` via Feishu form.
    """
    all_skills = list(skills or [])
    pinned_set = {str(x).strip().lower() for x in (pinned_skills or []) if str(x).strip()}
    pinned_list = [str(x).strip() for x in (pinned_skills or []) if str(x).strip()]

    total_cnt = len(all_skills)
    enabled_cnt = sum(1 for s in all_skills if s.get("enabled", True))
    disabled_cnt = total_cnt - enabled_cnt
    ws_cnt = sum(1 for s in all_skills if s.get("scope") == "workspace")
    global_cnt = sum(1 for s in all_skills if s.get("scope") == "global")
    builtin_cnt = sum(1 for s in all_skills if s.get("scope") == "builtin")
    pinned_cnt = sum(
        1
        for s in all_skills
        if str(s.get("name", "")).lower() in pinned_set or str(s.get("dir_name", "")).lower() in pinned_set
    )

    is_list_tab = active_tab != "add"
    elements: List[Dict[str, Any]] = []

    # 1. Notice banner
    if notice:
        icon = {"success": "✅", "warning": "⚠️", "error": "❌"}.get(notice_level, "ℹ️")
        elements.append({
            "tag": "markdown",
            "content": f"{icon} {notice}",
        })

    # 2. Session reload banner
    if needs_session_reload:
        elements.append({
            "tag": "markdown",
            "content": "💡 **生效提示**：技能库文件已变更，点击下方按钮可重建当前 ACP 持久会话以立即刷新技能列表（保留对话历史）。",
        })
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 重建当前会话以刷新技能库"},
                    "type": "primary",
                    "value": {
                        "action": "skill_reload_session",
                        "tab": active_tab,
                        "filter": filter_scope,
                        "page": page,
                        "q": search_query,
                    },
                }
            ],
        })

    # 3. Active pinned skills banner for current chat session
    if pinned_list:
        pinned_tags = ", ".join(f"`{p}`" for p in pinned_list)
        elements.append({
            "tag": "markdown",
            "content": f"📌 **当前会话已显式挂载技能 ({len(pinned_list)})**：{pinned_tags}\n*(Agent 在处理当前会话任务时将优先遵循上述挂载技能的 `SKILL.md` 规范)*",
        })
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧹 清空当前会话挂载"},
                    "type": "default",
                    "value": {
                        "action": "skill_clear_pins",
                        "filter": filter_scope,
                        "page": page,
                        "q": search_query,
                    },
                }
            ],
        })

    if notice or needs_session_reload or pinned_list:
        elements.append({"tag": "hr"})

    # 4. Top navigation tabs
    nav_actions: List[Dict[str, Any]] = [
        {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'▶ ' if is_list_tab else ''}📚 技能库 ({total_cnt})",
            },
            "type": "primary" if is_list_tab else "default",
            "value": {"action": "skill_tab", "tab": "list", "filter": filter_scope, "page": 1, "q": search_query},
        },
        {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'▶ ' if not is_list_tab else ''}➕ 新建 / 安装技能",
            },
            "type": "primary" if not is_list_tab else "default",
            "value": {"action": "skill_tab", "tab": "add", "filter": filter_scope, "page": page, "q": search_query},
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔄 刷新"},
            "type": "default",
            "value": {"action": "skill_refresh", "tab": active_tab, "filter": filter_scope, "page": page, "q": search_query},
        },
    ]
    elements.append({"tag": "action", "layout": "flow", "actions": nav_actions})
    elements.append({"tag": "hr"})

    if is_list_tab:
        # 5. Scope & status filter bar
        valid_filters = ("all", "pinned", "workspace", "global", "builtin", "disabled")
        f_norm = filter_scope if filter_scope in valid_filters else "all"
        filter_actions: List[Dict[str, Any]] = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'all' else ''}全部 ({total_cnt})"},
                "type": "primary" if f_norm == "all" else "default",
                "value": {"action": "skill_filter", "filter": "all", "q": search_query},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'pinned' else ''}📌 已挂载 ({pinned_cnt})"},
                "type": "primary" if f_norm == "pinned" else "default",
                "value": {"action": "skill_filter", "filter": "pinned", "q": search_query},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'workspace' else ''}📁 工作区 ({ws_cnt})"},
                "type": "primary" if f_norm == "workspace" else "default",
                "value": {"action": "skill_filter", "filter": "workspace", "q": search_query},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'global' else ''}🌐 全局 ({global_cnt})"},
                "type": "primary" if f_norm == "global" else "default",
                "value": {"action": "skill_filter", "filter": "global", "q": search_query},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'builtin' else ''}🏛️ 内置 ({builtin_cnt})"},
                "type": "primary" if f_norm == "builtin" else "default",
                "value": {"action": "skill_filter", "filter": "builtin", "q": search_query},
            },
        ]
        if disabled_cnt > 0 or f_norm == "disabled":
            filter_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'disabled' else ''}⚪ 已禁用 ({disabled_cnt})"},
                "type": "primary" if f_norm == "disabled" else "default",
                "value": {"action": "skill_filter", "filter": "disabled", "q": search_query},
            })
        elements.append({"tag": "action", "layout": "flow", "actions": filter_actions})

        # Keyword search bar form
        search_cols: List[Dict[str, Any]] = [
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    {
                        "tag": "button",
                        "name": "skill_search_btn",
                        "text": {"tag": "plain_text", "content": "🔍 搜索技能"},
                        "type": "primary",
                        "action_type": "form_submit",
                        "value": {"action": "skill_search_submit", "filter": f_norm},
                    }
                ],
            }
        ]
        elements.append({
            "tag": "form",
            "name": "skill_search_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "skill_query",
                    "required": False,
                    "width": "fill",
                    "max_length": 64,
                    "default_value": search_query,
                    "placeholder": {"tag": "plain_text", "content": "输入关键词过滤技能名称或描述（如 python / passport / react / seo）"},
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": search_cols,
                },
            ],
        })
        if search_query:
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"✖️ 清除搜索词「{search_query}」"},
                        "type": "default",
                        "value": {"action": "skill_search_clear", "filter": f_norm},
                    }
                ],
            })
        elements.append({"tag": "hr"})

        # Apply scope/status filter
        if f_norm == "pinned":
            filtered = [
                s
                for s in all_skills
                if str(s.get("name", "")).lower() in pinned_set or str(s.get("dir_name", "")).lower() in pinned_set
            ]
        elif f_norm in ("workspace", "global", "builtin"):
            filtered = [s for s in all_skills if s.get("scope") == f_norm]
        elif f_norm == "disabled":
            filtered = [s for s in all_skills if not s.get("enabled", True)]
        else:
            filtered = all_skills

        total_pages = max(1, math.ceil(len(filtered) / _PAGE_SIZE))
        cur_page = max(1, min(int(page or 1), total_pages))
        page_items = filtered[(cur_page - 1) * _PAGE_SIZE : cur_page * _PAGE_SIZE]

        if not page_items:
            empty_msg = (
                f"未找到与关键词「`{search_query}`」匹配的技能。"
                if search_query
                else "当前筛选分类下暂无技能。点击上方 **【➕ 新建 / 安装技能】** 可创建或从 GitHub 安装技能。"
            )
            elements.append({"tag": "markdown", "content": f"ℹ️ *{empty_msg}*"})
        else:
            for idx, sk in enumerate(page_items, start=(cur_page - 1) * _PAGE_SIZE + 1):
                name = str(sk.get("name") or "")
                dir_name = str(sk.get("dir_name") or name)
                scope = str(sk.get("scope") or "global")
                enabled = bool(sk.get("enabled", True))
                desc = str(sk.get("description") or "(未提供描述)")
                md_path = str(sk.get("path") or "")
                assets = list(sk.get("assets") or [])
                is_pinned = name.lower() in pinned_set or dir_name.lower() in pinned_set

                s_badge = _scope_badge(scope)
                st_badge = "🟢 已启用" if enabled else "⚪ 已禁用"
                pin_badge = " · 📌 `已挂载`" if is_pinned else ""
                asset_str = f"  |  附属目录: `{', '.join(assets)}`" if assets else ""

                md_lines = [
                    f"**{idx}. `{name}`**  {s_badge} · {st_badge}{pin_badge}{asset_str}",
                    f"• 触发说明：{_truncate(desc, 150)}",
                ]

                is_expanded = bool(expanded_name and expanded_name.lower() == name.lower())
                if is_expanded:
                    if len(desc) > 150:
                        md_lines.append(f"• 完整描述：{desc[:500]}")
                    if md_path:
                        md_lines.append(f"• 指令文件：`{md_path}`")
                    if sk.get("body_preview"):
                        preview_clean = str(sk["body_preview"]).replace("```", "'''")
                        md_lines.append(f"• 正文摘要：\n> {_truncate(preview_clean.replace(chr(10), ' '), 220)}")
                    md_lines.append(
                        f"• CLI 快捷操作：`/skills {'unuse' if is_pinned else 'use'} {name}`"
                        + (f" | `/skills {'disable' if enabled else 'enable'} {name}`" if scope != "builtin" else "")
                    )

                elements.append({"tag": "markdown", "content": "\n".join(md_lines)})

                row_actions: List[Dict[str, Any]] = []
                if enabled or is_pinned:
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📍 取消挂载" if is_pinned else "📌 挂载到会话"},
                        "type": "default" if is_pinned else "primary",
                        "value": {
                            "action": "skill_pin_toggle",
                            "name": name,
                            "pin": not is_pinned,
                            "filter": f_norm,
                            "page": cur_page,
                            "q": search_query,
                        },
                    })
                row_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔼 收起详情" if is_expanded else "🔍 详情"},
                    "type": "default",
                    "value": {
                        "action": "skill_detail",
                        "name": "" if is_expanded else name,
                        "filter": f_norm,
                        "page": cur_page,
                        "q": search_query,
                    },
                })
                if is_admin_user and scope != "builtin":
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏸️ 禁用" if enabled else "▶️ 启用"},
                        "type": "default" if enabled else "primary",
                        "value": {
                            "action": "skill_toggle",
                            "name": name,
                            "enable": not enabled,
                            "filter": f_norm,
                            "page": cur_page,
                            "q": search_query,
                        },
                    })
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗑️ 移除"},
                        "type": "danger",
                        "confirm": {
                            "title": {"tag": "plain_text", "content": f"确认移除技能「{name}」？"},
                            "text": {
                                "tag": "plain_text",
                                "content": "移除后该技能目录及其 SKILL.md 将被物理删除。",
                            },
                        },
                        "value": {
                            "action": "skill_remove",
                            "name": name,
                            "filter": f_norm,
                            "page": cur_page,
                            "q": search_query,
                        },
                    })

                elements.append({"tag": "action", "layout": "flow", "actions": row_actions})
                if idx < (cur_page - 1) * _PAGE_SIZE + len(page_items):
                    elements.append({"tag": "hr"})

        bottom_actions: List[Dict[str, Any]] = []
        if total_pages > 1:
            if cur_page > 1:
                bottom_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "‹ 上一页"},
                    "type": "default",
                    "value": {"action": "skill_page", "filter": f_norm, "page": cur_page - 1, "q": search_query},
                })
            if cur_page < total_pages:
                bottom_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页 ›"},
                    "type": "default",
                    "value": {"action": "skill_page", "filter": f_norm, "page": cur_page + 1, "q": search_query},
                })
        if is_admin_user and not needs_session_reload:
            bottom_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 重建会话刷新技能"},
                "type": "default",
                "value": {"action": "skill_reload_session", "tab": "list", "filter": f_norm, "page": cur_page, "q": search_query},
            })
        if bottom_actions:
            elements.append({"tag": "hr"})
            if total_pages > 1:
                elements.append({
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"第 {cur_page} / {total_pages} 页 · 当前匹配 {len(filtered)} 项"}],
                })
            elements.append({"tag": "action", "layout": "flow", "actions": bottom_actions})

    else:
        # TAB 2: INSTALL FROM GITHUB/LOCAL OR CREATE CUSTOM SKILL.MD
        ws_preview = workspace_dir or "当前工作区"
        elements.append({
            "tag": "markdown",
            "content": (
                "### 📥 模式一：从 GitHub 仓库或本地目录安装 Skill\n"
                "支持直接填入包含 `SKILL.md` 的 GitHub 仓库地址（或子目录链接 `https://github.com/owner/repo/tree/main/skills/xxx`）或本地目录路径："
            ),
        })
        if is_admin_user:
            elements.append({
                "tag": "form",
                "name": "skill_install_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "skill_source",
                        "required": True,
                        "width": "fill",
                        "max_length": 400,
                        "label": {"tag": "plain_text", "content": "GitHub 仓库 URL 或本地目录路径 *"},
                        "placeholder": {"tag": "plain_text", "content": "如 https://github.com/owner/skills-repo 或 /path/to/skill-dir"},
                    },
                    {
                        "tag": "select_static",
                        "name": "skill_install_scope",
                        "initial_option": "global",
                        "placeholder": {"tag": "plain_text", "content": "安装目标作用域"},
                        "options": [
                            {"text": {"tag": "plain_text", "content": "🌐 全局技能库（所有项目共享 ~/.gemini/antigravity-cli/skills）"}, "value": "global"},
                            {"text": {"tag": "plain_text", "content": f"📁 当前工作区（仅本项目 {_truncate(ws_preview, 32)}/.agents/skills）"}, "value": "workspace"},
                        ],
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
                                        "name": "skill_install_btn",
                                        "text": {"tag": "plain_text", "content": "📥 一键安装技能"},
                                        "type": "primary",
                                        "action_type": "form_submit",
                                        "value": {"action": "skill_install_submit"},
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "auto",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "name": "skill_install_reset_btn",
                                        "text": {"tag": "plain_text", "content": "重置"},
                                        "type": "default",
                                        "action_type": "form_reset",
                                    }
                                ],
                            },
                        ],
                    },
                ],
            })
        else:
            elements.append({"tag": "markdown", "content": "🔒 *仅管理员可安装或新建技能。*"})

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "### ✍️ 模式二：在线新建 / 更新自定义 Skill (`SKILL.md`)",
        })
        if is_admin_user:
            elements.append({
                "tag": "form",
                "name": "skill_create_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "skill_name",
                        "required": True,
                        "width": "fill",
                        "max_length": 64,
                        "label": {"tag": "plain_text", "content": "技能名称 (name) *"},
                        "placeholder": {"tag": "plain_text", "content": "如 deploy-check 或 stm32-flash（推荐小写加短横线）"},
                    },
                    {
                        "tag": "select_static",
                        "name": "skill_scope",
                        "initial_option": "workspace",
                        "placeholder": {"tag": "plain_text", "content": "保存位置"},
                        "options": [
                            {"text": {"tag": "plain_text", "content": f"📁 当前工作区 ({_truncate(ws_preview, 32)}/.agents/skills)"}, "value": "workspace"},
                            {"text": {"tag": "plain_text", "content": "🌐 全局技能库 (~/.gemini/antigravity-cli/skills)"}, "value": "global"},
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "skill_desc",
                        "required": True,
                        "width": "fill",
                        "max_length": 400,
                        "label": {"tag": "plain_text", "content": "触发描述 (description) *"},
                        "placeholder": {"tag": "plain_text", "content": "说明该技能的作用以及何时应激活使用它"},
                    },
                    {
                        "tag": "input",
                        "name": "skill_body",
                        "required": False,
                        "width": "fill",
                        "max_length": 1500,
                        "label": {"tag": "plain_text", "content": "技能执行步骤与规范正文 (Markdown)"},
                        "placeholder": {"tag": "plain_text", "content": "填写详细操作步骤、校验规则或脚本调用指南"},
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
                                        "name": "skill_create_btn",
                                        "text": {"tag": "plain_text", "content": "✅ 保存并生成 SKILL.md"},
                                        "type": "primary",
                                        "action_type": "form_submit",
                                        "value": {"action": "skill_create_submit"},
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "auto",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "name": "skill_create_reset_btn",
                                        "text": {"tag": "plain_text", "content": "重置"},
                                        "type": "default",
                                        "action_type": "form_reset",
                                    }
                                ],
                            },
                        ],
                    },
                ],
            })

        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "CLI 快捷方式：/skills | /skills search <词> | /skills use <名> | /skills enable|disable <名> | /skills install <URL>",
                }
            ],
        })

    perm_note = "管理员模式 · 支持挂载/启停/新建/安装/移除" if is_admin_user else "普通模式 · 支持浏览与会话挂载"
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"🧠 Antigravity Skills 技能管理中心 | {perm_note}"}],
    })
    elements.append(create_footer())

    subtitle = (
        f"共 {total_cnt} 个 · 🟢 {enabled_cnt} 启用 · 📌 {len(pinned_list)} 挂载 · "
        f"📁 {ws_cnt} 工作区 / 🌐 {global_cnt} 全局 / 🏛️ {builtin_cnt} 内置"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🧠 Antigravity Skills 技能管理中心"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "indigo" if is_list_tab else "blue",
        },
        "elements": elements,
    }
