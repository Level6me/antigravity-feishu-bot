"""Card builders: indicators."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def _guess_intent(text):
    if not text:
        return "✨ AI 思考中...", "正在为您生成回复，请稍候..."
    
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["代码", "脚本", "编程", "重构", "xcode", "编译", "bug", "报错", "前端", "后端", "python", "swift", "全栈", "架构"]):
        return "💻 代码工程模式", "AI 正在理解代码逻辑并为您进行开发与调试，请稍候..."
    elif any(kw in text_lower for kw in ["搜", "查一下", "找一下", "检索", "全网"]):
        return "🔍 数据检索模式", "AI 正在跨域检索并为您归纳相关信息，请稍候..."
    elif any(kw in text_lower for kw in ["翻译", "英文", "中文"]):
        return "🌐 翻译模式", "AI 正在为您进行精准翻译，请稍候..."
    elif any(kw in text_lower for kw in ["总结", "归纳", "提炼", "重点"]):
        return "📝 总结提炼模式", "AI 正在帮您提炼核心要点，请稍候..."
    elif any(kw in text_lower for kw in ["选项", "我的选择是"]):
        return "🎯 选项执行中", "AI 已收到您的选择，正在进行处理..."
    else:
        return "✨ AI 思考中...", "正在为您深度分析与生成回复，请稍候..."


def _get_dynamic_think_text(base_text, think_seconds):
    if think_seconds <= 0:
        return base_text
        
    phrases = [
        "🧠 正在深度思考上下文...",
        "🔍 正在系统内检索相关线索...",
        "⚙️ 正在为您规划行动路径...",
        "💡 马上就好，正在组织语言...",
        "🚀 正在全速冲刺，请稍等..."
    ]
    # Rotate phrase every 2 seconds
    idx = (think_seconds // 2) % len(phrases)
    return f"{base_text}\n\n*( {phrases[idx]} 已耗时 {think_seconds}s )*"


def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text="", think_seconds=0):
    title, content = _guess_intent(user_text)
    content = _get_dynamic_think_text(content, think_seconds)
    
    if downloaded_file_name:
        if download_success:
            content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
        else:
            content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": title, "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content
            },
            create_footer()
        ]
    }


def clean_action_text(text, max_len=28):
    """Format and streamline action text to avoid huge code snippets or verbose messages."""
    if not text:
        return "执行操作中"
    
    clean = str(text).strip(' "\'`')
    clean = re.sub(r'[\r\n\t]+', ' ', clean).strip()
    clean = re.sub(r'\s+', ' ', clean)
    clean = re.sub(r'^(正在执行\s*)+', '正在执行 ', clean)
    
    if any(clean.startswith(prefix) for prefix in [
        "正在执行 python", "python3", "python", "正在执行 timeout", "timeout "
    ]) or "import sqlite3" in clean or "sqlite3.connect" in clean:
        if "sqlite3" in clean or "db" in clean:
            return "查询本地数据库"
        return "执行 Python 脚本"
        
    if "docker ps" in clean or "docker container" in clean:
        return "查询容器状态"
    elif clean.startswith("git ") or "git " in clean:
        return "执行 Git 操作"
    elif "grep " in clean or "ripgrep" in clean or "find " in clean:
        return "检索项目内容"
    elif "curl " in clean or "wget " in clean or "http" in clean:
        return "请求网络资源"
        
    if len(clean) > max_len:
        return clean[:max_len - 3] + "..."
    return clean


def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True, think_seconds=0):
    title, content = _guess_intent(user_text)
    
    action_disp = clean_action_text(tool_action)
    time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
    content = f"**当前动作：** `{action_disp}`\n\n*(AI 正在运行底层命令或操作文件，{time_hint})*"
    
    if downloaded_file_name:
        if download_success:
            content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
        else:
            content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

    header_title = f"🛠️ {action_disp}" if not action_disp.startswith("🛠️") else action_disp
    if len(header_title) > 28:
        header_title = header_title[:25] + "..."

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"content": header_title, "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content
            },
            create_footer()
        ]
    }


def build_download_indicator(file_name, media_type="文件"):
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "wathet",
            "title": {"content": "📥 资源加载中...", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"正在为您下载并解析多媒体资源：**{file_name}**\n\n大文件（如视频、PDF）可能需要数秒至一分钟，请稍候..."
            },
            create_footer()
        ]
    }


def build_streaming_indicator(partial_text, tool_action=None, user_text="", think_seconds=0):
    title, _ = _guess_intent(user_text)
    header_title = f"⚡ 打字机生成中 | {title}"
    
    status_bar = f"*( ⏱️ 已思考 {think_seconds}s"
    if tool_action:
        action_disp = clean_action_text(tool_action)
        status_bar += f" | 🛠️ {action_disp}"
    status_bar += " )*\n\n---\n\n"
    
    # Trim to last 3500 chars if ultra-long to keep card patch within safe payload limit
    max_streaming_chars = 3500
    display_text = partial_text
    if len(display_text) > max_streaming_chars:
        display_text = "... (前文已自动隐藏) ...\n\n" + display_text[-max_streaming_chars:]
        
    full_content = status_bar + display_text + " ▌"
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": header_title, "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": full_content
            },
            create_footer()
        ]
    }


def build_stall_warning_card(user_prompt, think_seconds, stall_seconds):
    wait_time_desc = f"{stall_seconds // 60} 分钟" if stall_seconds >= 60 else f"{stall_seconds} 秒"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"content": "⏱️ AI 处于深度推理等待中", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"模型已持续运行 **{think_seconds} 秒**（当前静默等待已达 **{wait_time_desc}**）。\n\n*模型 API 正在云端进行深度推理、复杂任务规划或多工具检索，后台守护机制正常工作中，已为您自动延长等待时间。*"
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🟢 继续静默等待"},
                        "type": "primary",
                        "value": {"action": "extend_wait"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🛑 叫停任务"},
                        "type": "danger",
                        "value": {"action": "user_choice", "choice": "/stop", "label": "叫停任务"}
                    }
                ]
            },
            create_footer()
        ]
    }


def build_stall_error_card(user_prompt, think_seconds, stall_seconds):
    clean_prompt = user_prompt[:80] if user_prompt else "重试上条请求"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"content": "⚠️ 任务无响应已终止", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"任务已连续 **{stall_seconds // 60} 分钟** 无任何 Token、CPU 计算或日志写入，已判定为卡死并自动终止。\n\n**建议操作选项**："
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 重新发送此请求"},
                        "type": "primary",
                        "value": {"action": "user_choice", "choice": clean_prompt, "label": "重新发送请求"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 切换更快的模型"},
                        "type": "default",
                        "value": {"action": "user_choice", "choice": "/model", "label": "打开模型切换面板"}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🧹 清空上下文重试"},
                        "type": "default",
                        "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                    }
                ]
            },
            create_footer()
        ]
    }
