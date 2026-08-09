"""Card builders: indicators."""

import os
import re
from datetime import datetime

from cards.common import create_footer

def _guess_intent(text):
    if not text:
        return "✨ AI 思考中...", "正在为您生成回复，请稍候..."
    
    text_lower = text.lower()
    heavy_kws = ["全栈", "重构", "审查", "通宵", "架构", "重写", "彻底优化", "迁移", "整个项目", "全部代码", "深度检查", "从零搭建", "全盘"]
    if any(kw in text_lower for kw in heavy_kws) or len(text) > 250:
        return "🚀 超大型工程任务通道", "AI 已自动识别当前请求为超大型/长耗时工程，已自动开启长任务保护，正在分阶段为您攻坚..."
    elif any(kw in text_lower for kw in ["代码", "脚本", "编程", "xcode", "编译", "bug", "报错", "前端", "后端", "python", "swift"]):
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


def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True, think_seconds=0):
    title, content = _guess_intent(user_text)
    
    # Override the text with the actual tool action
    time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
    content = f"**当前动作：** `{tool_action}`\n\n*(AI 正在运行底层命令或操作文件，{time_hint})*"
    
    if downloaded_file_name:
        if download_success:
            content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
        else:
            content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"content": "🛠️ " + tool_action, "tag": "plain_text"}
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
