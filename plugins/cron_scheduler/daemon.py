#!/usr/bin/env python3
"""Standalone background daemon for cron_scheduler plugin.
Managed by PM2 or systemd for 7x24 high-precision scheduling.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

MAIN_BOT_DIR = "/home/jiang/github/antigravity-feishu-bot"
if MAIN_BOT_DIR not in sys.path:
    sys.path.append(MAIN_BOT_DIR)

from scheduler_db import (
    init_db,
    get_active_tasks,
    update_task_run,
    update_task_status,
    record_log,
    get_task
)
from scheduler import compute_next_run
from executors import execute_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [CronDaemon] - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("CronDaemon")

# 飞书 SDK 客户端初始化
lark_client = None
try:
    from config import APP_ID, APP_SECRET
    import lark_oapi
    if APP_ID and APP_SECRET:
        lark_client = lark_oapi.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
        logger.info("Lark OAPI Client initialized successfully.")
    else:
        logger.warning("APP_ID / APP_SECRET is empty in config, notifications will run in dry-run mode.")
async def send_card_to_feishu(chat_id: str, card_dict: dict) -> bool:
    """向指定飞书会话推送交互卡片"""
    target_chat = chat_id
    if not target_chat:
        try:
            from config import settings
            chats = [c.strip() for c in (settings.allowed_chats or "").split(",") if c.strip()]
            if chats:
                target_chat = chats[0]
        except Exception:
            pass

    if not lark_client or not target_chat:
        logger.info(f"[DryRun] Would send card to chat_id={target_chat}: {json.dumps(card_dict, ensure_ascii=False)[:100]}...")
        return True

    try:
        import lark_oapi
        req = lark_oapi.api.im.v1.CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(target_chat)
                .msg_type("interactive")
                .content(json.dumps(card_dict))
                .build()
            ).build()
        
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, lambda: lark_client.im.v1.message.create(req))
        if not resp.success():
            logger.error(f"Failed to send Feishu message: code={resp.code}, msg={resp.msg}")
            return False
        return True
    except Exception as e:
        logger.error(f"Exception sending Feishu card: {e}")
        return False


class CronDaemon:
    def __init__(self):
        self._running = False
        self._running_tasks = set()

    async def run_task_wrapper(self, task: dict):
        task_id = task["id"]
        task_name = task.get("name", "未命名任务")
        logger.info(f"Triggering task '{task_name}' ({task_id}) [Type: {task.get('task_type')}, Action: {task.get('action_type')}]")

        is_success, result_text, duration_ms = await execute_task(task, send_card_to_feishu)
        now_ts = int(time.time())

        # 1. 记录日志审计
        record_log(
            task_id=task_id,
            task_name=task_name,
            status="success" if is_success else "failed",
            result=result_text,
            duration_ms=duration_ms
        )

        # 2. 计算并更新下一次执行时间
        task_type = task.get("task_type", "cron")
        if task_type == "delay":
            # 倒计时一次性任务，完成后置为暂停
            update_task_status(task_id, False)
            update_task_run(task_id, now_ts, 0, inc_count=True)
            logger.info(f"Delay task '{task_name}' completed and deactivated.")
        else:
            # 周期性 Cron 任务，计算下次触发时间
            next_run = compute_next_run(task["cron_expr"], "cron", now_ts)
            update_task_run(task_id, now_ts, next_run, inc_count=True)
            logger.info(f"Cron task '{task_name}' completed. Next run at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_run))}")

        self._running_tasks.discard(task_id)

    async def start(self):
        init_db()
        self._running = True
        logger.info("🚀 CronDaemon scheduler loop started. Polling active tasks every 1 second.")

        while self._running:
            try:
                now_ts = int(time.time())
                active_tasks = get_active_tasks()

                for task in active_tasks:
                    task_id = task["id"]
                    if task_id in self._running_tasks:
                        continue

                    next_run = task.get("next_run_at", 0)
                    if next_run <= 0:
                        next_run = compute_next_run(task["cron_expr"], task.get("task_type", "cron"), now_ts)
                        update_task_run(task_id, task.get("last_run_at", 0), next_run, inc_count=False)
                        continue

                    if now_ts >= next_run:
                        self._running_tasks.add(task_id)
                        asyncio.create_task(self.run_task_wrapper(task))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)

            await asyncio.sleep(1.0)

        logger.info("CronDaemon scheduler loop stopped.")

def main():
    daemon = CronDaemon()
    try:
        asyncio.run(daemon.start())
    except (KeyboardInterrupt, SystemExit):
        logger.info("CronDaemon cleanly terminated.")
    except Exception as e:
        logger.error(f"CronDaemon unexpected crash: {e}", exc_info=True)


if __name__ == "__main__":
    main()
