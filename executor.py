import asyncio
import os
import time
import json
import uuid
import re
import subprocess
from config import ANTIGRAVITY_BIN, DANGEROUSLY_SKIP_PERMISSIONS, get_brain_dir, get_transcript_path, BASE_DIR
from logger import log
from card_builder import CardBuilder
from lark_client import patch_interactive_card_sdk, send_interactive_card_sdk, api_client
from multimodal import extract_and_upload_resources
from database import save_session_async
import stats
import app_state
from utils.auth import SCOPE_PROJECT, allow_execution, has_scope, is_admin

# 增量读取状态：transcript 路径 -> [offset, size]
_transcript_read_state = {}

# 追踪进程 CPU 时间差值: pid -> (last_check_timestamp, cumulative_cputime_seconds)
_process_cpu_tracker = {}

def _parse_cputime(s: str) -> float:
    """Parse cputime string like '0:00.02', '1:57.84', '01:23:45.67', '1-02:03:04' into seconds."""
    try:
        s = s.strip()
        if not s:
            return 0.0
        days = 0.0
        if '-' in s:
            d_part, s = s.split('-', 1)
            days = float(d_part)
        parts = s.split(':')
        if len(parts) == 3:
            h, m, sec = parts
            return days * 86400.0 + float(h) * 3600.0 + float(m) * 60.0 + float(sec)
        elif len(parts) == 2:
            m, sec = parts
            return days * 86400.0 + float(m) * 60.0 + float(sec)
        elif len(parts) == 1:
            return days * 86400.0 + float(parts[0])
    except Exception:
        return 0.0
    return 0.0

def _get_process_group_cpu_seconds(pid: int) -> float:
    """获取整个进程组（主进程 + 所有子进程/subagent）的累计 CPU 秒数。
    这样即使主进程在 I/O wait，只要子进程在工作也能检测到活跃。"""
    if not pid:
        return 0.0
    try:
        pgid = os.getpgid(pid)
        out = subprocess.check_output(
            ["ps", "-ax", "-o", "pgid=,cputime="],
            text=True, timeout=3
        ).strip()
        total = 0.0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                try:
                    if int(parts[0]) == pgid:
                        total += _parse_cputime(parts[1])
                except (ValueError, TypeError):
                    continue
        return total
    except Exception:
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "cputime="],
                text=True, timeout=2
            ).strip()
            return _parse_cputime(out)
        except Exception:
            return 0.0

def _is_process_group_active(pid: int) -> bool:
    """检测进程组是否有实际 CPU 活动（涵盖所有子进程/subagent）。
    只要进程组中有任何进程在消耗 CPU（>1% 占比），就认定为活跃。"""
    if not pid:
        return False
    current_cputime = _get_process_group_cpu_seconds(pid)
    now = time.time()
    if pid in _process_cpu_tracker:
        last_time, last_cputime = _process_cpu_tracker[pid]
        time_delta = now - last_time
        cpu_delta = current_cputime - last_cputime
        _process_cpu_tracker[pid] = (now, current_cputime)
        if time_delta > 0:
            return (cpu_delta / time_delta) > 0.01
    else:
        _process_cpu_tracker[pid] = (now, current_cputime)
    return False

def format_tool_action(tool_name: str = "", params: dict = None, raw_action: str = "") -> str:
    """Format and streamline tool actions to avoid verbose code snippets or multi-line commands."""
    params = params or {}
    tool_name = (tool_name or "").strip()

    # 1. 优先提取语义化的工具动作说明
    act = params.get("toolAction") or params.get("toolSummary") or raw_action or ""
    if isinstance(act, str):
        act = act.replace('"', '').replace("'", "").strip()
        act = re.sub(r'[\r\n\t]+', ' ', act).strip()
        act = re.sub(r'\s+', ' ', act)
    else:
        act = ""

    # 如果有明确简短的描述且不是底层代码/命令
    if act and not any(act.startswith(p) for p in ["python", "timeout", "sh", "bash", "cat", "echo", "export"]):
        if len(act) <= 25:
            return f"正在执行 {act}" if not act.startswith("正在") else act
        return f"正在执行 {act[:22]}..." if not act.startswith("正在") else f"{act[:22]}..."

    # 2. 针对 run_command 提取精炼用途，严禁直接展示多行代码
    cmd = (params.get("CommandLine") or params.get("command") or "").strip()
    if tool_name == "run_command" or cmd:
        clean_cmd = re.sub(r'^timeout\s+\d+\s+', '', cmd).strip()
        clean_cmd = re.sub(r'[\r\n\t]+', ' ', clean_cmd).strip(' "\'`')
        clean_cmd = re.sub(r'\s+', ' ', clean_cmd)

        if not clean_cmd:
            return "正在执行终端命令"
        first = clean_cmd.split()[0] if clean_cmd else ""
        if first in ("python", "python3"):
            if "sqlite3" in clean_cmd or "db" in clean_cmd:
                return "正在查询本地数据库"
            return "正在执行 Python 脚本"
        elif first in ("docker", "docker-compose"):
            return "正在查询 Docker 容器"
        elif first == "git":
            return "正在执行 Git 操作"
        elif first in ("grep", "rg"):
            return "正在检索文件内容"
        elif first in ("find", "fd", "ls"):
            return "正在查找系统文件"
        elif first in ("curl", "wget"):
            return "正在请求网络数据"
        else:
            brief = clean_cmd[:18] + "..." if len(clean_cmd) > 20 else clean_cmd
            return f"正在运行命令: {brief}"

    # 3. 针对常用文件与交互工具
    if tool_name == "view_file":
        path = params.get("AbsolutePath") or params.get("path") or ""
        fname = os.path.basename(path.strip(' "\'')) if path else ""
        return f"正在读取文件: {fname}" if fname else "正在读取文件"
    elif tool_name in ("write_to_file", "replace_file_content"):
        path = params.get("TargetFile") or params.get("path") or ""
        fname = os.path.basename(path.strip(' "\'')) if path else ""
        return f"正在编辑文件: {fname}" if fname else "正在修改文件"
    elif tool_name == "grep_search":
        q = (params.get("Query") or "").strip(' "\'')
        q = re.sub(r'[\r\n\t]+', ' ', q)[:12]
        return f"正在搜索代码: {q}" if q else "正在搜索代码"
    elif tool_name == "find_by_name":
        p = (params.get("Pattern") or "").strip(' "\'')
        p = re.sub(r'[\r\n\t]+', ' ', p)[:12]
        return f"正在查找文件: {p}" if p else "正在查找文件"
    elif tool_name == "list_dir":
        return "正在浏览目录结构"
    elif tool_name == "search_web":
        return "正在联网搜索资料"
    elif tool_name == "read_url_content":
        return "正在抓取网页内容"
    elif tool_name == "invoke_subagent":
        return "正在调度子智能体"
    elif tool_name == "manage_task":
        return "正在管理后台任务"

    # 工具名中文映射兜底
    TOOL_MAP = {
        "run_command": "执行终端命令",
        "view_file": "读取文件",
        "write_to_file": "写入文件",
        "replace_file_content": "编辑文件",
        "grep_search": "搜索代码",
        "find_by_name": "查找文件",
        "list_dir": "浏览目录",
        "search_web": "联网搜索",
        "read_url_content": "抓取网页",
        "manage_task": "管理任务",
        "invoke_subagent": "调度智能体",
    }
    if tool_name in TOOL_MAP:
        return f"正在{TOOL_MAP[tool_name]}"

    if act:
        return f"正在执行 {act[:20]}..." if len(act) > 22 else f"正在执行 {act}"

    return "正在执行操作"

async def _stream_typewriter_to_feishu(bot_reply_msg_id, full_text, user_text, think_seconds, feishu_call_fn, start_index=0):
    """Fast stream full_text onto Feishu interactive card without artificial delay."""
    if not bot_reply_msg_id or not full_text:
        return
        
    total_len = len(full_text)
    if start_index >= total_len:
        return
        
    remaining = total_len - start_index
    # 若剩余字符较少或已完整生成，直接一次性 patch 完成
    if remaining <= 400:
        card = CardBuilder.build_streaming_indicator(full_text, tool_action=None, user_text=user_text, think_seconds=think_seconds)
        await feishu_call_fn(
            lambda: patch_interactive_card_sdk(bot_reply_msg_id, card),
            label="typewriter patch fast"
        )
        return
        
    chunk_size = 500
    current_len = start_index
    while current_len < total_len:
        current_len += chunk_size
        if current_len > total_len:
            current_len = total_len
            
        typed_part = full_text[:current_len]
        card = CardBuilder.build_streaming_indicator(typed_part, tool_action=None, user_text=user_text, think_seconds=think_seconds)
        
        await feishu_call_fn(
            lambda: patch_interactive_card_sdk(bot_reply_msg_id, card),
            label="typewriter patch"
        )
        if current_len < total_len:
            await asyncio.sleep(0.05)

def parse_transcript_turn(transcript_path, initial_size=0):
    """
    解析本轮对话在 transcript.jsonl 中的状态。
    返回: (turn_lines, pending_tasks, is_turn_done)
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ([], set(), False)
    try:
        with open(transcript_path, 'r', encoding='utf-8', errors='ignore') as f:
            if initial_size > 0:
                try:
                    f.seek(initial_size)
                except Exception:
                    pass
            lines = f.readlines()
        if not lines:
            return ([], set(), False)

        turn_lines = []
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
            except Exception:
                continue
            if data.get("type") == "USER_INPUT":
                turn_lines = []
            else:
                turn_lines.append(data)

        if not turn_lines:
            return ([], set(), False)

        # 检查 Background Tasks 状态
        started_tasks = set()
        finished_tasks = set()
        for event in turn_lines:
            content = event.get("content") or ""
            if "Tool is running as a background task with task id:" in content or "task id:" in content:
                for m in re.finditer(r'task id:\s*([^\s\n]+)', content):
                    started_tasks.add(m.group(1).strip())
            if 'Task id "' in content and "finished with result" in content:
                for m in re.finditer(r'Task id\s*["\']([^"\']+)["\']\s+finished with result', content):
                    finished_tasks.add(m.group(1).strip())
            elif 'Task id "' in content and ("completed" in content or "failed" in content or "cancelled" in content):
                for m in re.finditer(r'Task id\s*["\']([^"\']+)["\']\s+(?:completed|failed|cancelled)', content):
                    finished_tasks.add(m.group(1).strip())

        pending_tasks = started_tasks - finished_tasks
        if pending_tasks:
            return (turn_lines, pending_tasks, False)

        # 检查最后一条事件是否已标记完成
        last_event = turn_lines[-1]
        is_done = False
        if (last_event.get("source") == "MODEL" and 
            last_event.get("type") == "PLANNER_RESPONSE" and 
            last_event.get("status") == "DONE"):
            tool_calls = last_event.get("tool_calls")
            if not tool_calls or len(tool_calls) == 0:
                is_done = True

        return (turn_lines, pending_tasks, is_done)
    except Exception as e:
        log.error(f"parse_transcript_turn error: {e}")
        return ([], set(), False)

def extract_final_response_from_turn_lines(turn_lines):
    if not turn_lines:
        return None
    try:
        valid_candidates = []
        for data in turn_lines:
            if data.get("source") == "MODEL" and data.get("type") == "PLANNER_RESPONSE":
                content = (data.get("content") or "").strip()
                if not content:
                    continue
                clean_text = extract_final_chinese_response(content)
                if not clean_text:
                    continue
                if (clean_text.startswith('Task "') or 
                    clean_text.startswith('<task_update') or
                    clean_text.startswith('The command exited with code') or
                    'completed with exit code' in clean_text):
                    continue
                if not valid_candidates or valid_candidates[-1] != clean_text:
                    valid_candidates.append(clean_text)

        if not valid_candidates:
            return None

        if len(valid_candidates) == 1:
            return valid_candidates[0]

        last_cand = valid_candidates[-1]
        if len(last_cand) >= 80 or '\n' in last_cand:
            prev_cand = valid_candidates[-2]
            if len(prev_cand) > 100 and prev_cand not in last_cand:
                return f"{prev_cand}\n\n{last_cand}"
            return last_cand
        else:
            detailed_cands = [c for c in valid_candidates if len(c) > len(last_cand) and len(c) >= 60]
            if detailed_cands:
                best_detailed = detailed_cands[-1]
                if last_cand not in best_detailed:
                    return f"{best_detailed}\n\n{last_cand}"
                return best_detailed
            return last_cand
    except Exception as e:
        log.error(f"extract_final_response_from_turn_lines error: {e}")
        return None

def extract_final_response_from_transcript(transcript_path, initial_size=0):
    turn_lines, _, _ = parse_transcript_turn(transcript_path, initial_size)
    return extract_final_response_from_turn_lines(turn_lines)

def is_transcript_turn_completed(transcript_path, initial_size=0):
    _, _, is_done = parse_transcript_turn(transcript_path, initial_size)
    return is_done

def extract_final_chinese_response(text):
    if not text:
        return ""
    
    # 1. 移除被 XML 标签包裹的思考过程与思维链，如 <thought>...</thought>, <thinking>...</thinking>, <think>...</think>
    text = re.sub(r'<(?:thought|thinking|think)>.*?</(?:thought|thinking|think)>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'^<(?:thought|thinking|think)>.*', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # 2. 移除 Task 执行日志 (例如 Task "timeout 15s bash ..." completed with exit code 0. Output: ...)
    text = re.sub(r'Task\s+"[^"]*"\s+(?:completed|finished)\s+with\s+exit\s+code\s+\d+.*?(?:Output:\s*.*?)?(?=\n\n|\Z)', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'<task_update\b.*?</task_update>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'<SYSTEM_MESSAGE>.*?</SYSTEM_MESSAGE>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 3. 移除终端原始退出状态及控制台片段 (例如 The command exited with code 0. Output: ...)
    text = re.sub(r'The command exited with code \d+\.?\s*(?:Output:\s*.*?)?(?=\n\n|\Z)', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'Warning: Permanently added [^\n]+\n*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'From https://github\.com/[^\n]+\n*', '', text, flags=re.IGNORECASE).strip()

    # 4. 移除开头的常见英文规划与描述前缀 (例如 "I will...", "Sure, I will...", "Let me...", "Here is...", "I need to...")
    text = re.sub(
        r'^(?:I\s+will|Sure,?\s+I\s+will|Let\s+me|Here\s+is|I\s+need\s+to|Based\s+on)\s+.*?\n\n',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

    # 5. 移除单行英文申明 (例如 "I will respond in Simplified Chinese.", "Sure, I will analyze the codebase in Chinese.")
    text = re.sub(
        r'^(?:I\s+will|Sure,?\s+I\s+will|Let\s+me)\s+(?:report|summarize|explain|respond|write|reply|communicate|answer|check|analyze)\b.+?(?:in\s+(?:Simplified\s+)?Chinese|below)\.?\s*',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

    # 6. 移除包含 Thinking Process, Thought:, Plan:, Thinking: 等小标题的说明段落
    text = re.sub(r'(?:\*\*|\#\#?\s*)?(?:Thinking Process|Thought|Thinking|Plan|Reasoning)(?:\*\*|:)?.*?(?=\n\n|\Z)', '', text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 7. 移除内部消息包装 (例如 [Message] timestamp=... content=...)
    text = re.sub(r'\[Message\]\s+timestamp=.*?content=.*?(?=\n\n|\Z)', '', text, flags=re.DOTALL).strip()

    # 8. 清理残留的动态思考占位符
    text = re.sub(r'\*\(\s*(?:🧠|🔍|⚙️|💡|🚀)?\s*正在.*?\)\*', '', text).strip()

    return text

def extract_choice_card_data(reply_text, transcript_path=None, initial_transcript_size=0):
    """
    Extract interactive choice options from:
    1. Explicit [CHOICE_CARD] Q: ... \n - opt1 \n - opt2 [/CHOICE_CARD] tags
    2. Antigravity 'ask_question' tool calls in transcript.jsonl
    3. Intelligent heuristic extraction for solution patterns (e.g. 方案一/方案二, 方案 1/方案 2, 选项 A/选项 B)
    Returns: (cleaned_reply_text, choice_card_dict_or_None)
    """
    if not reply_text:
        return reply_text, None

    # 1. 显式 [CHOICE_CARD] 标签优先匹配
    choice_pattern = re.compile(r'\[CHOICE_CARD\]\s*Q:\s*(.*?)\n(.*?)\s*\[/CHOICE_CARD\]', re.DOTALL | re.IGNORECASE)
    match = choice_pattern.search(reply_text)
    if match:
        question = match.group(1).strip()
        options_text = match.group(2).strip()
        options = [
            opt.strip()[1:].strip() if opt.strip().startswith(('-', '•', '*')) else opt.strip() 
            for opt in options_text.split('\n') if opt.strip()
        ]
        clean_text = choice_pattern.sub('', reply_text).strip()
        if options:
            return clean_text, {
                "question": question or "请选择：",
                "options": options
            }

    # 2. 从 transcript.jsonl 中捕获 ask_question 工具调用
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, 'r', encoding='utf-8', errors='ignore') as f:
                if initial_transcript_size > 0:
                    try:
                        f.seek(initial_transcript_size)
                    except Exception:
                        pass
                lines = f.readlines()
            for line in reversed(lines):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                except Exception:
                    continue
                if data.get("type") == "USER_INPUT":
                    break
                tool_calls = data.get("tool_calls") or []
                for tc in tool_calls:
                    if isinstance(tc, dict) and tc.get("name") == "ask_question":
                        args = tc.get("args", {})
                        questions = args.get("questions", [])
                        if questions and isinstance(questions, list):
                            first_q = questions[0]
                            q_text = first_q.get("question", "请选择：")
                            opts = first_q.get("options", [])
                            if opts:
                                return reply_text, {
                                    "question": q_text,
                                    "options": [str(o) for o in opts]
                                }
        except Exception as e:
            log.debug(f"Failed to extract ask_question from transcript: {e}")

    # 3. 智能启发式探测：方案/选项列表抽取
    heuristic_options = []
    lines = reply_text.split('\n')
    for line in lines:
        stripped = line.strip()
        # 匹配标题行或重点列表，例如：
        # "### 方案一：采用外部独立网关"
        # "### 方案 1: 热重载插件"
        # "1. **方案一（推荐）**：使用插件热重载"
        # "1. **方案 1**：修改配置"
        # "• **选项 A**：保留现状"
        m = re.match(
            r'^(?:[#*`\s\d.、•-]*)(方案\s*[一二三四五六七八九十0-9A-Za-z]+|选项\s*[A-Za-z0-9一二三四五]+)(?:（[^）]+）|\([^\)]+\))?[:：.、\s]+(.*)$',
            stripped
        )
        if m:
            prefix = m.group(1).strip()
            detail = m.group(2).strip().replace('*', '').replace('`', '').strip()
            # 过滤超长说明段落，只保留简要核心
            if len(detail) > 80:
                detail = detail[:80] + "..."
            opt_title = f"{prefix}: {detail}" if detail else prefix
            if opt_title not in heuristic_options:
                heuristic_options.append(opt_title)

    if 2 <= len(heuristic_options) <= 6:
        has_question_or_choice_intent = any(kw in reply_text for kw in [
            "请选择", "哪种方案", "哪个方案", "如何选择", "选择哪种", "建议采用", "可供选择",
            "方案一", "方案二", "方案 1", "方案 2", "您希望", "你希望", "选择如下", "以下方案",
            "方案选择", "采用哪种", "点击下方", "可选择"
        ])
        if has_question_or_choice_intent:
            return reply_text, {
                "question": "🎯 请选择您希望采用的方案：",
                "options": heuristic_options
            }

    return reply_text, None

async def execute_antigravity(
    chat_id, user_text, message_id, bot_reply_msg_id, session_data, 
    is_new_conversation, system_instruction, final_prompt, downloaded_file_name, 
    download_success, running_processes, is_resumed=False, task_meta=None
):
    loop = asyncio.get_running_loop()
    target_transcript_path = None

    # Daily execution limit for non-admin chats.
    if not allow_execution(chat_id):
        err_card = CardBuilder.build_ai_response(
            "⏳ 今日执行次数已达上限，请明天再试或联系管理员提升额度。",
            is_error=True,
        )
        try:
            if bot_reply_msg_id:
                await _feishu_call(lambda: patch_interactive_card_sdk(bot_reply_msg_id, err_card), label="daily-limit patch")
            else:
                await _feishu_call(lambda: send_interactive_card_sdk(message_id, err_card), label="daily-limit send")
        except Exception as e:
            log.error(f"[Executor] daily limit card failed: {e}")
        return True
    
    async def _feishu_call(sync_fn, timeout=30.0, label="feishu_call"):
        """在线程池中执行同步飞书 SDK 调用，并加 30s 超时硬上限。
        防止网络黑洞（NAT 会话挂起 / 中间设备静默丢包）让主循环无限阻塞。
        超时或异常统一返回 None，调用方负责降级。"""
        try:
            return await asyncio.wait_for(
                app_state.run_feishu_sync(loop, sync_fn),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            log.error(f"[Executor] {label} timed out after {timeout}s — network black-hole?")
            return None
        except Exception as e:
            log.error(f"[Executor] {label} failed: {e}")
            return None
    
    async def _sync_conversation_id_from_log(log_path):
        """从 antigravity 日志中解析 conversation ID 并同步落盘到 session_data。"""
        nonlocal target_transcript_path
        if not os.path.exists(log_path):
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                log_content = f.read()
            match = re.search(
                r'(?:Created|found|resuming|Loaded|Streaming|Forwarding user message to|Sending user message to|conversation[=:\s]+|conversationID=)\s*(?:conversation\s+)?["\'\s]*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})',
                log_content,
                re.IGNORECASE
            )
            if match:
                new_conv_id = match.group(1)
                if session_data.get("conversation") != new_conv_id:
                    session_data["conversation"] = new_conv_id
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=3.0)
                        log.info(f"[Executor] Synced and persisted conversation ID {new_conv_id} for chat {chat_id}")
                    except (asyncio.TimeoutError, Exception) as e:
                        log.error(f"save_session_async timed out or failed: {e}")
                if not target_transcript_path:
                    path = get_transcript_path(new_conv_id)
                    if os.path.exists(path):
                        target_transcript_path = path
            elif re.search(r'Warning:\s*conversation\s+["\']?[0-9a-fA-F-]+["\']?\s+not\s+found', log_content, re.IGNORECASE):
                if session_data.get("conversation") != "":
                    log.warning(f"[Executor] Conversation {session_data.get('conversation')} not found in agy store, resetting.")
                    session_data["conversation"] = ""
                    try:
                        await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=3.0)
                        log.info(f"[Executor] Conversation not found in log, reset conversation ID for chat {chat_id}")
                    except (asyncio.TimeoutError, Exception) as e:
                        log.error(f"save_session_async timed out or failed: {e}")
        except Exception as e:
            log.error(f"Failed to sync conversation id from log: {e}")

    async def _run_single_attempt(attempt):
        nonlocal bot_reply_msg_id, target_transcript_path
        logs_dir = os.path.abspath(os.path.join(BASE_DIR, "logs"))
        os.makedirs(logs_dir, exist_ok=True)
        log_file_path = os.path.join(logs_dir, f"agy_log_{uuid.uuid4().hex}.txt")
        cmd_args = [
            ANTIGRAVITY_BIN, 
            "-p", system_instruction + final_prompt, 
            "--output-format", "stream-json",
            "--model", session_data["model"],
            "--print-timeout", "60m",
            "--log-file", log_file_path
        ]
        is_admin_chat = is_admin(chat_id)
        if DANGEROUSLY_SKIP_PERMISSIONS and is_admin_chat:
            cmd_args.append("--dangerously-skip-permissions")
            
        cwd_dir = None
        allow_project = is_admin_chat or has_scope(chat_id, SCOPE_PROJECT)
        if allow_project and session_data.get("project") and session_data["project"] not in ["默认", "Default"]:
            proj_val = session_data["project"]
            if os.path.isdir(proj_val):
                cwd_dir = proj_val
                cmd_args.extend(["--add-dir", proj_val])
            else:
                cmd_args.extend(["--project", proj_val])
        
        cur_is_new_conv = (is_new_conversation and attempt == 1) or not session_data.get("conversation")
        if not cur_is_new_conv and session_data.get("conversation"):
            cmd_args.extend(["--conversation", session_data["conversation"]])
            
        target_transcript_path = None
        initial_transcript_size = 0
        if session_data.get("conversation"):
            conv_id = session_data["conversation"]
            path = get_transcript_path(conv_id)
            if os.path.exists(path):
                target_transcript_path = path
                initial_transcript_size = os.path.getsize(path)

        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        custom_env["PYTHONUNBUFFERED"] = "1"
        custom_env["STDOUT_LINE_BUFFERED"] = "1"
        sess_locked = False
        turn_stream = None
        try:
            from session_pool import session_pool
            conv_id_to_resume = session_data.get("conversation", "") if not is_new_conversation else ""
            sess = session_pool.get_or_create(chat_id, session_data["model"], cwd_dir, conversation_id=conv_id_to_resume)
            await sess.lock.acquire()
            sess_locked = True
            if not sess.is_alive():
                await sess.start()
            process = sess.process
            running_processes[chat_id] = process
            
            if attempt == 1:
                if is_resumed:
                    init_card = CardBuilder.build_typing_indicator(
                        downloaded_file_name, download_success,
                        f"🔄 检测到服务刚完成重启，正在自动继续执行未完成的任务...\n\n{user_text}"
                    )
                else:
                    init_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text)
            else:
                init_card = CardBuilder.build_typing_indicator(
                    downloaded_file_name, download_success,
                    f"🔄 首次发起因无响应挂死，正在自动为您重新发起第 {attempt}/2 次重试...\n\n{user_text}"
                )

            if bot_reply_msg_id:
                await _feishu_call(
                    lambda: patch_interactive_card_sdk(bot_reply_msg_id, init_card),
                    label=f"init-card patch attempt {attempt}"
                )
            else:
                new_id = await _feishu_call(
                    lambda: send_interactive_card_sdk(message_id, init_card),
                    label=f"init-card send attempt {attempt}"
                )
                if new_id:
                    bot_reply_msg_id = new_id
                    if task_meta is not None:
                        task_meta["bot_reply_msg_id"] = new_id
                        try:
                            from database import save_pending_task
                            save_pending_task(chat_id, task_meta)
                        except Exception:
                            pass
                else:
                    log.warning(f"[Executor] Initial typing card send failed for chat {chat_id}; will fall back to sending final card as new message")
            
            accumulated_text = ""
            stderr_text = ""
            stream_result_response = None
            stream_action = ""
            stream_is_done = False

            def get_latest_transcript_file():
                if session_data.get("conversation"):
                    conv_id = session_data["conversation"]
                    path = get_transcript_path(conv_id)
                    if os.path.exists(path):
                        return path
                brain_dir = get_brain_dir()
                if os.path.exists(brain_dir):
                    try:
                        for entry in os.listdir(brain_dir):
                            entry_path = os.path.join(brain_dir, entry)
                            if os.path.isdir(entry_path) and len(entry) == 36:
                                fp = os.path.join(entry_path, ".system_generated", "logs", "transcript.jsonl")
                                if os.path.exists(fp):
                                    mtime = os.path.getmtime(fp)
                                    if mtime >= process_start_time - 2:
                                        return fp
                    except Exception:
                        pass
                return None

            last_streamed_length = 0
            last_patch_time = time.time()
            process_start_time = time.time()
            last_progress_time = process_start_time
            last_cpu_check_time = 0
            is_cpu_busy = False
            last_tool_action = ""
            STALL_TIMEOUT = 300
            STALL_HARD_TIMEOUT = 600
            BASE_QUIET_WARNING_THRESHOLD = 120
            TOOL_QUIET_WARNING_THRESHOLD = 180

            turn_stream = await sess.send_prompt_and_stream(system_instruction + final_prompt)

            while True:
                event_data = await turn_stream.get_event(timeout=0.3)
                now = time.time()

                if event_data:
                    last_progress_time = now
                    event_type = event_data.get("event")
                    if event_type == "init":
                        conv_id = event_data.get("conversation_id")
                        if conv_id:
                            from session_pool import session_pool
                            session_pool.update_conversation_id(chat_id, conv_id)
                            if session_data.get("conversation") != conv_id:
                                session_data["conversation"] = conv_id
                                try:
                                    await asyncio.wait_for(save_session_async(chat_id, session_data), timeout=2.0)
                                except Exception:
                                    pass
                                path = get_transcript_path(conv_id)
                                if os.path.exists(path):
                                    target_transcript_path = path
                                    if initial_transcript_size == 0:
                                        initial_transcript_size = os.path.getsize(path)
                    elif event_type == "step_update":
                        step_up = event_data.get("step_update", {})
                        step_t = step_up.get("step_type")
                        if step_t == "agent_response":
                            delta = step_up.get("text_delta")
                            if delta:
                                accumulated_text += delta
                        elif step_t == "tool":
                            t_name = step_up.get("tool_name") or ""
                            t_info = step_up.get("tool_info", {})
                            t_params = t_info.get("parameters", {}) if isinstance(t_info, dict) else {}
                            stream_action = format_tool_action(t_name, t_params)
                            if stream_action and stream_action != last_tool_action:
                                last_tool_action = stream_action
                                from plugin_manager import plugin_manager
                                await plugin_manager.dispatch_tool_call(stream_action, {})
                    elif event_type == "result":
                        res_obj = event_data.get("result", {})
                        if res_obj.get("status") == "SUCCESS":
                            stream_result_response = res_obj.get("response")
                            stream_is_done = True
                            log.info(f"[Executor] Model turn completed via stream result event.")
                        else:
                            log.warning(f"[Executor] Model turn finished with status: {res_obj.get('status')}")
                        break
                    elif event_type in ["process_exit", "read_error"]:
                        log.warning(f"[Executor] Stream ended prematurely with {event_type}: {event_data}")
                        break
                    elif event_type == "raw_log":
                        accumulated_text += event_data.get("text", "") + "\n"

                think_seconds = int(now - process_start_time)
                stall_seconds = int(now - last_progress_time)

                if os.path.exists(log_file_path):
                    await _sync_conversation_id_from_log(log_file_path)

                if now - last_cpu_check_time >= 2.0:
                    last_cpu_check_time = now
                    is_cpu_busy = await loop.run_in_executor(
                        None, lambda: _is_process_group_active(process.pid)
                    )
                    if is_cpu_busy:
                        last_progress_time = now

                extend_until = app_state.extended_wait_chats.get(chat_id, 0)
                effective_stall_timeout = STALL_TIMEOUT
                if now < extend_until:
                    effective_stall_timeout += 300

                if think_seconds >= effective_stall_timeout and stall_seconds >= effective_stall_timeout:
                    if is_cpu_busy and stall_seconds < STALL_HARD_TIMEOUT:
                        log.info(f"[Executor] No event for {stall_seconds}s but process group still CPU-active, extending wait")
                    else:
                        log.error(f"[Executor] Process stalled for chat {chat_id}, closing session...")
                        await sess.close()
                        error_card = CardBuilder.build_stall_error_card(user_text, think_seconds, stall_seconds)
                        if bot_reply_msg_id:
                            await _feishu_call(
                                lambda: patch_interactive_card_sdk(bot_reply_msg_id, error_card),
                                label="stall error card patch"
                            )
                        stderr_text = f"⚠️ 任务已检测到卡死并自动终止（连续 {stall_seconds // 60} 分钟没有任何新 Token 或日志写入）。"
                        session_data["last_execution_error"] = True
                        return {"has_reply": True, "reply_text": stderr_text, "is_error": True}

                if think_seconds > 43200:
                    log.error(f"[Executor] Process timeout reached (43200s / 12h) for chat {chat_id}...")
                    await sess.close()
                    stderr_text = "⚠️ 执行超时 (12小时)：后台超大型任务已达到系统设定的 12 小时最高保护上限。"
                    session_data["last_execution_error"] = True
                    return {"has_reply": True, "reply_text": stderr_text, "is_error": True}

                has_active_tool = bool(stream_action or last_tool_action)
                effective_warning_threshold = TOOL_QUIET_WARNING_THRESHOLD if has_active_tool else BASE_QUIET_WARNING_THRESHOLD
                if now < extend_until:
                    effective_warning_threshold += 300

                partial_text = None
                if accumulated_text and accumulated_text.strip():
                    partial_text = extract_final_chinese_response(accumulated_text.strip())

                desired_patch_interval = 0.4
                if partial_text and len(partial_text.strip()) > 0:
                    clean_partial = re.sub(r'\[CHOICE_CARD\]\s*Q:.*?(?:\[/CHOICE_CARD\]|\Z)', '', partial_text, flags=re.DOTALL | re.IGNORECASE).strip()
                    if not clean_partial:
                        clean_partial = partial_text
                    target_len = len(clean_partial)
                    if last_streamed_length < target_len:
                        last_streamed_length = min(target_len, last_streamed_length + 150)
                    display_partial = clean_partial[:last_streamed_length]
                    indicator_card = CardBuilder.build_streaming_indicator(display_partial, stream_action or last_tool_action, user_text, think_seconds)
                    desired_patch_interval = 0.4
                elif stall_seconds >= effective_warning_threshold and not is_cpu_busy:
                    indicator_card = CardBuilder.build_stall_warning_card(user_text, think_seconds, stall_seconds)
                    desired_patch_interval = 2.0
                else:
                    display_action = stream_action or last_tool_action
                    if display_action:
                        indicator_card = CardBuilder.build_tool_indicator(display_action, user_text, downloaded_file_name, download_success, think_seconds)
                    else:
                        indicator_card = CardBuilder.build_typing_indicator(downloaded_file_name, download_success, user_text, think_seconds)
                    desired_patch_interval = 0.4

                if time.time() - last_patch_time >= desired_patch_interval:
                    last_patch_time = time.time()
                    if bot_reply_msg_id:
                        await _feishu_call(
                            lambda: patch_interactive_card_sdk(bot_reply_msg_id, indicator_card),
                            label="indicator patch"
                        )
            await turn_stream.aclose()

            is_error = (process.returncode != 0 and process.returncode is not None)
            session_data["last_execution_error"] = is_error

            transcript_path = target_transcript_path or await loop.run_in_executor(None, get_latest_transcript_file)

            # 优先从本轮直接实时接收到的流式文本 / 最终 result 中提取回答，完全避免读取到历史轮次的 transcript
            if stream_result_response and stream_result_response.strip():
                final_reply = stream_result_response.strip()
            elif accumulated_text and accumulated_text.strip():
                final_reply = accumulated_text.strip()
            else:
                final_reply = extract_final_response_from_transcript(transcript_path, initial_transcript_size)
            
            # 若提取最终回答失败且进程非正常退出（如 API 抖动 EOF、卡死强死、进程崩溃），且在允许重试范围内：
            # 暂不向飞书推送报错卡片，直接返回 has_reply=False 触发自动重试机制！
            if not final_reply and (process.returncode != 0 and process.returncode is not None) and attempt < 2:
                log.warning(f"[Executor] Attempt {attempt} failed without final response (returncode {process.returncode}). Bypassing error card send for auto-retry.")
                return {"has_reply": False, "returncode": process.returncode, "is_error": True}
            
            reply_text = final_reply or accumulated_text.strip() or ""
            reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
            reply_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', reply_text, flags=re.DOTALL).strip()
            reply_text = extract_final_chinese_response(reply_text)

            from plugin_manager import plugin_manager
            reply_text = await plugin_manager.dispatch_after_ai(reply_text, chat_id, session_data)
            
            if transcript_path and os.path.exists(transcript_path):
                try:
                    with open(transcript_path, 'r', encoding='utf-8') as f:
                        f.seek(initial_transcript_size)
                        for line in f.readlines():
                            try:
                                data = json.loads(line)
                                if data.get("type") == "GENERATE_IMAGE" or "generate_image" in line:
                                    match = re.search(r'Generated image is saved at (.*?\.(?:jpg|png|jpeg))', data.get("content", ""))
                                    if match:
                                        img_path = match.group(1)
                                        if img_path not in reply_text:
                                            reply_text += f"\n\n![Generated Image]({img_path})"
                            except Exception:
                                pass
                except Exception as e:
                    log.error(f"Failed to extract generated images from transcript: {e}")
            
                active_project = session_data.get("project")
                ws_root = session_data.get("workspace_root")
                allowed_dirs = [active_project, ws_root]
                
                await _feishu_call(
                    lambda: extract_and_upload_resources(reply_text, message_id, api_client, allowed_dirs),
                    timeout=120.0,
                    label="extract_and_upload_resources"
                )
                
                if os.path.exists(log_file_path):
                    await _sync_conversation_id_from_log(log_file_path)
                
                is_error = False
            if not reply_text:
                reply_text = stderr_text.strip() or "Sorry, I couldn't generate a response."
                is_error = True
            else:
                approx_tokens = len(user_text) + len(reply_text)
                if transcript_path and os.path.exists(transcript_path):
                    try:
                        current_size = os.path.getsize(transcript_path)
                        added_bytes = max(0, current_size - initial_transcript_size)
                        approx_tokens = int((len(user_text) + len(reply_text)) * 1.5 + added_bytes / 2.5)
                    except Exception as e:
                        log.error(f"Failed to calculate transcript token usage: {e}")
                        
                stats.record_tokens(approx_tokens)

            choice_card_data = None
            if not is_error:
                reply_text, choice_card_data = extract_choice_card_data(
                    reply_text,
                    transcript_path=transcript_path,
                    initial_transcript_size=initial_transcript_size
                )

            if reply_text:
                log.info(f"[Agent text]: {reply_text[:100]}...")
                
            if os.path.exists(log_file_path):
                await _sync_conversation_id_from_log(log_file_path)

            if not is_error and bot_reply_msg_id and reply_text:
                try:
                    safe_start_index = min(last_streamed_length, len(reply_text))
                    await _stream_typewriter_to_feishu(
                        bot_reply_msg_id, reply_text, user_text, think_seconds, _feishu_call,
                        start_index=safe_start_index
                    )
                except Exception as e:
                    log.error(f"[Executor] Typewriter streaming failed: {e}")

            final_card = CardBuilder.build_ai_response(
                reply_text, 
                choice_card_data=choice_card_data,
                current_model=session_data.get('model', 'Default'),
                current_project=session_data.get('project', '默认'),
                is_error=is_error,
                is_streaming=False,
                session_data=session_data
            )
            if bot_reply_msg_id:
                patch_ok = await _feishu_call(
                    lambda: patch_interactive_card_sdk(bot_reply_msg_id, final_card),
                    label="final-card patch"
                )
                if patch_ok is None:
                    from lark_client import send_reply_sdk
                    fallback_text = reply_text[:2000] if reply_text else "(AI 回复卡片发送失败，请查看工作区获取完整内容)"
                    await _feishu_call(
                        lambda: send_reply_sdk(message_id, fallback_text),
                        label="final-card text fallback"
                    )
            else:
                if message_id and str(message_id).startswith("om_"):
                    await _feishu_call(
                        lambda: send_interactive_card_sdk(message_id, final_card),
                        label="final-card send"
                    )
                else:
                    from lark_client import send_card_to_chat_sdk
                    await _feishu_call(
                        lambda: send_card_to_chat_sdk(chat_id, final_card),
                        label="final-card send to chat"
                    )
                
            return {"has_reply": bool(final_reply), "returncode": process.returncode, "is_error": is_error}
        
        finally:
            if turn_stream is not None:
                await turn_stream.aclose()
            if sess_locked and 'sess' in dir() and sess.lock.locked():
                sess.lock.release()
            if os.path.exists(log_file_path):
                try:
                    await _sync_conversation_id_from_log(log_file_path)
                finally:
                    try:
                        os.remove(log_file_path)
                    except Exception:
                        pass

    MAX_ATTEMPTS = 2
    for attempt in range(1, MAX_ATTEMPTS + 1):
        res = await _run_single_attempt(attempt)
        if res["has_reply"] or res["returncode"] == 0 or attempt == MAX_ATTEMPTS:
            return res["is_error"]
        log.warning(f"[Executor] Attempt {attempt}/{MAX_ATTEMPTS} failed/stalled without final response for chat {chat_id}. Automatically retrying attempt {attempt + 1}...")
        await asyncio.sleep(1.0)
    return True

