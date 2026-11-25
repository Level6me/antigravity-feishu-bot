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


def is_valid_schedule_expr(cron_expr: str, task_type: str = 'cron') -> bool:
    """Validate delay or standard 5-field cron expression."""
    expr = str(cron_expr or "").strip()
    if not expr:
        return False
    if task_type == 'delay':
        return bool(re.match(r'^(\d+)\s*([s|m|h|d])?$', expr.lower()))
    try:
        return bool(croniter.is_valid(expr))
    except Exception:
        return False


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
        self._loop_ref = None
        self._wakeup_event: asyncio.Event | None = None

    def start(self):
        """Start the cron engine background loop."""
        if self._running:
            return
        self._running = True
        self._loop_ref = asyncio.get_running_loop()
        self._wakeup_event = asyncio.Event()
        self._loop_task = asyncio.create_task(self._scheduler_loop())
        log.info("[CronEngine] Background event-driven scheduler loop started.")

    def stop(self):
        self._running = False
        if self._wakeup_event is not None:
            self._wakeup_event.set()
        if self._loop_task:
            self._loop_task.cancel()
        log.info("[CronEngine] Scheduler loop stopped.")

    def notify_schedule_changed(self):
        """Thread-safe instant wakeup when cron tasks are created, toggled, or deleted."""
        loop = self._loop_ref
        evt = self._wakeup_event
        if not self._running or loop is None or evt is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(evt.set)
        except RuntimeError:
            pass

    async def _scheduler_loop(self):
        # Grace period on startup to let main bot initialize
        await asyncio.sleep(2.0)

        while self._running:
            sleep_seconds = 3600.0
            try:
                if self._wakeup_event is not None:
                    self._wakeup_event.clear()

                now_f = time.time()
                now = int(now_f)
                active_tasks = await asyncio.get_running_loop().run_in_executor(None, get_active_cron_tasks)

                earliest_next_run = None
                for task in active_tasks:
                    task_id = task['id']
                    if task_id in self._running_tasks:
                        # Re-check reasonably soon if a task is mid-flight
                        earliest_next_run = min(earliest_next_run, now + 30) if earliest_next_run else (now + 30)
                        continue

                    next_run = task.get('next_run_at', 0)
                    # If next_run is uninitialized (0), calculate and save it
                    if next_run <= 0:
                        next_run = compute_next_run(task['cron_expr'], task.get('task_type', 'cron'), now)
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda tid=task_id, lr=task.get('last_run_at', 0), nr=next_run: update_cron_task_run(tid, lr, nr)
                        )
                        earliest_next_run = min(earliest_next_run, next_run) if earliest_next_run else next_run
                        continue

                    if now >= next_run:
                        tentative_next = compute_next_run(task['cron_expr'], task.get('task_type', 'cron'), now)
                        if task.get('task_type') == 'delay':
                            tentative_next = 0

                        from database import claim_cron_task
                        claimed = await asyncio.get_running_loop().run_in_executor(
                            None, lambda tid=task_id, tn=tentative_next: claim_cron_task(tid, now, tn)
                        )
                        if not claimed:
                            continue

                        self._running_tasks.add(task_id)
                        asyncio.create_task(self._run_task_wrapper(task))
                        if tentative_next > 0:
                            earliest_next_run = min(earliest_next_run, tentative_next) if earliest_next_run else tentative_next
                    else:
                        earliest_next_run = min(earliest_next_run, next_run) if earliest_next_run else next_run

                if earliest_next_run is not None:
                    sleep_seconds = max(0.5, min(3600.0, float(earliest_next_run) - time.time()))
                else:
                    # Zero active tasks: sleep up to 1 hour without any SQLite polling unless woken by notify_schedule_changed()
                    sleep_seconds = 3600.0

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[CronEngine] Exception in scheduler loop: {e}")
                sleep_seconds = 10.0

            try:
                if self._wakeup_event is not None:
                    await asyncio.wait_for(self._wakeup_event.wait(), timeout=sleep_seconds)
                else:
                    await asyncio.sleep(sleep_seconds)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    async def _run_task_wrapper(self, task):
        task_id = task['id']
        chat_id = task['chat_id']
        start_time = time.time()
        action_type = task.get("action_type", "reminder")
        name = task.get("name", "定时任务")
        prompt = task.get("prompt", "")
        
        log.info(f"[CronEngine] Triggering scheduled task '{name}' ({task_id}, type={action_type}) for chat {chat_id}")
        
        result_text = ""
        is_error = False
        
        try:
            if action_type == "reminder":
                # 纯提醒任务：直接发送提醒卡片，秒级直达且避免大模型冷启动与 Token 消耗
                from plugins.cron_scheduler.executors import build_reminder_card
                reminder_card = build_reminder_card(task)
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: send_card_to_chat_sdk(chat_id, reminder_card)
                )
                result_text = f"提醒已准时送达飞书：{prompt}"
            elif action_type == "shell":
                from plugins.cron_scheduler import executors as _exc
                is_success, res_str, _ = await _exc.execute_task(task, send_card_to_chat_sdk)
                is_error = not is_success
                result_text = res_str
            else:
                # 1. Send Start Interactive Card
                start_card = CardBuilder.build_cron_start_card(task)
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: send_card_to_chat_sdk(chat_id, start_card)
                )

                # 2. Prepare Isolated Session Context (prevent cron task from colliding with or polluting live user PersistentSession)
                base_session_data = await get_session_async(chat_id)
                session_data = dict(base_session_data)
                session_data["conversation"] = ""
                session_data["_isolated_pool_key"] = f"cron_{task_id}"
                session_data["_owner_chat_id"] = chat_id
                if task.get('project_path'):
                    session_data['project'] = task['project_path']
                
                # Import execute_antigravity dynamically to avoid circular dependencies
                from executor import execute_antigravity
                import app_state
                
                # Execute Agent task with empty message_id to send directly to chat
                # execute_antigravity 内部已完整发送 start_card + typing indicator + final_card
                # cron_engine 不再重复推送 result_card，避免用户收到两张卡片
                exec_err = await execute_antigravity(
                    chat_id=chat_id,
                    user_text=prompt,
                    message_id="",
                    bot_reply_msg_id=None,
                    session_data=session_data,
                    is_new_conversation=True,
                    system_instruction="你是由 Cron 引擎调度的自动化定时任务。请按照用户预设的 Prompt 准确执行，并生成详尽专业的结构化分析报告。",
                    final_prompt=prompt,
                    downloaded_file_name=None,
                    download_success=False,
                    running_processes=app_state.running_processes
                )
                is_error = bool(exec_err)
                
                # 仅提取 transcript 内容用于日志记录，不重复推送卡片
                from config import get_transcript_path
                from executor import extract_final_response_from_transcript
                
                conv_id = session_data.get("conversation", "") or session_data.get("conversation_id", "")
                transcript_path = get_transcript_path(conv_id) if conv_id else None
                
                extracted = extract_final_response_from_transcript(transcript_path) if (transcript_path and os.path.exists(transcript_path)) else None
                if is_error and not extracted:
                    result_text = "定时 Agent 任务执行异常或未能生成有效回复。"
                else:
                    result_text = extracted or "任务已成功触发执行完成。"
            
        except Exception as e:
            is_error = True
            result_text = f"定时任务执行过程中遇到异常: {str(e)}"
            log.error(f"[CronEngine] Task {task_id} failed: {e}")
        finally:
            try:
                duration_ms = int((time.time() - start_time) * 1000)
                now_ts = int(time.time())
                err_detail = result_text if is_error else ""
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: record_cron_log(task_id, "failed" if is_error else "success", result_text, err_detail, duration_ms, task_meta=task)
                )

                # 一次性倒计时任务触发完毕后立即从任务主表移除（仅保留 cron_logs 审计日志）
                if task.get('task_type') == 'delay':
                    from database import delete_cron_task
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: delete_cron_task(task_id, keep_logs=True)
                    )
                    log.info(f"[CronEngine] One-shot delay task '{name}' ({task_id}) completed and auto-removed (log preserved).")
                else:
                    latest_task = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: get_cron_task(task_id)
                    )
                    if latest_task:
                        eff_expr = latest_task.get('cron_expr') or task['cron_expr']
                        eff_type = latest_task.get('task_type') or task.get('task_type', 'cron')
                        next_run = compute_next_run(eff_expr, eff_type, now_ts)
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: update_cron_task_run(task_id, now_ts, next_run)
                        )
            except Exception as ex:
                log.error(f"[CronEngine] Error in finally state update: {ex}")
            finally:
                self._running_tasks.discard(task_id)


# Global singleton instance
cron_engine = CronEngine()
