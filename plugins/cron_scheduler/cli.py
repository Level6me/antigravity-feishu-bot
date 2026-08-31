#!/usr/bin/env python3
"""CLI and Python API helper for AI Agent to manage scheduled tasks in cron_scheduler."""

import argparse
import json
import os
import sys
import time
from datetime import datetime

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


def add_task_cli(args):
    now_ts = int(time.time())
    task_id = args.id or f"task_usr_{now_ts}"
    
    # 确定 task_type 与 cron_expr
    if args.delay_seconds is not None and args.delay_seconds > 0:
        task_type = "delay"
        cron_expr = f"{args.delay_seconds}s"
    elif args.cron:
        task_type = "cron"
        cron_expr = args.cron.strip()
    elif args.expr:
        expr = args.expr.strip()
        task_type = "delay" if (expr.endswith("s") or expr.endswith("m") or expr.endswith("h") or expr.isdigit()) else "cron"
        cron_expr = expr
    else:
        task_type = "delay"
        cron_expr = "60s"

    next_run = compute_next_run(cron_expr, task_type, now_ts)

    task_data = {
        "id": task_id,
        "chat_id": args.chat_id or "",
        "category": args.category or "user",
        "name": args.name or "计划任务",
        "task_type": task_type,
        "action_type": args.action_type or "reminder",
        "cron_expr": cron_expr,
        "prompt": args.prompt or args.name or "",
        "command": args.command or "",
        "project_path": args.project_path or "",
        "is_active": True,
        "created_by": args.created_by or "ai_agent",
        "created_at": now_ts,
        "next_run_at": next_run,
        "run_count": 0
    }

    ok = save_task(task_data)
    next_time_str = datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M:%S")
    
    result = {
        "success": ok,
        "task_id": task_id,
        "name": task_data["name"],
        "task_type": task_type,
        "action_type": task_data["action_type"],
        "cron_expr": cron_expr,
        "next_run_at": next_run,
        "next_run_readable": next_time_str,
        "prompt": task_data["prompt"]
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return ok


def list_tasks_cli(args):
    tasks = get_all_tasks(args.chat_id if hasattr(args, "chat_id") else None)
    print(json.dumps(tasks, ensure_ascii=False, indent=2))


def delete_task_cli(args):
    ok = delete_task(args.id)
    print(json.dumps({"success": ok, "task_id": args.id}, ensure_ascii=False))


def main():
    init_db()
    parser = argparse.ArgumentParser(description="Cron Scheduler CLI for AI Agent")
    subparsers = parser.add_subparsers(dest="subcommand")

    # add
    p_add = subparsers.add_parser("add", help="Add a new scheduled task")
    p_add.add_argument("--name", required=True, help="Task name")
    p_add.add_argument("--prompt", required=True, help="Task prompt or reminder content")
    p_add.add_argument("--cron", help="Cron expression (e.g. '0 9 * * *')")
    p_add.add_argument("--delay-seconds", type=int, help="Delay in seconds (e.g. 60, 3600, 86400)")
    p_add.add_argument("--expr", help="Time expression (e.g. '60s', '10m', '0 9 * * *')")
    p_add.add_argument("--action-type", choices=["reminder", "shell", "ai_agent", "hardware_led"], default="reminder", help="Action type")
    p_add.add_argument("--command", default="", help="Shell command if action-type is shell")
    p_add.add_argument("--chat-id", default="", help="Target Feishu chat_id")
    p_add.add_argument("--category", default="user", help="user or system")
    p_add.add_argument("--id", help="Custom task ID")
    p_add.add_argument("--project-path", default="", help="Project path")
    p_add.add_argument("--created-by", default="ai_agent", help="Creator identifier")

    # list
    p_list = subparsers.add_parser("list", help="List tasks")
    p_list.add_argument("--chat-id", help="Filter by chat_id")

    # delete
    p_del = subparsers.add_parser("delete", help="Delete a task")
    p_del.add_argument("--id", required=True, help="Task ID to delete")

    args = parser.parse_args()
    if args.subcommand == "add":
        add_task_cli(args)
    elif args.subcommand == "list":
        list_tasks_cli(args)
    elif args.subcommand == "delete":
        delete_task_cli(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
