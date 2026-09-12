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


def build_typing_indicator(downloaded_file_name=None, download_success=True, user_text="", think_seconds=0, is_voice=False):
    if is_voice:
        title = "🎙️ 正在生成语音回复..."
        content = "AI 正在组织语言并为您合成语音回复，请稍候..."
    else:
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
            "template": "turquoise" if is_voice else "blue",
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


def translate_action_verb(act: str) -> str:
    """Translate common English verb prefixes in tool actions to clean Chinese."""
    if not act:
        return ""
    patterns = [
        (r'^(?:Connecting to|Connect to|Connecting|Connect)\s+', '连接 '),
        (r'^(?:Downloading from|Downloading|Download)\s+', '下载 '),
        (r'^(?:Checking|Verifying|Validating|Check|Verify|Validate)\s+', '检查 '),
        (r'^(?:Inspecting|Analyzing|Inspect|Analyze)\s+', '分析 '),
        (r'^(?:Creating|Generating|Create|Generate)\s+', '生成 '),
        (r'^(?:Writing to|Writing|Write)\s+', '写入 '),
        (r'^(?:Executing|Running|Execute|Run)\s+', '执行 '),
        (r'^(?:Listing|Browsing|List|Browse)\s+', '查看 '),
        (r'^(?:Searching for|Searching|Grepping|Search|Grep|Find)\s+', '搜索 '),
        (r'^(?:Reading|Viewing|Read|View)\s+', '读取 '),
        (r'^(?:Editing|Modifying|Edit|Modify)\s+', '编辑 '),
        (r'^(?:Querying|Query)\s+', '查询 '),
        (r'^(?:Aggregating|Aggregate)\s+', '汇总 '),
        (r'^(?:Fetching|Fetch)\s+', '获取 '),
        (r'^(?:Uploading|Upload)\s+', '上传 '),
        (r'^(?:Sending|Send)\s+', '发送 '),
        (r'^(?:Formatting|Format)\s+', '整理 '),
    ]
    for pattern, repl in patterns:
        if re.search(pattern, act, flags=re.IGNORECASE):
            return re.sub(pattern, repl, act, count=1, flags=re.IGNORECASE).strip()
    return act


def clean_action_text(text, max_len=28):
    """Format and streamline action text to avoid huge code snippets or verbose messages."""
    if not text:
        return "执行操作中"
    
    clean = str(text).strip(' "\'`')
    clean = re.sub(r'[\r\n\t]+', ' ', clean).strip()
    clean = re.sub(r'\s+', ' ', clean)
    clean = translate_action_verb(clean)
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


def format_step_item(action: str, is_completed: bool = False, max_len: int = 32) -> str:
    """Format a single step into an itemized checklist entry with status icon."""
    if not action:
        return "• ✅ 已完成操作" if is_completed else "• ⏳ 正在执行操作..."
        
    disp = clean_action_text(action, max_len=max_len)
    
    # Strip any leading bullets, emojis, numbers
    disp = re.sub(r'^[•\-\*\s✅⏳🛠️\d\.\、\[\]]+\s*', '', disp).strip()
    
    # Extract core verb/noun phrase
    core = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', disp).strip()
    if not core:
        core = disp
        
    # Strip trailing ellipsis if present before formatting
    core = re.sub(r'\.{2,}$', '', core).strip()
    
    if len(core) > max_len:
        core = core[:max_len - 3] + "..."
        
    is_ascii_start = bool(re.match(r'^[a-zA-Z]', core))
    
    if is_completed:
        if is_ascii_start:
            return f"• ✅ 已执行 {core}"
        return f"• ✅ 已{core}"
    else:
        suffix = "..." if not core.endswith("...") else ""
        if is_ascii_start:
            return f"• ⏳ 正在执行 {core}{suffix}"
        return f"• ⏳ 正在{core}{suffix}"


def is_complex_task(user_text: str) -> bool:
    """Check if the user request likely involves multi-step execution."""
    if not user_text:
        return False
    t = user_text.lower()
    complex_keywords = [
        "excel", "xlsx", "csv", "word", "docx", "ppt", "pdf", "表格", "报表", "文档", "文件",
        "生成", "导出", "下载", "统计", "分析", "做一份", "做个",
        "ssh", "服务器", "docker", "部署", "重启", "查库", "数据库", "sqlite",
        "写一个", "开发", "实现", "修复", "重构", "脚本", "跑通", "测试",
        "审查", "排查", "漏洞", "安全", "审计", "检查", "解决", "处理", "开始处理", "帮我处理", "优化"
    ]
    return any(kw in t for kw in complex_keywords)


def parse_task_plan(text: str) -> list[str] | None:
    """Parse custom task plan tags emitted by the model: [TASK_PLAN] step1 | step2 ... [/TASK_PLAN]"""
    if not text:
        return None
    match = re.search(r'\[TASK_PLAN\]\s*(.*?)\s*\[/TASK_PLAN\]', text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        match = re.search(r'\[TASK_PLAN\]\s*(.*?)(?=\n\n|\n\#|\Z)', text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
        steps = [s.strip() for s in re.split(r'[\|\n\r]+', raw) if s.strip()]
        if len(steps) >= 2:
            return steps[:5]
    return None


def generate_task_plan(user_text: str) -> list[str]:
    """Generate a high-quality, structured 3-4 step execution plan based on the user's prompt."""
    text = (user_text or "").lower()
    
    # 0. 安全审查 / 漏洞排查 / 审计 / 风险检查
    if any(k in text for k in ["审查", "漏洞", "安全", "排查", "审计"]):
        return [
            "全面审查源码与系统调用链路",
            "排查潜在漏洞与逻辑安全风险",
            "定位缺陷根因并执行修复加固",
            "校验修复产物并输出分析报告"
        ]

    # 1. 短指令确认 / 开始处理 / 处理
    if text.strip() in ["开始处理", "开始", "继续", "处理", "帮我处理", "执行", "开整", "搞起", "修复"]:
        return [
            "梳理待处理事项与执行方案",
            "执行核心模块与代码逻辑改造",
            "校验语法规范并运行测试验证",
            "汇总处理产物并交付最终结果"
        ]

    # 2. 报表 / 导出 / 数据分析 / Excel / CSV / Office 文件 / 文档
    if any(k in text for k in ["excel", "xlsx", "csv", "表格", "报表", "统计", "分析", "导出", "word", "docx", "ppt", "pdf", "文档", "文件"]):
        if any(k in text for k in ["服务器", "ssh", "远程", "端口", "安全", "日志", "数据库", "db", "sql"]):
            return [
                "连接服务并获取原始日志数据",
                "提取关键指标与安全分类统计",
                "自动化生成分析报表文件",
                "发送文件至飞书并输出总结报告"
            ]
        elif any(k in text for k in ["笑话", "故事", "文案", "翻译", "总结", "诗"]):
            return [
                "构思并整理核心文本内容",
                "排版格式化并生成文档文件",
                "发送文档至飞书并完成回复"
            ]
        else:
            return [
                "梳理数据源与分析指标",
                "执行数据汇总与处理计算",
                "排版生成报表文件",
                "发送文件至飞书并输出分析结论"
            ]
            
    # 2. 编程开发 / 脚本编写 / 代码工程 / 修复 Bug
    if any(k in text for k in ["代码", "开发", "写一个", "脚本", "实现", "重构", "修复", "bug", "报错", "python", "前端", "后端", "api"]):
        if any(k in text for k in ["测试", "跑通", "执行", "运行", "验证"]):
            return [
                "分析业务需求与规划技术方案",
                "编写核心代码与功能逻辑",
                "运行测试用例并校验执行结果",
                "整理代码产物并输出使用说明"
            ]
        else:
            return [
                "分析需求与规划架构方案",
                "编写核心业务逻辑与模块实现",
                "校验代码语法与执行测试",
                "交付成果并输出说明文档"
            ]
            
    # 3. 运维巡检 / 服务器管理 / Docker / Linux 操作
    if any(k in text for k in ["服务器", "ssh", "docker", "容器", "进程", "部署", "重启", "磁盘", "cpu", "内存", "网络", "防火墙"]):
        return [
            "探测目标环境与验证连通性",
            "检查系统运行状态与服务日志",
            "执行运维指令与配置变更",
            "校验服务健康状态并输出汇总报告"
        ]
        
    # 4. 全网搜索 / 调研 / 资料收集
    if any(k in text for k in ["搜索", "调研", "全网", "查一下", "最新", "找一下", "收集"]):
        return [
            "拆解搜索关键词并检索相关资料",
            "交叉比对与信息真伪过滤",
            "提炼核心要点与深度归纳",
            "输出结构化调研报告"
        ]
        
    # 5. 通用多步骤兜底
    return [
        "检索环境与读取相关上下文",
        "执行核心操作与底层指令处理",
        "校验处理结果与生成数据产物",
        "整理交付内容并输出最终回复"
    ]


def match_step_index(planned_steps: list[str], current_idx: int, action: str, completed_count: int) -> int:
    """Advance current_idx forward based on semantic keywords and tool completion count."""
    if not planned_steps:
        return 0
    total = len(planned_steps)
    action_lower = (action or "").lower()
    
    # 1. 优先语义/关键词向前探测匹配（绝不倒退）
    for j in range(current_idx + 1, total):
        step_text = planned_steps[j].lower()
        keywords = [w for w in re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', step_text)]
        match_count = sum(1 for kw in keywords if kw in action_lower)
        if match_count >= 1:
            return j
            
    # 2. 次优：根据独立工具完成数平滑推进（保护最后一步留给回复生成/交付）
    max_tool_step = max(0, total - 2)
    suggested_idx = min(max_tool_step, completed_count)
    return max(current_idx, suggested_idx)


def build_planned_steps_indicator(
    planned_steps: list[str],
    current_step_idx: int = 0,
    tool_action: str = "",
    user_text: str = "",
    think_seconds: int = 0,
    downloaded_file_name: str = None,
    download_success: bool = True,
    is_all_completed: bool = False,
) -> dict:
    """Build a rich roadmap checklist card showing all planned steps upfront with dynamic progress icons."""
    total = len(planned_steps)
    current_step_idx = max(0, min(current_step_idx, total - 1))
    
    lines = []
    for i, raw_step in enumerate(planned_steps):
        step = raw_step.strip()
        core = re.sub(r'^[•\-\*\s✅⏳⚪\d\.\、\[\]]+\s*', '', step).strip()
        core = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', core).strip()
        
        if is_all_completed or i < current_step_idx:
            # ✅ 已完成
            completed_text = f"已{core}" if not core.startswith("已") else core
            lines.append(f"• ✅ **{i+1}. {completed_text}**")
        elif i == current_step_idx:
            # ⏳ 正在执行
            active_text = f"正在{core}" if not core.startswith("正在") else core
            if tool_action:
                act_disp = clean_action_text(tool_action, max_len=24)
                act_core = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', act_disp).strip()
                if act_core and act_core not in active_text:
                    lines.append(f"• ⏳ **{i+1}. {active_text}** `({act_core})`...")
                else:
                    lines.append(f"• ⏳ **{i+1}. {active_text}**...")
            else:
                lines.append(f"• ⏳ **{i+1}. {active_text}**...")
        else:
            # ⚪ 待开始 (已规划待执行)
            lines.append(f"• ⚪ {i+1}. {core}")

    steps_block = "\n".join(lines)
    time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
    
    if is_all_completed:
        content = f"**📋 任务规划与执行清单 (共 {total} 步)：**\n\n{steps_block}\n\n*({time_hint}，全部步骤已执行完毕，正在输出回复)*"
    else:
        content = f"**📋 任务规划与执行清单 (第 {current_step_idx + 1}/{total} 步)：**\n\n{steps_block}\n\n*({time_hint}，AI 正在多步骤自主推进中)*"
        
    if downloaded_file_name:
        if download_success:
            content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
        else:
            content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

    curr_title = planned_steps[current_step_idx] if not is_all_completed else "任务处理完成"
    clean_title = re.sub(r'^[•\-\*\s✅⏳⚪\d\.\、\[\]]+\s*', '', curr_title).strip()
    clean_title = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', clean_title).strip()
    
    if is_all_completed:
        header_title = f"✨ [{total}/{total}] 任务规划步骤已全部完成"
        template = "blue"
    else:
        header_title = f"🛠️ [{current_step_idx + 1}/{total}] {clean_title}"
        template = "turquoise"
        
    if len(header_title) > 28:
        header_title = header_title[:25] + "..."

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
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


def build_tool_indicator(
    tool_action,
    user_text="",
    downloaded_file_name=None,
    download_success=True,
    think_seconds=0,
    completed_steps=None,
    planned_steps=None,
    current_step_idx=0,
    is_all_completed=False,
):
    if planned_steps:
        return build_planned_steps_indicator(
            planned_steps=planned_steps,
            current_step_idx=current_step_idx,
            tool_action=tool_action,
            user_text=user_text,
            think_seconds=think_seconds,
            downloaded_file_name=downloaded_file_name,
            download_success=download_success,
            is_all_completed=is_all_completed,
        )

    completed_steps = completed_steps or []
    action_disp = clean_action_text(tool_action) if tool_action else ""
    
    time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
    
    # 构建多步骤清单
    if completed_steps or action_disp:
        total_steps = len(completed_steps) + (1 if action_disp else 0)
        lines = []
        
        # 最多在卡片中展开显示最近 4 个已完成步骤，防止垂直高度过大
        max_visible_history = 4
        if len(completed_steps) > max_visible_history:
            folded_count = len(completed_steps) - max_visible_history
            lines.append(f"*(已完成前 {folded_count} 个执行步骤)*")
            visible_history = completed_steps[-max_visible_history:]
        else:
            visible_history = completed_steps
            
        for step in visible_history:
            lines.append(format_step_item(step, is_completed=True))
            
        if action_disp:
            lines.append(format_step_item(action_disp, is_completed=False))
        elif completed_steps:
            lines.append("• ⏳ 正在整理并生成最终回复...")
            
        steps_block = "\n".join(lines)
        if total_steps > 1:
            content = f"**🤖 任务多步骤执行中 (第 {total_steps} 步)：**\n\n{steps_block}\n\n*({time_hint}，后台正在全速推进中)*"
        else:
            content = f"**🤖 任务执行进展：**\n\n{steps_block}\n\n*(AI 正在运行底层命令或操作文件，{time_hint})*"
    else:
        content = f"**🤖 任务执行进展：**\n\n• ⏳ 正在执行底层操作...\n\n*(AI 正在运行底层命令或操作文件，{time_hint})*"
    
    if downloaded_file_name:
        if download_success:
            content = f"✅ 已成功获取资源：**{downloaded_file_name}**\n\n{content}"
        else:
            content = f"❌ 获取资源失败：**{downloaded_file_name}**\n\n{content}"

    if completed_steps:
        step_idx = len(completed_steps) + 1
        short_action = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', action_disp).strip() or "处理中"
        header_title = f"🛠️ [{step_idx}] {short_action}"
    else:
        header_title = f"🛠️ {action_disp}" if action_disp else "🛠️ 正在执行操作"
        if not header_title.startswith("🛠️"):
            header_title = f"🛠️ {header_title}"
            
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


def build_streaming_indicator(
    partial_text,
    tool_action=None,
    user_text="",
    think_seconds=0,
    completed_steps_count=0,
    total_planned_steps=0,
):
    title, _ = _guess_intent(user_text)
    header_title = f"⚡ 打字机生成中 | {title}"
    if len(header_title) > 28:
        header_title = header_title[:25] + "..."
    
    status_bar = f"*( ⏱️ 已耗时 {think_seconds}s"
    if total_planned_steps > 0:
        status_bar += f" | ✅ 全套 {total_planned_steps} 个任务规划步骤已全部完成"
    elif completed_steps_count > 0:
        status_bar += f" | ✅ 已完成 {completed_steps_count} 个步骤"
    elif tool_action:
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
