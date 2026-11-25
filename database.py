import os
import atexit
import copy
import json
import time
import sqlite3
import threading
import asyncio
import weakref
from contextlib import asynccontextmanager, closing
import aiosqlite
from logger import log

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "antigravity_bot.db")
DB_TIMEOUT = 20.0

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('PRAGMA synchronous=NORMAL')
    except Exception as e:
        log.debug(f"[database] PRAGMA setup skipped: {e}")
    return conn

def init_db():
    with closing(get_db()) as conn:
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
                action_type  TEXT NOT NULL DEFAULT 'reminder',
                command      TEXT NOT NULL DEFAULT '',
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
        # Migrate existing cron_tasks schema if columns action_type / command are missing
        for col_def in (
            "action_type TEXT NOT NULL DEFAULT 'reminder'",
            "command TEXT NOT NULL DEFAULT ''",
        ):
            try:
                cursor.execute(f"ALTER TABLE cron_tasks ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cron_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                chat_id      TEXT NOT NULL DEFAULT '',
                task_name    TEXT NOT NULL DEFAULT '',
                task_type    TEXT NOT NULL DEFAULT 'cron',
                action_type  TEXT NOT NULL DEFAULT 'reminder',
                cron_expr    TEXT NOT NULL DEFAULT '',
                prompt       TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL,
                output       TEXT NOT NULL DEFAULT '',
                error_msg    TEXT NOT NULL DEFAULT '',
                duration_ms  INTEGER NOT NULL DEFAULT 0,
                executed_at  INTEGER NOT NULL DEFAULT 0
            )
        ''')
        for log_col_def in (
            "chat_id TEXT NOT NULL DEFAULT ''",
            "task_name TEXT NOT NULL DEFAULT ''",
            "task_type TEXT NOT NULL DEFAULT 'cron'",
            "action_type TEXT NOT NULL DEFAULT 'reminder'",
            "cron_expr TEXT NOT NULL DEFAULT ''",
            "prompt TEXT NOT NULL DEFAULT ''",
        ):
            try:
                cursor.execute(f"ALTER TABLE cron_logs ADD COLUMN {log_col_def}")
            except sqlite3.OperationalError:
                pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recent_messages (
                message_id  TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                create_time INTEGER NOT NULL
            )
        ''')
        conn.commit()

def migrate_from_json():
    with closing(get_db()) as conn:
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

# 事件循环隔离且线程安全的会话锁缓存，设置有界淘汰上限，避免跨事件循环绑定报错与内存泄漏
_session_locks = {}
_session_locks_guard = threading.Lock()
_db_session_rw_lock = threading.RLock()
_auth_write_lock = threading.RLock()
_profile_locks = {}
_profile_locks_guard = threading.Lock()


class SessionDict(dict):
    """原生 dict 透明子类，额外携带 `_db_baseline` 快照供 `save_session_async` 自动执行三路原子合并。"""
    def __init__(self, *args, _db_baseline=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._db_baseline = _db_baseline

    def copy(self):
        return SessionDict(
            super().copy(),
            _db_baseline=copy.deepcopy(self._db_baseline) if self._db_baseline is not None else None,
        )

    def __deepcopy__(self, memo):
        plain_copy = {copy.deepcopy(k, memo): copy.deepcopy(v, memo) for k, v in self.items()}
        base_copy = copy.deepcopy(self._db_baseline, memo) if self._db_baseline is not None else None
        res = SessionDict(plain_copy, _db_baseline=base_copy)
        memo[id(self)] = res
        return res


def _get_loop_key() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def _get_session_lock(chat_id: str) -> asyncio.Lock:
    compound_key = (_get_loop_key(), chat_id)
    with _session_locks_guard:
        lock = _session_locks.get(compound_key)
        if lock is None:
            if len(_session_locks) > 2000:
                idle_keys = [k for k, l in _session_locks.items() if not l.locked()]
                for k in idle_keys[:500]:
                    _session_locks.pop(k, None)
            lock = asyncio.Lock()
            _session_locks[compound_key] = lock
        return lock


def _get_profile_lock(profile_key: str) -> asyncio.Lock:
    compound_key = (_get_loop_key(), profile_key)
    with _profile_locks_guard:
        lock = _profile_locks.get(compound_key)
        if lock is None:
            if len(_profile_locks) > 2000:
                idle_keys = [k for k, l in _profile_locks.items() if not l.locked()]
                for k in idle_keys[:500]:
                    _profile_locks.pop(k, None)
            lock = asyncio.Lock()
            _profile_locks[compound_key] = lock
        return lock


from config import DEFAULT_MODEL


def _normalize_session_dict(raw_json: str) -> dict:
    try:
        data = json.loads(raw_json) if raw_json else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    # 防御清理历史可能误持久化的临时隔离池键
    data.pop("_isolated_pool_key", None)
    if not data.get('model') or data.get('model') in ['Gemini 3.5 Flash', 'Gemini 3.5 Flash (Medium)']:
        data['model'] = DEFAULT_MODEL
    return data


_async_db_pool = {}
_async_db_pool_guard = threading.Lock()

# L1 Write-Through Hot Memory Cache (eliminates 3~4 redundant session/profile DB reads per message)
_HOT_CACHE_TTL_SEC = 30.0
_hot_session_cache = {}  # {(db_path, chat_id): (ts, dict)}
_hot_profile_cache = {}  # {(db_path, user_id): (ts, list)}
_hot_cache_lock = threading.RLock()


def _hot_cache_get_session(chat_id: str):
    key = (os.path.abspath(DB_FILE), str(chat_id))
    with _hot_cache_lock:
        item = _hot_session_cache.get(key)
        if item and (time.monotonic() - item[0] <= _HOT_CACHE_TTL_SEC):
            return copy.deepcopy(item[1])
        if item:
            _hot_session_cache.pop(key, None)
    return None


def _hot_cache_put_session(chat_id: str, norm_dict: dict) -> None:
    key = (os.path.abspath(DB_FILE), str(chat_id))
    clean = {k: v for k, v in norm_dict.items() if k != "_isolated_pool_key"}
    with _hot_cache_lock:
        if len(_hot_session_cache) > 512 and key not in _hot_session_cache:
            _hot_session_cache.pop(next(iter(_hot_session_cache)), None)
        _hot_session_cache[key] = (time.monotonic(), copy.deepcopy(clean))


def _hot_cache_get_profile(user_id: str):
    key = (os.path.abspath(DB_FILE), str(user_id))
    with _hot_cache_lock:
        item = _hot_profile_cache.get(key)
        if item and (time.monotonic() - item[0] <= _HOT_CACHE_TTL_SEC):
            return copy.deepcopy(item[1])
        if item:
            _hot_profile_cache.pop(key, None)
    return None


def _hot_cache_put_profile(user_id: str, items: list) -> None:
    key = (os.path.abspath(DB_FILE), str(user_id))
    with _hot_cache_lock:
        if len(_hot_profile_cache) > 512 and key not in _hot_profile_cache:
            _hot_profile_cache.pop(next(iter(_hot_profile_cache)), None)
        _hot_profile_cache[key] = (time.monotonic(), copy.deepcopy(items))


def _notify_cron_engine() -> None:
    try:
        from cron_engine import cron_engine
        cron_engine.notify_schedule_changed()
    except Exception:
        pass


def _prune_closed_loop_db_conns() -> None:
    """Synchronously stop and remove cached aiosqlite connections belonging to closed/GC'd event loops."""
    dead_keys = []
    for key, entry in list(_async_db_pool.items()):
        loop_ref, conn, _ = entry
        loop_obj = loop_ref()
        if loop_obj is None or loop_obj.is_closed():
            dead_keys.append((key, conn))
    for key, conn in dead_keys:
        _async_db_pool.pop(key, None)
        try:
            if hasattr(conn, "stop"):
                conn.stop()
        except Exception:
            pass


def _shutdown_all_async_db_conns() -> None:
    """Stop all pooled aiosqlite worker threads cleanly at interpreter exit."""
    with _async_db_pool_guard:
        entries = list(_async_db_pool.values())
        _async_db_pool.clear()
    for _, conn, _ in entries:
        if conn is not None:
            try:
                if hasattr(conn, "stop"):
                    conn.stop()
            except Exception:
                pass


atexit.register(_shutdown_all_async_db_conns)


@asynccontextmanager
async def _shared_aiosqlite_conn():
    """Reuse a persistent WAL-mode aiosqlite connection per (event_loop, DB_FILE).
    Eliminates per-query worker thread creation, SQLite handshake, and PRAGMA overhead.
    """
    loop = asyncio.get_running_loop()
    db_path = os.path.abspath(DB_FILE)
    pool_key = (id(loop), db_path)

    with _async_db_pool_guard:
        _prune_closed_loop_db_conns()
        entry = _async_db_pool.get(pool_key)
        if entry is None:
            op_lock = asyncio.Lock()
            entry = [weakref.ref(loop), None, op_lock]
            _async_db_pool[pool_key] = entry

    op_lock = entry[2]
    async with op_lock:
        conn = entry[1]
        if conn is None or not getattr(conn, "_running", True):
            conn_obj = aiosqlite.connect(db_path, timeout=DB_TIMEOUT)
            conn_obj.daemon = True
            if hasattr(conn_obj, "_thread") and conn_obj._thread is not None:
                conn_obj._thread.daemon = True
            conn = await conn_obj
            await conn.execute('PRAGMA journal_mode=WAL')
            await conn.execute('PRAGMA busy_timeout=5000')
            await conn.execute('PRAGMA synchronous=NORMAL')
            conn.row_factory = aiosqlite.Row
            entry[1] = conn
        yield conn


async def get_session_async(chat_id):
    async with _get_session_lock(chat_id):
        cached = _hot_cache_get_session(chat_id)
        if cached is not None:
            return SessionDict(cached, _db_baseline=copy.deepcopy(cached))
        async with _shared_aiosqlite_conn() as db:
            async with db.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    norm = _normalize_session_dict(row['data'])
                else:
                    norm = {"conversation": "", "model": DEFAULT_MODEL, "role": "无", "project": "默认"}
                _hot_cache_put_session(chat_id, norm)
                return SessionDict(norm, _db_baseline=copy.deepcopy(norm))


async def _three_way_merge_locked(chat_id: str, data: dict, baseline: dict) -> dict:
    clean_local = {k: v for k, v in data.items() if k != "_isolated_pool_key"}
    async with _shared_aiosqlite_conn() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            async with db.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,)) as cursor:
                row = await cursor.fetchone()
            db_latest = _normalize_session_dict(row['data']) if row else {}
            merged = {}
            all_keys = set(db_latest.keys()) | set(baseline.keys()) | set(clean_local.keys())
            for k in all_keys:
                in_local = k in clean_local
                in_base = k in baseline
                local_changed = (in_local != in_base) or (in_local and clean_local[k] != baseline[k])
                if local_changed:
                    if in_local:
                        merged[k] = clean_local[k]
                else:
                    if k in db_latest:
                        merged[k] = copy.deepcopy(db_latest[k])
            await db.execute(
                'INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)',
                (chat_id, json.dumps(merged, ensure_ascii=False)),
            )
            await db.commit()
            _hot_cache_put_session(chat_id, merged)
        except Exception:
            await db.rollback()
            raise
    return merged


async def save_session_async(chat_id, data):
    # 若该会话数据携带 `_isolated_pool_key`（如 Cron 临时隔离会话），严禁污染写入主会话表
    if isinstance(data, dict) and data.get("_isolated_pool_key"):
        return
    tracked_base = getattr(data, "_db_baseline", None)
    if isinstance(tracked_base, dict):
        async with _get_session_lock(chat_id):
            merged = await _three_way_merge_locked(chat_id, data, tracked_base)
            data.clear()
            data.update(copy.deepcopy(merged))
            data._db_baseline = copy.deepcopy(merged)
        return
    async with _get_session_lock(chat_id):
        clean_data = {k: v for k, v in data.items() if k != "_isolated_pool_key"} if isinstance(data, dict) else data
        async with _shared_aiosqlite_conn() as db:
            await db.execute('BEGIN IMMEDIATE')
            try:
                await db.execute(
                    'INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)',
                    (chat_id, json.dumps(clean_data, ensure_ascii=False)),
                )
                await db.commit()
                if isinstance(clean_data, dict):
                    _hot_cache_put_session(chat_id, _normalize_session_dict(json.dumps(clean_data, ensure_ascii=False)))
            except Exception:
                await db.rollback()
                raise


async def merge_and_save_session_async(chat_id: str, data: dict, baseline: dict = None):
    """Three-way atomic merge for long-running turns so concurrent user edits (e.g. /model, /note, /dir) are never overwritten."""
    if isinstance(data, dict) and data.get("_isolated_pool_key"):
        return
    if baseline is None:
        await save_session_async(chat_id, data)
        return
    async with _get_session_lock(chat_id):
        merged = await _three_way_merge_locked(chat_id, data, baseline)
        data.clear()
        data.update(copy.deepcopy(merged))
        baseline.clear()
        baseline.update(copy.deepcopy(merged))
        if hasattr(data, "_db_baseline"):
            data._db_baseline = copy.deepcopy(merged)

async def get_profile_async(user_id, fallback_chat_id=None):
    primary_key = str(user_id or fallback_chat_id or "")
    if not primary_key:
        return []
    async with _get_profile_lock(primary_key):
        keys_to_query = [primary_key]
        if fallback_chat_id and str(fallback_chat_id) != primary_key:
            keys_to_query.append(str(fallback_chat_id))

        missing_keys = []
        per_key_items = {}
        for qk in keys_to_query:
            cached_p = _hot_cache_get_profile(qk)
            if cached_p is not None:
                per_key_items[qk] = cached_p
            else:
                missing_keys.append(qk)

        if missing_keys:
            async with _shared_aiosqlite_conn() as db:
                for qk in missing_keys:
                    q_list = []
                    async with db.execute('SELECT data FROM user_profiles WHERE user_id = ?', (qk,)) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            try:
                                items = json.loads(row['data'])
                                if isinstance(items, list):
                                    q_list = items
                            except Exception:
                                pass
                    _hot_cache_put_profile(qk, q_list)
                    per_key_items[qk] = q_list

        merged_items = []
        for qk in keys_to_query:
            for it in per_key_items.get(qk, []):
                if it not in merged_items:
                    merged_items.append(it)
        return merged_items

async def save_profile_async(user_id, data):
    primary_key = str(user_id or "")
    if not primary_key:
        return
    async with _get_profile_lock(primary_key):
        async with _shared_aiosqlite_conn() as db:
            await db.execute('INSERT OR REPLACE INTO user_profiles (user_id, data) VALUES (?, ?)', (primary_key, json.dumps(data, ensure_ascii=False)))
            await db.commit()
        if isinstance(data, list):
            _hot_cache_put_profile(primary_key, data)

def load_sessions():
    with _db_session_rw_lock:
        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT chat_id, data FROM chat_sessions')
            rows = cursor.fetchall()
    sessions = {}
    for row in rows:
        norm = _normalize_session_dict(row['data'])
        sessions[row['chat_id']] = norm
        _hot_cache_put_session(row['chat_id'], norm)
    return sessions

def save_sessions(sessions):
    with _db_session_rw_lock:
        with closing(get_db()) as conn:
            cursor = conn.cursor()
            for chat_id, data in sessions.items():
                cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data, ensure_ascii=False)))
            conn.commit()
        for chat_id, data in sessions.items():
            if isinstance(data, dict):
                _hot_cache_put_session(chat_id, _normalize_session_dict(json.dumps(data, ensure_ascii=False)))

def get_session_sync(chat_id):
    cached = _hot_cache_get_session(chat_id)
    if cached is not None:
        return cached
    with _db_session_rw_lock:
        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
    if row:
        norm = _normalize_session_dict(row['data'])
        _hot_cache_put_session(chat_id, norm)
        return norm
    default_norm = {"conversation": "", "model": DEFAULT_MODEL, "role": "无", "project": "默认"}
    _hot_cache_put_session(chat_id, default_norm)
    return default_norm

def save_session_sync(chat_id, data):
    with _db_session_rw_lock:
        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data, ensure_ascii=False)))
            conn.commit()
        if isinstance(data, dict):
            _hot_cache_put_session(chat_id, _normalize_session_dict(json.dumps(data, ensure_ascii=False)))


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
    """Atomically INSERT OR REPLACE an auth_sessions record inside a single BEGIN IMMEDIATE transaction."""
    chat_id_val = session.get("chat_id", "")
    with _auth_write_lock:
        conn = get_db()
        try:
            conn.isolation_level = None
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute('SELECT * FROM auth_sessions WHERE chat_id = ?', (chat_id_val,))
                existing = _auth_row_to_dict(cursor.fetchone())
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

                cursor.execute(
                    f'INSERT OR REPLACE INTO auth_sessions ({", ".join(_AUTH_COLUMNS)}) '
                    f'VALUES ({", ".join(["?"] * len(_AUTH_COLUMNS))})',
                    [merged.get(c, 0 if c in ("created_at", "updated_at", "request_count", "last_request_at", "last_hint_at") else "") for c in _AUTH_COLUMNS],
                )
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise
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
_dedup_insert_counter = 0
_dedup_counter_lock = threading.Lock()


def mark_message_seen(message_id: str, chat_id: str, create_time=None) -> bool:
    """Return True if this message is new; False if it was already processed.
    Old records older than 12h are pruned once per 100 inserts instead of every message."""
    global _dedup_insert_counter
    if not message_id:
        return True
    if create_time is None:
        create_time = int(time.time())
    with _dedup_counter_lock:
        _dedup_insert_counter += 1
        should_prune = (_dedup_insert_counter % 100 == 0)
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
        if should_prune:
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

_last_pending_ts = 0
_pending_ts_lock = threading.Lock()


def allocate_pending_task_ts() -> int:
    """Return a strictly monotonic millisecond timestamp so concurrent tasks in the same ms never collide."""
    global _last_pending_ts
    with _pending_ts_lock:
        now_ms = int(time.time() * 1000)
        if now_ms <= _last_pending_ts:
            now_ms = _last_pending_ts + 1
        _last_pending_ts = now_ms
        return now_ms


def _bump_pending_ts_floor(ts_val: int) -> None:
    global _last_pending_ts
    with _pending_ts_lock:
        if ts_val > _last_pending_ts:
            _last_pending_ts = ts_val


def save_pending_task(chat_id: str, task: dict):
    raw_ts = task.get("created_at")
    if raw_ts:
        created_at = int(raw_ts)
        _bump_pending_ts_floor(created_at)
    else:
        created_at = allocate_pending_task_ts()
    task["created_at"] = created_at
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


async def save_pending_task_async(chat_id: str, task: dict):
    """Non-blocking async persistence for pending_tasks using the shared WAL connection."""
    raw_ts = task.get("created_at")
    if raw_ts:
        created_at = int(raw_ts)
        _bump_pending_ts_floor(created_at)
    else:
        created_at = allocate_pending_task_ts()
    task["created_at"] = created_at
    try:
        async with _shared_aiosqlite_conn() as db:
            await db.execute(
                'INSERT OR REPLACE INTO pending_tasks (chat_id, task, created_at) VALUES (?, ?, ?)',
                (chat_id, json.dumps(task, ensure_ascii=False), created_at),
            )
            await db.commit()
    except Exception as e:
        log.error(f"[pending] save_pending_task_async failed: {e}")


async def delete_pending_task_async(chat_id: str, created_at):
    if not created_at:
        return
    try:
        async with _shared_aiosqlite_conn() as db:
            await db.execute(
                'DELETE FROM pending_tasks WHERE chat_id = ? AND created_at = ?',
                (chat_id, int(created_at)),
            )
            await db.commit()
    except Exception as e:
        log.warning(f"[pending] delete_pending_task_async failed for {chat_id} ({created_at}): {e}")


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
    except Exception as e:
        log.warning(f"[pending] delete_pending_task failed for {chat_id} ({created_at}): {e}")
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
    except Exception as e:
        log.warning(f"[pending] clear_pending_tasks_for_chat failed for {chat_id}: {e}")
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
                if not isinstance(task, dict):
                    continue
            except (json.JSONDecodeError, TypeError) as parse_err:
                log.warning(f"[pending] Skipping malformed pending task row for {row['chat_id']}: {parse_err}")
                continue
            row_ts = int(row["created_at"])
            task["created_at"] = row_ts
            _bump_pending_ts_floor(row_ts)
            result.append((row["chat_id"], task, row_ts))
        return result
    except Exception as e:
        log.error(f"[pending] load_pending_tasks failed: {e}")
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cron Task & Log Helper Functions
# ---------------------------------------------------------------------------

def _ensure_cron_logs_schema(cursor):
    for log_col_def in (
        "chat_id TEXT NOT NULL DEFAULT ''",
        "task_name TEXT NOT NULL DEFAULT ''",
        "task_type TEXT NOT NULL DEFAULT 'cron'",
        "action_type TEXT NOT NULL DEFAULT 'reminder'",
        "cron_expr TEXT NOT NULL DEFAULT ''",
        "prompt TEXT NOT NULL DEFAULT ''",
    ):
        try:
            cursor.execute(f"ALTER TABLE cron_logs ADD COLUMN {log_col_def}")
        except Exception:
            pass


def _archive_tasks_before_delete(cursor, where_clause: str, params: tuple = ()):
    """在自动清理或删除已完成任务前，将任务元数据同步/归档至 cron_logs，防止历史执行记录丢失名称与内容。"""
    _ensure_cron_logs_schema(cursor)
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


def get_all_cron_tasks(chat_id=None):
    conn = get_db()
    try:
        cursor = conn.cursor()
        # 仅当确实存在待归档的历史一次性倒计时任务时才开启写事务清理，消除常规读路径写放大
        cond = "task_type = 'delay' AND is_active = 0 AND (run_count > 0 OR last_run_at > 0)"
        cursor.execute(f"SELECT 1 FROM cron_tasks WHERE {cond} LIMIT 1")
        if cursor.fetchone():
            _archive_tasks_before_delete(cursor, cond)
            cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}")
            if cursor.rowcount > 0:
                conn.commit()
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
                id, chat_id, category, name, task_type, action_type, command, cron_expr, prompt,
                project_path, is_active, created_by, created_at, updated_at,
                last_run_at, next_run_at, run_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                category=excluded.category,
                name=excluded.name,
                task_type=excluded.task_type,
                action_type=excluded.action_type,
                command=excluded.command,
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
            task_data.get('action_type', 'reminder'),
            task_data.get('command', ''),
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
        _notify_cron_engine()
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
        _notify_cron_engine()
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


def delete_cron_task(task_id: str, keep_logs: bool = False):
    conn = get_db()
    try:
        cursor = conn.cursor()
        if keep_logs:
            _archive_tasks_before_delete(cursor, "id = ?", (task_id,))
        cursor.execute('DELETE FROM cron_tasks WHERE id = ?', (task_id,))
        if not keep_logs:
            cursor.execute('DELETE FROM cron_logs WHERE task_id = ?', (task_id,))
        conn.commit()
        _notify_cron_engine()
        return True
    except Exception as e:
        log.error(f"[cron] delete_cron_task failed: {e}")
        return False
    finally:
        conn.close()


def cleanup_completed_delay_tasks(chat_id: str = None, include_paused: bool = False) -> int:
    """清理已完成的一次性任务（当 include_paused=True 时同时清理已暂停的用户任务），保留 cron_logs 执行日志。"""
    conn = get_db()
    try:
        cursor = conn.cursor()
        is_adm = bool(chat_id and get_bot_meta("admin_chat_id") == chat_id)
        if include_paused:
            if chat_id and not is_adm:
                cond = "is_active = 0 AND chat_id = ? AND category NOT IN ('system', 'maintenance')"
                _archive_tasks_before_delete(cursor, cond, (chat_id,))
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}", (chat_id,))
            elif chat_id:
                cond = "is_active = 0 AND (chat_id = ? OR chat_id = '' OR chat_id IS NULL) AND category NOT IN ('system', 'maintenance')"
                _archive_tasks_before_delete(cursor, cond, (chat_id,))
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}", (chat_id,))
            else:
                cond = "is_active = 0 AND category NOT IN ('system', 'maintenance')"
                _archive_tasks_before_delete(cursor, cond)
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}")
        else:
            if chat_id and not is_adm:
                cond = "task_type = 'delay' AND (is_active = 0 OR run_count > 0 OR last_run_at > 0) AND chat_id = ?"
                _archive_tasks_before_delete(cursor, cond, (chat_id,))
                cursor.execute(f"DELETE FROM cron_tasks WHERE {cond}", (chat_id,))
            elif chat_id:
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
    except Exception as e:
        log.error(f"[cron] cleanup_completed_delay_tasks failed: {e}")
        return 0
    finally:
        conn.close()


def record_cron_log(task_id: str, status: str, output: str = '', error_msg: str = '', duration_ms: int = 0, task_meta: dict = None):
    conn = get_db()
    now = int(time.time())
    try:
        cursor = conn.cursor()
        _ensure_cron_logs_schema(cursor)
        meta = dict(task_meta or {})
        if not meta.get("name") and not meta.get("task_name"):
            cursor.execute("SELECT * FROM cron_tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                meta = dict(row)
        chat_id_val = meta.get("chat_id", "") or ""
        task_name_val = meta.get("task_name") or meta.get("name") or ""
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
            output[:2000] if output else '',
            error_msg[:1000] if error_msg else '',
            duration_ms,
            now,
        ))
        conn.commit()
        return True
    except Exception as e:
        log.error(f"[cron] record_cron_log failed: {e}")
        return False
    finally:
        conn.close()


def get_cron_logs(task_id=None, limit=15, chat_id=None):
    conn = get_db()
    try:
        cursor = conn.cursor()
        _ensure_cron_logs_schema(cursor)
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
            if get_bot_meta("admin_chat_id") == chat_id:
                cursor.execute(
                    f"{base_select} WHERE l.chat_id = ? OR l.chat_id = '' OR l.chat_id IS NULL OR t.chat_id = ? ORDER BY l.executed_at DESC, l.id DESC LIMIT ?",
                    (chat_id, chat_id, limit),
                )
            else:
                cursor.execute(
                    f"{base_select} WHERE l.chat_id = ? OR t.chat_id = ? ORDER BY l.executed_at DESC, l.id DESC LIMIT ?",
                    (chat_id, chat_id, limit),
                )
        else:
            cursor.execute(f"{base_select} ORDER BY l.executed_at DESC, l.id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"[cron] get_cron_logs failed: {e}")
        return []
    finally:
        conn.close()


def clear_cron_logs(chat_id: str = None) -> int:
    """清空历史执行审计记录（非管理员仅限清空归属自身会话的日志）。"""
    conn = get_db()
    try:
        cursor = conn.cursor()
        _ensure_cron_logs_schema(cursor)
        if chat_id:
            if get_bot_meta("admin_chat_id") == chat_id:
                cursor.execute("DELETE FROM cron_logs WHERE chat_id = ? OR chat_id = '' OR chat_id IS NULL", (chat_id,))
            else:
                cursor.execute("DELETE FROM cron_logs WHERE chat_id = ?", (chat_id,))
        else:
            cursor.execute("DELETE FROM cron_logs")
        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.commit()
        return deleted
    except Exception as e:
        log.error(f"[cron] clear_cron_logs failed: {e}")
        return 0
    finally:
        conn.close()


def save_pending_update_notice(chat_id, message_id, old_version=""):
    data = json.dumps({"chat_id": chat_id, "message_id": message_id, "old_version": old_version, "timestamp": time.time()})
    with closing(get_db()) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)', ('pending_update_notice', data))
        conn.commit()


def get_and_clear_pending_update_notice():
    with closing(get_db()) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_meta WHERE key = ?', ('pending_update_notice',))
        row = cursor.fetchone()
        if row:
            cursor.execute('DELETE FROM bot_meta WHERE key = ?', ('pending_update_notice',))
            conn.commit()
            try:
                return json.loads(row['value'])
            except Exception as e:
                log.warning(f"[update_notice] Failed to parse pending_update_notice JSON: {e}")
    return None

