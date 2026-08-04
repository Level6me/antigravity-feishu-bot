"""Permission & access-control core for the bot.

Roles:
    admin   – first p2p chat bound at bootstrap, all capabilities
    user    – granted chat, scoped capabilities
    pending – requested access, awaiting admin approval
    guest   – not yet authorized (silent mode, only /auth works)
    banned  – blacklisted, fully silent

Legacy compatibility: if ALLOWED_USERS / ALLOWED_CHATS are configured and a
chat/sender matches, it is treated as an authorized `user` with the "dev"
scope bundle (no shell / quota), so existing deployments upgrade seamlessly.
"""

import json
import threading
import time

from config import ALLOWED_USERS, ALLOWED_CHATS
from database import (
    get_auth_session,
    get_bot_meta,
    list_auth_sessions,
    save_auth_session,
    set_bot_meta,
)

# ---------------------------------------------------------------------------
# Roles / scopes
# ---------------------------------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_PENDING = "pending"
ROLE_GUEST = "guest"
ROLE_BANNED = "banned"

SCOPE_CHAT = "chat"
SCOPE_MEDIA = "media"
SCOPE_FILES = "files"
SCOPE_PROJECT = "project"
SCOPE_SHELL = "shell"
SCOPE_QUOTA = "quota"
SCOPE_NOTES = "notes_memory"

TIER_BASIC = "basic"
TIER_DEV = "dev"
TIER_FULL = "full"

SCOPE_TIERS = {
    TIER_BASIC: [SCOPE_CHAT, SCOPE_MEDIA, SCOPE_NOTES],
    TIER_DEV: [SCOPE_CHAT, SCOPE_MEDIA, SCOPE_NOTES, SCOPE_PROJECT, SCOPE_FILES],
    TIER_FULL: [SCOPE_CHAT, SCOPE_MEDIA, SCOPE_NOTES, SCOPE_PROJECT, SCOPE_FILES, SCOPE_SHELL, SCOPE_QUOTA],
}

# Messages / groups are rate limited; admins are not.
MESSAGE_PER_MINUTE_LIMIT = 5
DAILY_EXECUTION_LIMIT = 100


def _now() -> int:
    return int(time.time())


def _legacy_allowed(chat_id: str, sender_open_id: str = "") -> bool:
    if not ALLOWED_USERS and not ALLOWED_CHATS:
        return False
    if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
        return True
    if ALLOWED_USERS and sender_open_id and sender_open_id in ALLOWED_USERS:
        return True
    return False


def get_admin_chat_id():
    return get_bot_meta("admin_chat_id")


def is_bootstrapped() -> bool:
    return bool(get_bot_meta("admin_chat_id"))


def try_bootstrap_admin(chat_id: str, chat_type: str) -> bool:
    """Bind the first p2p chat as the admin. Group chats can never bootstrap."""
    if get_bot_meta("admin_chat_id"):
        return False
    if chat_type != "p2p":
        return False
    now = _now()
    set_bot_meta("admin_chat_id", chat_id)
    set_bot_meta("bootstrap_done", "1")
    save_auth_session({
        "chat_id": chat_id,
        "chat_type": chat_type,
        "display_name": "管理员",
        "role": ROLE_ADMIN,
        "scopes": [],
        "created_at": now,
        "updated_at": now,
    })
    return True


def get_role(chat_id: str, sender_open_id: str = "") -> str:
    """Return the effective role for a chat, materializing legacy whitelist
    matches into a persisted `user` session (dev tier) on first contact."""
    sess = get_auth_session(chat_id)
    if sess:
        return sess.get("role") or ROLE_GUEST

    if _legacy_allowed(chat_id, sender_open_id):
        now = _now()
        save_auth_session({
            "chat_id": chat_id,
            "chat_type": "p2p" if sender_open_id else "group",
            "sender_open_id": sender_open_id,
            "display_name": "旧白名单用户",
            "role": ROLE_USER,
            "scopes": list(SCOPE_TIERS[TIER_DEV]),
            "created_at": now,
            "updated_at": now,
            "granted_by": "legacy-whitelist",
        })
        return ROLE_USER
    return ROLE_GUEST


def is_admin(chat_id: str) -> bool:
    return get_role(chat_id) == ROLE_ADMIN


def is_authorized(chat_id: str, sender_open_id: str = "") -> bool:
    return get_role(chat_id, sender_open_id) in (ROLE_ADMIN, ROLE_USER)


def has_scope(chat_id: str, scope: str, sender_open_id: str = "") -> bool:
    role = get_role(chat_id, sender_open_id)
    if role == ROLE_ADMIN:
        return True
    if role != ROLE_USER:
        return False
    sess = get_auth_session(chat_id) or {}
    return scope in (sess.get("scopes") or [])


def get_scopes(chat_id: str) -> list:
    sess = get_auth_session(chat_id) or {}
    scopes = sess.get("scopes") or []
    return list(scopes)


def set_session_role(chat_id: str, role: str, scopes: list, operator: str = "") -> None:
    sess = get_auth_session(chat_id) or {}
    sess.update({
        "chat_id": chat_id,
        "role": role,
        "scopes": list(scopes),
        "updated_at": _now(),
        "granted_by": operator or sess.get("granted_by", ""),
    })
    save_auth_session(sess)


def request_access(chat_id: str, chat_type: str, sender_open_id: str, message_text: str = "") -> str:
    """Register an access request. Returns a status string:
    ok / already / admin / banned / rate."""
    now = _now()
    sess = get_auth_session(chat_id) or {}
    role = sess.get("role")
    if role == ROLE_ADMIN:
        return "admin"
    if role == ROLE_USER:
        return "already"
    if role == ROLE_BANNED:
        return "banned"

    last_req = sess.get("last_request_at") or 0
    if now - last_req < 600:
        return "rate"

    sess.update({
        "chat_id": chat_id,
        "chat_type": chat_type,
        "sender_open_id": sender_open_id or sess.get("sender_open_id", ""),
        "role": ROLE_PENDING,
        "request_count": (sess.get("request_count") or 0) + 1,
        "last_request_at": now,
        "last_message": (message_text or "")[:50],
        "updated_at": now,
    })
    save_auth_session(sess)
    return "ok"


def list_sessions() -> list:
    return list_auth_sessions()


# ---------------------------------------------------------------------------
# Rate limiting (in-memory; admins exempt)
# ---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._minute = {}   # chat_id -> [window_start, count]
        self._daily = {}    # chat_id -> [day_key, count]

    def _minute_window(self, ts: int) -> int:
        return ts // 60

    def _day_key(self, ts: int) -> str:
        return time.strftime("%Y%m%d", time.localtime(ts))

    def allow_minute(self, chat_id: str) -> bool:
        now = _now()
        window = self._minute_window(now)
        with self._lock:
            entry = self._minute.get(chat_id)
            if not entry or entry[0] != window:
                self._minute[chat_id] = [window, 1]
                return True
            if entry[1] >= MESSAGE_PER_MINUTE_LIMIT:
                return False
            entry[1] += 1
            return True

    def allow_daily_execution(self, chat_id: str) -> bool:
        now = _now()
        day = self._day_key(now)
        with self._lock:
            entry = self._daily.get(chat_id)
            if not entry or entry[0] != day:
                self._daily[chat_id] = [day, 1]
                return True
            if entry[1] >= DAILY_EXECUTION_LIMIT:
                return False
            entry[1] += 1
            return True


rate_limiter = _RateLimiter()


def allow_message(chat_id: str) -> bool:
    """True if the chat may send another message (admins always allowed)."""
    if is_admin(chat_id):
        return True
    return rate_limiter.allow_minute(chat_id)


def allow_execution(chat_id: str) -> bool:
    """True if the chat may trigger another agy execution (admins exempt)."""
    if is_admin(chat_id):
        return True
    return rate_limiter.allow_daily_execution(chat_id)
