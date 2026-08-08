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
    allowed_users: str = Field(default="", alias="ALLOWED_USERS")
    allowed_chats: str = Field(default="", alias="ALLOWED_CHATS")
    dangerously_skip_permissions: bool = Field(default=True, alias="DANGEROUSLY_SKIP_PERMISSIONS")
    workspace_root: str = Field(
        default_factory=lambda: os.path.expanduser("~"),
        alias="WORKSPACE_ROOT",
    )

    # Optional mirror URL (may embed credentials) used as /update fallback
    gitee_mirror_url: str = Field(default="", alias="GITEE_MIRROR_URL")

    # Antigravity / agy installation overrides (portable across machines & containers)
    antigravity_bin: str = Field(default="", alias="ANTIGRAVITY_BIN")
    antigravity_home: str = Field(
        default="",
        alias="ANTIGRAVITY_HOME",
        description="Root dir of antigravity-cli data (default: ~/.gemini/antigravity-cli)",
    )

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()

APP_ID = settings.feishu_app_id or settings.app_id
APP_SECRET = settings.feishu_app_secret or settings.app_secret

SESSION_FILE = os.path.join(BASE_DIR, "chat_sessions.json")
PROFILE_FILE = os.path.join(BASE_DIR, "user_profiles.json")


def _default_antigravity_home() -> str:
    return os.path.expanduser("~/.gemini/antigravity-cli")


def get_antigravity_home() -> str:
    """Return the antigravity-cli data root (overridable via ANTIGRAVITY_HOME)."""
    raw = (settings.antigravity_home or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return _default_antigravity_home()


def get_brain_dir() -> str:
    """Conversation brain directory that holds per-conversation transcript logs."""
    return os.path.join(get_antigravity_home(), "brain")


def get_transcript_path(conv_id: str) -> str:
    """Canonical transcript.jsonl path for a conversation id."""
    return os.path.join(
        get_brain_dir(),
        conv_id,
        ".system_generated",
        "logs",
        "transcript.jsonl",
    )


def get_oauth_token_path() -> str:
    return os.path.join(get_antigravity_home(), "antigravity-oauth-token")


def get_global_memory_path() -> str:
    return os.path.join(get_antigravity_home(), "global_memory.json")


def find_antigravity_bin() -> Optional[str]:
    # Explicit override first
    explicit = (settings.antigravity_bin or "").strip()
    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
        if os.path.exists(path):
            return path

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
    return None


ANTIGRAVITY_BIN = find_antigravity_bin()

# --- Versioning Configuration ---
BASE_VERSION_PREFIX = "v2.0."
VERSION_START_COMMIT = 235  # Used to calculate patch number (commit_count - start_commit)

# --- Whitelist & Permission Configuration ---
ALLOWED_USERS = [uid.strip() for uid in settings.allowed_users.split(",") if uid.strip()]
ALLOWED_CHATS = [cid.strip() for cid in settings.allowed_chats.split(",") if cid.strip()]
DANGEROUSLY_SKIP_PERMISSIONS = settings.dangerously_skip_permissions

# --- Workspace & Project Directory Configuration ---
WORKSPACE_ROOT = settings.workspace_root

GITEE_MIRROR_URL = settings.gitee_mirror_url.strip()

# Back-compat aliases for path helpers (prefer the get_* functions)
ANTIGRAVITY_HOME = get_antigravity_home()
BRAIN_DIR = get_brain_dir()
