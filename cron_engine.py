"""Cron Engine: Background scheduled tasks engine for antigravity-feishu-bot."""

import asyncio
import os
import re
import time
from datetime import datetime
from croniter import croniter

from logger import log
from database import (
    get_active_cron_tasks,
    get_cron_task,
    save_cron_task,
    update_cron_task_run,
    record_cron_log,
    get_session_async
)
from cards import CardBuilder
from lark_client import send_card_to_chat_sdk


def parse_delay_seconds(expr: str) -> int:
    """Parse delay string like '600s', '10m', '2h', '300' into integer seconds."""
    expr = str(expr).strip().lower()
    match = re.match(r'^(\d+)\s*([s|m|h|d])?$', expr)
    if not match:
        return 300  # Default 5 minutes
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
        iter = croniter(cron_expr, now)
        return int(iter.get_next(float))
    except Exception as e:
        log.error(f"[cron_engine] Invalid cron expression '{cron_expr}': {e}")
        return int(now + 3600)  # Default fallback 1 hour


class CronEngine:
    def __init__(self):
        self._running = False
        self._loop_task = None
        self._running_tasks = set()

    def start(self):
        """Start the cron engine background loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._scheduler_loop())
        log.info("[CronEngine] Background scheduler loop started.")

    def stop(self):
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
        log.info("[CronEngine] Scheduler loop stopped.")

    async def _scheduler_loop(self):
        # Grace period on startup to let main bot initialize
        await asyncio.sleep(3.0)
        
        while self._running:
            try:
                now = int(time.time())
                active_tasks = await asyncio.get_running_loop().run_in_executor(None, get_active_cron_tasks)
                
                for task in active_tasks:
                    task_id = task['id']
                    if task_id in self._running_tasks:
                        continue  # Task is already executing
                    
                    next_run = task.get('next_run_at', 0)
                    # If next_run is uninitialized (0), calculate and save it
                    if next_run <= 0:
                        next_run = compute_next_run(task['cron_expr'], task.get('task_type', 'cron'), now)
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: update_cron_task_run(task_id, task.get('last_run_at', 0), next_run)
                        )
                        continue
                    
                    if now >= next_run:
                        self._running_tasks.add(task_id)
                        asyncio.create_task(self._run_task_wrapper(task))
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[CronEngine] Exception in scheduler loop: {e}")
            
            await asyncio.sleep(5.0)  # Check every 5 seconds

    async def _run_task_wrapper(self, task):
        task_id = task['id']
        chat_id = task['chat_id']
        start_time = time.time()
        
        log.info(f"[CronEngine] Triggering scheduled task '{task.get('name')}' ({task_id}) for chat {chat_id}")
        
        # 1. Send Start Interactive Card
        start_card = CardBuilder.build_cron_start_card(task)
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: send_card_to_chat_sdk(chat_id, start_card)
        )
        
        result_text = ""
        is_error = False
        
        try:
            # 2. Prepare Session Context
            session_data = await get_session_async(chat_id)
            if task.get('project_path'):
                session_data['project'] = task['project_path']
            
            prompt = task.get('prompt', '')
            
            # Import execute_antigravity dynamically to avoid circular dependencies
            from executor import execute_antigravity
            import app_state
            
            # Execute Agent task
            # Generate dummy message_id for cron run
            dummy_msg_id = f"cron_msg_{task_id}_{int(start_time)}"
            
            await execute_antigravity(
                chat_id=chat_id,
                user_text=prompt,
                message_id=dummy_msg_id,
                bot_reply_msg_id=None,
                session_data=session_data,
                is_new_conversation=False,
                system_instruction="你是由 Cron 引擎调度的自动化定时任务。请按照用户预设的 Prompt 准确执行，并生成详尽专业的结构化分析报告。",
                final_prompt=prompt,
                downloaded_file_name=None,
                download_success=False,
                running_processes=app_state.running_processes
            )
            
            # Get latest transcript result
            from config import get_transcript_path
            from executor import extract_final_response_from_transcript
            
            conv_id = session_data.get("conversation", "") or session_data.get("conversation_id", "")
            transcript_path = get_transcript_path(conv_id) if conv_id else None
            
            extracted = extract_final_response_from_transcript(transcript_path) if (transcript_path and os.path.exists(transcript_path)) else None
            result_text = extracted or "任务已成功触发执行完成。"
            
        except Exception as e:
            is_error = True
            result_text = f"定时任务执行过程中遇到异常: {str(e)}"
            log.error(f"[CronEngine] Task {task_id} failed: {e}")
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # 3. Record Execution Log & Update Next Run Time
        now_ts = int(time.time())
        next_run = compute_next_run(task['cron_expr'], task.get('task_type', 'cron'), now_ts)
        
        # If it's a one-shot delay task, deactivate after single execution
        if task.get('task_type') == 'delay':
            from database import update_cron_task_status
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: update_cron_task_status(task_id, False)
            )
        
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: update_cron_task_run(task_id, now_ts, next_run)
        )
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: record_cron_log(task_id, "failed" if is_error else "success", result_text, "", duration_ms)
        )
        
        # 4. Send Execution Result Card
        result_card = CardBuilder.build_cron_execution_card(task, result_text, is_error=is_error, duration_ms=duration_ms)
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: send_card_to_chat_sdk(chat_id, result_card)
        )
        
        self._running_tasks.remove(task_id)


# Global singleton instance
cron_engine = CronEngine()
