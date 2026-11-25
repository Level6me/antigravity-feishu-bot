"""Interactive Feishu card builder for `/perm` permission & capability diagnostics."""
from __future__ import annotations

from typing import Any, Dict, List

from cards.common import create_footer


def build_perm_check_card(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build Feishu interactive diagnostic card from `check_feishu_permissions()` report."""
    app_id = report.get("app_id") or "未知"
    masked_app_id = f"{app_id[:7]}...{app_id[-4:]}" if len(app_id) > 12 else app_id
    console_url = report.get("console_url") or "https://open.feishu.cn/app"
    elapsed_ms = report.get("elapsed_ms", 0.0)
    err_msg = report.get("error") or ""
    items: List[Dict[str, Any]] = list(report.get("items") or [])
    missing_req: List[str] = list(report.get("missing_required") or [])
    missing_opt: List[str] = list(report.get("missing_optional") or [])

    if err_msg or not report.get("token_ok"):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"content": "🔴 飞书应用凭据与权限自检失败", "tag": "plain_text"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**应用 ID**：`{masked_app_id}`\n\n"
                        f"❌ **错误详情**：{err_msg or '鉴权失败'}\n\n"
                        "请检查服务器 `.env` 中的 `FEISHU_APP_ID` 与 `FEISHU_APP_SECRET` 配置。"
                    ),
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔄 重新检测"},
                            "type": "primary",
                            "value": {"action": "refresh_feishu_perm"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔗 打开飞书开发者后台"},
                            "type": "default",
                            "url": console_url,
                        },
                    ],
                },
                create_footer(),
            ],
        }

    ok_count = sum(1 for x in items if x.get("status") == "ok")
    total_count = len(items)

    if missing_req:
        header_tpl = "red"
        header_title = f"🔴 飞书权限体检：缺失 {len(missing_req)} 项核心权限"
        summary_badge = f"🔴 **检测到核心权限缺失**（已就绪 `{ok_count}/{total_count}` 项 · 并发探测 `{elapsed_ms}ms`）"
    elif missing_opt or ok_count < total_count:
        header_tpl = "orange"
        header_title = f"🟡 飞书权限体检：核心正常（{total_count - ok_count} 项可选增强待开通）"
        summary_badge = f"🟡 **核心对话与多模态正常，部分可选增强处于自动降级态**（已就绪 `{ok_count}/{total_count}` · `{elapsed_ms}ms`）"
    else:
        header_tpl = "green"
        header_title = "✅ 飞书权限体检：全部权限已完备就绪"
        summary_badge = f"🟢 **全部核心与增强权限均已开通**（`{ok_count}/{total_count}` 项全绿 · 并发探测 `{elapsed_ms}ms`）"

    elements: List[Dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": (
                f"{summary_badge}\n"
                f"🔑 **当前应用**：`{masked_app_id}`  ·  🔌 **长连接回调 (`WS`)**：`🟢 在线`"
            ),
        },
        {"tag": "hr"},
    ]

    status_icons = {
        "ok": "🟢 **已开通**",
        "degraded": "🟡 **未开通 (已自动降级)**",
        "missing": "🔴 **核心缺失**",
        "warning": "🟠 **需留意范围配置**",
    }

    lines: List[str] = []
    for item in items:
        st = item.get("status", "ok")
        badge = status_icons.get(st, "⚪ 未知")
        scopes_str = " / ".join(f"`{s}`" for s in (item.get("scopes") or []))
        lat = item.get("latency_ms", 0.0)
        line = f"• **{item.get('title')}** — {badge} *({lat}ms)*\n  权限标识：{scopes_str}"
        if st != "ok" or "热激活" in str(item.get("detail", "")):
            line += f"\n  说明：{item.get('detail', '')}"
        lines.append(line)

    elements.append({
        "tag": "markdown",
        "content": "\n\n".join(lines),
    })

    all_missing = missing_req + [s for s in missing_opt if s not in missing_req]
    if all_missing:
        elements.append({"tag": "hr"})
        scopes_block = "\n".join(all_missing)
        elements.append({
            "tag": "markdown",
            "content": (
                "🛠️ **一键开通指引**：\n"
                "1. 点击下方 **「🔗 打开当前应用权限配置页」** 进入飞书开放平台『权限管理』；\n"
                "2. 在搜索框依次粘贴以下缺失的权限标识勾选开通，并在『版本管理与发布』创建版本发布生效；\n"
                "3. 发布后直接点击下方 **「🔄 重新检测权限」** 即可原地验证并自动热激活（无需重启机器人）：\n"
                f"```text\n{scopes_block}\n```"
            ),
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 重新检测权限"},
                "type": "primary",
                "value": {"action": "refresh_feishu_perm"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔗 打开当前应用权限配置页"},
                "type": "default",
                "url": console_url,
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📊 运行状态 (/status)"},
                "type": "default",
                "value": {"action": "user_choice", "choice": "/status", "label": "运行状态"},
            },
        ],
    })
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_tpl,
            "title": {"content": header_title, "tag": "plain_text"},
        },
        "elements": elements,
    }
