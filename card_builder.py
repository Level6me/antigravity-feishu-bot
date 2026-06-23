import re
from datetime import datetime

class CardBuilder:
    @staticmethod
    def _create_footer():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"⚡ Powered by Antigravity | 🕒 {now}"
                }
            ]
        }

    @staticmethod
    def build_model_panel(available_models, current_model):
        actions = []
        for i, model_name in enumerate(available_models[:10]):
            button_type = "primary" if i == 0 else "default"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": model_name},
                "type": button_type,
                "value": {"action": "switch_model", "model": model_name}
            })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "🤖 机器人控制面板", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前正在使用的模型**: {current_model}\n**可用模型列表**（点击下方按钮快速切换）："
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": actions
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def _guess_intent(text):
        if not text:
            return "✨ AI 思考中...", "正在为您生成回复，请稍候..."
        
        text = text.lower()
        if any(kw in text for kw in ["代码", "脚本", "编程", "重构", "xcode", "编译", "bug", "报错", "前端", "后端", "python", "swift"]):
            return "💻 代码工程模式", "AI 正在理解代码逻辑并为您进行开发与调试，请稍候..."
        elif any(kw in text for kw in ["搜", "查一下", "找一下", "检索", "全网"]):
            return "🔍 数据检索模式", "AI 正在跨域检索并为您归纳相关信息，请稍候..."
        elif any(kw in text for kw in ["翻译", "英文", "中文"]):
            return "🌐 翻译模式", "AI 正在为您进行精准翻译，请稍候..."
        elif any(kw in text for kw in ["总结", "归纳", "提炼", "重点"]):
            return "📝 总结提炼模式", "AI 正在帮您提炼核心要点，请稍候..."
        elif any(kw in text for kw in ["选项", "我的选择是"]):
            return "🎯 选项执行中", "AI 已收到您的选择，正在进行处理..."
        else:
            return "✨ AI 思考中...", "正在为您深度分析与生成回复，请稍候..."

    @staticmethod
    def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text=""):
        title, content = CardBuilder._guess_intent(user_text)
        
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
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True):
        title, content = CardBuilder._guess_intent(user_text)
        
        # Override the text with the actual tool action
        content = f"**当前动作：** `{tool_action}`\n\n*(AI 正在运行底层命令或操作文件，请稍候...)*"
        
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
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
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
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
    def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_role="无", is_error=False, is_streaming=False):
        elements = []
        
        # 1. Main Text
        if reply_text:
            content = reply_text
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
            is_long_options = any(len(opt) > 15 for opt in choice_card_data["options"])
            
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
            elements.append({
                "tag": "action",
                "layout": "flow",
                "actions": actions
            })

        # 3. Context Info Row
        if not is_error:
            elements.append({
                "tag": "markdown",
                "content": f"<font color='grey'>🤖 模型: {current_model} | 🎭 角色: {current_role} | 💡 键入 /help 查看指令</font>"
            })

        # 4. Standard Footer
        elements.append(CardBuilder._create_footer())

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
