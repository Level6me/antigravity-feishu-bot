"""Card builders: response."""

import os
import re
from datetime import datetime
from utils import get_context_usage_stats

from cards.common import create_footer

# 飞书卡片/消息体有 ~30KB 上限，markdown 字段预留安全余量
MAX_MARKDOWN_CHARS = 25000
TRUNCATION_NOTICE = "\n\n_(回复过长已截断，完整内容请在工作区查看)_"

def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_project="默认", is_error=False, is_streaming=False, session_data=None):
    elements = []
    
    # 1. Main Text
    if reply_text:
        content = reply_text
        if len(content) > MAX_MARKDOWN_CHARS:
            content = content[:MAX_MARKDOWN_CHARS] + TRUNCATION_NOTICE
        if is_streaming:
            content += " ⏳" # Blinking cursor effect
        elements.append({
            "tag": "markdown",
            "content": content
        })
        
    # 2. Interactive Options
    if choice_card_data and choice_card_data.get("options"):
        if reply_text:
            elements.append({"tag": "hr"})
            
        actions = []
        markdown_options = []
        is_long_options = any(len(opt) > 6 for opt in choice_card_data["options"])
        
        for i, opt in enumerate(choice_card_data["options"][:10]):
            prefix_match = re.match(r'^([a-zA-Z0-9\u4e00-\u9fa5]+)[:：.、]\s*(.*)$', opt)
            
            if prefix_match:
                prefix = prefix_match.group(1).strip()
                rest_text = prefix_match.group(2).strip()
                if len(prefix) == 1 and prefix.encode('utf-8').isalpha():
                    btn_label = f"选项 {prefix}"
                elif len(prefix) <= 4:
                    btn_label = prefix
                else:
                    btn_label = f"选项 {i+1}"
            else:
                btn_label = f"选项 {i+1}"
                rest_text = opt
            
            if not is_long_options:
                btn_label = opt[:50]
                
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn_label},
                "type": "default",
                "value": {"action": "user_choice", "choice": opt, "label": btn_label}
            })
            if is_long_options:
                markdown_options.append(f"- **{btn_label}**: {rest_text}")

        question_text = f"**{choice_card_data.get('question', '请选择：')}**"
        if is_long_options:
            question_text += "\n\n" + "\n".join(markdown_options)
            
        elements.append({
            "tag": "markdown",
            "content": question_text
        })
        
        # 根据选项数量动态设置布局，保证按钮等宽对齐
        layout_mode = "flow"
        if len(actions) == 2:
            layout_mode = "bisect"
        elif len(actions) == 3:
            layout_mode = "trisection"
            
        elements.append({
            "tag": "action",
            "layout": layout_mode,
            "actions": actions
        })

    # 3. Context Info Row
    if not is_error:
        project_name_only = "默认"
        if current_project and current_project not in ["默认", "Default"]:
            project_name_only = os.path.basename(current_project) or current_project
        else:
            project_name_only = current_project or "默认"
            
        from utils import get_context_usage_stats
        stats = get_context_usage_stats(session_data)
        
        def format_k(num):
            if num >= 1000000:
                return f"{num / 1000000:.1f}M"
            elif num >= 1000:
                return f"{num / 1000:.1f}K"
            return str(num)

        raw_free_pct = stats.get("free_pct", 100.0)
        total_tokens = stats.get("total_tokens", 0)
        free_tokens = stats.get("free_tokens", 1000000)
        max_tokens = stats.get("max_tokens", 1000000)

        if total_tokens == 0 or raw_free_pct >= 100.0:
            free_pct_str = "100.0"
        elif raw_free_pct >= 99.9:
            free_pct_str = f"{raw_free_pct:.2f}" if raw_free_pct < 99.99 else "99.99"
        else:
            free_pct_str = f"{raw_free_pct:.1f}"

        context_str = f"🧠 上下文剩余: {free_pct_str}% ({format_k(free_tokens)}/{format_k(max_tokens)})"

        elements.append({
            "tag": "markdown",
            "content": f"<font color='grey'>🤖 模型: {current_model} | 🗂️ 项目: {project_name_only} | {context_str}</font>"
        })

    # 4. Standard Footer
    elements.append(create_footer())

    header_template = "red" if is_error else ("wathet" if is_streaming else "blue")
    header_title = "❌ 发生错误" if is_error else ("✨ AI 回复中..." if is_streaming else "✨ AI 回复")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue" if not is_error else "red",
            "title": {"content": header_title, "tag": "plain_text"}
        },
        "elements": elements
    }
