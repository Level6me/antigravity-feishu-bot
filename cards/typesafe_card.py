"""Card builders: TypeSafe AI (System One Jev) interactive configuration."""

from typing import Optional, Dict, Any
from cards.common import create_footer


def build_typesafe_config_card(
    api_key: str = "",
    enabled: bool = True,
    model: str = "jev-latest",
    base_url: Optional[str] = None,
    test_result: Optional[Dict[str, Any]] = None
) -> dict:
    """Build high-interactivity configuration card for TypeSafe AI Gateway."""
    has_key = bool(api_key and api_key.strip())
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if (has_key and len(api_key) > 8) else ("已配置" if has_key else "未配置")

    # Determine status template
    if not enabled:
        header_template = "grey"
        status_badge = "⚪ 已停用 (DISABLED)"
        mode_desc = "网关已手动停用，系统当前使用本地规则兜底。"
    elif not has_key:
        header_template = "orange"
        status_badge = "🟠 启发式降级模式 (FALLBACK)"
        mode_desc = "未配置 API Key，已自动激活本地启发式安全与意图规则，所有功能正常可用。"
    else:
        header_template = "green"
        status_badge = "🟢 正常运行中 (ACTIVE)"
        mode_desc = "System One 毫秒级决策引擎已全面接管安全门禁、插件直通与复杂度路由。"

    content_lines = [
        f"**⚡ TypeSafe AI (System One Jev) 决策网关控制中心**\n",
        f"- **网关状态**：{status_badge}",
        f"- **主导模型**：`{model or 'jev-latest'}`",
        f"- **API Key**：`{masked_key}`",
        f"- **网关开关**：`{'已开启 (True)' if enabled else '已关闭 (False)'}`",
    ]
    if base_url:
        content_lines.append(f"- **服务端点**：`{base_url}`")
    else:
        content_lines.append(f"- **服务端点**：`https://api.typesafe.ai/v1` (官方默认)")

    if test_result:
        status_icon = "🟢" if test_result.get("status") == "ok" else "🔴"
        content_lines.append(f"\n**{status_icon} 连通性测试结果：**")
        if "latency_ms" in test_result:
            content_lines.append(f"- 端到端延迟：`{test_result['latency_ms']} ms`")
        content_lines.append(f"- 返回信息：{test_result.get('message', '')}")
    else:
        content_lines.append(f"\n💡 **运行说明**：{mode_desc}")

    elements = [
        {
            "tag": "markdown",
            "content": "\n".join(content_lines)
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚙️ **快捷交互配置** (点击下方按钮即时修改)："
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔑 设置/更新 API Key"},
                    "type": "primary",
                    "value": {"action": "prompt_typesafe_key"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🔴 停用网关" if enabled else "🟢 启用网关"
                    },
                    "type": "default",
                    "value": {"action": "toggle_typesafe_enabled"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⚡ 立即测试连通性 (Ping)"},
                    "type": "primary",
                    "value": {"action": "test_typesafe_ping"}
                }
            ]
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🔵 选定: jev-latest" if model == "jev-latest" else "🤖 切为 jev-latest"
                    },
                    "type": "primary" if model == "jev-latest" else "default",
                    "value": {"action": "set_typesafe_model", "model": "jev-latest"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🔵 选定: jev-1.13.0" if model == "jev-1.13.0" else "🤖 切为 jev-1.13.0"
                    },
                    "type": "primary" if model == "jev-1.13.0" else "default",
                    "value": {"action": "set_typesafe_model", "model": "jev-1.13.0"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🌐 设置 Base URL"},
                    "type": "default",
                    "value": {"action": "prompt_typesafe_base_url"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🗑️ 清除 Key"},
                    "type": "danger",
                    "value": {"action": "clear_typesafe_key"}
                }
            ]
        },
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚡ TypeSafe AI 决策网关控制台"},
            "template": header_template
        },
        "elements": elements
    }
