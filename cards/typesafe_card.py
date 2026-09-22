"""Card builders: TypeSafe AI (System One Jev) interactive configuration."""

from typing import Optional, Dict, Any
from cards.common import create_footer


def build_typesafe_config_card(
    api_key: str = "",
    enabled: bool = True,
    model: str = "jev-latest",
    base_url: Optional[str] = None,
    test_result: Optional[Dict[str, Any]] = None,
    tier: Optional[str] = None
) -> dict:
    """Build minimalist, modern configuration card for TypeSafe AI Gateway."""
    import config
    current_tier = tier or getattr(config, "TYPESAFE_TIER", "gateway") or "gateway"
    has_key = bool(api_key and api_key.strip())
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if (has_key and len(api_key) > 8) else ("已配置" if has_key else "未配置")

    # Tier labels & descriptions
    tier_info = {
        "gateway": {
            "name": "Level 1 · 边缘网关 (Gateway)",
            "desc": "专注前置拦截：破坏性指令防御、插件秒级直达与快慢模态切换",
        },
        "sentry": {
            "name": "Level 2 · 双向守卫 (Sentry)",
            "desc": "包含 L1 全部能力 + 出口安全质检与敏感凭证泄露防护",
        },
        "copilot": {
            "name": "Level 3 · 全链路副驾 (Co-Pilot)",
            "desc": "包含 L2 全部能力 + 工具执行前风险微决策与执行报错反思",
        }
    }
    cur_tier_meta = tier_info.get(current_tier, tier_info["gateway"])

    # Determine status badge without colorful circle emojis
    if not enabled:
        header_template = "grey"
        status_badge = "[DISABLED] 已停用"
        mode_desc = "网关已手动停用，系统当前使用本地规则兜底。"
    elif not has_key:
        header_template = "orange"
        status_badge = "[FALLBACK] 启发式降级模式"
        mode_desc = "未配置 API Key，已自动激活本地启发式安全与意图规则。"
    else:
        header_template = "blue"
        status_badge = "[ACTIVE] 正常运行中"
        mode_desc = "System One 毫秒级决策引擎已全面接管。"

    content_lines = [
        "**TypeSafe AI (System One Jev) 决策网关控制台**\n",
        f"- **网关状态**：`{status_badge}`",
        f"- **介入级别**：`{cur_tier_meta['name']}`",
        f"- **主导模型**：`{model or 'jev-latest'}`",
        f"- **API Key**：`{masked_key}`",
        f"- **网关开关**：`{'已开启' if enabled else '已关闭'}`",
    ]
    if base_url:
        content_lines.append(f"- **服务端点**：`{base_url}`")
    else:
        content_lines.append("- **服务端点**：`https://api.typesafe.ai/v1` (官方默认)")

    content_lines.append(f"\n**级别能力说明**：{cur_tier_meta['desc']}")

    if test_result:
        status_text = "[OK] 连通正常" if test_result.get("status") == "ok" else "[FAIL] 连接异常"
        content_lines.append(f"\n**连通性测试结果 ({status_text})：**")
        if "latency_ms" in test_result:
            content_lines.append(f"- 端到端延迟：`{test_result['latency_ms']} ms`")
        content_lines.append(f"- 返回信息：{test_result.get('message', '')}")
    else:
        content_lines.append(f"**运行状态说明**：{mode_desc}")

    elements = [
        {
            "tag": "markdown",
            "content": "\n".join(content_lines)
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚡ **介入深度级别切换 (三级阶梯模式)**："
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ L1 边缘网关" if current_tier == "gateway" else "切为 L1 边缘网关"
                    },
                    "type": "primary" if current_tier == "gateway" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "gateway"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ L2 双向守卫" if current_tier == "sentry" else "切为 L2 双向守卫"
                    },
                    "type": "primary" if current_tier == "sentry" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "sentry"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ L3 全链路副驾" if current_tier == "copilot" else "切为 L3 全链路副驾"
                    },
                    "type": "primary" if current_tier == "copilot" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "copilot"}
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚙️ **模型与参数配置**："
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ jev-latest" if model == "jev-latest" else "切为 jev-latest"
                    },
                    "type": "primary" if model == "jev-latest" else "default",
                    "value": {"action": "set_typesafe_model", "model": "jev-latest"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ jev-1.13.0" if model == "jev-1.13.0" else "切为 jev-1.13.0"
                    },
                    "type": "primary" if model == "jev-1.13.0" else "default",
                    "value": {"action": "set_typesafe_model", "model": "jev-1.13.0"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "测试连通性 (Ping)"},
                    "type": "default",
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
                    "text": {"tag": "plain_text", "content": "设置 API Key"},
                    "type": "primary",
                    "value": {"action": "prompt_typesafe_key"}
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "停用网关" if enabled else "启用网关"
                    },
                    "type": "default",
                    "value": {"action": "toggle_typesafe_enabled"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "设置 Base URL"},
                    "type": "default",
                    "value": {"action": "prompt_typesafe_base_url"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "清除 Key"},
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
            "title": {"tag": "plain_text", "content": "TypeSafe AI 决策网关控制台"},
            "template": header_template
        },
        "elements": elements
    }
