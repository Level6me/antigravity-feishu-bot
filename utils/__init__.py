"""Utility helpers package (formerly utils.py)."""

import time
from functools import wraps
from logger import log
from config import get_brain_dir, get_transcript_path

class NonRetryableError(Exception):
    """Raised for deterministic client/payload errors that must not be retried."""


def with_retry(max_retries=3, initial_delay=1.0, backoff_factor=2.0, retryable_exceptions=None):
    """
    Exponential backoff retry decorator for synchronous functions.
    By default retries on all Exception subclasses (covers Feishu API rate
    limits, network errors, etc.).  Pass *retryable_exceptions* as a tuple
    to restrict which exceptions trigger a retry.

    NOTE: decorated functions call time.sleep() and MUST be invoked from a
    thread (e.g. via asyncio.run_in_executor), never directly on the event loop.
    """
    retryable = retryable_exceptions or (Exception,)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except NonRetryableError:
                    raise
                except retryable as e:
                    if attempt == max_retries:
                        log.error(f"[Retry] Function {func.__name__} failed after {max_retries} retries. Error: {e}")
                        raise
                    log.warning(f"[Retry] Function {func.__name__} failed (attempt {attempt+1}/{max_retries}). Retrying in {delay}s... Error: {e}")
                    time.sleep(delay)
                    delay *= backoff_factor
        return wrapper
    return decorator


_SESSION_TOKEN_CACHE = {}
# 格式：{ conv_id: {"tokens": int, "mtime": float} }
# mtime 是上次读取 transcript 文件时的文件修改时间戳，防止文件缩小后还使用过期高缓存

_TRANSCRIPT_INCREMENTAL_CACHE = {}
# 格式：{ transcript_path: {"offset": int, "mtime": float, "user": int, "agent": int, "tool": int, "steps": int} }


def _estimate_tokens_fast(text) -> int:
    """Fast C-level UTF-8 byte-delta token estimator (~100x faster than per-char Python loop)."""
    if not text:
        return 0
    if not isinstance(text, str):
        import json
        text = json.dumps(text, ensure_ascii=False)
    char_len = len(text)
    if char_len == 0:
        return 0
    byte_len = len(text.encode("utf-8", errors="ignore"))
    # Each 3-byte UTF-8 CJK character contributes +2 bytes over ASCII char count
    cjk_count = min(char_len, max(0, (byte_len - char_len) // 2))
    other_count = char_len - cjk_count
    return int(cjk_count * 1.5 + other_count / 3.8)


def get_context_usage_stats(session_data=None):
    """
    获取或估算当前对话会话的上下文 Token 使用统计信息。
    v3.3 优化：
      1. 若会话携带实时 ACP `acp_last_usage`，直接以 O(1) 零磁盘 I/O 返回精确上下文窗口用量；
      2. 回退读取 `transcript.jsonl` 时采用字节偏移量游标增量解析 + C 层 UTF-8 字节差快速估算，
         避免长会话每次回复重扫数 MB 历史日志。
    """
    import os
    import json
    import time

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
    conv_id = session_data.get("conversation") if isinstance(session_data, dict) else None

    # Fast Path 1: O(1) zero-disk-IO usage directly from live ACP usage_update telemetry
    if isinstance(session_data, dict):
        acp_usage = session_data.get("acp_last_usage")
        if isinstance(acp_usage, dict):
            acp_used = int(acp_usage.get("used") or 0)
            acp_size = int(acp_usage.get("size") or 0)
            acp_conv = str(acp_usage.get("conv_id") or "")
            if acp_used > 0 and (not conv_id or not acp_conv or acp_conv == str(conv_id)):
                eff_max = acp_size if acp_size > 0 else max_tokens
                free_toks = max(0, eff_max - acp_used)
                total_pct = round((acp_used / eff_max) * 100, 2)
                free_pct = round(max(0.0, 100.0 - total_pct), 2)
                if free_pct >= 100.0:
                    free_pct = 99.99
                u_toks = int(acp_usage.get("user_tokens") or min(acp_used, 2500))
                rem_toks = max(0, acp_used - u_toks)
                a_toks = int(acp_usage.get("agent_tokens") or int(rem_toks * 0.45))
                t_toks = max(0, acp_used - u_toks - a_toks)
                return {
                    "model": model_name,
                    "conv_id": conv_id or acp_conv or "N/A",
                    "user_tokens": u_toks,
                    "agent_tokens": a_toks,
                    "tool_tokens": t_toks,
                    "total_tokens": acp_used,
                    "max_tokens": eff_max,
                    "free_tokens": free_toks,
                    "user_pct": round((u_toks / eff_max) * 100, 2),
                    "agent_pct": round((a_toks / eff_max) * 100, 2),
                    "tool_pct": round((t_toks / eff_max) * 100, 2),
                    "total_pct": total_pct,
                    "free_pct": free_pct,
                    "steps_count": int(acp_usage.get("steps_count") or 0),
                }

    transcript_path = None

    if session_data is not None:
        if conv_id:
            # 【修复】有明确 conv_id 时，直接使用对应 transcript，
            # 绝不进行"扫描最新文件"的兜底，防止误选 subagent transcript 导致数值大幅跳变
            p = get_transcript_path(conv_id)
            if os.path.exists(p):
                transcript_path = p
        else:
            # 仅在 conv_id 完全缺失时（新会话刚发起）才走一次兜底扫描
            brain_dir = get_brain_dir()
            if os.path.exists(brain_dir):
                now = time.time()
                newest_file = None
                newest_mtime = 0
                try:
                    for entry in os.listdir(brain_dir):
                        entry_path = os.path.join(brain_dir, entry)
                        if os.path.isdir(entry_path):
                            fp = os.path.join(entry_path, ".system_generated", "logs", "transcript.jsonl")
                            if os.path.exists(fp):
                                mtime = os.path.getmtime(fp)
                                if mtime > newest_mtime and (now - mtime < 120):
                                    newest_mtime = mtime
                                    newest_file = fp
                                    conv_id = entry
                except Exception:
                    pass
                if newest_file:
                    transcript_path = newest_file
    else:
        brain_dir = get_brain_dir()
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
            "conv_id": conv_id or "新会话",
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

    # Fast Path 2: Incremental byte-offset cursor over transcript.jsonl
    base_system_prompt_tokens = 2500
    try:
        st = os.stat(transcript_path)
        file_size = st.st_size
        current_mtime = st.st_mtime
    except Exception:
        file_size = 0
        current_mtime = 0.0

    inc = _TRANSCRIPT_INCREMENTAL_CACHE.get(transcript_path)
    if (
        inc is not None
        and file_size == inc["offset"]
        and current_mtime == inc["mtime"]
    ):
        user_tokens = inc["user"]
        agent_tokens = inc["agent"]
        tool_tokens = inc["tool"]
        steps_count = inc["steps"]
    else:
        if inc is not None and file_size > inc["offset"] and current_mtime >= inc["mtime"]:
            start_offset = inc["offset"]
            user_tokens = inc["user"]
            agent_tokens = inc["agent"]
            tool_tokens = inc["tool"]
            steps_count = inc["steps"]
        else:
            start_offset = 0
            user_tokens = base_system_prompt_tokens
            agent_tokens = 0
            tool_tokens = 0
            steps_count = 0

        new_offset = start_offset
        try:
            with open(transcript_path, "rb") as fb:
                if start_offset > 0:
                    fb.seek(start_offset)
                while True:
                    raw_line = fb.readline()
                    if not raw_line:
                        break
                    # If file grew without trailing newline yet and is non-empty, only commit complete lines
                    # unless we reached current EOF cleanly in a completed write
                    line_len = len(raw_line)
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        new_offset += line_len
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        # Incomplete trailing JSON line mid-flush: stop cursor before it so next poll re-reads cleanly
                        if not raw_line.endswith(b"\n"):
                            break
                        new_offset += line_len
                        continue

                    new_offset += line_len
                    stype = data.get("type", "")
                    source = data.get("source", "")
                    cnt = data.get("content", "")
                    t_calls = data.get("tool_calls", "")
                    thinking = data.get("thinking", "")
                    results = data.get("results", "") or data.get("output", "")

                    if stype == "CONVERSATION_HISTORY" and not cnt and not t_calls and not thinking:
                        continue

                    steps_count += 1
                    c_toks = _estimate_tokens_fast(cnt)
                    th_toks = _estimate_tokens_fast(thinking)
                    tc_toks = _estimate_tokens_fast(t_calls)
                    res_toks = _estimate_tokens_fast(results)

                    if stype in ["USER_INPUT", "USER_EXPLICIT"] or source == "USER_EXPLICIT":
                        user_tokens += c_toks
                    elif stype in ["PLANNER_RESPONSE", "MODEL_RESPONSE"] or source == "MODEL":
                        agent_tokens += c_toks + th_toks
                    else:
                        tool_tokens += c_toks + tc_toks + res_toks

            if len(_TRANSCRIPT_INCREMENTAL_CACHE) >= 256 and transcript_path not in _TRANSCRIPT_INCREMENTAL_CACHE:
                oldest_key = next(iter(_TRANSCRIPT_INCREMENTAL_CACHE))
                _TRANSCRIPT_INCREMENTAL_CACHE.pop(oldest_key, None)
            _TRANSCRIPT_INCREMENTAL_CACHE[transcript_path] = {
                "offset": new_offset,
                "mtime": current_mtime,
                "user": user_tokens,
                "agent": agent_tokens,
                "tool": tool_tokens,
                "steps": steps_count,
            }
        except Exception as e:
            log.error(f"Error reading transcript for context stats: {e}")

    total_tokens = user_tokens + agent_tokens + tool_tokens

    # 【修复】单会话单调递增防护（带 mtime 时间戳）：
    if conv_id and conv_id != "新会话":
        cached = _SESSION_TOKEN_CACHE.get(conv_id, {})
        cached_tokens = cached.get("tokens", 0)
        cached_mtime = cached.get("mtime", 0)

        if total_tokens < cached_tokens and current_mtime >= cached_mtime:
            total_tokens = cached_tokens
        else:
            _SESSION_TOKEN_CACHE[conv_id] = {"tokens": total_tokens, "mtime": current_mtime}

    free_tokens = max(0, max_tokens - total_tokens)

    user_pct = round((user_tokens / max_tokens) * 100, 2)
    agent_pct = round((agent_tokens / max_tokens) * 100, 2)
    tool_pct = round((tool_tokens / max_tokens) * 100, 2)
    total_pct = round((total_tokens / max_tokens) * 100, 2)

    if total_tokens > 0:
        free_pct = round(max(0.0, 100.0 - total_pct), 2)
        # 当实际有消耗且 free_pct 接近 100% 时，设为 99.99% 防呆，绝不出 100.0%
        if free_pct >= 100.0:
            free_pct = 99.99
    else:
        free_pct = 100.0

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


