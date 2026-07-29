"""Shared runtime state for the Feishu bot process."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}

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
