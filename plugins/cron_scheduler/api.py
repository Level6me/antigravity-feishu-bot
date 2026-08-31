"""Python API interface for AI Agent and external modules to interact with cron_scheduler."""

import os
import sys
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from scheduler_db import (
    init_db,
    save_task,
    get_task,
    get_all_tasks,
    delete_task,
    update_task_status,
    get_recent_logs
)
from scheduler import compute_next_run


def create_task(
    name: str,
    prompt: str,
    cron_expr: str = "60s",
    task_type: str = "delay",
    action_type: str = "reminder",
    chat_id: str = "",
    category: str = "user",
    command: str = "",
    project_path: str = "",
    created_by: str = "ai_agent"
) -> Dict[str, Any]:
    """
    供 AI Agent 分析意图后直接调用的计划任务创建接口
    """
    init_db()
    now_ts = int(time.time())
    task_id = f"task_{category[:3]}_{now_ts}"
    next_run = compute_next_run(cron_expr, task_type, now_ts)

    task_data = {
        "id": task_id,
        "chat_id": chat_id,
        "category": category,
        "name": name,
        "task_type": task_type,
        "action_type": action_type,
        "cron_expr": cron_expr,
        "prompt": prompt,
        "command": command,
        "project_path": project_path,
        "is_active": True,
        "created_by": created_by,
        "created_at": now_ts,
        "next_run_at": next_run,
        "run_count": 0
    }

    ok = save_task(task_data)
    next_time_str = datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "success": ok,
        "task_id": task_id,
        "name": name,
        "task_type": task_type,
        "action_type": action_type,
        "cron_expr": cron_expr,
        "next_run_at": next_run,
        "next_run_readable": next_time_str,
        "prompt": prompt
    }


def list_tasks(chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    return get_all_tasks(chat_id)


def remove_task(task_id: str) -> bool:
    init_db()
    return delete_task(task_id)
