"""Database module for cron_scheduler plugin & daemon."""

import os
import sqlite3
import time
from typing import Optional, List, Dict, Any

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# 统一使用主机器人数据目录，保证 CLI、Daemon 与 Feishu Bot 访问同一份任务库
BOT_DATA_DIR = "/home/jiang/github/antigravity-feishu-bot/plugin_data/cron_scheduler"
LOCAL_DATA_DIR = os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "plugin_data", "cron_scheduler"))
DATA_DIR = BOT_DATA_DIR if os.path.exists(os.path.dirname(BOT_DATA_DIR)) else LOCAL_DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "scheduler.db")


def get_db(db_path: str = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(db_path: str = None):
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        # 任务主表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cron_tasks (
                id VARCHAR(64) PRIMARY KEY,
                chat_id VARCHAR(128) NOT NULL,
                category VARCHAR(32) DEFAULT 'user',
                name VARCHAR(128) NOT NULL,
                task_type VARCHAR(32) DEFAULT 'cron',
                action_type VARCHAR(32) DEFAULT 'reminder',
                cron_expr VARCHAR(64) NOT NULL,
                prompt TEXT NOT NULL,
                command TEXT DEFAULT '',
                project_path VARCHAR(255) DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_by VARCHAR(128) DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_run_at INTEGER DEFAULT 0,
                next_run_at INTEGER DEFAULT 0,
                run_count INTEGER DEFAULT 0
            );
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_active ON cron_tasks (is_active, next_run_at);')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_chat ON cron_tasks (chat_id);')

        # 任务执行日志审计表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cron_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id VARCHAR(64) NOT NULL,
                task_name VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL,
                result TEXT,
                duration_ms INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            );
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_task ON cron_logs (task_id, created_at DESC);')
        conn.commit()
    finally:
        conn.close()


def save_task(task_data: dict, db_path: str = None) -> bool:
    conn = get_db(db_path)
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cron_tasks (
                id, chat_id, category, name, task_type, action_type, cron_expr, prompt,
                command, project_path, is_active, created_by, created_at, updated_at,
                last_run_at, next_run_at, run_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                category=excluded.category,
                name=excluded.name,
                task_type=excluded.task_type,
                action_type=excluded.action_type,
                cron_expr=excluded.cron_expr,
                prompt=excluded.prompt,
                command=excluded.command,
                project_path=excluded.project_path,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at,
                last_run_at=excluded.last_run_at,
                next_run_at=excluded.next_run_at,
                run_count=excluded.run_count
        ''', (
            task_data.get('id'),
            task_data.get('chat_id'),
            task_data.get('category', 'user'),
            task_data.get('name', '未命名任务'),
            task_data.get('task_type', 'cron'),
            task_data.get('action_type', 'reminder'),
            task_data.get('cron_expr', '0 9 * * *'),
            task_data.get('prompt', ''),
            task_data.get('command', ''),
            task_data.get('project_path', ''),
            1 if task_data.get('is_active', True) else 0,
            task_data.get('created_by', ''),
            task_data.get('created_at', now),
            now,
            task_data.get('last_run_at', 0),
            task_data.get('next_run_at', 0),
            task_data.get('run_count', 0),
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"[database] save_task failed: {e}")
        return False
    finally:
        conn.close()


def get_task(task_id: str, db_path: str = None) -> Optional[Dict[str, Any]]:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM cron_tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_tasks(chat_id: Optional[str] = None, db_path: str = None) -> List[Dict[str, Any]]:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        if chat_id:
            cursor.execute('''
                SELECT * FROM cron_tasks 
                WHERE chat_id = ? OR chat_id = "" OR chat_id IS NULL OR category IN ("system", "maintenance") 
                ORDER BY created_at DESC
            ''', (chat_id,))
        else:
            cursor.execute('SELECT * FROM cron_tasks ORDER BY created_at DESC')
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_tasks(db_path: str = None) -> List[Dict[str, Any]]:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM cron_tasks WHERE is_active = 1')
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_task(task_id: str, db_path: str = None) -> bool:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM cron_tasks WHERE id = ?', (task_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def update_task_status(task_id: str, is_active: bool, db_path: str = None) -> bool:
    conn = get_db(db_path)
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('UPDATE cron_tasks SET is_active = ?, updated_at = ? WHERE id = ?', (1 if is_active else 0, now, task_id))
        conn.commit()
        return True
    finally:
        conn.close()


def update_task_run(task_id: str, last_run_at: int, next_run_at: int, inc_count: bool = True, db_path: str = None) -> bool:
    conn = get_db(db_path)
    now = int(time.time())
    try:
        cursor = conn.cursor()
        if inc_count:
            cursor.execute('''
                UPDATE cron_tasks 
                SET last_run_at = ?, next_run_at = ?, run_count = run_count + 1, updated_at = ? 
                WHERE id = ?
            ''', (last_run_at, next_run_at, now, task_id))
        else:
            cursor.execute('''
                UPDATE cron_tasks 
                SET last_run_at = ?, next_run_at = ?, updated_at = ? 
                WHERE id = ?
            ''', (last_run_at, next_run_at, now, task_id))
        conn.commit()
        return True
    finally:
        conn.close()


def record_log(task_id: str, task_name: str, status: str, result: str, duration_ms: int = 0, db_path: str = None) -> bool:
    conn = get_db(db_path)
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cron_logs (task_id, task_name, status, result, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (task_id, task_name, status, result, duration_ms, now))
        conn.commit()
        return True
    finally:
        conn.close()


def get_recent_logs(task_id: Optional[str] = None, limit: int = 20, db_path: str = None) -> List[Dict[str, Any]]:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        if task_id:
            cursor.execute('SELECT * FROM cron_logs WHERE task_id = ? ORDER BY created_at DESC LIMIT ?', (task_id, limit))
        else:
            cursor.execute('SELECT * FROM cron_logs ORDER BY created_at DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# 模块加载时自动初始化数据表
init_db()
