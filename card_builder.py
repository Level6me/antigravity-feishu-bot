import re
import os
from datetime import datetime
from config import WORKSPACE_ROOT

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

    @staticmethod
    def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text="", think_seconds=0):
        title, content = CardBuilder._guess_intent(user_text)
        content = CardBuilder._get_dynamic_think_text(content, think_seconds)
        
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
    def build_tool_indicator(tool_action, user_text="", downloaded_file_name=None, download_success=True, think_seconds=0):
        title, content = CardBuilder._guess_intent(user_text)
        
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
    def build_ai_response(reply_text, choice_card_data=None, current_model="Default", current_role="无", current_project="默认", is_error=False, is_streaming=False):
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
            project_name_only = "默认"
            if current_project and current_project not in ["默认", "Default"]:
                project_name_only = os.path.basename(current_project) or current_project
            else:
                project_name_only = current_project or "默认"
                
            elements.append({
                "tag": "markdown",
                "content": f"<font color='grey'>🤖 模型: {current_model} | 🎭 角色: {current_role} | 📂 项目: {project_name_only} | 💡 键入 /help 查看指令</font>"
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

    @staticmethod
    def build_dir_browser_card(active_project_path, recent_projects=None, recent_page=1, workspace_root=None, ignored_projects=None):
        elements = []
        
        # 1. 顶部当前活跃项目展示
        elements.append({
            "tag": "markdown",
            "content": f"📂 **当前活跃开发工作区**：\n`{active_project_path}`"
        })
        
        # 确定公共根目录
        proj_root = workspace_root if workspace_root else WORKSPACE_ROOT
        
        elements.append({
            "tag": "markdown",
            "content": f"⚙️ **当前公共项目根目录**：\n`{proj_root}`"
        })
        
        # 2. 新建项目动作行
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "➕ 新建项目"},
                    "type": "default",
                    "value": {"action": "create_project_prompt", "parent_path": proj_root}
                }
            ]
        })
        elements.append({"tag": "hr"})
        
        # 3. 扫描项目根目录 proj_root 下的所有子文件夹作为项目列表
        proj_root = proj_root
        all_projects = []
        try:
            for name in os.listdir(proj_root):
                if name.startswith('.') or name in ["venv", "downloads"]:
                    continue
                full_path = os.path.join(proj_root, name)
                if ignored_projects and full_path in ignored_projects:
                    continue
                if os.path.isdir(full_path):
                    all_projects.append((name, full_path))
            # 排序
            all_projects.sort(key=lambda x: x[0].lower())
        except Exception as e:
            log.error(f"Failed to scan project root: {e}")
            
        # 4. 合并数据库中记录的 recent_projects（防止有用户在外部路径单独添加的项目）
        if recent_projects:
            for p in recent_projects:
                if ignored_projects and p in ignored_projects:
                    continue
                if os.path.exists(p) and p not in [x[1] for x in all_projects] and p != "/":
                    all_projects.append((os.path.basename(p) or p, p))
                    
        # 5. 内嵌分页展示全部项目列表
        if all_projects:
            items_per_page = 5
            total_items = len(all_projects)
            total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
            
            page = max(1, min(recent_page, total_pages))
            
            elements.append({
                "tag": "markdown",
                "content": f"📁 **项目选择列表** (第 {page}/{total_pages} 页)："
            })
            
            start_idx = (page - 1) * items_per_page
            end_idx = start_idx + items_per_page
            page_items = all_projects[start_idx:end_idx]
            
            for name, p in page_items:
                # 高亮当前活跃项目
                is_active = (p == active_project_path)
                name_display = f"🌟 **{name} (当前活跃)**" if is_active else f"📁 **{name}**"
                
                columns = [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"{name_display}\n*`{p}`*"
                            }
                        ]
                    }
                ]
                
                # 如果不是当前活跃项目，提供选择按钮和列表删除按钮并排在右侧
                if not is_active:
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "选择"},
                                "type": "primary",
                                "value": {"action": "select_project", "path": p}
                            }
                        ]
                    })
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "删除"},
                                "type": "danger",
                                "value": {"action": "remove_project_from_list", "path": p}
                            }
                        ]
                    })
                else:
                    columns.append({
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "✅ 已选"},
                                "type": "default",
                                "value": {"action": "already_active"}
                            }
                        ]
                    })
                    
                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": columns
                })
                
            # 分页控制
            page_actions = []
            if page > 1:
                page_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                    "type": "default",
                    "value": {"action": "browse_recent_page", "page": page - 1, "current_path": active_project_path}
                })
            if page < total_pages:
                page_actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页 ▶️"},
                    "type": "default",
                    "value": {"action": "browse_recent_page", "page": page + 1, "current_path": active_project_path}
                })
                
            if page_actions:
                elements.append({
                    "tag": "action",
                    "actions": page_actions
                })
        else:
            elements.append({
                "tag": "markdown",
                "content": "📭 *当前没有可用的项目，请点击上方按钮新建一个项目！*"
            })
            
        elements.append(CardBuilder._create_footer())
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "📁 项目管理器", "tag": "plain_text"}
            },
            "elements": elements
        }

    @staticmethod
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
                    "content": f"**当前运行版本**：`{current_version}`\n\n🎉 太棒了！经过全网云端探测，您的机器人的核心引擎已经是最新形态，无需任何更新操作。"
                },
                CardBuilder._create_footer()
            ]
        }

    @staticmethod
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
                CardBuilder._create_footer()
            ]
        }
