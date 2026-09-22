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
    """Build minimalist, modern configuration card for TypeSafe AI Gateway (Main Console)."""
    import config
    current_tier = tier or getattr(config, "TYPESAFE_TIER", "gateway") or "gateway"
    has_key = bool(api_key and api_key.strip())
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if (has_key and len(api_key) > 8) else ("已配置" if has_key else "未配置")

    tier_names = {
        "gateway": "Level 1 · 边缘网关 (Gateway)",
        "sentry": "Level 2 · 双向守卫 (Sentry)",
        "copilot": "Level 3 · 全链路副驾 (Co-Pilot)"
    }
    cur_tier_display = tier_names.get(current_tier, tier_names["gateway"])

    # Status badges without colorful circle emojis
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
        mode_desc = "System One 决策网关处于运行状态，负责请求安全与意图分流。"

    content_lines = [
        "**System One 决策网关控制台**\n",
        f"- **网关状态**：`{status_badge}`",
        f"- **治理深度**：`{cur_tier_display}`",
        f"- **主导模型**：`{model or 'jev-latest'}`",
        f"- **API Key**：`{masked_key}`",
        f"- **网关开关**：`{'已开启' if enabled else '已关闭'}`",
    ]
    if base_url:
        content_lines.append(f"- **服务端点**：`{base_url}`")
    else:
        content_lines.append("- **服务端点**：`https://api.typesafe.ai/v1` (官方默认)")

    if test_result:
        status_text = "[OK] 连通正常" if test_result.get("status") == "ok" else "[FAIL] 连接异常"
        content_lines.append(f"\n**连通性测试结果 ({status_text})：**")
        if "latency_ms" in test_result:
            content_lines.append(f"- 端到端延迟：`{test_result['latency_ms']} ms`")
        content_lines.append(f"- 返回信息：{test_result.get('message', '')}")
    else:
        content_lines.append(f"\n**运行说明**：{mode_desc}")

    elements = [
        {
            "tag": "markdown",
            "content": "\n".join(content_lines)
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "🛡️ **治理深度配置**："
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🛡️ 治理深度级别设置 →"
                    },
                    "type": "primary",
                    "value": {"action": "open_typesafe_tier_menu"}
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚙️ **参数与管理操作**："
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
            "title": {"tag": "plain_text", "content": "System One · 决策网关控制台"},
            "template": header_template
        },
        "elements": elements
    }


def build_typesafe_tier_menu_card(tier: Optional[str] = None) -> dict:
    """Build dedicated secondary menu card for TypeSafe AI tier configuration with compact, modern UI."""
    import config
    current_tier = tier or getattr(config, "TYPESAFE_TIER", "gateway") or "gateway"

    tier_names = {
        "gateway": "Level 1 · 边缘网关",
        "sentry": "Level 2 · 智能哨兵",
        "copilot": "Level 3 · 全链路副驾"
    }
    cur_tier_display = tier_names.get(current_tier, tier_names["gateway"])

    elements = [
        {
            "tag": "markdown",
            "content": f"🛡️ **当前生效治理深度**：`{cur_tier_display}`"
        },
        {"tag": "hr"},
        # --- Level 1 ---
        {
            "tag": "markdown",
            "content": (
                "**Level 1 · 边缘网关 (Gateway)**\n"
                "• **核心能力**：前置恶意请求拦截与插件任务快速分流\n"
                "• **适用场景**：日常对话、轻量级问答与低延迟交互"
            )
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "当前生效：L1 边缘网关" if current_tier == "gateway" else "切换至 L1 边缘网关"
                    },
                    "type": "primary" if current_tier == "gateway" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "gateway", "from_sub_menu": True}
                }
            ]
        },
        {"tag": "hr"},
        # --- Level 2 ---
        {
            "tag": "markdown",
            "content": (
                "**Level 2 · 智能哨兵 (Sentry)**\n"
                "• **核心能力**：包含 Level 1 能力，增加输出内容敏感密钥与凭证防泄露审查\n"
                "• **适用场景**：代码审查、常规工程开发与配置管理"
            )
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "当前生效：L2 智能哨兵" if current_tier == "sentry" else "切换至 L2 智能哨兵"
                    },
                    "type": "primary" if current_tier == "sentry" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "sentry", "from_sub_menu": True}
                }
            ]
        },
        {"tag": "hr"},
        # --- Level 3 ---
        {
            "tag": "markdown",
            "content": (
                "**Level 3 · 全链路副驾 (Co-Pilot)**\n"
                "• **核心能力**：包含 Level 2 能力，增加终端命令执行前风险评估与异常根因诊断\n"
                "• **适用场景**：系统运维操作、长任务排查与自动化脚本执行"
            )
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "当前生效：L3 全链路副驾" if current_tier == "copilot" else "切换至 L3 全链路副驾"
                    },
                    "type": "primary" if current_tier == "copilot" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "copilot", "from_sub_menu": True}
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "← 返回主控制台"
                    },
                    "type": "default",
                    "value": {"action": "open_typesafe_main_card"}
                }
            ]
        },
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "System One · 治理深度级别设置"},
            "template": "blue"
        },
        "elements": elements
    }
