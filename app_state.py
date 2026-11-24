"""Shared runtime state for the Feishu bot process."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

main_loop = None
running_processes = {}
active_subprocess_pids = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}
extended_wait_chats = {}


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
