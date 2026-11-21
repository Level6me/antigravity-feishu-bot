"""Interactive Feishu card builder for Antigravity CLI native plugin management (`/agyplugin`)."""
from __future__ import annotations

import math
from typing import Any, Dict, List

from cards.common import create_footer

_PAGE_SIZE = 5


def _truncate(text: str, limit: int = 120) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _format_scope_badge(scope: str) -> str:
    s = (scope or "global").lower()
    if s == "workspace":
        return "📁 `工作区`"
    if s == "builtin":
        return "🏛️ `内置`"
    return "🌐 `全局`"


def build_agy_plugin_panel_card(
    plugins: List[Dict[str, Any]],
    *,
    active_tab: str = "list",
    filter_status: str = "all",
    page: int = 1,
    expanded_name: str = "",
    notice: str = "",
    notice_level: str = "info",
    needs_session_reload: bool = False,
    is_admin_user: bool = True,
    workspace_dir: str = "",
    error_msg: str = "",
) -> Dict[str, Any]:
    """Build an interactive multi-tab Feishu card for managing `agy` native plugins.

    Tabs:
      - `list`: Installed plugins (workspace / global / builtin) with filter, detail, validate, toggle, uninstall.
      - `install`: Quick import (`claude` / `gemini`) + custom install/validate Feishu Card Form.
    """
    all_plugins = list(plugins or [])
    total_cnt = len(all_plugins)
    enabled_cnt = sum(1 for p in all_plugins if p.get("enabled", True))
    disabled_cnt = total_cnt - enabled_cnt

    is_list_tab = active_tab != "install"
    elements: List[Dict[str, Any]] = []

    # 1. Feedback / Error banner
    if error_msg:
        elements.append({
            "tag": "markdown",
            "content": f"⚠️ **查询异常**：{error_msg}",
        })
    elif notice:
        icon = {"success": "✅", "warning": "⚠️", "error": "❌"}.get(notice_level, "ℹ️")
        elements.append({
            "tag": "markdown",
            "content": f"{icon} {notice}",
        })

    # 2. Session reload banner after mutations
    if needs_session_reload:
        elements.append({
            "tag": "markdown",
            "content": "💡 **生效提示**：原生插件状态已变更，点击下方按钮可重建当前 ACP 会话以立即加载最新插件捆绑包（保留对话历史）。",
        })
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 重建当前会话以应用插件变更"},
                    "type": "primary",
                    "value": {
                        "action": "agy_plugin_reload_session",
                        "tab": active_tab,
                        "filter": filter_status,
                        "page": page,
                    },
                }
            ],
        })

    if error_msg or notice or needs_session_reload:
        elements.append({"tag": "hr"})

    # 3. Top navigation bar
    nav_actions: List[Dict[str, Any]] = [
        {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'▶ ' if is_list_tab else ''}📦 已安装插件 ({total_cnt})",
            },
            "type": "primary" if is_list_tab else "default",
            "value": {"action": "agy_plugin_tab", "tab": "list", "filter": filter_status, "page": 1},
        },
        {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'▶ ' if not is_list_tab else ''}➕ 安装 / 导入与校验",
            },
            "type": "primary" if not is_list_tab else "default",
            "value": {"action": "agy_plugin_tab", "tab": "install", "filter": filter_status, "page": page},
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔄 刷新"},
            "type": "default",
            "value": {"action": "agy_plugin_refresh", "tab": active_tab, "filter": filter_status, "page": page},
        },
    ]
    elements.append({"tag": "action", "layout": "flow", "actions": nav_actions})
    elements.append({"tag": "hr"})

    # 4. Tab content
    if is_list_tab:
        f_norm = filter_status if filter_status in ("all", "enabled", "disabled") else "all"
        filter_actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'all' else ''}全部 ({total_cnt})"},
                "type": "primary" if f_norm == "all" else "default",
                "value": {"action": "agy_plugin_filter", "filter": "all"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'enabled' else ''}🟢 已启用 ({enabled_cnt})"},
                "type": "primary" if f_norm == "enabled" else "default",
                "value": {"action": "agy_plugin_filter", "filter": "enabled"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'disabled' else ''}⚪ 已禁用 ({disabled_cnt})"},
                "type": "primary" if f_norm == "disabled" else "default",
                "value": {"action": "agy_plugin_filter", "filter": "disabled"},
            },
        ]
        elements.append({"tag": "action", "layout": "flow", "actions": filter_actions})

        if f_norm == "enabled":
            filtered = [p for p in all_plugins if p.get("enabled", True)]
        elif f_norm == "disabled":
            filtered = [p for p in all_plugins if not p.get("enabled", True)]
        else:
            filtered = all_plugins

        total_pages = max(1, math.ceil(len(filtered) / _PAGE_SIZE))
        cur_page = max(1, min(int(page or 1), total_pages))
        page_items = filtered[(cur_page - 1) * _PAGE_SIZE : cur_page * _PAGE_SIZE]

        if not page_items:
            empty_hint = (
                "当前尚未发现已安装的 `agy` 原生插件。\n\n"
                "👉 点击上方 **【➕ 安装 / 导入与校验】** 可一键从 Claude / Gemini 导入或安装插件，也可发送：\n"
                "`/agyplugin install <目录路径或GitHub地址>`"
                if total_cnt == 0
                else "当前筛选条件下没有匹配的原生插件。"
            )
            elements.append({"tag": "markdown", "content": f"ℹ️ *{empty_hint}*"})
        else:
            for idx, item in enumerate(page_items, start=(cur_page - 1) * _PAGE_SIZE + 1):
                name = str(item.get("name") or "")
                display_name = str(item.get("display_name") or name)
                dir_name = str(item.get("dir_name") or name)
                version = str(item.get("version") or "-")
                scope = str(item.get("scope") or "global")
                enabled = bool(item.get("enabled", True))
                desc = str(item.get("description") or "").strip()
                p_path = str(item.get("path") or "").strip()
                comps = item.get("components") if isinstance(item.get("components"), dict) else {}

                scope_badge = _format_scope_badge(scope)
                status_badge = "🟢 已启用" if enabled else "⚪ 已禁用"
                ver_tag = f" `v{version}`" if version and version != "-" else ""
                title_part = f"**{idx}. {display_name}** (`{name}`{ver_tag})" if display_name != name else f"**{idx}. `{name}`**{ver_tag}"

                comp_badges: List[str] = []
                for ck, clabel in (
                    ("skills", "Skills"),
                    ("rules", "Rules"),
                    ("mcpServers", "MCP"),
                    ("agents", "Agents"),
                    ("hooks", "Hooks"),
                    ("sidecars", "Sidecars"),
                ):
                    c_val = int(comps.get(ck) or 0)
                    if c_val > 0:
                        comp_badges.append(f"{clabel}: {c_val}")
                comp_str = f"  |  {' · '.join(comp_badges)}" if comp_badges else ""

                md_lines = [f"{title_part}  {scope_badge} · {status_badge}{comp_str}"]
                if desc:
                    md_lines.append(f"• 说明：{_truncate(desc, 140)}")

                is_expanded = bool(expanded_name and expanded_name == name)
                if is_expanded:
                    if p_path:
                        md_lines.append(f"• 路径：`{_truncate(p_path, 140)}`")
                    if item.get("skill_names"):
                        md_lines.append(f"• 包含 Skills：`{', '.join(item['skill_names'][:12])}`")
                    if item.get("mcp_names"):
                        md_lines.append(f"• 包含 MCP：`{', '.join(item['mcp_names'][:10])}`")
                    if item.get("rule_files"):
                        md_lines.append(f"• 包含 Rules：`{', '.join(item['rule_files'][:8])}`")
                    if item.get("suggested_prompts"):
                        prompts_preview = " / ".join(f"“{_truncate(sp, 35)}”" for sp in item["suggested_prompts"][:2])
                        md_lines.append(f"• 推荐示例：{prompts_preview}")
                    md_lines.append(
                        f"• CLI 快捷操作：`/agyplugin {'disable' if enabled else 'enable'} {dir_name}` | `/agyplugin validate {p_path or dir_name}`"
                    )

                elements.append({"tag": "markdown", "content": "\n".join(md_lines)})

                row_actions: List[Dict[str, Any]] = []
                if is_admin_user:
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏸️ 禁用" if enabled else "▶️ 启用"},
                        "type": "default" if enabled else "primary",
                        "value": {
                            "action": "agy_plugin_toggle",
                            "name": name,
                            "dir_name": dir_name,
                            "enable": not enabled,
                            "filter": f_norm,
                            "page": cur_page,
                        },
                    })
                row_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔼 收起详情" if is_expanded else "🔍 详情"},
                    "type": "default",
                    "value": {
                        "action": "agy_plugin_detail",
                        "name": "" if is_expanded else name,
                        "filter": f_norm,
                        "page": cur_page,
                    },
                })
                if is_admin_user and (p_path or dir_name):
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🩺 校验"},
                        "type": "default",
                        "value": {
                            "action": "agy_plugin_validate",
                            "target": p_path or dir_name,
                            "filter": f_norm,
                            "page": cur_page,
                        },
                    })
                if is_admin_user and scope != "builtin":
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗑️ 卸载"},
                        "type": "danger",
                        "confirm": {
                            "title": {"tag": "plain_text", "content": f"确认卸载原生插件「{name}」？"},
                            "text": {
                                "tag": "plain_text",
                                "content": "卸载后该插件捆绑的 Skills、Rules 与 MCP 将不再加载。",
                            },
                        },
                        "value": {
                            "action": "agy_plugin_uninstall",
                            "name": dir_name or name,
                            "path": p_path,
                            "scope": scope,
                            "filter": f_norm,
                            "page": cur_page,
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
                    "value": {"action": "agy_plugin_page", "filter": f_norm, "page": cur_page - 1},
                })
            if cur_page < total_pages:
                bottom_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页 ›"},
                    "type": "default",
                    "value": {"action": "agy_plugin_page", "filter": f_norm, "page": cur_page + 1},
                })
        if is_admin_user and total_cnt > 0 and not needs_session_reload:
            bottom_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 重建会话加载插件"},
                "type": "default",
                "value": {"action": "agy_plugin_reload_session", "tab": "list", "filter": f_norm, "page": cur_page},
            })
        if bottom_actions:
            elements.append({"tag": "hr"})
            if total_pages > 1:
                elements.append({
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"第 {cur_page} / {total_pages} 页 · 共 {len(filtered)} 项"}],
                })
            elements.append({"tag": "action", "layout": "flow", "actions": bottom_actions})

    else:
        # TAB 2: INSTALL / IMPORT / VALIDATE
        ws_preview = workspace_dir or "当前工作区"
        elements.append({
            "tag": "markdown",
            "content": (
                "### ⚡ 模式一：一键导入或校验工作区插件\n"
                "支持从本地 **Claude Code** 或 **Gemini CLI** 自动迁移扩展插件，或直接校验当前工作区插件结构："
            ),
        })
        if is_admin_user:
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📥 从 Claude Code 导入插件"},
                        "type": "primary",
                        "value": {"action": "agy_plugin_import", "source": "claude"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📥 从 Gemini CLI 导入插件"},
                        "type": "default",
                        "value": {"action": "agy_plugin_import", "source": "gemini"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🩺 校验当前工作区"},
                        "type": "default",
                        "value": {"action": "agy_plugin_validate", "target": ws_preview},
                    },
                ],
            })
        else:
            elements.append({
                "tag": "markdown",
                "content": "🔒 *仅管理员可执行插件导入、安装与校验操作。*",
            })

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "### 🛠️ 模式二：安装或校验指定插件（表单提交）",
        })

        if is_admin_user:
            elements.append({
                "tag": "form",
                "name": "agy_plugin_form",
                "elements": [
                    {
                        "tag": "select_static",
                        "name": "plugin_op",
                        "initial_option": "install",
                        "placeholder": {"tag": "plain_text", "content": "选择执行操作"},
                        "options": [
                            {"text": {"tag": "plain_text", "content": "➕ 安装插件 (agy plugin install <目录/GitHub URL/名称>)"}, "value": "install"},
                            {"text": {"tag": "plain_text", "content": "🩺 校验插件结构 (agy plugin validate <目录/URL/.zip>)"}, "value": "validate"},
                            {"text": {"tag": "plain_text", "content": "🩺 严格校验插件 (agy plugin validate --strict)"}, "value": "validate_strict"},
                            {"text": {"tag": "plain_text", "content": "📥 从自定义路径导入 (agy plugin import <路径>)"}, "value": "import"},
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "plugin_target",
                        "required": True,
                        "width": "fill",
                        "max_length": 400,
                        "label": {"tag": "plain_text", "content": "插件目录路径、GitHub 仓库 URL 或插件名 *"},
                        "placeholder": {"tag": "plain_text", "content": "如 /path/to/plugin 或 https://github.com/owner/repo"},
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
                                        "name": "agy_plugin_submit_btn",
                                        "text": {"tag": "plain_text", "content": "✅ 提交执行"},
                                        "type": "primary",
                                        "action_type": "form_submit",
                                        "value": {"action": "agy_plugin_form_submit"},
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "auto",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "name": "agy_plugin_reset_btn",
                                        "text": {"tag": "plain_text", "content": "重置表单"},
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
                    "content": "CLI 快捷方式：/agyplugin list | /agyplugin install <目标> | /agyplugin enable|disable <名称> | /agyplugin validate <目标>",
                }
            ],
        })

    perm_note = "管理员模式 · 支持启停/安装/校验/卸载" if is_admin_user else "只读视图 · 修改操作仅管理员可用"
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"🔌 Antigravity 原生插件中心 | {perm_note}"}],
    })
    elements.append(create_footer())

    subtitle = f"共 {total_cnt} 个 · 🟢 {enabled_cnt} 已启用 · ⚪ {disabled_cnt} 已禁用"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔌 Antigravity 原生插件管理中心"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "indigo" if is_list_tab else "blue",
        },
        "elements": elements,
    }
