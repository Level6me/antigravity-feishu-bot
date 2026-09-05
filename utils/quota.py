"""Quota fetching utilities (cross-platform).

Port discovery order:
    1. ANTIGRAVITY_LSP_PORT env var
    2. heartbeat file <ANTIGRAVITY_HOME>/lsp_port
    3. Linux: /proc scan for the agy process listen port
    4. macOS/Linux: lsof LISTEN scan filtered by agy process

TLS is verified by default. To allow self-signed local LSP certs set
ANTIGRAVITY_QUOTA_INSECURE=true (Google API still verifies certs).
"""

import asyncio
from datetime import datetime, timezone
import glob
import json
import os
import re
import ssl
import subprocess
import time
from typing import Optional
import urllib.request

from config import get_antigravity_home, get_oauth_token_path
from logger import log

_PORT_CACHE = None  # (timestamp, port)
_PORT_CACHE_TTL = 300


def _ssl_context(insecure_allowed=True):
    """Default: verify certificates. Opt-out only via explicit env flag."""
    flag = os.environ.get("ANTIGRAVITY_QUOTA_INSECURE", "").strip().lower()
    if insecure_allowed and flag in ("1", "true", "yes", "on"):
        log.warning("[quota] TLS verification disabled (ANTIGRAVITY_QUOTA_INSECURE=true)")
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _discover_linux_proc():
    """Scan /proc for agy processes and collect their listen ports (Linux)."""
    candidate_ports = set()
    if not os.path.isdir("/proc"):
        return candidate_ports
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="ignore")
                if "agy" not in cmdline and "antigravity" not in cmdline:
                    continue
                fd_dir = f"/proc/{pid_dir}/fd"
                if not os.path.isdir(fd_dir):
                    continue
                for fd in os.listdir(fd_dir):
                    try:
                        link = os.readlink(f"{fd_dir}/{fd}")
                        if "socket:" not in link:
                            continue
                        inode = link.split("[")[1].rstrip("]")
                        with open("/proc/net/tcp", "r") as tcp_f:
                            for tcp_line in tcp_f:
                                parts = tcp_line.strip().split()
                                if len(parts) >= 10 and parts[9] == inode and parts[3] == "0A":
                                    hex_port = parts[1].split(":")[1]
                                    candidate_ports.add(int(hex_port, 16))
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception as e:
        log.warning(f"[quota] /proc scan error: {e}")
    return candidate_ports


def _discover_lsof():
    """Fallback: lsof LISTEN scan filtered by agy process name (macOS/Linux)."""
    candidate_ports = set()
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if "agy" not in line and "antigravity" not in line:
                continue
            # COMMAND  PID  USER  FD  TYPE DEVICE SIZE/OFF NODE NAME
            parts = line.split()
            if len(parts) >= 9:
                m = re.search(r"127\.0\.0\.1:(\d+)|localhost:(\d+)|\[::1\]:(\d+)", parts[8])
                if m:
                    port = int(m.group(1) or m.group(2) or m.group(3))
                    candidate_ports.add(port)
    except Exception as e:
        log.warning(f"[quota] lsof scan error: {e}")
    return candidate_ports


def discover_lsp_port():
    """Discover the local agy LSP port, cached for 5 minutes."""
    global _PORT_CACHE
    now = time.time()
    if _PORT_CACHE and now - _PORT_CACHE[0] < _PORT_CACHE_TTL:
        return _PORT_CACHE[1]

    port = None
    # 1. explicit env override
    env_port = os.environ.get("ANTIGRAVITY_LSP_PORT", "").strip()
    if env_port.isdigit():
        port = int(env_port)
    # 2. heartbeat file written by agy-daemon (cross-platform)
    if port is None:
        heartbeat = os.path.join(get_antigravity_home(), "lsp_port")
        if os.path.exists(heartbeat):
            try:
                with open(heartbeat, "r", encoding="utf-8") as f:
                    p = f.read().strip()
                if p.isdigit():
                    port = int(p)
            except Exception as e:
                log.warning(f"[quota] heartbeat read error: {e}")
    # 3. Linux /proc scan
    if port is None:
        ports = _discover_linux_proc()
        if ports:
            port = min(ports)
    # 4. lsof fallback (macOS/Linux)
    if port is None:
        ports = _discover_lsof()
        if ports:
            port = min(ports)

    _PORT_CACHE = (now, port)
    return port


def _probe_lsp(port):
    """Query the local LSP gRPC endpoint for the quota summary."""
    try:
        metadata_payload = b'{"metadata": {"ideName": "antigravity", "extensionName": "antigravity"}}'
        url = f"https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        req = urllib.request.Request(
            url,
            data=metadata_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Local LSP on 127.0.0.1 uses a self-signed cert; bypass local verification
        local_ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=local_ctx, timeout=3) as response:
            data = json.loads(response.read().decode())
            if "response" in data and "groups" in data["response"]:
                return data
    except Exception as e:
        log.warning(f"[quota] LSP probe on port {port} failed: {e}")
    return None


def _fetch_remote_fallback():
    """Fallback: use the local OAuth token against the Google quota API."""
    token_path = get_oauth_token_path()
    if not os.path.exists(token_path):
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token_info = json.load(f)
        access_token = token_info["token"]["access_token"]
        # 脱敏日志：只记录 token 前缀，避免泄露完整凭据
        log.info(f"[quota] remote fallback using token {str(access_token)[:6]}...")
        url = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
        req = urllib.request.Request(
            url,
            data=b'{"project":"high-battery-8d2jw"}',
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": "antigravity/cli/1.1.3",
            },
            method="POST",
        )
        # Google API always verifies TLS certificates
        with urllib.request.urlopen(req, context=_ssl_context(insecure_allowed=False), timeout=8) as response:
            api_data = json.loads(response.read().decode())
            return {"response": api_data}
    except Exception as e:
        log.error(f"[quota] remote fallback failed: {e}")
    return None


def fetch_quota():
    """Return quota data dict (or None). Uses local LSP first, remote fallback."""
    port = discover_lsp_port()
    if port is not None:
        data = _probe_lsp(port)
        if data:
            return data
    return _fetch_remote_fallback()


def format_duration(seconds: int) -> str:
    """Format duration in seconds to Antigravity style 'XhXmXs' (e.g. 1h50m17s, 4h40m18s)."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    elif days > 0:
        parts.append("0h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    elif hours > 0 or days > 0:
        parts.append("0m")
    parts.append(f"{secs}s")
    return "".join(parts)


def get_reset_duration_for_model(quota_data: Optional[dict], model_name: str = "") -> Optional[str]:
    """Calculate soonest reset duration string for the given model from quota data."""
    if not quota_data or "response" not in quota_data or "groups" not in quota_data["response"]:
        return None

    groups = quota_data["response"].get("groups", [])
    model_lower = (model_name or "").lower()

    target_groups = []
    for g in groups:
        d_name = g.get("displayName", "").lower()
        desc = g.get("description", "").lower()
        if "gemini" in model_lower:
            if "gemini" in d_name or "gemini" in desc:
                target_groups.append(g)
        elif any(k in model_lower for k in ["claude", "gpt", "3p"]):
            if any(k in d_name or k in desc for k in ["claude", "gpt", "3p"]):
                target_groups.append(g)

    if not target_groups:
        target_groups = groups

    now_utc = datetime.now(timezone.utc)
    soonest_seconds = None

    # Priority 1: buckets that are exhausted (remainingFraction <= 0.05)
    exhausted_buckets = []
    for g in target_groups:
        for b in g.get("buckets", []):
            if b.get("remainingFraction", 1.0) <= 0.05:
                exhausted_buckets.append(b)

    candidates = exhausted_buckets if exhausted_buckets else [b for g in target_groups for b in g.get("buckets", [])]

    for b in candidates:
        reset_time_str = b.get("resetTime")
        if reset_time_str:
            try:
                dt = datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))
                diff = int((dt - now_utc).total_seconds())
                if diff > 0:
                    if soonest_seconds is None or diff < soonest_seconds:
                        soonest_seconds = diff
            except Exception:
                pass

    if soonest_seconds is not None and soonest_seconds > 0:
        return format_duration(soonest_seconds)
    return None


def is_quota_error(text: str) -> bool:
    """Check if the text represents a quota exhaustion error."""
    if not text:
        return False
    lower = text.lower()
    quota_signatures = [
        "individual quota reached",
        "resource_exhausted",
        "quota reached",
        "quota exceeded",
        "exceeded your current quota",
        "rate limit exceeded",
    ]
    return any(sig in lower for sig in quota_signatures)


def extract_quota_duration_from_text(text: str) -> Optional[str]:
    """Extract 'Resets in X' from raw error text."""
    if not text:
        return None
    m = re.search(r'Resets in\s+([0-9a-zA-Z]+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def check_latest_agy_log_for_quota_error(max_lines: int = 50) -> Optional[str]:
    """Scan the tail of the latest agy CLI log file for quota errors."""
    try:
        log_dir = os.path.join(get_antigravity_home(), "log")
        if not os.path.isdir(log_dir):
            return None
        files = glob.glob(os.path.join(log_dir, "*.log"))
        if not files:
            return None
        latest_file = max(files, key=os.path.getmtime)
        if time.time() - os.path.getmtime(latest_file) > 120:
            return None
        with open(latest_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            for line in reversed(lines[-max_lines:]):
                if is_quota_error(line):
                    return line.strip()
    except Exception as e:
        log.warning(f"[quota] Failed to check latest agy log: {e}")
    return None


async def detect_and_format_quota_error(raw_text: str = "", model: str = "") -> Optional[str]:
    """Detect if quota is exhausted and format standard Antigravity message.
    Returns: '⚠ Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 1h50m17s.'
    or None if not a quota error.
    """
    text_to_check = raw_text or ""
    quota_detected = is_quota_error(text_to_check)
    extracted_duration = extract_quota_duration_from_text(text_to_check)

    # If duration not found yet, check latest agy log file
    if not extracted_duration:
        log_line = check_latest_agy_log_for_quota_error()
        if log_line:
            quota_detected = True
            extracted_duration = extract_quota_duration_from_text(log_line)

    # If still not detected or no duration, check live quota API
    if not quota_detected:
        try:
            quota_data = await asyncio.get_running_loop().run_in_executor(None, fetch_quota)
            if quota_data and "response" in quota_data and "groups" in quota_data["response"]:
                groups = quota_data["response"].get("groups", [])
                model_lower = (model or "").lower()
                for g in groups:
                    d_name = g.get("displayName", "").lower()
                    desc = g.get("description", "").lower()
                    match_group = False
                    if "gemini" in model_lower and ("gemini" in d_name or "gemini" in desc):
                        match_group = True
                    elif any(k in model_lower for k in ["claude", "gpt", "3p"]) and any(k in d_name or k in desc for k in ["claude", "gpt", "3p"]):
                        match_group = True
                    elif not model_lower:
                        match_group = True
                    if match_group:
                        for b in g.get("buckets", []):
                            if b.get("remainingFraction", 1.0) <= 0.0:
                                quota_detected = True
                                break
        except Exception as e:
            log.warning(f"[quota] API check error: {e}")

    if not quota_detected:
        return None

    # If duration is still missing, calculate it from quota API
    if not extracted_duration:
        try:
            quota_data = await asyncio.get_running_loop().run_in_executor(None, fetch_quota)
            extracted_duration = get_reset_duration_for_model(quota_data, model)
        except Exception as e:
            log.warning(f"[quota] duration calculation error: {e}")

    if extracted_duration:
        return f"⚠ Individual quota reached. Please upgrade your subscription to increase your limits. Resets in {extracted_duration}."
    return "⚠ Individual quota reached. Please upgrade your subscription to increase your limits."

