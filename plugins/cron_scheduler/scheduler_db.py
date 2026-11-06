"""Database module for cron_scheduler plugin & daemon."""

import os
import sqlite3
import time
from typing import Optional, List, Dict, Any

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(PLUGIN_DIR, "..", ".."))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "antigravity_bot.db")


def get_db(db_path: str = None) -> sqlite3.Connection:
    if not db_path:
        import sys
        db_mod = sys.modules.get("database")
        db_path = getattr(db_mod, "DB_FILE", None) or DEFAULT_DB_PATH
    path = db_path
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
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

        # 任务执行日志审计表 (兼容 main bot 与 daemon 审计结构)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cron_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                chat_id TEXT DEFAULT '',
                task_name TEXT DEFAULT '',
                task_type TEXT DEFAULT 'cron',
                action_type TEXT DEFAULT 'reminder',
                cron_expr TEXT DEFAULT '',
                prompt TEXT DEFAULT '',
                status TEXT NOT NULL,
                output TEXT DEFAULT '',
                error_msg TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0,
                executed_at INTEGER DEFAULT 0
            );
        ''')
        for log_col_def in (
            "chat_id TEXT DEFAULT ''",
            "task_name TEXT DEFAULT ''",
            "task_type TEXT DEFAULT 'cron'",
            "action_type TEXT DEFAULT 'reminder'",
            "cron_expr TEXT DEFAULT ''",
            "prompt TEXT DEFAULT ''",
        ):
            try:
                cursor.execute(f"ALTER TABLE cron_logs ADD COLUMN {log_col_def}")
            except Exception:
                pass
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_task ON cron_logs (task_id, executed_at DESC);')
        conn.commit()
    finally:
        conn.close()


def _ensure_logs_schema(cursor):
    for log_col_def in (
        "chat_id TEXT DEFAULT ''",
        "task_name TEXT DEFAULT ''",
        "task_type TEXT DEFAULT 'cron'",
        "action_type TEXT DEFAULT 'reminder'",
        "cron_expr TEXT DEFAULT ''",
        "prompt TEXT DEFAULT ''",
    ):
        try:
            cursor.execute(f"ALTER TABLE cron_logs ADD COLUMN {log_col_def}")
        except Exception:
            pass


def _archive_tasks_before_delete(cursor, where_clause: str, params: tuple = ()):
    _ensure_logs_schema(cursor)
    cursor.execute(f"SELECT * FROM cron_tasks WHERE {where_clause}", params)
    tasks = [dict(r) for r in cursor.fetchall()]
    now = int(time.time())
    for t in tasks:
        t_id = t.get("id", "")
        if not t_id:
            continue
        cursor.execute(
            """
            UPDATE cron_logs
            SET chat_id = CASE WHEN chat_id = '' OR chat_id IS NULL THEN ? ELSE chat_id END,
                task_name = CASE WHEN task_name = '' OR task_name IS NULL THEN ? ELSE task_name END,
                task_type = CASE WHEN task_type = '' OR task_type IS NULL THEN ? ELSE task_type END,
                action_type = CASE WHEN action_type = '' OR action_type IS NULL THEN ? ELSE action_type END,
                cron_expr = CASE WHEN cron_expr = '' OR cron_expr IS NULL THEN ? ELSE cron_expr END,
                prompt = CASE WHEN prompt = '' OR prompt IS NULL THEN ? ELSE prompt END
            WHERE task_id = ?
            """,
            (
                t.get("chat_id", "") or "",
                t.get("name", "未命名任务") or "未命名任务",
                t.get("task_type", "cron") or "cron",
                t.get("action_type", "reminder") or "reminder",
                t.get("cron_expr", "") or "",
                (t.get("prompt", "") or t.get("command", "") or "")[:500],
                t_id,
            ),
        )
        if int(t.get("run_count", 0) or 0) > 0 or int(t.get("last_run_at", 0) or 0) > 0:
            cursor.execute("SELECT 1 FROM cron_logs WHERE task_id = ? LIMIT 1", (t_id,))
            if not cursor.fetchone():
                exec_ts = int(t.get("last_run_at", 0) or t.get("updated_at", 0) or now)
                cursor.execute(
                    """
                    INSERT INTO cron_logs (
                        task_id, chat_id, task_name, task_type, action_type, cron_expr, prompt,
                        status, output, error_msg, duration_ms, executed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?, '', 0, ?)
                    """,
                    (
                        t_id,
                        t.get("chat_id", "") or "",
                        t.get("name", "未命名任务") or "未命名任务",
                        t.get("task_type", "cron") or "cron",
                        t.get("action_type", "reminder") or "reminder",
                        t.get("cron_expr", "") or "",
                        (t.get("prompt", "") or t.get("command", "") or "")[:500],
                        f"已完成执行（累计运行 {t.get('run_count', 1) or 1} 次）",
                        exec_ts,
                    ),
                )


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
        # 自动清理已触发完毕的历史一次性倒计时任务（先归档元数据至 cron_logs 再移除主表）
        cond = "task_type = 'delay' AND is_active = 0 AND (run_count > 0 OR last_run_at > 0)"
        _archive_tasks_before_delete(cursor, cond)
        cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}")
        if cursor.rowcount > 0:
            conn.commit()
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


def delete_task(task_id: str, db_path: str = None, keep_logs: bool = True) -> bool:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        if keep_logs:
            _archive_tasks_before_delete(cursor, "id = ?", (task_id,))
        cursor.execute('DELETE FROM cron_tasks WHERE id = ?', (task_id,))
        if not keep_logs:
            cursor.execute('DELETE FROM cron_logs WHERE task_id = ?', (task_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def cleanup_completed_delay_tasks(chat_id: Optional[str] = None, include_paused: bool = False, db_path: str = None) -> int:
    """清理已完成的一次性任务（当 include_paused=True 时同时清理已暂停的用户任务），保留 cron_logs 执行日志。"""
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        if include_paused:
            if chat_id:
                cond = "is_active = 0 AND (chat_id = ? OR chat_id = '' OR chat_id IS NULL) AND category NOT IN ('system', 'maintenance')"
                _archive_tasks_before_delete(cursor, cond, (chat_id,))
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}", (chat_id,))
            else:
                cond = "is_active = 0 AND category NOT IN ('system', 'maintenance')"
                _archive_tasks_before_delete(cursor, cond)
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}")
        else:
            if chat_id:
                cond = "task_type = 'delay' AND (is_active = 0 OR run_count > 0 OR last_run_at > 0) AND (chat_id = ? OR chat_id = '' OR chat_id IS NULL)"
                _archive_tasks_before_delete(cursor, cond, (chat_id,))
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}", (chat_id,))
            else:
                cond = "task_type = 'delay' AND (is_active = 0 OR run_count > 0 OR last_run_at > 0)"
                _archive_tasks_before_delete(cursor, cond)
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}")
        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.commit()
        return deleted
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


def claim_task(task_id: str, now_ts: int, tentative_next_run: int, db_path: str = None) -> bool:
    """Atomically claim a cron task for execution so multiple schedulers never duplicate runs."""
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE cron_tasks
            SET next_run_at = ?, last_run_at = ?, updated_at = ?
            WHERE id = ? AND is_active = 1 AND next_run_at <= ?
            """,
            (tentative_next_run, now_ts, now_ts, task_id, now_ts)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()


def record_log(task_id: str, task_name: str, status: str, result: str, duration_ms: int = 0, db_path: str = None, task_meta: dict = None) -> bool:
    conn = get_db(db_path)
    now = int(time.time())
    try:
        cursor = conn.cursor()
        _ensure_logs_schema(cursor)
        meta = dict(task_meta or {})
        if not meta.get("chat_id") and not meta.get("prompt"):
            cursor.execute("SELECT * FROM cron_tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                meta = dict(row)
        chat_id_val = meta.get("chat_id", "") or ""
        task_name_val = task_name or meta.get("name") or meta.get("task_name") or ""
        task_type_val = meta.get("task_type") or ("delay" if "delay" in str(task_id) else "cron")
        action_type_val = meta.get("action_type") or "reminder"
        cron_expr_val = meta.get("cron_expr") or ""
        prompt_val = (meta.get("prompt") or meta.get("command") or "")[:500]
        cursor.execute('''
            INSERT INTO cron_logs (
                task_id, chat_id, task_name, task_type, action_type, cron_expr, prompt,
                status, output, error_msg, duration_ms, executed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            task_id,
            chat_id_val,
            task_name_val,
            task_type_val,
            action_type_val,
            cron_expr_val,
            prompt_val,
            status,
            result if status == "success" else "",
            result if status != "success" else "",
            duration_ms,
            now,
        ))
        conn.commit()
        return True
    finally:
        conn.close()


def get_recent_logs(task_id: Optional[str] = None, limit: int = 20, db_path: str = None, chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        _ensure_logs_schema(cursor)
        base_select = """
            SELECT
                l.id,
                l.task_id,
                COALESCE(NULLIF(l.chat_id, ''), t.chat_id, '') AS chat_id,
                COALESCE(NULLIF(l.task_name, ''), t.name, l.task_id) AS task_name,
                COALESCE(NULLIF(l.task_type, ''), t.task_type, 'cron') AS task_type,
                COALESCE(NULLIF(l.action_type, ''), t.action_type, 'reminder') AS action_type,
                COALESCE(NULLIF(l.cron_expr, ''), t.cron_expr, '') AS cron_expr,
                COALESCE(NULLIF(l.prompt, ''), t.prompt, t.command, '') AS prompt,
                l.status,
                l.output,
                l.error_msg,
                l.duration_ms,
                l.executed_at
            FROM cron_logs l
            LEFT JOIN cron_tasks t ON l.task_id = t.id
        """
        if task_id:
            cursor.execute(f"{base_select} WHERE l.task_id = ? ORDER BY l.executed_at DESC, l.id DESC LIMIT ?", (task_id, limit))
        elif chat_id:
            cursor.execute(
                f"{base_select} WHERE l.chat_id = ? OR l.chat_id = '' OR l.chat_id IS NULL OR t.chat_id = ? ORDER BY l.executed_at DESC, l.id DESC LIMIT ?",
                (chat_id, chat_id, limit),
            )
        else:
            cursor.execute(f"{base_select} ORDER BY l.executed_at DESC, l.id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_logs(chat_id: Optional[str] = None, db_path: str = None) -> int:
    conn = get_db(db_path)
    try:
        cursor = conn.cursor()
        _ensure_logs_schema(cursor)
        if chat_id:
            cursor.execute("DELETE FROM cron_logs WHERE chat_id = ? OR chat_id = '' OR chat_id IS NULL", (chat_id,))
        else:
            cursor.execute("DELETE FROM cron_logs")
        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.commit()
        return deleted
    finally:
        conn.close()

# 模块加载时自动初始化数据表
init_db()
