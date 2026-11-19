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


def build_help_card():
    elements = [
        {
            "tag": "markdown",
            "content": "🤖 **欢迎使用 Antigravity 智能助理控制台！**\n包含系统的所有指令与交互菜单，点击下方按钮或发送斜杠指令即可快速调起操作："
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "🎛️ **模型与记忆控制：**\n"
                "• `/model` : 弹出大模型选择面板，自由热切换模型\n"
                "• `/context` : 查看对话上下文 Token 占用统计与容量看板\n"
                "• `/memory` : 查看与管理您的个人偏好设定（支持交互式新增与删除）\n"
                "• `/brain` : 查看 Antigravity 全局跨会话记忆库\n"
                "• `/clear` : 彻底清空当前会话的上下文记忆，重新开始"
            )
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "📁 **项目与工作区工程：**\n"
                "• `/project` : 弹出项目管理器（支持切换工作区、新建项目、设置路径与翻页）\n"
                "• `/adddir [add|remove|clear|路径]` : 挂载/管理额外工作区目录（多仓库协同 `--add-dir`）\n"
                "• `/mode [auto|accept-edits|plan]` : 切换 CLI 执行模式（支持 ACP 原地热切换）\n"
                "• `/note` : 记事本管理看板（支持添加、查看详情、删除与清空）\n"
                "• `/quota` : 查询 Google AI Pro 套餐官方剩余额度看板\n"
                "• `/status` : 查看机器人的运行状态、CPU/内存/Uptime 与日志"
            )
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "⚡ **系统管理、ACP 与原生生态：**\n"
                "• `/steer <追加内容>` : 向运行中的 ACP 会话排队追加指令（当前回合结束后自动续跑，无需 `/stop`）\n"
                "• `/transport [acp|stream-json]` : 查看或切换会话传输引擎（默认 ACP）\n"
                "• `/mcp [list|enable|disable|remove|add ...]` : 管理 `agy` 原生 MCP 服务\n"
                "• `/agyplugin [list|validate|install|enable|disable|uninstall ...]` : 管理 `agy` 原生插件\n"
                "• `/stop` : 中止当前会话中正在执行的后台任务\n"
                "• `/s1` : 打开 System One 决策网关控制台\n"
                "• `/update` : 检查并更新机器人服务至最新版本\n"
                "• `/help` : 显示此帮助卡片"
            )
        },
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
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                }
            ]
        },
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
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "💡 Antigravity 帮助与控制大厅", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status="未知", bot_stats=None):
    status_emoji = "🟢" if status == "online" else "🔴"
    if not bot_stats:
        bot_stats = {"total_requests": 0, "success_requests": 0, "failed_requests": 0}
        
    elements = [
        {
            "tag": "markdown",
            "content": f"**服务状态**：{status_emoji} {status.upper()}\n**运行时长**：{uptime_str}\n**重启次数**：{restarts} 次"
        },
        {
            "tag": "hr"
        },
        {
            "tag": "markdown",
            "content": f"**🌿 代码库状态 (Git)**\n{git_status}"
        },
        {
            "tag": "hr"
        },
        {
            "tag": "markdown",
            "content": f"**📈 机器人请求统计 (自上次重启以来)**\n- **总请求数**: {bot_stats.get('total_requests', 0)}\n- **成功处理**: {bot_stats.get('success_requests', 0)}\n- **执行异常**: {bot_stats.get('failed_requests', 0)}"
        },
        {
            "tag": "hr"
        },
        {
            "tag": "markdown",
            "content": f"**💡 模型算力消耗统计 (自上次重启以来 · 自带模型)**\n- **累计消耗 Tokens**: {bot_stats.get('total_tokens', 0):,}"
        },
        {
            "tag": "hr"
        },
        {
            "tag": "markdown",
            "content": f"**💻 资源占用**\n- **CPU**：{cpu}%\n- **内存**：{mem_mb} MB"
        }
    ]
    
    elements.append({
        "tag": "hr"
    })
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 刷新状态"},
                "type": "primary",
                "value": {"action": "refresh_status"}
            }
        ]
    })
    
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📊 服务器运行状态", "tag": "plain_text"}
        },
        "elements": elements
    }
