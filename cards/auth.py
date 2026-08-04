"""Auth / permission interactive cards."""

from datetime import datetime

from cards.common import create_footer
from utils.auth import SCOPE_TIERS

TIER_LABELS = {
    "basic": "基础权限",
    "dev": "开发权限",
    "full": "完全权限",
}


def _short_id(cid):
    """Return the full chat id (retained for call-site compatibility)."""
    return str(cid or "")

ROLE_LABELS = {
    "admin": "管理员",
    "user": "已授权",
    "pending": "待审批",
    "guest": "未授权",
    "banned": "已拉黑",
}

TIER_NOTES = {
    "basic": "对话、图片/文件解析、笔记偏好",
    "dev": "基础权限 + 项目切换、文件回传",
    "full": "开发权限 + 执行 Shell、查看额度",
}


def _tier_label(scopes):
    """Map a scope list to a human-friendly tier label."""
    scope_set = set(scopes or [])
    if not scope_set:
        return "全部权限"
    for tier in ("full", "dev", "basic"):
        if scope_set >= set(SCOPE_TIERS[tier]):
            return TIER_LABELS[tier]
    return "自定义权限"


def _fmt_ts(ts):
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def build_auth_hint_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey",
            "title": {"content": "🔒 当前会话未授权", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "您好，当前飞书会话尚未获得本机器人的使用权限。\n\n"
                    "如需使用，请发送 **`/auth`** 向管理员申请授权。"
                ),
            },
            create_footer(),
        ],
    }


def build_admin_welcome_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"content": "👑 您已成为管理员", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "首次部署绑定成功，本会话已被设为**最高管理员权限**。\n\n"
                    "- 其他会话发来的 `/auth` 申请会推送到这里，您可以在卡片上直接通过 / 拒绝。\n"
                    "- 使用 **`/user`** 管理已授权用户/群。\n"
                    "- 您的会话不受权限与限流限制。"
                ),
            },
            create_footer(),
        ],
    }


def build_auth_request_card(req):
    chat_type = req.get("chat_type", "p2p")
    chat_type_label = "私聊" if chat_type != "group" else "群聊"
    display_name = req.get("display_name") or "（未获取到名称）"
    if req.get("display_name") is None or not str(req.get("display_name", "")).strip():
        display_name = _short_id(req.get("chat_id", "")) or "（未获取到名称）"
    last_msg = (req.get("last_message") or "").strip() or "（无）"

    elements = [
        {
            "tag": "markdown",
            "content": (
                f"**{chat_type_label}会话**：`{display_name}`\n"
                f"**会话 ID**：`{req.get('chat_id', '')}`\n"
                f"**申请者**：`{req.get('sender_open_id') or '-'}`\n"
                f"**申请时间**：{_fmt_ts(req.get('last_request_at'))}\n"
                f"**累计申请**：{req.get('request_count') or 0} 次\n"
                f"**最近消息**：{last_msg}"
            ),
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "请选择授权档位，或拒绝该申请：",
        },
    ]

    actions = []
    for tier in ("basic", "dev", "full"):
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"✅ {TIER_LABELS[tier]}"},
            "type": "primary",
            "value": {"action": "auth_approve", "chat_id": req.get("chat_id", ""), "tier": tier},
        })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "❌ 拒绝"},
        "type": "default",
        "value": {"action": "auth_deny", "chat_id": req.get("chat_id", "")},
    })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "🚫 拉黑"},
        "type": "danger",
        "value": {"action": "auth_ban", "chat_id": req.get("chat_id", "")},
    })

    elements.append({"tag": "action", "actions": actions})
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "🔔 新的权限申请", "tag": "plain_text"},
        },
        "elements": elements,
    }


def build_auth_result_card(ok: bool, detail: str = ""):
    if ok:
        title = "✅ 授权成功"
        template = "green"
    else:
        title = "⛔ 申请未通过"
        template = "red"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template, "title": {"content": title, "tag": "plain_text"}},
        "elements": [
            {"tag": "markdown", "content": detail or "管理员已处理您的权限申请。"},
            create_footer(),
        ],
    }


def build_user_edit_card(sess):
    """Inline permission-tier editor card (replaces the panel card in place)."""
    display = (sess.get("display_name") or "").strip()
    chat_short = _short_id(sess.get("chat_id", ""))
    if display and display != chat_short and not display.startswith("oc_"):
        who = f"**{display}** (`{chat_short}`)"
    else:
        who = f"**{display or chat_short or '未知会话'}**"

    current_tier = _tier_label(sess.get("scopes") or [])
    elements = [
        {
            "tag": "markdown",
            "content": (
                f"✏️ 正在编辑：{who}\n"
                f"当前权限级别：**{current_tier}**\n\n"
                "请选择新的权限级别："
            ),
        },
        {"tag": "hr"},
    ]

    for tier in ("basic", "dev", "full"):
        elements.append({
            "tag": "markdown",
            "content": f"**{TIER_LABELS[tier]}**：{TIER_NOTES[tier]}",
        })
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"设为{TIER_LABELS[tier]}"},
                "type": "primary",
                "value": {"action": "user_set_tier", "chat_id": sess.get("chat_id", ""), "tier": tier},
            }],
        })

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "取消"},
            "type": "default",
            "value": {"action": "user_edit_cancel"},
        }],
    })
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"content": "✏️ 编辑会话权限", "tag": "plain_text"},
        },
        "elements": elements,
    }


def build_user_panel_card(sessions, page=1, page_size=6):
    total = len(sessions)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    page_sessions = sessions[start:start + page_size]

    elements = [
        {
            "tag": "markdown",
            "content": f"**会话管理面板**（第 {page}/{total_pages} 页，共 {total} 个会话）",
        }
    ]

    for sess in page_sessions:
        role = sess.get("role", "guest")
        chat_type = sess.get("chat_type", "p2p")
        label = ROLE_LABELS.get(role, role)
        display = (sess.get("display_name") or "").strip()
        chat_short = _short_id(sess.get("chat_id", ""))
        # 未解析到姓名时：显示缩写 ID，但不重复显示括号里的同一串 ID
        if display and display != chat_short and not display.startswith("oc_"):
            name_part = f"**{display}** (`{chat_short}`)"
        else:
            name_part = f"**{display or chat_short or '未知会话'}**"
        scopes = sess.get("scopes") or []

        content = (
            f"{name_part}\n"
            f"类型：{'群聊' if chat_type == 'group' else '私聊'} | 角色：**{label}** | 权限级别：**{_tier_label(scopes)}**\n"
            f"更新时间：{_fmt_ts(sess.get('updated_at'))}"
        )

        row_actions = []
        if role == "pending":
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "✅ 通过"},
                "type": "primary",
                "value": {"action": "auth_approve", "chat_id": sess.get("chat_id", ""), "tier": "basic"},
            })
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                "type": "default",
                "value": {"action": "auth_deny", "chat_id": sess.get("chat_id", "")},
            })
        elif role == "user":
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "撤销"},
                "type": "default",
                "value": {"action": "user_action", "op": "revoke", "chat_id": sess.get("chat_id", "")},
            })
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "编辑"},
                "type": "primary",
                "value": {"action": "user_edit", "chat_id": sess.get("chat_id", "")},
            })
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "🚫 拉黑"},
                "type": "danger",
                "value": {"action": "auth_ban", "chat_id": sess.get("chat_id", "")},
            })
        elif role == "banned":
            row_actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "解除拉黑"},
                "type": "default",
                "value": {"action": "user_action", "op": "unban", "chat_id": sess.get("chat_id", "")},
            })

        # 信息与按钮并排：左列三排信息，右列竖排操作按钮
        left_col = {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "elements": [{"tag": "markdown", "content": content}],
        }
        # 列内直接放 button 元素（column 不支持嵌套 action 容器，
        # 使用 action 会导致整个卡片无法渲染/发送失败）
        right_col = {
            "tag": "column",
            "width": "auto",
            "elements": list(row_actions),
        }
        elements.append({
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [left_col, right_col],
        })
        elements.append({"tag": "hr"})

    if not page_sessions:
        elements.append({"tag": "markdown", "content": "📭 暂无会话记录。"})

    page_actions = []
    if page > 1:
        page_actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "◀️ 上一页"},
            "type": "default",
            "value": {"action": "user_page", "page": page - 1},
        })
    if page < total_pages:
        page_actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "下一页 ▶️"},
            "type": "default",
            "value": {"action": "user_page", "page": page + 1},
        })
    if page_actions:
        elements.append({"tag": "action", "actions": page_actions})

    elements.append(create_footer())
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "👥 用户 / 群权限管理", "tag": "plain_text"},
        },
        "elements": elements,
    }


def build_rate_limit_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "yellow",
            "title": {"content": "⏳ 操作过于频繁", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "请求过于频繁，请稍等片刻再试。",
            },
            create_footer(),
        ],
    }
