import os
import time
import asyncio
from logger import log

async def garbage_collector(interval_seconds=3600, max_age_seconds=86400):
    """
    Background task to periodically clean up old downloads and logs.
    :param interval_seconds: How often to run the cleanup (default 1 hour).
    :param max_age_seconds: Files older than this will be deleted (default 24 hours).
    """
    directories_to_clean = ["downloads", "logs"]
    
    while True:
        try:
            now = time.time()
            deleted_count = 0
            
            # Clean structured directories
            for d in directories_to_clean:
                if os.path.exists(d):
                    for filename in os.listdir(d):
                        filepath = os.path.join(d, filename)
                        if os.path.isfile(filepath):
                            file_age = now - os.path.getmtime(filepath)
                            if file_age > max_age_seconds:
                                try:
                                    os.remove(filepath)
                                    deleted_count += 1
                                except Exception as e:
                                    log.error(f"[GC] Error deleting {filepath}: {e}")
            
            # Clean legacy files in the root directory (for backward compatibility)
            root_dir = "."
            for filename in os.listdir(root_dir):
                if filename.startswith("img_") or filename.startswith("file_v3_") or filename.startswith("agy_log_") or filename.startswith("audio_") or filename.startswith("video_"):
                    filepath = os.path.join(root_dir, filename)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > max_age_seconds:
                            try:
                                os.remove(filepath)
                                deleted_count += 1
                            except Exception as e:
                                log.error(f"[GC] Error deleting legacy file {filepath}: {e}")

            if deleted_count > 0:
                log.info(f"[GC] Garbage collection finished. Deleted {deleted_count} old files.")
                
        except Exception as e:
            log.error(f"[GC] Exception in garbage_collector loop: {e}")
        
        await asyncio.sleep(interval_seconds)
