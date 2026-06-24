import time
from functools import wraps
from logger import log

def with_retry(max_retries=3, initial_delay=1.0, backoff_factor=2.0):
    """
    Exponential backoff retry decorator for synchronous functions.
    Catches network exceptions and retries with increasing delays.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        log.error(f"[Retry] Function {func.__name__} failed after {max_retries} retries. Error: {e}")
                        raise
                    log.warning(f"[Retry] Function {func.__name__} failed (attempt {attempt+1}/{max_retries}). Retrying in {delay}s... Error: {e}")
                    time.sleep(delay)
                    delay *= backoff_factor
        return wrapper
    return decorator
