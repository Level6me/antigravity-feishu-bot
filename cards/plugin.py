"""Plugin management card builder with multi-tab support for antigravity-feishu-bot."""

from datetime import datetime
from cards.common import create_footer
from plugin_store import load_plugin_sources


FEATURED_REMOTE_PLUGINS = [
    {
        "id": "server_health",
        "name": "🖥️ 服务器巡检与健康报告",
        "author": "Antigravity",
        "repo_url": "https://github.com/Level6me/antigravity-feishu-bot",
        "description": "监控 CPU 负载、内存率、磁盘余量，发送 /sysinfo 即可查看"
    },
    {
        "id": "git_notifier",
        "name": "🐙 GitHub/GitLab Webhook 提通知",
        "author": "Community",
        "repo_url": "https://github.com/Level6me/antigravity-feishu-bot",
        "description": "接收 Git Commit & PR 事件并推送优雅飞书通知卡片"
    },
    {
        "id": "rss_subscriber",
        "name": "📰 RSS 订阅与定时新闻推送",
        "author": "Community",
        "repo_url": "https://github.com/Level6me/antigravity-feishu-bot",
        "description": "订阅科技/博客 RSS，配合 Cron 定时生成每日要闻总结"
    }
]


def build_plugin_panel_card(plugin_list: list, active_tab: str = "installed") -> dict:
    """Build interactive card displaying installed plugins or plugin store sources."""
    
    # 1. Navigation Tab Buttons
    is_installed = active_tab == "installed"
    tab_actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"{'▶ ' if is_installed else ''}📦 已安装插件 ({len(plugin_list)})"},
            "type": "primary" if is_installed else "default",
            "value": {"action": "switch_plugin_tab", "tab": "installed"}
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"{'▶ ' if not is_installed else ''}🏪 插件源与商店"},
            "type": "primary" if not is_installed else "default",
            "value": {"action": "switch_plugin_tab", "tab": "sources"}
        }
    ]

    elements = [
        {
            "tag": "action",
            "layout": "flow",
            "actions": tab_actions
        },
        {"tag": "hr"}
    ]

    if is_installed:
        # TAB 1: INSTALLED PLUGINS
        if not plugin_list:
            elements.append({
                "tag": "markdown",
                "content": "⚠️ *当前 `plugins/` 目录下暂无安装的插件。切换到【🏪 插件源与商店】Tab 或使用 GitHub URL 即可一键安装扩展能力。*"
            })
        else:
            for p in plugin_list:
                pid = p.get("id", "")
                name = p.get("name", pid)
                version = p.get("version", "1.0.0")
                cmds = p.get("commands", [])
                cmd_str = ", ".join([f"`{c}`" for c in cmds]) if cmds else "无专属指令"
                enabled = p.get("enabled", True)
                status_tag = "🟢 已激活" if enabled else "⚪ 已禁用"

                elements.append({
                    "tag": "markdown",
                    "content": f"**{name}** (`{pid}` v{version})\n" \
                               f"• **运行状态**：{status_tag}\n" \
                               f"• **注册指令**：{cmd_str}"
                })
                elements.append({
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🔄 检查更新"},
                            "type": "default",
                            "value": {"action": "update_plugin", "plugin_id": pid}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🗑️ 物理卸载"},
                            "type": "danger",
                            "value": {"action": "uninstall_plugin", "plugin_id": pid}
                        }
                    ]
                })
                elements.append({"tag": "hr"})

        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📥 从 GitHub URL 安装插件"},
                    "type": "primary",
                    "value": {"action": "prompt_install_github"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 热重载插件库 (Reload)"},
                    "type": "default",
                    "value": {"action": "reload_plugins"}
                }
            ]
        })

    else:
        # TAB 2: PLUGIN SOURCES & STORE
        sources = load_plugin_sources()
        elements.append({
            "tag": "markdown",
            "content": f"**🏪 GitHub 插件仓库与商店** (已配置 **{len(sources)}** 个插件源)\n" \
                       f"可以直接安装精选扩展插件，或提交 GitHub 仓库 URL 进行在线克隆安装。"
        })
        elements.append({"tag": "hr"})

        elements.append({
            "tag": "markdown",
            "content": "**🌟 精选推荐插件库 (Featured Plugins)**"
        })

        installed_ids = {p.get("id") for p in plugin_list}

        for rem in FEATURED_REMOTE_PLUGINS:
            r_id = rem["id"]
            r_name = rem["name"]
            r_url = rem["repo_url"]
            r_desc = rem["description"]
            is_installed_rem = r_id in installed_ids

            btn_text = "已安装" if is_installed_rem else "安装"
            btn_type = "default" if is_installed_rem else "primary"

            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "default",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 4,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"**{r_name}** (`{r_id}`)\n{r_desc}"
                            }
                        ]
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "center",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": btn_text},
                                "type": btn_type,
                                "value": {"action": "install_github_repo", "repo_url": r_url, "plugin_id": r_id}
                            }
                        ]
                    }
                ]
            })
            elements.append({"tag": "hr"})

        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "➕ 添加 GitHub 插件源"},
                    "type": "primary",
                    "value": {"action": "prompt_add_source"}
                }
            ]
        })

    elements.append(create_footer())

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "🧩 机器人插件中心与应用商店"
            },
            "template": "indigo"
        },
        "elements": elements
    }
    return card
