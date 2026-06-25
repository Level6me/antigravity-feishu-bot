import os
import sys
import shutil
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

APP_ID = os.getenv("FEISHU_APP_ID") or os.getenv("APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET") or os.getenv("APP_SECRET", "")

SESSION_FILE = os.path.join(BASE_DIR, "chat_sessions.json")
PROFILE_FILE = os.path.join(BASE_DIR, "user_profiles.json")

def find_antigravity_bin():
    # Try finding in PATH
    for name in ["agy", "antigravity"]:
        path = shutil.which(name)
        if path:
            return path
            
    # Try checking relative to the current python executable
    # This covers virtual environments and pm2's python interpreter
    if sys.executable:
        bin_dir = os.path.dirname(sys.executable)
        for name in ["agy", "antigravity"]:
            c = os.path.join(bin_dir, name)
            if os.path.exists(c):
                return c

    # Try common locations
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local/bin/agy"),
        os.path.join(home, ".local/bin/antigravity"),
        "/root/.local/bin/agy",
        "/root/.local/bin/antigravity",
        "/usr/local/bin/agy",
        "/usr/local/bin/antigravity",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
            
ANTIGRAVITY_BIN = find_antigravity_bin()

# --- Versioning Configuration ---
# To upgrade the major/minor version (e.g. to v1.1.x), change BASE_VERSION_PREFIX to "v1.1." 
# and update VERSION_START_COMMIT to the current git commit count minus 1.
BASE_VERSION_PREFIX = "v1.0."
VERSION_START_COMMIT = 62  # Used to calculate patch number (commit_count - start_commit)

# --- Whitelist & Permission Configuration ---
ALLOWED_USERS = [uid.strip() for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()]
ALLOWED_CHATS = [cid.strip() for cid in os.getenv("ALLOWED_CHATS", "").split(",") if cid.strip()]
DANGEROUSLY_SKIP_PERMISSIONS = os.getenv("DANGEROUSLY_SKIP_PERMISSIONS", "true").lower() == "true"


