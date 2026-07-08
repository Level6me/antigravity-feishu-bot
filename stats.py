BOT_STATS = {
    "total_requests": 0,
    "success_requests": 0,
    "failed_requests": 0,
    "total_tokens": 0
}

def record_request():
    BOT_STATS["total_requests"] += 1

def record_success():
    BOT_STATS["success_requests"] += 1

def record_failure():
    BOT_STATS["failed_requests"] += 1

def record_tokens(count):
    BOT_STATS["total_tokens"] += count

def get_stats():
    return BOT_STATS.copy()
