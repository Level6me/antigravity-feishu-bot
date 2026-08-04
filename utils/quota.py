"""Quota fetching utilities (cross-platform).

Port discovery order:
    1. ANTIGRAVITY_LSP_PORT env var
    2. heartbeat file <ANTIGRAVITY_HOME>/lsp_port
    3. Linux: /proc scan for the agy process listen port
    4. macOS/Linux: lsof LISTEN scan filtered by agy process

TLS is verified by default. To allow self-signed local LSP certs set
ANTIGRAVITY_QUOTA_INSECURE=true (Google API still verifies certs).
"""

import json
import os
import re
import ssl
import subprocess
import time
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
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=5) as response:
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
