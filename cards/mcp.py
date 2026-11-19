"""Interactive Feishu card builder for MCP server management (`/mcp`)."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from cards.common import create_footer
from utils.mcp_manager import MCP_TEMPLATES

_PAGE_SIZE = 5


def _truncate(text: str, limit: int = 110) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def build_mcp_panel_card(
    servers: List[Dict[str, Any]],
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
    """Build an interactive multi-tab Feishu card for managing `agy` MCP servers.

    Tabs:
      - `list`: Configured MCP servers with filter, pagination, detail expand, toggle & remove.
      - `add`: Dual-mode quick-install curated templates + custom Feishu Card Form.
    """
    all_servers = list(servers or [])
    total_cnt = len(all_servers)
    enabled_cnt = sum(1 for s in all_servers if s.get("enabled", True))
    disabled_cnt = total_cnt - enabled_cnt
    installed_names = {str(s.get("name", "")) for s in all_servers}

    is_list_tab = active_tab != "add"

    elements: List[Dict[str, Any]] = []

    # 1. Optional feedback / error banner
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

    # 2. Session reload banner & button after any MCP config mutation
    if needs_session_reload:
        elements.append({
            "tag": "markdown",
            "content": "💡 **生效提示**：MCP 配置已变更，运行中的 ACP 持久会话需要重建连接以加载最新 MCP 工具（不会清空对话历史）。",
        })
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 重建当前会话以加载新 MCP"},
                    "type": "primary",
                    "value": {
                        "action": "mcp_reload_session",
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
                "content": f"{'▶ ' if is_list_tab else ''}📦 已配置服务 ({total_cnt})",
            },
            "type": "primary" if is_list_tab else "default",
            "value": {"action": "mcp_tab", "tab": "list", "filter": filter_status, "page": 1},
        },
        {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'▶ ' if not is_list_tab else ''}➕ 添加服务",
            },
            "type": "primary" if not is_list_tab else "default",
            "value": {"action": "mcp_tab", "tab": "add", "filter": filter_status, "page": page},
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔄 刷新"},
            "type": "default",
            "value": {"action": "mcp_refresh", "tab": active_tab, "filter": filter_status, "page": page},
        },
    ]
    elements.append({"tag": "action", "layout": "flow", "actions": nav_actions})
    elements.append({"tag": "hr"})

    # 4. Tab body
    if is_list_tab:
        # Filter bar
        f_norm = filter_status if filter_status in ("all", "enabled", "disabled") else "all"
        filter_actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'all' else ''}全部 ({total_cnt})"},
                "type": "primary" if f_norm == "all" else "default",
                "value": {"action": "mcp_filter", "filter": "all"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'enabled' else ''}🟢 已启用 ({enabled_cnt})"},
                "type": "primary" if f_norm == "enabled" else "default",
                "value": {"action": "mcp_filter", "filter": "enabled"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'● ' if f_norm == 'disabled' else ''}⚪ 已禁用 ({disabled_cnt})"},
                "type": "primary" if f_norm == "disabled" else "default",
                "value": {"action": "mcp_filter", "filter": "disabled"},
            },
        ]
        elements.append({"tag": "action", "layout": "flow", "actions": filter_actions})

        if f_norm == "enabled":
            filtered = [s for s in all_servers if s.get("enabled", True)]
        elif f_norm == "disabled":
            filtered = [s for s in all_servers if not s.get("enabled", True)]
        else:
            filtered = all_servers

        total_pages = max(1, math.ceil(len(filtered) / _PAGE_SIZE))
        cur_page = max(1, min(int(page or 1), total_pages))
        page_items = filtered[(cur_page - 1) * _PAGE_SIZE : cur_page * _PAGE_SIZE]

        if not page_items:
            empty_hint = (
                "当前尚未配置任何 MCP 服务。\n\n"
                "👉 点击上方 **【➕ 添加服务】** 可一键安装常用官方模板或通过表单添加，也可直接发送：\n"
                "`/mcp add <名称> <命令或URL> [参数...]`"
                if total_cnt == 0
                else "当前筛选条件下没有匹配的 MCP 服务。"
            )
            elements.append({"tag": "markdown", "content": f"ℹ️ *{empty_hint}*"})
        else:
            for idx, srv in enumerate(page_items, start=(cur_page - 1) * _PAGE_SIZE + 1):
                name = str(srv.get("name", ""))
                transport = str(srv.get("transport", "stdio")).lower()
                enabled = bool(srv.get("enabled", True))
                target = str(srv.get("target") or srv.get("url") or srv.get("command") or "(未提供目标)")
                env_keys = list(srv.get("env_keys") or [])
                header_keys = list(srv.get("header_keys") or [])

                t_badge = "🌐 `http`" if transport == "http" else "⚡ `stdio`"
                s_badge = "🟢 已启用" if enabled else "⚪ 已禁用"
                extra_bits: List[str] = []
                if env_keys:
                    extra_bits.append(f"Env: {len(env_keys)} 项")
                if header_keys:
                    extra_bits.append(f"Header: {len(header_keys)} 项")
                extra_str = f"  |  {' · '.join(extra_bits)}" if extra_bits else ""

                md_lines = [
                    f"**{idx}. `{name}`**  {t_badge}  ·  {s_badge}{extra_str}",
                    f"• 目标：`{_truncate(target, 120)}`",
                ]

                is_expanded = bool(expanded_name and expanded_name == name)
                if is_expanded:
                    if srv.get("args"):
                        args_str = " ".join(str(a) for a in srv["args"])
                        md_lines.append(f"• 参数：`{_truncate(args_str, 180)}`")
                    if env_keys:
                        md_lines.append(f"• 环境变量键名：`{', '.join(env_keys)}`（值已隐藏脱敏）")
                    if header_keys:
                        md_lines.append(f"• HTTP Header 键名：`{', '.join(header_keys)}`（值已隐藏脱敏）")
                    md_lines.append(
                        f"• CLI 快捷操作：`/mcp {'disable' if enabled else 'enable'} {name}` | `/mcp remove {name}`"
                    )

                elements.append({"tag": "markdown", "content": "\n".join(md_lines)})

                row_actions: List[Dict[str, Any]] = []
                if is_admin_user:
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏸️ 禁用" if enabled else "▶️ 启用"},
                        "type": "default" if enabled else "primary",
                        "value": {
                            "action": "mcp_toggle",
                            "name": name,
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
                        "action": "mcp_detail",
                        "name": "" if is_expanded else name,
                        "filter": f_norm,
                        "page": cur_page,
                    },
                })
                if is_admin_user:
                    row_actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗑️ 移除"},
                        "type": "danger",
                        "confirm": {
                            "title": {"tag": "plain_text", "content": f"确认移除 MCP 服务「{name}」？"},
                            "text": {
                                "tag": "plain_text",
                                "content": "移除后该服务配置将从 agy 中删除，如有需要可随时重新添加。",
                            },
                        },
                        "value": {
                            "action": "mcp_remove",
                            "name": name,
                            "filter": f_norm,
                            "page": cur_page,
                        },
                    })

                elements.append({"tag": "action", "layout": "flow", "actions": row_actions})
                if idx < (cur_page - 1) * _PAGE_SIZE + len(page_items):
                    elements.append({"tag": "hr"})

        # Pagination + quick reload footer action
        bottom_actions: List[Dict[str, Any]] = []
        if total_pages > 1:
            if cur_page > 1:
                bottom_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "‹ 上一页"},
                    "type": "default",
                    "value": {"action": "mcp_page", "filter": f_norm, "page": cur_page - 1},
                })
            if cur_page < total_pages:
                bottom_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页 ›"},
                    "type": "default",
                    "value": {"action": "mcp_page", "filter": f_norm, "page": cur_page + 1},
                })
        if is_admin_user and total_cnt > 0 and not needs_session_reload:
            bottom_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 重建会话加载 MCP"},
                "type": "default",
                "value": {"action": "mcp_reload_session", "tab": "list", "filter": f_norm, "page": cur_page},
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
        # TAB 2: DUAL-MODE ADD (Curated Templates + Custom Form)
        ws_preview = workspace_dir or "当前工作区"
        elements.append({
            "tag": "markdown",
            "content": "### ⚡ 模式一：常用官方 MCP 模板（一键安装）",
        })
        for tpl in MCP_TEMPLATES:
            t_name = tpl["name"]
            already = t_name in installed_names
            badge = " ✅ 已配置" if already else ""
            desc = tpl["description"]
            if t_name == "filesystem":
                desc = f"{desc}（目录：`{_truncate(ws_preview, 45)}`）"
            elements.append({
                "tag": "markdown",
                "content": f"**{tpl['title']}**{badge}\n{desc}",
            })
            if is_admin_user:
                elements.append({
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "🔄 重新应用模板" if already else "➕ 一键安装",
                            },
                            "type": "default" if already else "primary",
                            "value": {"action": "mcp_quick_add", "template_id": tpl["id"]},
                        }
                    ],
                })

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "### 🛠️ 模式二：自定义添加 MCP 服务（表单提交）",
        })

        if is_admin_user:
            elements.append({
                "tag": "form",
                "name": "mcp_add_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "mcp_name",
                        "required": True,
                        "width": "fill",
                        "max_length": 64,
                        "label": {"tag": "plain_text", "content": "服务名称 *"},
                        "placeholder": {"tag": "plain_text", "content": "如 github / my-api（仅字母数字._-）"},
                    },
                    {
                        "tag": "select_static",
                        "name": "mcp_transport",
                        "initial_option": "auto",
                        "placeholder": {"tag": "plain_text", "content": "传输协议类型"},
                        "options": [
                            {"text": {"tag": "plain_text", "content": "自动识别（推荐：URL 走 http，其余走 stdio）"}, "value": "auto"},
                            {"text": {"tag": "plain_text", "content": "stdio（本地可执行命令，如 npx / uvx / docker）"}, "value": "stdio"},
                            {"text": {"tag": "plain_text", "content": "http（远程 MCP HTTP/SSE 端点）"}, "value": "http"},
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "mcp_target",
                        "required": True,
                        "width": "fill",
                        "max_length": 400,
                        "label": {"tag": "plain_text", "content": "启动命令或 URL *"},
                        "placeholder": {"tag": "plain_text", "content": "stdio 如: npx -y @pkg/mcp  |  http 如: https://example.com/mcp"},
                    },
                    {
                        "tag": "input",
                        "name": "mcp_args",
                        "required": False,
                        "width": "fill",
                        "max_length": 400,
                        "label": {"tag": "plain_text", "content": "额外参数（可选，仅 stdio）"},
                        "placeholder": {"tag": "plain_text", "content": "空格分隔，如: /path/to/dir --read-only"},
                    },
                    {
                        "tag": "input",
                        "name": "mcp_kv",
                        "required": False,
                        "width": "fill",
                        "max_length": 800,
                        "label": {"tag": "plain_text", "content": "环境变量或 Header（可选，不回显明文）"},
                        "placeholder": {"tag": "plain_text", "content": "每行一项：stdio 填 KEY=VALUE，http 填 Authorization: Bearer xxx"},
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
                                        "name": "mcp_submit_btn",
                                        "text": {"tag": "plain_text", "content": "✅ 添加 / 更新服务"},
                                        "type": "primary",
                                        "action_type": "form_submit",
                                        "value": {"action": "mcp_add_submit"},
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "auto",
                                "elements": [
                                    {
                                        "tag": "button",
                                        "name": "mcp_reset_btn",
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
        else:
            elements.append({
                "tag": "markdown",
                "content": "🔒 *仅管理员可添加或修改 MCP 服务配置。*",
            })

        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "CLI 快捷方式：/mcp add <名称> <命令或URL> [参数...]  |  /mcp add --header \"Authorization: Bearer ...\" api https://...",
                }
            ],
        })

    # 5. Footer
    perm_note = "管理员模式 · 支持启停/增删" if is_admin_user else "只读视图 · 启停/增删仅管理员可用"
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"🧩 MCP 控制面板 | {perm_note}"}],
    })
    elements.append(create_footer())

    subtitle = f"共 {total_cnt} 个 · 🟢 {enabled_cnt} 已启用 · ⚪ {disabled_cnt} 已禁用"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🧩 MCP 服务管理中心"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "indigo" if is_list_tab else "blue",
        },
        "elements": elements,
    }
