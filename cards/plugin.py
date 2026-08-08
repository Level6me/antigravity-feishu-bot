"""Plugin management card builder for antigravity-feishu-bot."""

from datetime import datetime
from cards.common import create_footer


def build_plugin_panel_card(plugin_list: list) -> dict:
    """Build interactive card displaying all installed plugins and status."""
    elements = [
        {
            "tag": "markdown",
            "content": f"**🧩 插件扩展中心 (Plugin Center)**\n" \
                       f"已加载运行插件数：**{len(plugin_list)}** 个 | 支持指令与事件 Hook 响应"
        },
        {"tag": "hr"}
    ]

    if not plugin_list:
        elements.append({
            "tag": "markdown",
            "content": "⚠️ *当前 `plugins/` 目录暂无已加载的插件。将包含 `manifest.json` 与 `plugin.py` 的文件夹放入 `plugins/` 目录即可扩展能力。*"
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
                           f"• **状态**：{status_tag}\n" \
                           f"• **注册指令**：{cmd_str}"
            })
            elements.append({"tag": "hr"})

    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 热重载插件 (Reload)"},
                "type": "primary",
                "value": {"action": "reload_plugins"}
            }
        ]
    })
    elements.append(create_footer())

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "🧩 机器人插件中心"
            },
            "template": "indigo"
        },
        "elements": elements
    }
    return card
