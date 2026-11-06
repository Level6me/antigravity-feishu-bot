import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import patch


class TestCronOneshotLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_fd, self.tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)
        import database
        import plugins.cron_scheduler.scheduler_db as sdb
        self._orig_db_file = database.DB_FILE
        self._orig_default_db = sdb.DEFAULT_DB_PATH
        database.DB_FILE = self.tmp_db
        sdb.DEFAULT_DB_PATH = self.tmp_db
        database.init_db()
        sdb.init_db(self.tmp_db)

    def tearDown(self):
        import database
        import plugins.cron_scheduler.scheduler_db as sdb
        database.DB_FILE = self._orig_db_file
        sdb.DEFAULT_DB_PATH = self._orig_default_db
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except OSError:
                pass

    async def test_cron_engine_auto_removes_delay_task_and_keeps_log(self):
        import database
        from cron_engine import CronEngine

        now = int(time.time())
        task_data = {
            "id": "task_delay_1",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "喝水提醒",
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": "60s",
            "prompt": "提醒喝水",
            "is_active": True,
            "created_at": now - 60,
            "next_run_at": now,
            "run_count": 0,
        }
        database.save_cron_task(task_data)
        self.assertIsNotNone(database.get_cron_task("task_delay_1"))

        engine = CronEngine()
        with patch("cron_engine.send_card_to_chat_sdk", return_value=True):
            await engine._run_task_wrapper(task_data)

        # 一次性任务执行后应从 cron_tasks 主表自动移除
        self.assertIsNone(database.get_cron_task("task_delay_1"))
        # 但审计日志表 cron_logs 中应完整保留本次执行记录与任务名称/内容元数据
        logs = database.get_cron_logs("task_delay_1")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "success")
        self.assertEqual(logs[0]["task_name"], "喝水提醒")
        self.assertEqual(logs[0]["prompt"], "提醒喝水")
        self.assertEqual(logs[0]["task_type"], "delay")

    async def test_cron_engine_keeps_recurring_cron_task(self):
        import database
        from cron_engine import CronEngine

        now = int(time.time())
        task_data = {
            "id": "task_cron_1",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "每日早报",
            "task_type": "cron",
            "action_type": "reminder",
            "cron_expr": "0 9 * * *",
            "prompt": "发送早报",
            "is_active": True,
            "created_at": now - 3600,
            "next_run_at": now,
            "run_count": 0,
        }
        database.save_cron_task(task_data)

        engine = CronEngine()
        with patch("cron_engine.send_card_to_chat_sdk", return_value=True):
            await engine._run_task_wrapper(task_data)

        # 周期任务应保留在主表中，且 run_count + 1、next_run_at 推进到未来
        saved = database.get_cron_task("task_cron_1")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["run_count"], 1)
        self.assertGreater(saved["next_run_at"], now)

    async def test_daemon_auto_removes_delay_task_and_keeps_log(self):
        import plugins.cron_scheduler.scheduler_db as sdb
        from plugins.cron_scheduler.daemon import CronDaemon

        now = int(time.time())
        task_data = {
            "id": "daemon_delay_1",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "站会提醒",
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": "30s",
            "prompt": "准备站会",
            "is_active": True,
            "created_at": now - 30,
            "next_run_at": now,
            "run_count": 0,
        }
        sdb.save_task(task_data)

        daemon = CronDaemon()
        with patch("plugins.cron_scheduler.daemon.send_card_to_feishu", return_value=True):
            await daemon.run_task_wrapper(task_data)

        self.assertIsNone(sdb.get_task("daemon_delay_1"))
        logs = sdb.get_recent_logs("daemon_delay_1")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "success")

    def test_legacy_completed_delay_tasks_auto_pruned_on_list(self):
        import database

        now = int(time.time())
        # 1. 已触发完成的历史残留一次性任务（is_active=0, run_count=1）
        database.save_cron_task({
            "id": "legacy_done_delay",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "历史一次性提醒",
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": "60s",
            "prompt": "历史任务",
            "is_active": False,
            "created_at": now - 1000,
            "last_run_at": now - 940,
            "next_run_at": 0,
            "run_count": 1,
        })
        # 2. 尚未触发的活跃一次性任务（is_active=1, run_count=0）
        database.save_cron_task({
            "id": "pending_delay",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "待触发提醒",
            "task_type": "delay",
            "action_type": "reminder",
            "cron_expr": "300s",
            "prompt": "待触发任务",
            "is_active": True,
            "created_at": now,
            "last_run_at": 0,
            "next_run_at": now + 300,
            "run_count": 0,
        })

        tasks = database.get_all_cron_tasks("oc_test_chat")
        ids = [t["id"] for t in tasks]
        self.assertNotIn("legacy_done_delay", ids)
        self.assertIn("pending_delay", ids)
        # 自动清理历史残留一次性任务前，应自动将其归档至 cron_logs 历史记录中
        archived_logs = database.get_cron_logs("legacy_done_delay")
        self.assertEqual(len(archived_logs), 1)
        self.assertEqual(archived_logs[0]["task_name"], "历史一次性提醒")

    def test_panel_card_and_cleanup_paused_tasks(self):
        import json
        import database
        from cards.cron import build_cron_panel_card

        now = int(time.time())
        database.save_cron_task({
            "id": "paused_cron_1",
            "chat_id": "oc_test_chat",
            "category": "user",
            "name": "已暂停周期任务",
            "task_type": "cron",
            "action_type": "reminder",
            "cron_expr": "0 10 * * *",
            "prompt": "测试",
            "is_active": False,
            "created_at": now,
            "next_run_at": now + 3600,
            "run_count": 2,
        })
        tasks = database.get_all_cron_tasks("oc_test_chat")
        card = build_cron_panel_card(tasks, active_tab="user")
        action_blocks = [el for el in card["elements"] if el.get("tag") == "action"]
        top_buttons = [btn["value"].get("action") for btn in action_blocks[0]["actions"]]
        top_tabs = [btn["value"].get("tab") for btn in action_blocks[0]["actions"] if btn["value"].get("action") == "switch_cron_tab"]
        self.assertIn("clear_completed_cron", top_buttons)
        self.assertIn("history", top_tabs)

        removed = database.cleanup_completed_delay_tasks("oc_test_chat", include_paused=True)
        self.assertEqual(removed, 1)
        self.assertEqual(len(database.get_all_cron_tasks("oc_test_chat")), 0)

        # 切换到 history 标签页应能看到刚归档的历史任务记录
        logs = database.get_cron_logs(chat_id="oc_test_chat")
        self.assertEqual(len(logs), 1)
        hist_card = build_cron_panel_card([], active_tab="history", logs=logs)
        hist_json = json.dumps(hist_card, ensure_ascii=False)
        self.assertIn("已暂停周期任务", hist_json)
        self.assertIn("clear_cron_logs", hist_json)
        self.assertEqual(database.clear_cron_logs("oc_test_chat"), 1)
        self.assertEqual(len(database.get_cron_logs(chat_id="oc_test_chat")), 0)


if __name__ == "__main__":
    unittest.main()
