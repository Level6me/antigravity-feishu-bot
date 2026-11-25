"""Card builders: system."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def build_no_update_card(current_version):
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"content": "✅ 系统已是最新版本", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**当前运行版本**：`{current_version}`\n\n🎉 经过全网云端探测，本地代码已与云端保持一致。"
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 强制重启重载服务"},
                        "type": "default",
                        "value": {"action": "user_choice", "choice": "/update confirm", "label": "强制重启重载服务"}
                    }
                ]
            },
            create_footer()
        ]
    }


def build_update_card(current_version, latest_version, changelog):
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "🔄 系统 OTA 升级提醒", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**当前版本**：`{current_version}`\n**发现新版本**：`{latest_version}`\n\n**更新日志 (Changelog)**：\n{changelog}\n\n<font color='red'>⚠️ 警告：执行升级将进行强制同步，会覆盖本地所有未提交的代码修改（您的 .env 配置和本地数据库不受影响）。</font>"
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认并执行升级"},
                        "type": "primary",
                        "value": {"action": "user_choice", "choice": "/update confirm", "label": "确认并执行升级"}
                    }
                ]
            },
            create_footer()
        ]
    }


def build_welcome_card():
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"content": "🎉 部署成功！欢迎使用 Antigravity 助手", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "您好！我是您的 **Antigravity 智能编程与系统开发助理**。\n\n当您看到这条消息，说明您的飞书机器人已经**成功部署并激活绑定**！\n\n我可以读取并修改您电脑上的文件、直接执行终端命令、跨网检索知识，还能接收并分析您发送给我的 PDF 文件、语音或截图。\n\n期待与您的合作，让我们开始吧！"
            },
            {
                "tag": "hr"
            },
            {
                "tag": "markdown",
                "content": "💡 **快捷功能推荐** (点击下方按钮立即体验)："
            },
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📁 工作区项目"},
                        "type": "primary",
                        "value": {"action": "user_choice", "choice": "/project", "label": "工作区项目"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🤖 切换模型"},
                        "type": "default",
                        "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🎭 查看帮助"},
                        "type": "default",
                        "value": {"action": "user_choice", "choice": "/help", "label": "查看帮助"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🧹 清空上下文"},
                        "type": "default",
                        "confirm": {
                            "title": {"tag": "plain_text", "content": "确认清空当前对话上下文"},
                            "text": {"tag": "plain_text", "content": "清空后将开启全新对话，确定继续吗？"}
                        },
                        "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                    }
                ]
            },
            create_footer()
        ]
    }


def build_security_warning(blocked_command):
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"content": "⚠️ 安全威胁拦截警告", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"🚨 **高危系统命令执行请求已被安全沙箱拦截！**\n\n您的输入中检测到了包含系统破坏性或高风险的黑名单特征指令，为了保护基座宿主系统的运行安全，已对该请求进行强行拦截与截断。\n\n**拦截的请求特征**：\n> `{blocked_command}`\n\n*(如果您确实有系统维护管理需求，请登录物理终端进行手动执行。)*"
            },
            create_footer()
        ]
    }


def build_acp_permission_card(perm_token: str, params: dict):
    """Build Feishu interactive approval card for ACP `session/request_permission`."""
    tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
    title = str(tool_call.get("title") or params.get("title") or tool_call.get("name") or "工具执行权限请求")
    kind = str(tool_call.get("kind") or "")
    raw_input = tool_call.get("rawInput") or params.get("rawInput") or {}
    if isinstance(raw_input, dict):
        preview_items = []
        for k, v in list(raw_input.items())[:5]:
            v_str = str(v).replace("\n", " ")
            if len(v_str) > 120:
                v_str = v_str[:120] + "..."
            preview_items.append(f"- `{k}`: `{v_str}`")
        preview_md = "\n".join(preview_items) if preview_items else "_无额外参数_"
    else:
        preview_md = f"`{str(raw_input)[:200]}`"

    options = params.get("options") or []
    actions = []
    for opt in options[:4]:
        opt_id = str(opt.get("optionId") or "")
        opt_name = str(opt.get("name") or opt_id or "确认")
        opt_kind = str(opt.get("kind") or "").lower()
        btn_type = "primary" if "allow" in opt_kind else ("danger" if "reject" in opt_kind else "default")
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": opt_name[:24]},
            "type": btn_type,
            "value": {
                "action": "acp_permission_decision",
                "perm_token": perm_token,
                "option_id": opt_id,
                "label": opt_name[:32],
                "title": title[:60],
            },
        })

    if not actions:
        actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "✅ 允许"},
                "type": "primary",
                "value": {"action": "acp_permission_decision", "perm_token": perm_token, "option_id": "allow", "label": "允许", "title": title[:60]},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                "type": "danger",
                "value": {"action": "acp_permission_decision", "perm_token": perm_token, "option_id": "reject", "label": "拒绝", "title": title[:60]},
            },
        ]

    header_sub = f" (`{kind}`)" if kind else ""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "🔐 ACP 交互式权限审批", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**待审批操作**：{title}{header_sub}\n\n**参数详情**：\n{preview_md}\n\n*请在 3 分钟内点击下方按钮做出决定，超时将自动安全拒绝。*",
            },
            {"tag": "action", "layout": "flow", "actions": actions},
            create_footer(),
        ],
    }


def build_acp_permission_result_card(title: str, chosen_label: str):
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"content": "✅ ACP 权限审批已处理", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**操作**：{title}\n**已选择**：**{chosen_label}**\n\n已回传给 Antigravity ACP 会话继续执行。",
            },
            create_footer(),
        ],
    }


_HELP_STATIC_COMMANDS = {
    "/model", "/card", "/menu", "/context", "/memory", "/brain", "/clear",
    "/project", "/adddir", "/add-dir", "/mode", "/note", "/notes", "/cron",
    "/schedule", "/quota", "/status", "/steer", "/plugin", "/plugins",
    "/pluginreload", "/mcp", "/agyplugin", "/skills", "/skill", "/stop", "/s1", "/sys1", "/system1",
    "/ping", "/update", "/help", "/user", "/auth", "/transport", "/perm", "/perms",
}


def build_help_card(is_admin_user: bool = False, plugins=None, active_tab: str = "all"):
    if plugins is None:
        try:
            from plugin_manager import plugin_manager
            plugins = plugin_manager.get_plugin_list()
        except Exception:
            plugins = []

    valid_tabs = {"all", "common", "project", "eco", "admin"}
    tab = active_tab if active_tab in valid_tabs else "all"
    if tab == "admin" and not is_admin_user:
        tab = "all"

    tab_defs = [
        ("all", "📋 全览"),
        ("common", "⚡ 常用与模型"),
        ("project", "📁 工程与调度"),
        ("eco", "🧩 扩展生态"),
    ]
    if is_admin_user:
        tab_defs.append(("admin", "🔐 运维管理"))

    tab_buttons = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": "primary" if tab == t_key else "default",
            "value": {"action": "help_switch_tab", "tab": t_key},
        }
        for t_key, label in tab_defs
    ]

    elements = [
        {
            "tag": "markdown",
            "content": "🤖 **欢迎使用 Antigravity 智能助理控制台！**\n点击下方分类标签毫秒级切换视图，或点击底部按钮一键唤起面板：",
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": tab_buttons,
        },
    ]

    if tab in ("all", "common"):
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "🎛️ **模型与记忆控制：**\n"
                    "• `/model` : 弹出大模型矩阵控制台（支持推理档位、配额余量与执行模式热切）\n"
                    "• `/context` : 查看对话上下文 Token 占用统计与容量看板\n"
                    "• `/memory` : 查看与管理您的个人偏好设定（支持卡片内联新增与擦除）\n"
                    "• `/brain` : 查看 Antigravity 全局跨会话记忆库\n"
                    "• `/clear` : 彻底清空当前会话的上下文记忆，重新开始"
                ),
            },
        ])

    if tab in ("all", "project"):
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "📁 **项目与工作区工程：**\n"
                    "• `/project` : 弹出项目管理器（支持内联新建/克隆项目、设根目录、置顶切换与翻页）\n"
                    "• `/adddir [add|remove|clear|路径]` : 挂载/管理额外工作区目录（多仓库协同 `--add-dir`）\n"
                    "• `/mode [auto|accept-edits|plan]` : 切换 CLI 执行模式（支持 ACP 原地热切换）\n"
                    "• `/note` : 记事本看板（支持内联添加、原卡展开详情、删除与清空）\n"
                    "• `/cron`（或 `/schedule`）: 定时任务与周期 AI 巡检调度中心（支持可视化表单创建）\n"
                    "• `/quota` : 查询 Google AI Pro 套餐官方剩余额度看板\n"
                    "• `/status` : 秒级查看机器人 CPU/内存/温池会话与 Git 状态"
                ),
            },
        ])

    if tab in ("all", "eco"):
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "⚡ **系统管理、ACP 与生态扩展：**\n"
                    "• `/steer <追加内容>` : 向运行中的 ACP 会话排队追加指令（当前回合结束后自动续跑，无需 `/stop`）\n"
                    "• `/plugin [reload]` : 管理**机器人扩展插件**（查看、GitHub 安装与热重载）\n"
                    "• `/skills [list|add|search|use|unuse|enable|disable|install ...]` : 弹出 Antigravity Skills 技能管理中心\n"
                    "• `/agyplugin [list|install|validate|enable|disable|uninstall|import ...]` : 弹出 `agy` 原生 CLI 插件管理中心\n"
                    "• `/mcp [list|enable|disable|remove|add ...]` : 弹出 `agy` 原生 MCP 服务管理中心\n"
                    "• `/stop` : 中止当前会话中正在执行的后台任务\n"
                    "• `/s1` : 打开 System One 决策网关控制台（支持表单无痕配置凭据）\n"
                    "• `/ping` : 检测机器人在线状态与响应连通性\n"
                    "• `/update` : 检查并更新机器人服务至最新版本\n"
                    "• `/help` : 显示此帮助卡片"
                ),
            },
        ])

        # 动态渲染已启用的第三方/硬件扩展插件命令（自动去重内置核心指令）
        dynamic_plugin_lines = []
        for p in (plugins or []):
            if not isinstance(p, dict) or not p.get("enabled", True):
                continue
            raw_cmds = p.get("commands") or []
            extra_cmds = [
                c.strip()
                for c in raw_cmds
                if isinstance(c, str) and c.strip() and c.strip().lower() not in _HELP_STATIC_COMMANDS
            ]
            if not extra_cmds:
                continue
            cmd_str = " ".join(f"`{c}`" for c in extra_cmds)
            p_name = p.get("name") or p.get("id") or "扩展插件"
            dynamic_plugin_lines.append(f"• {cmd_str} : {p_name}")

        if dynamic_plugin_lines:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": "🧩 **已加载扩展插件指令：**\n" + "\n".join(dynamic_plugin_lines),
            })

    if is_admin_user and tab in ("all", "admin"):
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": (
                "🔐 **管理员专属指令：**\n"
                "• `/perm` : 一键体检飞书机器人所需的应用权限开通状态（支持热激活与直达配置后台）\n"
                "• `/user [grant|revoke|ban|unban]` : 管理多会话用户授权与权限分级\n"
                "• `/auth` : 查看当前会话授权状态\n"
                "• `/transport [acp|stream-json]` : 查看或切换底层会话传输引擎（默认 ACP）\n"
                "• `/pluginreload` : 一键热重载机器人插件与命令模块"
            ),
        })

    elements.extend([
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "🎯 **快捷交互控制** (点击一键调起)："
        },
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📁 项目管理"},
                    "type": "primary",
                    "value": {"action": "user_choice", "choice": "/project", "label": "项目管理"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🤖 切换模型"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "⏱️ 计划任务"},
                    "type": "default",
                    "value": {"action": "open_cron_panel"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧠 技能中心"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/skills", "label": "技能中心"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔌 原生插件"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/agyplugin", "label": "原生插件"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🛠️ MCP 服务"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/mcp", "label": "MCP 服务"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧠 偏好记忆"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/memory", "label": "偏好记忆"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📝 记事本"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/note", "label": "记事本"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📊 容量看板"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/context", "label": "容量看板"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧹 清空上下文"},
                    "type": "danger",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认清空当前对话上下文"},
                        "text": {"tag": "plain_text", "content": "清空后将开启全新对话（项目长效记忆不受影响），确定继续吗？"}
                    },
                    "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                }
            ]
        },
    ])

    if tab == "all":
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "⚙️ **系统功能特性：**\n"
                    "• **多模态文件解析**：支持处理 PDF、Word、语音、视频与图片格式\n"
                    "• **受控终端执行**：支持受控执行终端命令与工作区文件读写\n"
                    "• **网页内容解析**：输入网页链接可自动提取关键内容摘要"
                )
            },
        ])

    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "💡 Antigravity 帮助与控制大厅", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs="", git_status="未知", bot_stats=None):
    status_emoji = "🟢" if status == "online" else "🔴"
    if not bot_stats:
        bot_stats = {"total_requests": 0, "success_requests": 0, "failed_requests": 0}

    warm_sessions = bot_stats.get("warm_sessions")
    load_avg = bot_stats.get("load_avg", "")
    warm_line = f"\n- **ACP 温池活跃会话**：{warm_sessions} 个" if warm_sessions is not None else ""
    load_line = f"\n- **系统平均负载 (1/5/15m)**：`{load_avg}`" if load_avg else ""

    elements = [
        {
            "tag": "markdown",
            "content": f"**服务状态**：{status_emoji} {status.upper()}\n**运行时长**：{uptime_str}\n**重启次数**：{restarts} 次"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**🌿 代码库状态 (Git)**\n{git_status}"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                f"**📈 机器人运行与算力统计 (自上次启动以来)**\n"
                f"- **总请求数**: {bot_stats.get('total_requests', 0)} "
                f"(成功 {bot_stats.get('success_requests', 0)} · 异常 {bot_stats.get('failed_requests', 0)})\n"
                f"- **累计消耗 Tokens**: {bot_stats.get('total_tokens', 0):,}"
            )
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"**💻 进程与宿主资源占用**\n- **CPU**：{cpu}% · **常驻内存 (RSS)**：{mem_mb} MB{warm_line}{load_line}"
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 秒级刷新"},
                    "type": "primary",
                    "value": {"action": "refresh_status", "fetch_remote": False}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔍 检查远端更新"},
                    "type": "default",
                    "value": {"action": "refresh_status", "fetch_remote": True}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔐 权限体检"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/perm", "label": "权限体检"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📊 查看配额"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/quota", "label": "查看配额"}
                }
            ]
        },
        create_footer(),
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📊 服务器运行状态", "tag": "plain_text"}
        },
        "elements": elements
    }
