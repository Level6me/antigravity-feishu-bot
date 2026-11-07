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


# 宏观规划步骤上限：支持 2~8 步自适应弹性展示（轻量任务 2~4 步，常规工程 4~6 步，复杂多阶段工程 6~8 步）
MAX_PLANNED_STEPS = 8
MAX_VISIBLE_SUBSTEPS = 6


def parse_task_plan(text: str, max_steps: int = MAX_PLANNED_STEPS) -> list[str] | None:
    """Parse custom task plan tags emitted by the model: [TASK_PLAN] step1 | step2 ... [/TASK_PLAN].
    Supports elastic 2 ~ 8 macro steps based on task complexity without discarding middle stages.
    """
    if not text:
        return None
    match = re.search(r'\[TASK_PLAN\]\s*(.*?)\s*\[/TASK_PLAN\]', text, flags=re.DOTALL | re.IGNORECASE)
    is_closed = bool(match)
    if not match:
        match = re.search(r'\[TASK_PLAN\]\s*([^\n\r]+)', text, flags=re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
        if "|" in raw:
            steps = [s.strip() for s in raw.split("|") if s.strip()]
        elif is_closed:
            steps = [s.strip() for s in re.split(r'[\|\n\r]+', raw) if s.strip()]
        else:
            return None
        cleaned_steps = []
        for s in steps:
            cs = re.sub(r'^(?:步骤\s*\d+[:：\.\、\s]*|阶段\s*\d+[:：\.\、\s]*|[Pp]\d+[:：\.\、\s]*|\d+[\.\、\s]+)', '', s).strip()
            if not cs:
                continue
            if len(cs) > 44:
                cs = cs[:42].rstrip() + "…"
            cleaned_steps.append(cs)
        if len(cleaned_steps) >= 2:
            if len(cleaned_steps) > max_steps:
                # Preserve first step, last step, and evenly sample (max_steps - 2) intermediate milestones
                mid_pool = cleaned_steps[1:-1]
                keep_mid = max_steps - 2
                sampled_mid = [
                    mid_pool[int(round(i * (len(mid_pool) - 1) / max(1, keep_mid - 1)))]
                    for i in range(keep_mid)
                ]
                cleaned_steps = [cleaned_steps[0]] + sampled_mid + [cleaned_steps[-1]]
            return cleaned_steps
    return None


def generate_task_plan(user_text: str) -> list[str]:
    """Generate a context-aware, dynamic 3~7 step execution plan tailored to the user's prompt."""
    text = (user_text or "").lower()

    # 识别是否包含额外后置阶段（单元测试、Git提交推送、远端/树莓派部署）
    has_test_stage = any(k in text for k in ["测试", "单元测试", "unittest", "pytest", "跑通", "验证"])
    has_git_stage = any(k in text for k in ["git", "push", "推送", "提交", "github"])
    has_remote_stage = any(k in text for k in ["树莓派", "服务器", "ssh", "部署", "上线", "同步到", "pm2"])

    def _append_tail_stages(base_steps: list[str], final_delivery: str) -> list[str]:
        steps = list(base_steps)
        if has_test_stage and not any("测试" in s for s in steps):
            steps.append("运行单元测试与边界校验")
        if has_git_stage and not any("提交" in s or "推送" in s for s in steps):
            steps.append("提交代码变更并推送到远端仓库")
        if has_remote_stage and not any("部署" in s or "服务器" in s or "树莓派" in s for s in steps):
            steps.append("同步部署至目标节点并验证服务")
        steps.append(final_delivery)
        return steps[:MAX_PLANNED_STEPS]

    # 0. 只读探查 / 状态排查 (简洁 3 步)
    if any(k in text for k in ["看看有没有", "查一下", "是什么原因", "为什么", "看下状态", "运行的什么版本"]) and not any(
        k in text for k in ["修改", "修复", "重构", "实现", "重写", "重新设计"]
    ):
        return [
            "连接并读取目标环境与日志状态",
            "交叉比对上下文与定位核心原因",
            "汇总排查结论并输出诊断报告",
        ]

    # 1. 安全审查 / 漏洞排查 / 审计
    if any(k in text for k in ["审查", "漏洞", "安全", "排查", "审计"]):
        return _append_tail_stages(
            [
                "全面审查源码与系统调用链路",
                "排查潜在漏洞与逻辑边界风险",
                "定位缺陷根因并执行修复加固",
            ],
            "校验修复产物并输出分析报告",
        )

    # 2. 短指令确认 / 开始处理 / 继续执行
    if text.strip() in ["开始处理", "开始", "继续", "处理", "帮我处理", "执行", "开整", "搞起", "修复"] or "[continue_paused_task]" in text:
        return [
            "加载项目记忆胶囊与梳理待办事项",
            "执行核心模块与代码逻辑改造",
            "运行语法检查与自动化测试验证",
            "归档工程状态并交付最终结果",
        ]

    # 3. 报表 / 导出 / 数据分析 / Office 文档
    if any(k in text for k in ["excel", "xlsx", "csv", "表格", "报表", "统计", "导出", "word", "docx", "ppt", "pdf", "文档"]):
        if any(k in text for k in ["服务器", "ssh", "远程", "端口", "安全", "日志", "数据库", "db", "sql"]):
            return [
                "连接目标服务并采集原始日志数据",
                "清洗数据并执行多维指标统计计算",
                "自动化排版并生成结构化报表文件",
                "推送文件至飞书并输出分析结论",
            ]
        return [
            "梳理数据源结构与核心分析指标",
            "执行数据聚合与格式化排版处理",
            "生成导出文件并推送至当前会话",
        ]

    # 4. 编程开发 / 架构重新设计 / 修复 Bug
    if any(k in text for k in ["代码", "开发", "写一个", "脚本", "实现", "重构", "重新设计", "修复", "bug", "报错", "python", "前端", "后端", "逻辑", "步骤"]):
        return _append_tail_stages(
            [
                "审查现有模块代码与调用链路",
                "设计技术架构与边界处理方案",
                "重构核心业务逻辑与关联接口",
            ],
            "整理代码变更并输出交付总结",
        )

    # 5. 运维巡检 / 服务器管理 / Docker / 硬件调试
    if any(k in text for k in ["服务器", "ssh", "docker", "容器", "进程", "部署", "重启", "磁盘", "cpu", "内存", "树莓派", "openocd", "烧录"]):
        return [
            "探测目标主机环境与验证连通性",
            "检查系统进程状态与实时运行日志",
            "执行目标运维指令与配置变更",
            "校验服务健康状态并输出汇总报告",
        ]

    # 6. 全网搜索 / 调研 / 资料收集
    if any(k in text for k in ["搜索", "调研", "全网", "最新", "找一下", "收集"]):
        return [
            "拆解检索关键词并跨域抓取资料",
            "交叉比对多源信息与真伪过滤",
            "提炼核心要点并输出结构化报告",
        ]

    # 7. 通用多步骤兜底
    return _append_tail_stages(
        [
            "检索工作区环境与读取相关上下文",
            "执行核心操作与底层指令处理",
            "校验处理结果与生成数据产物",
        ],
        "整理交付内容并输出最终回复",
    )


def _classify_action_phase(action: str) -> str:
    """Classify a tool action into one of 4 execution phases: explore | implement | verify | deliver."""
    act = (action or "").lower()
    if any(k in act for k in ["推送代码", "git push", "提交 git", "git commit", "重载后台服务", "部署", "发送", "上传", "导出"]):
        return "deliver"
    if any(k in act for k in ["测试", "unittest", "pytest", "语法", "py_compile", "编译", "构建", "校验", "验证"]):
        return "verify"
    if any(k in act for k in ["编辑文件", "写入文件", "修改文件", "生成", "创建", "重构", "执行脚本", "python 分析", "python 处理", "python 脚本", "远程连接"]):
        return "implement"
    return "explore"


_STEP_STOP_KEYWORDS = {"正在", "执行", "进行", "处理", "操作", "任务", "步骤", "模块", "文件", "代码", "系统", "相关", "结果"}


def match_step_index(planned_steps: list[str], current_idx: int, action: str, completed_count: int) -> int:
    """Advance current_idx forward using Phase-Aware Bounded Lookahead + Budget-Proportional Pacing.

    Strictly monotonic (never returns < current_idx) and guards the final stage (`total - 1`)
    against premature jumps during early exploration/implementation tool calls.
    """
    if not planned_steps:
        return 0
    total = len(planned_steps)
    if total <= 1:
        return 0
    current_idx = max(0, min(current_idx, total - 1))
    action_lower = (action or "").lower()
    phase = _classify_action_phase(action_lower)

    # Guard: do not allow jumping into the very last step (total - 1) during early tool calls
    # unless the action is an explicit final delivery action (e.g. git push / service reload / file upload).
    max_reachable_idx = (total - 1) if (phase == "deliver" or completed_count >= max(4, total * 2)) else max(0, total - 2)

    # 1. 局部前瞻语义匹配（仅向后探测 1~2 步，防止首步探查直接跳到末步）
    lookahead_limit = min(max_reachable_idx + 1, current_idx + 3)
    for j in range(current_idx + 1, lookahead_limit):
        step_text = planned_steps[j].lower()
        keywords = [
            w for w in re.findall(r'[\u4e00-\u9fa5]{2,4}|[a-zA-Z0-9_]{3,}', step_text)
            if w not in _STEP_STOP_KEYWORDS
        ]
        if any(kw in action_lower for kw in keywords):
            return max(current_idx, min(j, max_reachable_idx))
        # Phase-transition matching for the immediate next step (j == current_idx + 1)
        if j == current_idx + 1:
            step_phase = _classify_action_phase(step_text)
            if phase == step_phase and phase in ("implement", "verify", "deliver"):
                return max(current_idx, min(j, max_reachable_idx))

    # 2. 针对收尾交付动作（如 git push / 重载服务 / 上传产物），允许推进至对应交付阶段
    if phase == "deliver":
        for j in range(total - 1, current_idx, -1):
            if _classify_action_phase(planned_steps[j]) == "deliver":
                return j
        return max(current_idx, total - 1)

    if phase == "verify":
        for j in range(current_idx + 1, max_reachable_idx + 1):
            if _classify_action_phase(planned_steps[j]) == "verify":
                return j

    # 3. 阶段受限的平滑比例推进（每约 3 次底层工具调用推进 1 个宏观步，且不超过当前动作所处阶段的合理上限）
    phase_ceiling_ratio = {
        "explore": 0.38,
        "implement": 0.72,
        "verify": 0.88,
        "deliver": 1.0,
    }.get(phase, 0.65)
    max_allowed_by_phase = min(max_reachable_idx, int(total * phase_ceiling_ratio))
    max_allowed_by_phase = max(0, max_allowed_by_phase)

    paced_idx = completed_count // 3
    suggested_idx = min(max_allowed_by_phase, paced_idx)
    return max(current_idx, suggested_idx)


def _collapse_consecutive_steps(steps: list[str]) -> list[tuple[str, int]]:
    """Collapse consecutive identical step labels into (label, count) pairs."""
    collapsed: list[tuple[str, int]] = []
    for s in (steps or []):
        if not s:
            continue
        if collapsed and collapsed[-1][0] == s:
            collapsed[-1] = (s, collapsed[-1][1] + 1)
        else:
            collapsed.append((s, 1))
    return collapsed


def _compute_monotonic_progress_pct(
    current_idx: int,
    total: int,
    is_all_completed: bool,
    tool_count: int,
    min_pct: int = 0,
) -> int:
    """Compute a strictly monotonic (non-decreasing) progress percentage in [10, 100].

    Replaces the old `(tool_count % 3)` modulo formula (which caused 91% -> 80% -> 83% -> 91% oscillations
    whenever a stage spanned more than 3 tool calls) with a smooth hyperbolic saturation curve
    plus an explicit `min_pct` high-water mark floor.
    """
    if is_all_completed:
        return 100
    total = max(1, total)
    done_stages = max(0, min(current_idx, total - 1))
    # Map stages 0 .. (total - 1) across [8%, 92%], reserving 93%~95% for late multi-tool convergence
    stage_span = 84.0 / total
    base_pct = 8.0 + done_stages * stage_span
    tools_in_stage = max(0, tool_count - done_stages * 2)
    # Strictly increasing saturation curve in [0.12, 0.88) as tools_in_stage grows: never oscillates or resets!
    intra_ratio = 0.12 + 0.76 * (1.0 - 1.0 / (1.0 + 0.35 * tools_in_stage))
    raw_pct = int(round(base_pct + stage_span * intra_ratio))
    clamped_pct = max(10, min(95, raw_pct))
    return max(min(95, int(min_pct or 0)), clamped_pct)


def _build_visual_progress_bar(
    current_idx: int,
    total: int,
    is_all_completed: bool,
    tool_count: int,
    think_seconds: int,
    bar_width: int = 10,
    min_pct: int = 0,
) -> str:
    """Build a 10-cell block progress bar (■■■■■□□□□□) and telemetry line for the card top."""
    if total <= 0:
        total = 1
    pct = _compute_monotonic_progress_pct(current_idx, total, is_all_completed, tool_count, min_pct=min_pct)
    if is_all_completed:
        bar = "■" * bar_width
        stage_str = f"全部 {total}/{total} 阶段已完成"
    else:
        done_stages = max(0, min(current_idx, total - 1))
        filled = max(1, min(bar_width - 1, int(round((pct / 100.0) * bar_width))))
        bar = ("■" * filled) + ("□" * (bar_width - filled))
        stage_str = f"阶段 {done_stages + 1}/{total}"

    parts = [f"`{bar}` **{pct}%**", stage_str]
    if tool_count > 0:
        parts.append(f"已调用 {tool_count} 次工具")
    if think_seconds > 0:
        parts.append(f"耗时 {think_seconds}s")
    return " · ".join(parts)


def build_planned_steps_indicator(
    planned_steps: list[str],
    current_step_idx: int = 0,
    tool_action: str = "",
    user_text: str = "",
    think_seconds: int = 0,
    downloaded_file_name: str = None,
    download_success: bool = True,
    is_all_completed: bool = False,
    process_status: dict = None,
    completed_steps: list[str] = None,
    min_pct: int = 0,
) -> dict:
    """Build a Dual-Layer Adaptive Progress Card:
    - Top: Visual progress bar + stage/tool/time telemetry.
    - Layer 1: Macro planned roadmap (supports 2~8 steps) with live indented sub-action under the active stage.
    - Layer 2: Recent completed tool operations log (with consecutive deduplication `×N`).
    """
    completed_steps = completed_steps or []
    planned_steps = planned_steps[:MAX_PLANNED_STEPS]
    total = len(planned_steps)
    current_step_idx = max(0, min(current_step_idx, total - 1))
    total_tools_run = len(completed_steps) + (1 if (tool_action and not is_all_completed) else 0)

    progress_bar_line = _build_visual_progress_bar(
        current_step_idx, total, is_all_completed, total_tools_run, think_seconds, min_pct=min_pct
    )

    lines = []
    for i, raw_step in enumerate(planned_steps):
        step = raw_step.strip()
        core = re.sub(r'^[•\-\*\s✅⏳⚪\d\.\、\[\]]+\s*', '', step).strip()
        core = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', core).strip()

        if is_all_completed or i < current_step_idx:
            completed_text = f"已{core}" if not core.startswith("已") else core
            lines.append(f"• ✅ **{i+1}. {completed_text}**")
        elif i == current_step_idx:
            active_text = f"正在{core}" if not core.startswith("正在") else core
            lines.append(f"• ⏳ **{i+1}. {active_text}**")
            if tool_action:
                act_disp = clean_action_text(tool_action, max_len=36)
                act_core = re.sub(r'^(?:正在执行|正在运行|正在|已执行|已运行|已)\s*', '', act_disp).strip()
                if act_core:
                    lines.append(f"  └─ 🔄 *当前动作：正在{act_core}...*")
        else:
            lines.append(f"• ⚪ {i+1}. {core}")

    steps_block = "\n".join(lines)

    # Layer 2: 实时底层工具操作轨迹回放 (折叠连续重复项，展示最近 MAX_VISIBLE_SUBSTEPS 项)
    substeps_block = ""
    if completed_steps:
        collapsed = _collapse_consecutive_steps(completed_steps)
        sub_lines = []
        if len(collapsed) > MAX_VISIBLE_SUBSTEPS:
            hidden_cnt = sum(c for _, c in collapsed[:-MAX_VISIBLE_SUBSTEPS])
            sub_lines.append(f"<font color='grey'>*(前序已完成 {hidden_cnt} 项底层操作)*</font>")
            visible_collapsed = collapsed[-MAX_VISIBLE_SUBSTEPS:]
        else:
            visible_collapsed = collapsed

        for step_lbl, cnt in visible_collapsed:
            formatted = format_step_item(step_lbl, is_completed=True, max_len=36)
            # Replace heavy ✅ with lightweight ✓ for sub-actions so macro steps stand out visually
            formatted = formatted.replace("• ✅ ", "  • `✓` ")
            if cnt > 1:
                formatted += f" `(×{cnt})`"
            sub_lines.append(formatted)
        substeps_block = f"\n\n**🔧 实时底层执行轨迹 (已完成 {len(completed_steps)} 步)：**\n" + "\n".join(sub_lines)

    # 智能进程诊断状态横幅
    diag_banner = ""
    if process_status and process_status.get("active") and not is_all_completed:
        act_desc = process_status.get("status_desc") or ""
        workers = process_status.get("workers") or []
        detail_cmd = process_status.get("detail_cmd") or ""
        worker_info = f"`{', '.join(workers[:3])}`" if workers else ""
        if act_desc:
            diag_banner = f"\n\n> ⚙️ **底层任务探针**：{act_desc}"
            if worker_info and not detail_cmd:
                diag_banner += f" (活动进程: {worker_info})"
            if detail_cmd:
                diag_banner += f"\n> 💻 **实际运行命令**：`{detail_cmd[:80]}`"

    if is_all_completed:
        content = (
            f"📊 {progress_bar_line}\n\n"
            f"**📋 任务规划与执行清单 (共 {total} 步)：**\n\n{steps_block}{substeps_block}\n\n"
            f"*(全部规划阶段已执行完毕，正在输出总结回复)*"
        )
    else:
        content = (
            f"📊 {progress_bar_line}\n\n"
            f"**📋 任务规划与执行清单 (第 {current_step_idx + 1}/{total} 阶段)：**\n\n{steps_block}{substeps_block}{diag_banner}"
        )

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
    process_status=None,
    min_pct=0,
):
    completed_steps = completed_steps or []
    # Fallback: If model did not emit [TASK_PLAN], automatically synthesize a dynamic 3~7 step macro plan
    if not planned_steps and (tool_action or completed_steps):
        planned_steps = generate_task_plan(user_text)[:MAX_PLANNED_STEPS]
        latest_act = tool_action or (completed_steps[-1] if completed_steps else "")
        current_step_idx = match_step_index(
            planned_steps, current_step_idx, latest_act, len(completed_steps)
        )

    if planned_steps:
        return build_planned_steps_indicator(
            planned_steps=planned_steps[:MAX_PLANNED_STEPS],
            current_step_idx=current_step_idx,
            tool_action=tool_action,
            user_text=user_text,
            think_seconds=think_seconds,
            downloaded_file_name=downloaded_file_name,
            download_success=download_success,
            is_all_completed=is_all_completed,
            process_status=process_status,
            completed_steps=completed_steps,
            min_pct=min_pct,
        )
    action_disp = clean_action_text(tool_action) if tool_action else ""
    
    time_hint = f"已运行 {think_seconds}s" if think_seconds > 0 else "请稍候..."
    
    # 智能进程诊断状态横幅
    diag_banner = ""
    if process_status and process_status.get("active") and not is_all_completed:
        act_desc = process_status.get("status_desc") or ""
        workers = process_status.get("workers") or []
        detail_cmd = process_status.get("detail_cmd") or ""
        worker_info = f"`{', '.join(workers[:3])}`" if workers else ""
        if act_desc:
            diag_banner = f"\n\n> ⚙️ **底层任务探针**：{act_desc}"
            if worker_info and not detail_cmd:
                diag_banner += f" (活动进程: {worker_info})"
            if detail_cmd:
                diag_banner += f"\n> 💻 **实际运行命令**：`{detail_cmd[:80]}`"

    # 构建多步骤清单
    if completed_steps or action_disp:
        total_steps = len(completed_steps) + (1 if action_disp else 0)
        lines = []
        
        # 最多在卡片中展开显示最近 8 个已完成步骤，防止垂直高度过大
        max_visible_history = 8
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
        filled_fallback = max(1, min(9, total_steps))
        fallback_bar = ("■" * filled_fallback) + ("□" * (10 - filled_fallback))
        if total_steps > 1:
            content = f"📊 `{fallback_bar}` · **第 {total_steps} 步** · {time_hint}\n\n**🤖 任务多步骤执行中：**\n\n{steps_block}{diag_banner}\n\n*(后台正在全速推进中)*"
        else:
            content = f"📊 `{fallback_bar}` · {time_hint}\n\n**🤖 任务执行进展：**\n\n{steps_block}{diag_banner}\n\n*(AI 正在运行底层命令或操作文件)*"
    else:
        content = f"📊 `■□□□□□□□□□` · {time_hint}\n\n**🤖 任务执行进展：**\n\n• ⏳ 正在执行底层操作...{diag_banner}\n\n*(AI 正在运行底层命令或操作文件)*"
    
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
    
    status_bar = f"*( 📊 `■■■■■■■■■■` **100%** | ⏱️ 已耗时 {think_seconds}s"
    if total_planned_steps > 0:
        status_bar += f" | ✅ 全套 {total_planned_steps} 个规划阶段已完成"
    elif completed_steps_count > 0:
        status_bar += f" | ✅ 已完成 {completed_steps_count} 项底层操作"
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
