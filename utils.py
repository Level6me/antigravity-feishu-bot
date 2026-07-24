import time
from functools import wraps
from logger import log

def with_retry(max_retries=3, initial_delay=1.0, backoff_factor=2.0):
    """
    Exponential backoff retry decorator for synchronous functions.
    Catches network exceptions and retries with increasing delays.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        log.error(f"[Retry] Function {func.__name__} failed after {max_retries} retries. Error: {e}")
                        raise
                    log.warning(f"[Retry] Function {func.__name__} failed (attempt {attempt+1}/{max_retries}). Retrying in {delay}s... Error: {e}")
                    time.sleep(delay)
                    delay *= backoff_factor
        return wrapper
    return decorator


def get_context_usage_stats(session_data=None):
    """
    获取或估算当前对话会话的上下文 Token 使用统计信息
    """
    import os
    import json

    model_name = session_data.get("model", "Gemini 3.6 Flash (High)") if session_data else "Gemini 3.6 Flash (High)"
    if model_name in ["Default", "default"]:
        model_name = "Gemini 3.6 Flash (High)"

    def get_max_tokens(m_name):
        if not m_name:
            return 1000000
        mn = m_name.lower()
        if "claude" in mn:
            return 200000
        elif "gpt-4" in mn or "gpt4" in mn or "gpt-3" in mn or "gpt" in mn:
            return 128000
        elif "deepseek" in mn:
            return 64000
        elif "qwen" in mn:
            return 128000
        return 1000000

    max_tokens = get_max_tokens(model_name)

    conv_id = None
    transcript_path = None

    if session_data is not None:
        conv_id = session_data.get("conversation")
        if conv_id:
            p = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
            if os.path.exists(p):
                transcript_path = p
        else:
            # 新会话/重置上下文时，显式返回 0 使用量，严禁盲目读取历史会话日志
            return {
                "model": model_name,
                "conv_id": "新会话",
                "user_tokens": 0,
                "agent_tokens": 0,
                "tool_tokens": 0,
                "total_tokens": 0,
                "max_tokens": max_tokens,
                "free_tokens": max_tokens,
                "user_pct": 0.0,
                "agent_pct": 0.0,
                "tool_pct": 0.0,
                "total_pct": 0.0,
                "free_pct": 100.0,
                "steps_count": 0
            }
    else:
        # 仅在没有 session_data 时，只检查一级主会话目录
        brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
        if os.path.exists(brain_dir):
            newest_file = None
            newest_mtime = 0
            try:
                for entry in os.listdir(brain_dir):
                    entry_path = os.path.join(brain_dir, entry)
                    if os.path.isdir(entry_path):
                        fp = os.path.join(entry_path, ".system_generated", "logs", "transcript.jsonl")
                        if os.path.exists(fp):
                            mtime = os.path.getmtime(fp)
                            if mtime > newest_mtime:
                                newest_mtime = mtime
                                newest_file = fp
                                conv_id = entry
            except Exception:
                pass
            transcript_path = newest_file

    if not transcript_path or not os.path.exists(transcript_path):
        return {
            "model": model_name,
            "conv_id": conv_id or "N/A",
            "user_tokens": 0,
            "agent_tokens": 0,
            "tool_tokens": 0,
            "total_tokens": 0,
            "max_tokens": max_tokens,
            "free_tokens": max_tokens,
            "user_pct": 0.0,
            "agent_pct": 0.0,
            "tool_pct": 0.0,
            "total_pct": 0.0,
            "free_pct": 100.0,
            "steps_count": 0
        }

    def estimate_tokens(text):
        if not text:
            return 0
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_count = len(text) - cjk_count
        return int(cjk_count * 1.5 + other_count / 3.8)

    user_tokens = 0
    agent_tokens = 0
    tool_tokens = 0
    steps_count = 0

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    stype = data.get("type", "")
                    source = data.get("source", "")
                    cnt = data.get("content", "")
                    t_calls = data.get("tool_calls", "")

                    if stype == "CONVERSATION_HISTORY" and not cnt and not t_calls:
                        continue

                    steps_count += 1
                    toks = estimate_tokens(cnt) + estimate_tokens(t_calls)
                    
                    if stype == "USER_INPUT" or source == "USER_EXPLICIT":
                        user_tokens += toks
                    elif stype == "PLANNER_RESPONSE" or source == "MODEL":
                        agent_tokens += toks
                    else:
                        tool_tokens += toks
                except Exception:
                    pass
    except Exception as e:
        log.error(f"Error reading transcript for context stats: {e}")

    total_tokens = user_tokens + agent_tokens + tool_tokens
    free_tokens = max(0, max_tokens - total_tokens)

    user_pct = round((user_tokens / max_tokens) * 100, 2)
    agent_pct = round((agent_tokens / max_tokens) * 100, 2)
    tool_pct = round((tool_tokens / max_tokens) * 100, 2)
    total_pct = round((total_tokens / max_tokens) * 100, 2)
    free_pct = round(100.0 - total_pct, 2)

    return {
        "model": model_name,
        "conv_id": conv_id or "N/A",
        "user_tokens": user_tokens,
        "agent_tokens": agent_tokens,
        "tool_tokens": tool_tokens,
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
        "free_tokens": free_tokens,
        "user_pct": user_pct,
        "agent_pct": agent_pct,
        "tool_pct": tool_pct,
        "total_pct": total_pct,
        "free_pct": free_pct,
        "steps_count": steps_count
    }

