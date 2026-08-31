"""Scheduler engine: time parsing, natural language intent understanding, and cron calculation."""

import re
import time
from typing import Optional, Dict, Any
from croniter import croniter


def parse_delay_seconds(expr: str) -> int:
    """Parse delay string like '600s', '10m', '2h', '1d', '300' into integer seconds."""
    expr = str(expr).strip().lower()
    match = re.match(r'^(\d+)\s*([s|m|h|d])?$', expr)
    if not match:
        return 300  # Default 5 minutes fallback
    val = int(match.group(1))
    unit = match.group(2)
    if unit == 'm':
        return val * 60
    elif unit == 'h':
        return val * 3600
    elif unit == 'd':
        return val * 86400
    return val


def compute_next_run(cron_expr: str, task_type: str = 'cron', base_time: float = None) -> int:
    """Compute next execution timestamp (epoch seconds)."""
    now = base_time or time.time()
    if task_type == 'delay':
        delay_sec = parse_delay_seconds(cron_expr)
        return int(now + delay_sec)
    
    # Standard cron expression
    try:
        iter_cron = croniter(cron_expr, now)
        return int(iter_cron.get_next(float))
    except Exception as e:
        print(f"[scheduler] Invalid cron expression '{cron_expr}': {e}")
        return int(now + 3600)  # Default fallback 1 hour


def clean_action_text(text: str) -> str:
    """清理自然语言中的前缀动词、助词及后缀，提取任务核心动作"""
    text = text.strip()
    text = re.sub(r"^(去|做|帮我|执行|提醒我|叫我|要|帮我看下|帮我查下|帮我把|把)\s*", "", text)
    text = re.sub(r"(一次|一下|一趟)$", "", text)
    return text.strip()


def parse_schedule_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    高精度中文及混合自然语言时间意图解析器
    支持:
    - 相对倒计时: "一分钟后提醒我喝水", "10分钟后检查日志", "半小时后开会", "2小时后备份"
    - 每日固定时间: "每天早上9点提醒我站会", "每天23:30检查数据备份"
    - 工作日固定时间: "工作日早上9点站会"
    - 周期性间隔: "每隔10分钟巡检一次服务器", "每小时检查一次网络"
    """
    text = text.strip()
    if not text:
        return None

    cn_num = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10
    }

    # 1.0 明天/后天这个时候: "明天这个时候提醒我喝水", "后天此时提醒我"
    m_tmr_now = re.search(r"(明天|后天|大后天)\s*(这个时候|此时|现在|同一时间)\s*(提醒我|叫我|帮我|执行|做|去)?\s*(.*)", text)
    if m_tmr_now:
        day_str, _, _, raw_act = m_tmr_now.groups()
        days = 1 if day_str == "明天" else (2 if day_str == "后天" else 3)
        sec = days * 86400
        act = clean_action_text(raw_act) or f"{day_str}定时提醒"
        prompt = f"时间到：提醒用户{act}" if ("提醒" not in act and "巡检" not in act and "检查" not in act) else act
        return {
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": f"{sec}s",
            "name": f"{day_str}提醒{act}" if not act.startswith(day_str) else act,
            "prompt": prompt
        }

    # 1.0.1 明天/后天具体时间点: "明天早上9点", "明天下午3点半", "明天23:30", "后天上午10点"
    m_future_day = re.search(r"(明天|后天|大后天)\s*(早上|上午|中午|下午|晚上|夜里)?\s*(\d{1,2}|[一二两三四五六七八九十]+)\s*([点|时|:：])\s*(\d{1,2}|半)?(分)?\s*(提醒我|叫我|帮我|执行|做)?\s*(.*)", text)
    if m_future_day:
        from datetime import datetime, timedelta
        day_str, period, hour_str, sep, min_str, _, _, raw_act = m_future_day.groups()
        days = 1 if day_str == "明天" else (2 if day_str == "后天" else 3)
        hour = int(hour_str) if hour_str.isdigit() else cn_num.get(hour_str, 9)
        if period in ["下午", "晚上", "夜里"] and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12

        minute = 0
        if min_str == "半":
            minute = 30
        elif min_str and min_str.isdigit():
            minute = int(min_str)

        now = datetime.now()
        target_date = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay_sec = max(10, int((target_date - now).total_seconds()))

        act = clean_action_text(raw_act) or f"{day_str}{hour:02d}:{minute:02d}定时任务"
        prompt = f"时间到：提醒用户{act}" if ("提醒" not in act and "巡检" not in act and "检查" not in act) else act
        return {
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": f"{delay_sec}s",
            "name": f"{day_str}{act}" if not act.startswith(day_str) else act,
            "prompt": prompt
        }

    # 1.1 相对倒计时: "半小时后 / 半个钟后"
    m_half = re.search(r"半(个?小时|个?钟头)后\s*(提醒我|叫我|帮我|执行|做|去)?\s*(.*)", text)
    if m_half:
        raw_act = m_half.group(3).strip()
        act = clean_action_text(raw_act) or "半小时定时提醒"
        prompt = f"时间到：提醒用户{act}" if ("提醒" not in act and "巡检" not in act and "检查" not in act) else act
        return {
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": "1800s",
            "name": f"提醒{act}" if not act.startswith("提醒") else act,
            "prompt": prompt
        }

    # 1.2 相对倒计时: 数字+单位后 (e.g. 1分钟后, 一分钟后, 10秒后, 2小时后, 1天后)
    m_delay = re.search(r"([0-9零一二两三四五六七八九十]+)\s*(个?半?小时|个?钟头|分钟|分|秒钟|秒|天|周)后\s*(提醒我|叫我|帮我|执行|做|去)?\s*(.*)", text)
    if m_delay:
        num_str, unit_str, _, raw_act = m_delay.groups()
        num = int(num_str) if num_str.isdigit() else cn_num.get(num_str, 1)

        sec = num * 60
        if "秒" in unit_str:
            sec = num
        elif "小时" in unit_str or "钟头" in unit_str:
            sec = num * 3600
        elif "天" in unit_str:
            sec = num * 86400
        elif "周" in unit_str:
            sec = num * 604800

        act = clean_action_text(raw_act) or f"{num_str}{unit_str}倒计时"
        prompt = f"时间到：提醒用户{act}" if ("提醒" not in act and "巡检" not in act and "检查" not in act) else act
        return {
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": f"{sec}s",
            "name": f"提醒{act}" if not act.startswith("提醒") else act,
            "prompt": prompt
        }

    # 2. 每天固定时间: "每天早上9点", "每天下午3点半", "每天23:30"
    m_daily = re.search(r"每天\s*(早上|上午|中午|下午|晚上|夜里)?\s*(\d{1,2}|[一二两三四五六七八九十]+)\s*([点|时|:：])\s*(\d{1,2}|半)?(分)?\s*(提醒我|叫我|帮我|执行|做)?\s*(.*)", text)
    if m_daily:
        period, hour_str, sep, min_str, _, _, raw_act = m_daily.groups()
        hour = int(hour_str) if hour_str.isdigit() else cn_num.get(hour_str, 9)
        if period in ["下午", "晚上", "夜里"] and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12

        minute = 0
        if min_str == "半":
            minute = 30
        elif min_str and min_str.isdigit():
            minute = int(min_str)

        act = clean_action_text(raw_act) or f"每日{hour:02d}:{minute:02d}定时任务"
        return {
            "task_type": "cron",
            "action_type": "reminder" if "提醒" in text else "ai_agent",
            "cron_expr": f"{minute} {hour} * * *",
            "name": f"每日{act}" if not act.startswith("每日") else act,
            "prompt": act
        }

    # 3. 工作日固定时间: "工作日早上9点站会"
    m_workday = re.search(r"工作日\s*(早上|上午|中午|下午|晚上)?\s*(\d{1,2}|[一二两三四五六七八九十]+)\s*([点|时|:：])\s*(\d{1,2}|半)?(分)?\s*(提醒我|叫我|帮我|执行|做)?\s*(.*)", text)
    if m_workday:
        period, hour_str, sep, min_str, _, _, raw_act = m_workday.groups()
        hour = int(hour_str) if hour_str.isdigit() else cn_num.get(hour_str, 9)
        if period in ["下午", "晚上"] and hour < 12:
            hour += 12
        minute = 0
        if min_str == "半":
            minute = 30
        elif min_str and min_str.isdigit():
            minute = int(min_str)

        act = clean_action_text(raw_act) or f"工作日{hour:02d}:{minute:02d}任务"
        return {
            "task_type": "cron",
            "action_type": "reminder" if "提醒" in text else "ai_agent",
            "cron_expr": f"{minute} {hour} * * 1-5",
            "name": f"工作日{act}" if not act.startswith("工作日") else act,
            "prompt": act
        }

    # 4. 周期性间隔: "每隔10分钟巡检一次服务器", "每小时检查一次网络"
    m_interval = re.search(r"每(隔)?\s*(\d+|[一二两三四五六七八九十]+)?\s*(分钟|分|小时|秒)\s*(提醒我|叫我|帮我|执行|巡检|检查|运行)?\s*(.*)", text)
    if m_interval:
        _, num_str, unit_str, _, raw_act = m_interval.groups()
        num = int(num_str) if (num_str and num_str.isdigit()) else (cn_num.get(num_str, 1) if num_str else 1)

        if "分" in unit_str:
            cron_expr = f"*/{num} * * * *" if num > 1 else "* * * * *"
        elif "小时" in unit_str:
            cron_expr = f"0 */{num} * * *" if num > 1 else "0 * * * *"
        else:
            cron_expr = f"{num}s"

        prefix_num = num_str if num_str else ""
        act = clean_action_text(raw_act) or f"每{prefix_num}{unit_str}任务"
        return {
            "task_type": "cron" if not cron_expr.endswith("s") else "delay",
            "action_type": "ai_agent" if ("巡检" in act or "检查" in act) else "reminder",
            "cron_expr": cron_expr,
            "name": f"定时{act}" if not act.startswith("定时") else act,
            "prompt": act
        }

    return None
