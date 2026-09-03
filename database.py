import os
import json
import time
import sqlite3
import threading
import asyncio
import aiosqlite
from logger import log

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "antigravity_bot.db")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            chat_id TEXT PRIMARY KEY,
            data JSON NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            data JSON NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auth_sessions (
            chat_id         TEXT PRIMARY KEY,
            chat_type       TEXT NOT NULL DEFAULT 'p2p',
            display_name    TEXT NOT NULL DEFAULT '',
            sender_open_id  TEXT NOT NULL DEFAULT '',
            role            TEXT NOT NULL DEFAULT 'guest',
            scopes          TEXT NOT NULL DEFAULT '[]',
            created_at      INTEGER NOT NULL DEFAULT 0,
            updated_at      INTEGER NOT NULL DEFAULT 0,
            granted_by      TEXT NOT NULL DEFAULT '',
            request_count   INTEGER NOT NULL DEFAULT 0,
            last_request_at INTEGER NOT NULL DEFAULT 0,
            last_hint_at    INTEGER NOT NULL DEFAULT 0,
            last_message    TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_tasks (
            chat_id    TEXT NOT NULL,
            task       TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (chat_id, created_at)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cron_tasks (
            id           TEXT PRIMARY KEY,
            chat_id      TEXT NOT NULL,
            category     TEXT NOT NULL DEFAULT 'user',
            name         TEXT NOT NULL,
            task_type    TEXT NOT NULL DEFAULT 'cron',
            cron_expr    TEXT NOT NULL,
            prompt       TEXT NOT NULL,
            project_path TEXT NOT NULL DEFAULT '',
            is_active    INTEGER NOT NULL DEFAULT 1,
            created_by   TEXT NOT NULL DEFAULT '',
            created_at   INTEGER NOT NULL DEFAULT 0,
            updated_at   INTEGER NOT NULL DEFAULT 0,
            last_run_at  INTEGER NOT NULL DEFAULT 0,
            next_run_at  INTEGER NOT NULL DEFAULT 0,
            run_count    INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cron_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      TEXT NOT NULL,
            status       TEXT NOT NULL,
            output       TEXT NOT NULL DEFAULT '',
            error_msg    TEXT NOT NULL DEFAULT '',
            duration_ms  INTEGER NOT NULL DEFAULT 0,
            executed_at  INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recent_messages (
            message_id  TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL,
            create_time INTEGER NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def migrate_from_json():
    with get_db() as conn:
        cursor = conn.cursor()
        if os.path.exists("chat_sessions.json"):
            try:
                with open("chat_sessions.json", "r") as f:
                    sessions = json.load(f)
                for chat_id, data in sessions.items():
                    cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
                os.rename("chat_sessions.json", "chat_sessions.json.bak")
            except Exception as e:
                log.error(f"Error migrating sessions: {e}")
                
        if os.path.exists("user_profiles.json"):
            try:
                with open("user_profiles.json", "r") as f:
                    profiles = json.load(f)
                for user_id, data in profiles.items():
                    cursor.execute('INSERT OR REPLACE INTO user_profiles (user_id, data) VALUES (?, ?)', (user_id, json.dumps(data)))
                os.rename("user_profiles.json", "user_profiles.json.bak")
            except Exception as e:
                log.error(f"Error migrating profiles: {e}")
                
        conn.commit()

init_db()
migrate_from_json()

# 线程安全的会话锁缓存，设置有界淘汰上限，避免无限制增长造成内存泄漏
_session_locks = {}
_session_locks_guard = threading.Lock()

def _get_session_lock(chat_id: str) -> asyncio.Lock:
    with _session_locks_guard:
        lock = _session_locks.get(chat_id)
        if lock is None:
            # 超过 2000 个会话时，主动清理未加锁的闲置对象
            if len(_session_locks) > 2000:
                idle_keys = [k for k, l in _session_locks.items() if not l.locked()]
                for k in idle_keys[:500]:
                    _session_locks.pop(k, None)
            lock = asyncio.Lock()
            _session_locks[chat_id] = lock
        return lock

async def get_session_async(chat_id):
    async with _get_session_lock(chat_id):
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    data = json.loads(row['data'])
                    if data.get('model') == 'Gemini 3.5 Flash':
                        data['model'] = 'Gemini 3.5 Flash (Medium)'
                    return data
                return {"conversation": "", "model": "Gemini 3.5 Flash (Medium)", "role": "无", "project": "默认"}

async def save_session_async(chat_id, data):
    async with _get_session_lock(chat_id):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
            await db.commit()

async def get_profile_async(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT data FROM user_profiles WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row['data'])
            return []

async def save_profile_async(user_id, data):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute('INSERT OR REPLACE INTO user_profiles (user_id, data) VALUES (?, ?)', (user_id, json.dumps(data)))
        await db.commit()

def load_sessions():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, data FROM chat_sessions')
        rows = cursor.fetchall()
    sessions = {}
    for row in rows:
        data = json.loads(row['data'])
        if data.get('model') == 'Gemini 3.5 Flash':
            data['model'] = 'Gemini 3.5 Flash (Medium)'
        sessions[row['chat_id']] = data
    return sessions

def save_sessions(sessions):
    with get_db() as conn:
        cursor = conn.cursor()
        for chat_id, data in sessions.items():
            cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
        conn.commit()

def get_session_sync(chat_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
    if row:
        data = json.loads(row['data'])
        if data.get('model') == 'Gemini 3.5 Flash':
            data['model'] = 'Gemini 3.5 Flash (Medium)'
        return data
    return {"conversation": "", "model": "Gemini 3.5 Flash (Medium)", "role": "无", "project": "默认"}

def save_session_sync(chat_id, data):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
        conn.commit()


# ---------------------------------------------------------------------------
# Auth / permission persistence (sync sqlite — safe to call from any thread
# since every helper opens its own connection)
# ---------------------------------------------------------------------------

_AUTH_COLUMNS = [
    "chat_id", "chat_type", "display_name", "sender_open_id", "role", "scopes",
    "created_at", "updated_at", "granted_by", "request_count",
    "last_request_at", "last_hint_at", "last_message",
]


def _auth_row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    try:
        d["scopes"] = json.loads(d.get("scopes") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["scopes"] = []
    return d


def get_bot_meta(key: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_meta WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_bot_meta(key: str, value: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)',
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_auth_session(chat_id: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM auth_sessions WHERE chat_id = ?', (chat_id,))
        return _auth_row_to_dict(cursor.fetchone())
    finally:
        conn.close()


def save_auth_session(session: dict):
    """INSERT OR REPLACE an auth_sessions record. Accepts either the full
    dict (with 'scopes' as list) or a partial dict (missing fields keep DB
    defaults / existing values)."""
    existing = get_auth_session(session.get("chat_id", ""))
    merged = dict(existing or {})
    merged.update(session)
    merged.setdefault("chat_id", "")
    merged.setdefault("chat_type", "p2p")
    merged.setdefault("display_name", "")
    merged.setdefault("sender_open_id", "")
    merged.setdefault("role", "guest")
    merged.setdefault("scopes", [])
    merged.setdefault("created_at", 0)
    merged.setdefault("updated_at", 0)
    merged.setdefault("granted_by", "")
    merged.setdefault("request_count", 0)
    merged.setdefault("last_request_at", 0)
    merged.setdefault("last_hint_at", 0)
    merged.setdefault("last_message", "")

    if isinstance(merged["scopes"], (list, tuple, set)):
        merged["scopes"] = json.dumps(list(merged["scopes"]), ensure_ascii=False)
    elif not isinstance(merged["scopes"], str):
        merged["scopes"] = json.dumps(merged["scopes"], ensure_ascii=False)

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f'INSERT OR REPLACE INTO auth_sessions ({", ".join(_AUTH_COLUMNS)}) '
            f'VALUES ({", ".join(["?"] * len(_AUTH_COLUMNS))})',
            [merged.get(c, 0 if c in ("created_at", "updated_at", "request_count", "last_request_at", "last_hint_at") else "") for c in _AUTH_COLUMNS],
        )
        conn.commit()
    finally:
        conn.close()


def list_auth_sessions():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM auth_sessions ORDER BY updated_at DESC')
        return [_auth_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Message dedup persistence (survives restarts; keeps a 12h window)
# ---------------------------------------------------------------------------

_DEDUP_WINDOW_SECONDS = 12 * 3600


def mark_message_seen(message_id: str, chat_id: str, create_time=None) -> bool:
    """Return True if this message is new; False if it was already processed.
    Old records older than 12h are pruned opportunistically."""
    if not message_id:
        return True
    if create_time is None:
        create_time = int(time.time())
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM recent_messages WHERE message_id = ?', (message_id,))
        if cursor.fetchone():
            return False
        cursor.execute(
            'INSERT OR REPLACE INTO recent_messages (message_id, chat_id, create_time) VALUES (?, ?, ?)',
            (message_id, chat_id, int(create_time)),
        )
        cursor.execute(
            'DELETE FROM recent_messages WHERE create_time < ?',
            (int(time.time()) - _DEDUP_WINDOW_SECONDS,),
        )
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[dedup] mark_message_seen failed: {e}")
        return True  # 失败时放行，避免丢消息
    finally:
        conn.close()


def is_message_seen(message_id: str) -> bool:
    if not message_id:
        return False
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM recent_messages WHERE message_id = ?', (message_id,))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pending task persistence (queue survives restarts)
# ---------------------------------------------------------------------------

def save_pending_task(chat_id: str, task: dict):
    created_at = task.get("created_at") or int(time.time() * 1000)
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO pending_tasks (chat_id, task, created_at) VALUES (?, ?, ?)',
            (chat_id, json.dumps(task, ensure_ascii=False), created_at),
        )
        conn.commit()
    except Exception as e:
        log.error(f"[pending] save_pending_task failed: {e}")
    finally:
        conn.close()


def delete_pending_task(chat_id: str, created_at):
    if not created_at:
        return
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM pending_tasks WHERE chat_id = ? AND created_at = ?',
            (chat_id, int(created_at)),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def clear_pending_tasks_for_chat(chat_id: str):
    if not chat_id:
        return
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM pending_tasks WHERE chat_id = ?', (chat_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def load_pending_tasks():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id, task, created_at FROM pending_tasks ORDER BY created_at ASC')
        rows = cursor.fetchall()
        result = []
        for row in rows:
            try:
                task = json.loads(row["task"])
            except (json.JSONDecodeError, TypeError):
                continue
            result.append((row["chat_id"], task, row["created_at"]))
        return result
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cron Task & Log Helper Functions
# ---------------------------------------------------------------------------

def get_all_cron_tasks(chat_id=None):
    conn = get_db()
    try:
        cursor = conn.cursor()
        if chat_id:
            cursor.execute('SELECT * FROM cron_tasks WHERE chat_id = ? OR chat_id = "" OR chat_id IS NULL OR category IN ("system", "maintenance") ORDER BY created_at DESC', (chat_id,))
        else:
            cursor.execute('SELECT * FROM cron_tasks ORDER BY created_at DESC')
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"[cron] get_all_cron_tasks failed: {e}")
        return []
    finally:
        conn.close()


def get_active_cron_tasks():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM cron_tasks WHERE is_active = 1')
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"[cron] get_active_cron_tasks failed: {e}")
        return []
    finally:
        conn.close()


def get_cron_task(task_id: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM cron_tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.error(f"[cron] get_cron_task failed: {e}")
        return None
    finally:
        conn.close()


def save_cron_task(task_data: dict):
    conn = get_db()
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cron_tasks (
                id, chat_id, category, name, task_type, cron_expr, prompt,
                project_path, is_active, created_by, created_at, updated_at,
                last_run_at, next_run_at, run_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                category=excluded.category,
                name=excluded.name,
                task_type=excluded.task_type,
                cron_expr=excluded.cron_expr,
                prompt=excluded.prompt,
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
            task_data.get('cron_expr', '0 9 * * *'),
            task_data.get('prompt', ''),
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
        log.error(f"[cron] save_cron_task failed: {e}")
        return False
    finally:
        conn.close()


def update_cron_task_status(task_id: str, is_active: bool):
    conn = get_db()
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('UPDATE cron_tasks SET is_active = ?, updated_at = ? WHERE id = ?', (1 if is_active else 0, now, task_id))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[cron] update_cron_task_status failed: {e}")
        return False
    finally:
        conn.close()


def update_cron_task_run(task_id: str, last_run_at: int, next_run_at: int):
    conn = get_db()
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE cron_tasks
            SET last_run_at = ?, next_run_at = ?, run_count = run_count + 1, updated_at = ?
            WHERE id = ?
        ''', (last_run_at, next_run_at, now, task_id))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[cron] update_cron_task_run failed: {e}")
        return False
    finally:
        conn.close()


def claim_cron_task(task_id: str, now_ts: int, tentative_next_run: int) -> bool:
    """Atomically claim a cron task for execution so concurrent schedulers never duplicate runs."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE cron_tasks
            SET next_run_at = ?, last_run_at = ?, updated_at = ?
            WHERE id = ? AND is_active = 1 AND next_run_at <= ?
        ''', (tentative_next_run, now_ts, now_ts, task_id, now_ts))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log.error(f"[cron] claim_cron_task failed: {e}")
        return False
    finally:
        conn.close()


def delete_cron_task(task_id: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM cron_tasks WHERE id = ?', (task_id,))
        cursor.execute('DELETE FROM cron_logs WHERE task_id = ?', (task_id,))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[cron] delete_cron_task failed: {e}")
        return False
    finally:
        conn.close()


def record_cron_log(task_id: str, status: str, output: str = '', error_msg: str = '', duration_ms: int = 0):
    conn = get_db()
    now = int(time.time())
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cron_logs (task_id, status, output, error_msg, duration_ms, executed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (task_id, status, output[:2000] if output else '', error_msg[:1000] if error_msg else '', duration_ms, now))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[cron] record_cron_log failed: {e}")
        return False
    finally:
        conn.close()


def get_cron_logs(task_id=None, limit=10):
    conn = get_db()
    try:
        cursor = conn.cursor()
        if task_id:
            cursor.execute('SELECT * FROM cron_logs WHERE task_id = ? ORDER BY executed_at DESC LIMIT ?', (task_id, limit))
        else:
            cursor.execute('SELECT * FROM cron_logs ORDER BY executed_at DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"[cron] get_cron_logs failed: {e}")
        return []
    finally:
        conn.close()


def save_pending_update_notice(chat_id, message_id, old_version=""):
    data = json.dumps({"chat_id": chat_id, "message_id": message_id, "old_version": old_version, "timestamp": time.time()})
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)', ('pending_update_notice', data))
        conn.commit()


def get_and_clear_pending_update_notice():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_meta WHERE key = ?', ('pending_update_notice',))
        row = cursor.fetchone()
        if row:
            cursor.execute('DELETE FROM bot_meta WHERE key = ?', ('pending_update_notice',))
            conn.commit()
            try:
                return json.loads(row['value'])
            except Exception:
                pass
    return None

