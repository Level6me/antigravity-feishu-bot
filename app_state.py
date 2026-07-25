"""Shared runtime state for the Feishu bot process."""

main_loop = None
running_processes = {}
chat_queues = {}
chat_workers = {}
chat_media_batches = {}
