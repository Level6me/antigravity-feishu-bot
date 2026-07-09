import os
import sys
import shutil
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Settings(BaseSettings):
    app_id: str = Field(default="", alias="APP_ID")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    app_secret: str = Field(default="", alias="APP_SECRET")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    allowed_users: str = ""
    allowed_chats: str = ""
    dangerously_skip_permissions: bool = True
    workspace_root: str = Field(default_factory=lambda: os.path.expanduser("~"))

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

APP_ID = settings.feishu_app_id or settings.app_id
APP_SECRET = settings.feishu_app_secret or settings.app_secret

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
BASE_VERSION_PREFIX = "v1.0."
VERSION_START_COMMIT = 62  # Used to calculate patch number (commit_count - start_commit)

# --- Whitelist & Permission Configuration ---
ALLOWED_USERS = [uid.strip() for uid in settings.allowed_users.split(",") if uid.strip()]
ALLOWED_CHATS = [cid.strip() for cid in settings.allowed_chats.split(",") if cid.strip()]
DANGEROUSLY_SKIP_PERMISSIONS = settings.dangerously_skip_permissions

# --- Workspace & Project Directory Configuration ---
WORKSPACE_ROOT = settings.workspace_root
