import os
import json
import time
import sqlite3
import asyncio
import aiosqlite
from logger import log

DB_FILE = "antigravity_bot.db"

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
        CREATE TABLE IF NOT EXISTS recent_messages (
            message_id  TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL,
            create_time INTEGER NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def migrate_from_json():
    conn = get_db()
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
    conn.close()

init_db()
migrate_from_json()

_session_locks = {}

def _get_session_lock(chat_id):
    if chat_id not in _session_locks:
        _session_locks[chat_id] = asyncio.Lock()
    return _session_locks[chat_id]

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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id, data FROM chat_sessions')
    rows = cursor.fetchall()
    conn.close()
    sessions = {}
    for row in rows:
        data = json.loads(row['data'])
        if data.get('model') == 'Gemini 3.5 Flash':
            data['model'] = 'Gemini 3.5 Flash (Medium)'
        sessions[row['chat_id']] = data
    return sessions

def save_sessions(sessions):
    conn = get_db()
    cursor = conn.cursor()
    for chat_id, data in sessions.items():
        cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
    conn.commit()
    conn.close()

def get_session_sync(chat_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        data = json.loads(row['data'])
        if data.get('model') == 'Gemini 3.5 Flash':
            data['model'] = 'Gemini 3.5 Flash (Medium)'
        return data
    return {"conversation": "", "model": "Gemini 3.5 Flash (Medium)", "role": "无", "project": "默认"}

def save_session_sync(chat_id, data):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
    conn.commit()
    conn.close()


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
