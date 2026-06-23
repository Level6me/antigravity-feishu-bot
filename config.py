import os
import shutil
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID") or os.getenv("APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET") or os.getenv("APP_SECRET", "")

SESSION_FILE = "chat_sessions.json"
PROFILE_FILE = "user_profiles.json"

def find_antigravity_bin():
    # Try finding in PATH
    for name in ["agy", "antigravity"]:
        path = shutil.which(name)
        if path:
            return path
            
    # Try common locations
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local/bin/agy"),
        os.path.join(home, ".local/bin/antigravity"),
        "/root/.local/bin/agy",
        "/Users/YOUR_USERNAME/.local/bin/antigravity",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
            
    # Fallback
    return "agy"

ANTIGRAVITY_BIN = find_antigravity_bin()

