import os
import re
import sys
import shutil
import json
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

    # Optional default model configuration
    default_model: str = Field(default="gemini-3.7-flash-low", alias="DEFAULT_MODEL")

    # Native voice reply settings
    tts_voice: str = Field(default="zh-CN-XiaoxiaoNeural", alias="TTS_VOICE")
    enable_voice_reply: bool = Field(default=True, alias="ENABLE_VOICE_REPLY")

    # TypeSafe AI (System One Model Jev) settings
    typesafe_api_key: str = Field(default="", alias="TYPESAFE_API_KEY")
    typesafe_enabled: bool = Field(default=True, alias="TYPESAFE_ENABLED")
    typesafe_model: str = Field(default="jev-latest", alias="TYPESAFE_MODEL")
    typesafe_base_url: Optional[str] = Field(default=None, alias="TYPESAFE_BASE_URL")
    typesafe_tier: str = Field(default="gateway", alias="TYPESAFE_TIER")  # gateway | sentry | copilot
    typesafe_auto_mode: bool = Field(default=True, alias="TYPESAFE_AUTO_MODE")

    # Task stall & compile watchdog timeouts (seconds)
    stall_timeout: int = Field(default=600, alias="STALL_TIMEOUT")
    stall_hard_timeout: int = Field(default=3600, alias="STALL_HARD_TIMEOUT")
    tool_execution_timeout: int = Field(default=3600, alias="TOOL_EXECUTION_TIMEOUT")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()

APP_ID = settings.feishu_app_id or settings.app_id
DEFAULT_MODEL = settings.default_model or "gemini-3.7-flash-low"
APP_SECRET = settings.feishu_app_secret or settings.app_secret
TTS_VOICE = settings.tts_voice or "zh-CN-XiaoxiaoNeural"
ENABLE_VOICE_REPLY = settings.enable_voice_reply

# Watchdog timeouts exports
STALL_TIMEOUT = settings.stall_timeout
STALL_HARD_TIMEOUT = settings.stall_hard_timeout
TOOL_EXECUTION_TIMEOUT = settings.tool_execution_timeout

# TypeSafe AI exports
TYPESAFE_API_KEY = settings.typesafe_api_key or os.getenv("TYPESAFE_API_KEY", "")
TYPESAFE_ENABLED = settings.typesafe_enabled
TYPESAFE_MODEL = settings.typesafe_model or "jev-latest"
TYPESAFE_BASE_URL = settings.typesafe_base_url
TYPESAFE_TIER = settings.typesafe_tier or os.getenv("TYPESAFE_TIER", "gateway")
TYPESAFE_AUTO_MODE = getattr(settings, "typesafe_auto_mode", True)


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


def get_transcript_full_path(conv_id: str) -> str:
    """Canonical transcript_full.jsonl path for a conversation id."""
    return os.path.join(
        get_brain_dir(),
        conv_id,
        ".system_generated",
        "logs",
        "transcript_full.jsonl",
    )


def recover_full_tool_call(
    conv_id: str,
    tool_name: str = "run_command",
    prefix_hint: str = "",
    retries: int = 2,
    retry_delay: float = 0.08,
) -> Optional[str]:
    """Recover un-truncated full tool call command from transcript_full.jsonl (or transcript.jsonl).
    agy in stream-json mode truncates long tool parameters to ~512 chars with an ellipsis ('…' or '...').
    This function reads the un-truncated full argument from transcript_full.jsonl.
    """
    if not conv_id:
        return None
    full_path = get_transcript_full_path(conv_id)
    compact_path = get_transcript_path(conv_id)
    paths_to_try = [full_path, compact_path]

    clean_hint = (prefix_hint or "").rstrip("…. ").strip()
    clean_hint_no_timeout = re.sub(r"^timeout\s+(?:-k\s+\S+\s+)?\d+(?:\.\d+)?[smhd]?\s+", "", clean_hint).strip()

    def _matches_hint(candidate: str) -> bool:
        if not clean_hint:
            return True
        check_len = min(60, len(clean_hint))
        if check_len > 0 and candidate.startswith(clean_hint[:check_len]):
            return True
        cand_no_timeout = re.sub(r"^timeout\s+(?:-k\s+\S+\s+)?\d+(?:\.\d+)?[smhd]?\s+", "", candidate).strip()
        check_len_nt = min(60, len(clean_hint_no_timeout))
        if check_len_nt > 0 and cand_no_timeout.startswith(clean_hint_no_timeout[:check_len_nt]):
            return True
        return False

    import time as _time
    max_attempts = max(1, retries + 1)
    for attempt in range(max_attempts):
        for p in paths_to_try:
            if not os.path.exists(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    tool_calls = list(data.get("tool_calls") or [])
                    step_obj = data.get("step")
                    if isinstance(step_obj, dict):
                        t_info = step_obj.get("tool_info")
                        if isinstance(t_info, dict):
                            tool_calls.append({
                                "name": step_obj.get("tool_name") or step_obj.get("name") or "",
                                "args": t_info.get("parameters") or t_info.get("args") or {},
                            })
                    for tc in tool_calls:
                        if tc.get("name") == tool_name or not tool_name:
                            args = tc.get("args") or tc.get("parameters") or {}
                            if not isinstance(args, dict):
                                continue
                            cmd = args.get("CommandLine") or args.get("command")
                            if isinstance(cmd, str) and cmd.strip():
                                clean_cmd = cmd.strip()
                                # Never treat a still-truncated string from compact transcript.jsonl as recovered
                                if clean_cmd.endswith("…") or clean_cmd.endswith("..."):
                                    continue
                                if _matches_hint(clean_cmd):
                                    return clean_cmd
            except Exception:
                pass
        if attempt < max_attempts - 1 and retry_delay > 0:
            _time.sleep(retry_delay)
    return None


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
BASE_VERSION_PREFIX = "v3.2."
VERSION_START_COMMIT = 362  # Used to calculate patch number (commit_count - start_commit)

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
