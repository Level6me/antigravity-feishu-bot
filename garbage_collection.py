import os
import time
import sqlite3
import asyncio
from logger import log

BACKUP_DIR = "backups"
BACKUP_KEEP = 7


def backup_database_if_due():
    """Daily SQLite backup via VACUUM INTO; keeps the newest BACKUP_KEEP copies."""
    from database import DB_FILE

    if not os.path.exists(DB_FILE):
        return
    today = time.strftime("%Y%m%d", time.localtime())
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"antigravity_bot_{today}.db")
    if os.path.exists(target):
        return  # 当天已备份

    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute(f"VACUUM INTO '{target}'")
        finally:
            conn.close()
        log.info(f"[GC] Database backup created: {target}")
    except Exception as e:
        log.error(f"[GC] Database backup failed: {e}")
        return

    # 保留最近 BACKUP_KEEP 份备份
    try:
        backups = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith("antigravity_bot_") and f.endswith(".db")
        )
        for old in backups[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception as e:
        log.error(f"[GC] Backup cleanup failed: {e}")


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

            # 每日数据库备份
            backup_database_if_due()
            
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

            # Clean scratch directory (files older than 7 days = 604800s)
            scratch_dir = "scratch"
            scratch_max_age = 7 * 86400
            if os.path.exists(scratch_dir):
                for filename in os.listdir(scratch_dir):
                    filepath = os.path.join(scratch_dir, filename)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > scratch_max_age:
                            try:
                                os.remove(filepath)
                                deleted_count += 1
                            except Exception as e:
                                log.error(f"[GC] Error deleting scratch file {filepath}: {e}")

            if deleted_count > 0:
                log.info(f"[GC] Garbage collection finished. Deleted {deleted_count} old files.")
                
        except Exception as e:
            log.error(f"[GC] Exception in garbage_collector loop: {e}")
        
        await asyncio.sleep(interval_seconds)
