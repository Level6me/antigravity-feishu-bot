"""Card builders: TypeSafe AI (System One Jev) interactive configuration."""

from typing import Optional, Dict, Any
from cards.common import create_footer


def build_typesafe_config_card(
    api_key: str = "",
    enabled: bool = True,
    model: str = "jev-latest",
    base_url: Optional[str] = None,
    test_result: Optional[Dict[str, Any]] = None,
    tier: Optional[str] = None,
    auto_mode: Optional[bool] = None
) -> dict:
    """Build minimalist, modern configuration card for TypeSafe AI Gateway (Main Console)."""
    import config
    current_tier = tier or getattr(config, "TYPESAFE_TIER", "gateway") or "gateway"
    current_auto_mode = auto_mode if auto_mode is not None else getattr(config, "TYPESAFE_AUTO_MODE", True)
    has_key = bool(api_key and api_key.strip())
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if (has_key and len(api_key) > 8) else ("已配置" if has_key else "未配置")

    tier_names = {
        "gateway": "L1 · 入口决策",
        "sentry": "L2 · 双向决策",
        "copilot": "L3 · 全流程决策"
    }
    cur_tier_display = tier_names.get(current_tier, tier_names["gateway"])

    # Status badges with classic, intuitive emojis
    if not enabled:
        header_template = "grey"
        status_badge = "🔴 已停用 (DISABLED)"
        mode_desc = "网关已手动停用，系统当前使用本地规则兜底。"
    elif not has_key:
        header_template = "orange"
        status_badge = "🟡 启发式降级模式 (FALLBACK)"
        mode_desc = "未配置 API Key，已自动激活本地启发式安全与意图规则。"
    else:
        header_template = "blue"
        status_badge = "🟢 正常运行中 (ACTIVE)"
        mode_desc = "System One 毫秒级决策引擎已全面接管安全门禁、插件直通与复杂度路由。"

    switch_text = "已开启 (True)" if enabled else "已关闭 (False)"
    endpoint_text = f"`{base_url}`" if base_url else "`https://api.typesafe.ai/v1` (官方默认)"
    auto_mode_text = "⚡️ 自动调度 (Low/Med/High)" if current_auto_mode else "锁定手动模式"

    content_lines = [
        "⚡ **System One (Jev) 决策网关控制中心**\n",
        f"• **网关状态**: {status_badge}",
        f"• **决策级别**: ⚡️ {cur_tier_display}",
        f"• **模式自适应**: {auto_mode_text}",
        f"• **主导模型**: `{model or 'jev-latest'}`",
        f"• **API Key**: `{masked_key}`",
        f"• **网关开关**: {switch_text}",
        f"• **服务端点**: {endpoint_text}",
    ]

    if test_result:
        status_text = "🟢 连通正常 (OK)" if test_result.get("status") == "ok" else "🔴 连接异常 (FAIL)"
        content_lines.append(f"\n📡 **连通性测试结果 ({status_text})：**")
        if "latency_ms" in test_result:
            content_lines.append(f"• 端到端延迟：`{test_result['latency_ms']} ms`")
        content_lines.append(f"• 返回信息：{test_result.get('message', '')}")
    else:
        content_lines.append(f"\n💡 **运行说明**: {mode_desc}")

    elements = [
        {
            "tag": "markdown",
            "content": "\n".join(content_lines)
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚡ **核心功能与决策调度**："
        },
        {
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "⚡ 自适应：已开启" if current_auto_mode else "⚡ 自适应：已关闭"
                    },
                    "type": "primary" if current_auto_mode else "default",
                    "value": {"action": "toggle_typesafe_auto_mode"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📡 连通性测试 (Ping)"},
                    "type": "default",
                    "value": {"action": "test_typesafe_ping"}
                }
            ]
        },
        {
            "tag": "action",
            "layout": "bisect",
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
                        "content": "🎛️ 决策级别配置与说明 →"
                    },
                    "type": "default",
                    "value": {"action": "open_typesafe_tier_menu"}
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "⚙️ **系统配置与凭据管理**："
        },
        {
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔑 设置 API Key"},
                    "type": "default",
                    "value": {"action": "prompt_typesafe_key"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🌐 设置 Base URL"},
                    "type": "default",
                    "value": {"action": "prompt_typesafe_base_url"}
                }
            ]
        },
        {
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🛑 停用网关" if enabled else "🟢 启用网关"
                    },
                    "type": "default" if enabled else "primary",
                    "value": {"action": "toggle_typesafe_enabled"}
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
            "title": {"tag": "plain_text", "content": "⚡ System One 决策网关控制台"},
            "template": header_template
        },
        "elements": elements
    }


def build_typesafe_tier_menu_card(tier: Optional[str] = None) -> dict:
    """Build dedicated secondary menu card for TypeSafe AI tier configuration with compact, modern UI."""
    import config
    current_tier = tier or getattr(config, "TYPESAFE_TIER", "gateway") or "gateway"

    tier_names = {
        "gateway": "L1 · 入口决策",
        "sentry": "L2 · 双向决策",
        "copilot": "L3 · 全流程决策"
    }
    cur_tier_display = tier_names.get(current_tier, tier_names["gateway"])

    elements = [
        {
            "tag": "markdown",
            "content": f"⚡️ **当前生效级别**：{cur_tier_display}"
        },
        {"tag": "hr"},
        # --- Level: Gateway ---
        {
            "tag": "markdown",
            "content": (
                "**L1 · 入口决策 (Gateway)**\n"
                "• **核心决策机制**：在用户请求输入端进行意图分类、插件指令快速分流与恶意破坏性请求拦截。\n"
                "• **适用场景**：日常对话交互、低延迟轻量问答与常规指令直达。"
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
                        "content": "当前生效：L1·入口决策" if current_tier == "gateway" else "切换至 L1·入口决策"
                    },
                    "type": "primary" if current_tier == "gateway" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "gateway", "from_sub_menu": True}
                }
            ]
        },
        {"tag": "hr"},
        # --- Level: Sentry ---
        {
            "tag": "markdown",
            "content": (
                "**L2 · 双向决策 (Sentry)**\n"
                "• **核心决策机制**：涵盖入口决策能力，并在模型输出端执行敏感凭据（Token/密钥/密码）防泄露审查。\n"
                "• **适用场景**：代码审查开发、环境配置管理与团队共享协作场景。"
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
                        "content": "当前生效：L2·双向决策" if current_tier == "sentry" else "切换至 L2·双向决策"
                    },
                    "type": "primary" if current_tier == "sentry" else "default",
                    "value": {"action": "set_typesafe_tier", "tier": "sentry", "from_sub_menu": True}
                }
            ]
        },
        {"tag": "hr"},
        # --- Level: Co-Pilot ---
        {
            "tag": "markdown",
            "content": (
                "**L3 · 全流程决策 (Co-Pilot)**\n"
                "• **核心决策机制**：覆盖输入、输出及执行全链路，增加终端命令执行前风险评估阻断与执行报错自愈根因诊断。\n"
                "• **适用场景**：复杂系统运维、长任务排查与自动化高权限脚本执行。"
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
                        "content": "当前生效：L3·全流程决策" if current_tier == "copilot" else "切换至 L3·全流程决策"
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
            "title": {"tag": "plain_text", "content": "⚡ System One · 决策级别配置"},
            "template": "blue"
        },
        "elements": elements
    }


# Standard System One aliases
build_system_one_config_card = build_typesafe_config_card
build_system_one_tier_menu_card = build_typesafe_tier_menu_card


def build_l3_high_risk_confirm_card(
    tool_name: str,
    command: str,
    risk_level: str = "critical_risk",
    reason: str = "",
    chat_id: str = "",
    project_dir: str = "",
    call_id: str = ""
) -> dict:
    """Build interactive confirmation card for L3 high-risk tool execution."""
    level_map = {
        "critical_risk": "🚨 高危破坏级别 (Critical)",
        "high_risk": "⚠️ 较高风险级别 (High)",
        "medium_risk": "⚡ 中度风险级别 (Medium)",
        "low_risk": "ℹ️ 较低风险 (Low)"
    }
    risk_display = level_map.get(risk_level, f"⚠️ {risk_level}")

    clean_cmd = command.strip()
    cmd_preview = clean_cmd if len(clean_cmd) <= 300 else f"{clean_cmd[:300]}..."
    clean_reason = reason.strip() or "Jev 认知模型判定该操作具有破坏性或不确定性风险"
    cwd_display = project_dir if (project_dir and project_dir not in ["默认", "Default"]) else "默认工作区"

    elements = [
        {
            "tag": "markdown",
            "content": (
                "🛡️ **System One (L3 全流程守卫) 高危操作审批**\n\n"
                "检测到当前任务尝试在主机环境中调用具有高风险的系统指令。为了防止误操作或系统破坏，**执行已暂缓**，请您核对并选择操作："
            )
        },
        {
            "tag": "markdown",
            "content": (
                f"• **待执行指令**：\n```bash\n{cmd_preview}\n```\n"
                f"• **风险评级**：`{risk_display}`\n"
                f"• **判定原因**：{clean_reason}\n"
                f"• **执行环境**：`{cwd_display}`\n"
                f"• **调用工具**：`{tool_name}`"
            )
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "⚠️ 授权执行 (Authorize)"
                    },
                    "type": "danger",
                    "value": {
                        "action": "l3_confirm_action",
                        "decision": "approve",
                        "tool": tool_name,
                        "command": clean_cmd,
                        "risk_level": risk_level,
                        "cwd": project_dir,
                        "call_id": call_id,
                        "chat_id": chat_id
                    }
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🚫 拒绝并终止 (Reject)"
                    },
                    "type": "default",
                    "value": {
                        "action": "l3_confirm_action",
                        "decision": "reject",
                        "tool": tool_name,
                        "command": clean_cmd,
                        "risk_level": risk_level,
                        "cwd": project_dir,
                        "call_id": call_id,
                        "chat_id": chat_id
                    }
                }
            ]
        },
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 高危敏感指令执行确认 (L3 安全守卫)"},
            "template": "orange"
        },
        "elements": elements
    }


def build_l3_confirm_result_card(
    status: str,
    command: str,
    operator_id: str = "",
    output: str = "",
    returncode: Optional[int] = None,
    elapsed_seconds: Optional[float] = None
) -> dict:
    """Build result card for L3 high-risk action confirmation state transition."""
    operator_tag = f"<at user_id=\"{operator_id}\"></at>" if operator_id else "管理员"
    cmd_preview = command.strip()
    if len(cmd_preview) > 300:
        cmd_preview = f"{cmd_preview[:300]}..."

    if status == "running":
        header_template = "turquoise"
        title_text = "⏳ 高危指令已授权，正在执行..."
        desc = f"👤 操作人：{operator_tag}\n\n已确认授权执行该高危敏感指令，系统正在受控环境中调度执行，请稍候..."
        out_block = []
    elif status == "rejected":
        header_template = "grey"
        title_text = "🚫 高危指令执行已拒绝 (L3 守卫)"
        desc = f"👤 操作人：{operator_tag}\n\n已拒绝执行该高危敏感指令，后续任务已安全终止，未对主机系统做出任何变更。"
        out_block = []
    elif status == "success":
        header_template = "green"
        title_text = "✅ 高危指令授权执行完毕"
        elapsed_str = f"`{elapsed_seconds:.2f}s`" if elapsed_seconds is not None else "`未知`"
        ret_str = f"`{returncode}`" if returncode is not None else "`0`"
        desc = (
            f"👤 审批人：{operator_tag}\n"
            f"• **执行状态**：成功完成\n"
            f"• **执行耗时**：{elapsed_str}\n"
            f"• **退出状态码**：{ret_str}"
        )
        clean_out = output.strip()
        if len(clean_out) > 2000:
            clean_out = f"{clean_out[:2000]}\n... (日志过长已截断)"
        out_content = clean_out if clean_out else "(命令执行成功，无标准输出)"
        out_block = [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"📋 **执行输出 (Output)**：\n```bash\n{out_content}\n```"
            }
        ]
    else:  # failed
        header_template = "carmine"
        title_text = "❌ 高危指令执行返回异常"
        elapsed_str = f"`{elapsed_seconds:.2f}s`" if elapsed_seconds is not None else "`未知`"
        ret_str = f"`{returncode}`" if returncode is not None else "`非0`"
        desc = (
            f"👤 审批人：{operator_tag}\n"
            f"• **执行状态**：执行失败 / 异常中断\n"
            f"• **执行耗时**：{elapsed_str}\n"
            f"• **退出状态码**：{ret_str}"
        )
        clean_out = output.strip()
        if len(clean_out) > 2000:
            clean_out = f"{clean_out[:2000]}\n... (日志过长已截断)"
        out_content = clean_out if clean_out else "(无详细错误输出)"
        out_block = [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"⚠️ **错误信息 (Error)**：\n```bash\n{out_content}\n```"
            }
        ]

    elements = [
        {
            "tag": "markdown",
            "content": f"• **目标指令**：\n```bash\n{cmd_preview}\n```\n{desc}"
        }
    ]
    elements.extend(out_block)
    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title_text},
            "template": header_template
        },
        "elements": elements
    }


