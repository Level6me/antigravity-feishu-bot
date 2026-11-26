"""Shared runtime state for the Feishu bot process."""
import asyncio
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

main_loop = None
running_processes = {}
active_subprocess_pids = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}
extended_wait_chats = {}

# Bounded thread-safe registries for Schema 2.0 (CardKit) message tracking and
# one-shot interactive card action idempotency locks.
_CARD_STATE_LOCK = threading.Lock()
_V2_CARD_MESSAGES = OrderedDict()  # {message_id: card_id or ""}
_V2_CARD_MAX_ENTRIES = 2000
_CLAIMED_CARD_ACTIONS = OrderedDict()  # {action_key: expire_monotonic_ts}
_CLAIMED_ACTION_MAX_ENTRIES = 2000


def register_v2_card_message(message_id: str, card_id: str = "") -> None:
    """Record that a Feishu message_id uses Card JSON Schema 2.0 (CardKit)."""
    mid = str(message_id or "").strip()
    if not mid:
        return
    cid = str(card_id or "").strip()
    with _CARD_STATE_LOCK:
        existing = _V2_CARD_MESSAGES.get(mid, "")
        _V2_CARD_MESSAGES[mid] = cid or existing
        _V2_CARD_MESSAGES.move_to_end(mid)
        while len(_V2_CARD_MESSAGES) > _V2_CARD_MAX_ENTRIES:
            _V2_CARD_MESSAGES.popitem(last=False)


def is_v2_card_message(message_id: str) -> bool:
    """Return True if message_id is known to be a Card JSON Schema 2.0 message."""
    mid = str(message_id or "").strip()
    if not mid:
        return False
    with _CARD_STATE_LOCK:
        return mid in _V2_CARD_MESSAGES


def get_v2_card_id(message_id: str) -> str:
    """Return tracked CardKit card_id for message_id, if available."""
    mid = str(message_id or "").strip()
    if not mid:
        return ""
    with _CARD_STATE_LOCK:
        return _V2_CARD_MESSAGES.get(mid, "")


def claim_card_action(action_key: str, ttl_seconds: float = 3600.0) -> bool:
    """Atomically claim a one-shot card action key.

    Returns True for the first claimant within ttl_seconds and False for
    duplicate clicks / webhook retries.
    """
    key = str(action_key or "").strip()
    if not key:
        return True
    now = time.monotonic()
    with _CARD_STATE_LOCK:
        exp = _CLAIMED_CARD_ACTIONS.get(key)
        if exp is not None and exp > now:
            return False
        _CLAIMED_CARD_ACTIONS[key] = now + max(1.0, float(ttl_seconds or 3600.0))
        _CLAIMED_CARD_ACTIONS.move_to_end(key)
        # Opportunistically prune expired or excess entries
        while len(_CLAIMED_CARD_ACTIONS) > _CLAIMED_ACTION_MAX_ENTRIES:
            _CLAIMED_CARD_ACTIONS.popitem(last=False)
        for k in list(_CLAIMED_CARD_ACTIONS.keys())[:32]:
            if _CLAIMED_CARD_ACTIONS.get(k, 0.0) <= now:
                _CLAIMED_CARD_ACTIONS.pop(k, None)
        return True


def release_card_action(action_key: str) -> None:
    """Release a previously claimed card action key (e.g. if rejected before execution)."""
    key = str(action_key or "").strip()
    if not key:
        return
    with _CARD_STATE_LOCK:
        _CLAIMED_CARD_ACTIONS.pop(key, None)


def schedule_bg_coro(
    coro_or_factory,
    *,
    label: str = "CardAction",
    claim_key: str = "",
    release_on_error: bool = False,
):
    """Safely schedule a coroutine on main_loop with centralized exception logging
    and optional idempotency-lock rollback on failure (prevents silent background
    crashes and stuck 1-hour claim locks).
    """
    from logger import log

    loop = main_loop
    if not (loop and loop.is_running()):
        if callable(coro_or_factory):
            pass
        elif asyncio.iscoroutine(coro_or_factory):
            try:
                coro_or_factory.close()
            except Exception:
                pass
        if release_on_error and claim_key:
            release_card_action(claim_key)
        log.warning(f"[{label}] main_loop is not running; skipped scheduling background task")
        return None

    async def _guarded_runner():
        try:
            coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if release_on_error and claim_key:
                release_card_action(claim_key)
            log.error(f"[{label}] Background task failed: {exc}", exc_info=True)
            return None

    fut = asyncio.run_coroutine_threadsafe(_guarded_runner(), loop)

    def _on_done(done_fut):
        try:
            if done_fut.cancelled():
                return
            exc = done_fut.exception()
            if exc is not None:
                if release_on_error and claim_key:
                    release_card_action(claim_key)
                log.error(f"[{label}] Unhandled future exception: {exc}")
        except Exception:
            pass

    fut.add_done_callback(_on_done)
    return fut


def register_subprocess(key: str, proc) -> None:
    """Track a spawned subprocess both by session key and globally by PID until reaped."""
    if proc is None:
        return
    if key:
        running_processes[key] = proc
    pid = getattr(proc, "pid", None)
    if pid:
        active_subprocess_pids[pid] = proc


def unregister_subprocess(key: str, proc) -> None:
    """Remove subprocess tracking ONLY after termination/reaping completes."""
    if key and proc is not None and running_processes.get(key) is proc:
        running_processes.pop(key, None)
    pid = getattr(proc, "pid", None)
    if pid and active_subprocess_pids.get(pid) is proc:
        active_subprocess_pids.pop(pid, None)


def snapshot_all_tracked_subprocesses() -> list:
    """Return deduplicated list of all live/unreaped subprocesses across pool swaps."""
    seen = set()
    procs = []
    for p in list(running_processes.values()) + list(active_subprocess_pids.values()):
        if p is not None and id(p) not in seen:
            seen.add(id(p))
            procs.append(p)
    return procs

_feishu_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="feishu_pool")

def get_feishu_executor():
    global _feishu_executor
    if _feishu_executor._shutdown:
        _feishu_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="feishu_pool")
    return _feishu_executor

async def run_feishu_sync(loop, sync_fn):
    executor = get_feishu_executor()
    try:
        return await loop.run_in_executor(executor, sync_fn)
    except RuntimeError as e:
        if "shutdown" in str(e).lower():
            global _feishu_executor
            _feishu_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="feishu_pool")
            return await loop.run_in_executor(_feishu_executor, sync_fn)
        raise
