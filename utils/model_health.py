"""Model capacity circuit breaker, fast micro-probe, and smart fallback selector.

Provides three coordinated protections against server-side HTTP 503 ("No capacity available")
and silent prefill hangs on large resumed conversations:
  1. In-memory TTL circuit breaker (`mark_model_unhealthy` / `mark_model_healthy`).
  2. Lightweight singleflight micro-probe (`probe_model_capacity`) that detects 503
     on a model in ~2-5s by watching both stream-json stdout and the ephemeral probe
     transcript.jsonl for the very first 503 `ERROR_MESSAGE` (instead of waiting 3-5m
     for all 8 internal `agy` retries).
  3. Cross-family healthy fallback selector (`pick_healthy_fallback_model`).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config
from config import ANTIGRAVITY_BIN, find_antigravity_bin, get_transcript_path
from logger import log

DEFAULT_UNHEALTHY_TTL = 180.0  # 3 minutes circuit breaker for 503 capacity errors
DEFAULT_HEALTHY_TTL = 120.0    # 2 minutes fresh healthy cache
DEFAULT_PROBE_CACHE_WINDOW = 20.0  # Reuse probe verdict within 20s

_CAPACITY_ERROR_RE = re.compile(
    r"(?:no capacity available for model|unavailable\s*\(code\s*503\)|experiencing high traffic right now|overloaded_error|model is overloaded)",
    re.IGNORECASE,
)
_TIER_SUFFIX_RE = re.compile(r"-(low|medium|high|xhigh|max)$", re.IGNORECASE)

# { model_id_lower: {"status": "healthy"|"unhealthy", "expires_at": float, "updated_at": float, "reason": str, "latency_ms": float, "model": str} }
_HEALTH_REGISTRY: Dict[str, Dict[str, Any]] = {}
_PROBE_LOCKS: Dict[str, asyncio.Lock] = {}


def _norm(model_id: str) -> str:
    return (model_id or "").strip().lower()


def extract_model_family(model_id: str) -> str:
    """Strip trailing effort suffix (-low/-medium/-high/-xhigh/-max) to get model family."""
    m = _norm(model_id)
    if not m:
        return ""
    return _TIER_SUFFIX_RE.sub("", m)


def extract_model_tier(model_id: str) -> str:
    """Extract embedded effort tier suffix if present (low/medium/high/xhigh/max)."""
    m = _TIER_SUFFIX_RE.search(_norm(model_id))
    return m.group(1).lower() if m else ""


def clear_health_cache() -> None:
    """Clear all in-memory model health records (mainly for unit tests)."""
    _HEALTH_REGISTRY.clear()


def clear_model_health(model_id: str) -> None:
    """Clear health/circuit-breaker record for a single `model_id` (e.g., when user chooses to retry current model)."""
    key = _norm(model_id)
    if key:
        _HEALTH_REGISTRY.pop(key, None)


def mark_model_unhealthy(
    model_id: str,
    reason: str = "503 No capacity",
    ttl_seconds: float = DEFAULT_UNHEALTHY_TTL,
) -> None:
    """Open the circuit breaker for `model_id` for `ttl_seconds`."""
    key = _norm(model_id)
    if not key or key in ("default", "默认"):
        return
    now = time.time()
    _HEALTH_REGISTRY[key] = {
        "model": model_id.strip(),
        "status": "unhealthy",
        "reason": str(reason or "503 No capacity")[:200],
        "latency_ms": 0.0,
        "updated_at": now,
        "expires_at": now + max(10.0, float(ttl_seconds)),
    }
    log.warning(
        f"[ModelHealth] Circuit OPEN for {model_id} (ttl={int(ttl_seconds)}s): {str(reason)[:120]}"
    )


def mark_model_healthy(
    model_id: str,
    latency_ms: float = 0.0,
    ttl_seconds: float = DEFAULT_HEALTHY_TTL,
) -> None:
    """Record `model_id` as healthy and close any previously open circuit breaker."""
    key = _norm(model_id)
    if not key or key in ("default", "默认"):
        return
    now = time.time()
    _HEALTH_REGISTRY[key] = {
        "model": model_id.strip(),
        "status": "healthy",
        "reason": "",
        "latency_ms": round(float(latency_ms or 0.0), 1),
        "updated_at": now,
        "expires_at": now + max(10.0, float(ttl_seconds)),
    }


def get_model_health(model_id: str) -> Dict[str, Any]:
    """Return current health dict for `model_id` (`healthy`, `unhealthy`, or `unknown`)."""
    key = _norm(model_id)
    if not key:
        return {"status": "unknown", "remaining_ttl": 0.0, "reason": "", "latency_ms": 0.0}
    rec = _HEALTH_REGISTRY.get(key)
    now = time.time()
    if not rec or now >= rec.get("expires_at", 0.0):
        if rec:
            _HEALTH_REGISTRY.pop(key, None)
        return {"status": "unknown", "remaining_ttl": 0.0, "reason": "", "latency_ms": 0.0}
    return {
        "model": rec.get("model", model_id),
        "status": rec.get("status", "unknown"),
        "remaining_ttl": max(0.0, round(rec["expires_at"] - now, 1)),
        "age_seconds": max(0.0, round(now - rec.get("updated_at", now), 1)),
        "reason": rec.get("reason", ""),
        "latency_ms": rec.get("latency_ms", 0.0),
    }


def is_model_circuit_open(model_id: str) -> bool:
    """Return True if `model_id` currently has an active (unexpired) 503 circuit breaker."""
    return get_model_health(model_id).get("status") == "unhealthy"


def get_all_health_statuses() -> Dict[str, Dict[str, Any]]:
    """Return all currently active (non-expired) health statuses keyed by lowercase model_id."""
    now = time.time()
    active: Dict[str, Dict[str, Any]] = {}
    expired_keys = []
    for k, rec in list(_HEALTH_REGISTRY.items()):
        if now >= rec.get("expires_at", 0.0):
            expired_keys.append(k)
        else:
            active[k] = {
                "model": rec.get("model", k),
                "status": rec.get("status", "unknown"),
                "remaining_ttl": max(0.0, round(rec["expires_at"] - now, 1)),
                "reason": rec.get("reason", ""),
                "latency_ms": rec.get("latency_ms", 0.0),
            }
    for k in expired_keys:
        _HEALTH_REGISTRY.pop(k, None)
    return active


def _load_cached_model_entries() -> List[str]:
    """Read raw model list from `.model_cache.json` without blocking on subprocess."""
    cache_path = os.path.join(config.BASE_DIR, ".model_cache.json")
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            models = data.get("models")
            if isinstance(models, list) and models:
                return [str(m) for m in models if m]
    except Exception:
        pass
    try:
        from utils.model_tiers import FALLBACK_MODELS_CATALOG
        return [str(m) for m in FALLBACK_MODELS_CATALOG if m]
    except Exception:
        return []


def _parse_model_id(entry: str) -> str:
    s = (entry or "").strip()
    if not s:
        return ""
    if "\t" in s:
        return s.split("\t", 1)[0].strip()
    parts = s.split(maxsplit=1)
    return parts[0].strip() if parts else ""


def _parse_model_label(entry: str) -> Tuple[str, str]:
    s = (entry or "").strip()
    if not s:
        return "", ""
    if "\t" in s:
        m_id, m_label = s.split("\t", 1)
        return m_id.strip(), (m_label.strip() or m_id.strip())
    parts = s.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return s, s


def _family_priority(family: str) -> int:
    """Lower number = higher priority when choosing a cross-family fallback model."""
    f = (family or "").lower()
    order = [
        "gemini-4-neon",
        "claude-sonnet",
        "claude-opus",
        "gemini-4-argon",
        "gemini-4-hydrogen",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gpt-oss",
    ]
    for idx, prefix in enumerate(order):
        if f.startswith(prefix):
            return idx
    return len(order) + 1


def _tier_distance(candidate_tier: str, target_tier: str) -> int:
    ranks = {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 4}
    c_r = ranks.get((candidate_tier or "").lower(), 2)
    t_r = ranks.get((target_tier or "").lower(), 3)
    return abs(c_r - t_r)


def pick_healthy_fallback_model(
    failed_model: str,
    available_models: Optional[Iterable[str]] = None,
    preferred_tier: str = "",
    exclude_models: Optional[Set[str]] = None,
) -> Optional[str]:
    """Select the best cross-family non-congested fallback model for `failed_model`.

    Selection priority:
      1. Never selects `failed_model`, any model in `exclude_models`, or any model whose
         circuit breaker is open (`is_model_circuit_open`).
      2. Strongly prefers a DIFFERENT model family from `failed_model` (so an overloaded
         Argon/Hydrogen cluster falls back to Neon/Claude rather than another Argon tier).
      3. Prefers models verified `healthy` first, then `unknown`.
      4. Prefers flagship families (`gemini-4-neon` > `claude-sonnet` > `claude-opus` > ...)
         and closest effort tier match.
    """
    raw_entries = list(available_models) if available_models is not None else _load_cached_model_entries()
    if not raw_entries:
        return None

    failed_norm = _norm(failed_model)
    failed_family = extract_model_family(failed_model)
    target_tier = (preferred_tier or extract_model_tier(failed_model) or "high").lower()
    excluded = {_norm(m) for m in (exclude_models or set()) if m}
    if failed_norm:
        excluded.add(failed_norm)

    candidates: List[Tuple[Tuple[int, int, int, int], str]] = []
    seen: Set[str] = set()
    for entry in raw_entries:
        m_id = _parse_model_id(entry)
        m_norm = _norm(m_id)
        if not m_id or m_norm in seen or m_norm in excluded:
            continue
        seen.add(m_norm)
        # Skip orchestrator meta-models for regular fallback
        if "orchestrator" in m_norm:
            continue
        if is_model_circuit_open(m_id):
            continue

        fam = extract_model_family(m_id)
        tier = extract_model_tier(m_id)
        health_status = get_model_health(m_id).get("status", "unknown")

        same_family_penalty = 1 if (failed_family and fam == failed_family) else 0
        # Healthy models get priority 0, unknown models priority 1
        health_rank = 0 if health_status == "healthy" else 1
        fam_rank = _family_priority(fam)
        tier_dist = _tier_distance(tier, target_tier)

        candidates.append(((same_family_penalty, health_rank, fam_rank, tier_dist), m_id))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    # Never fall back to the exact same family if cross-family exists (already sorted first);
    # even if only same-family exists, avoid returning same-family when it's likely the same backend pool.
    best_key, best_model = candidates[0]
    if best_key[0] == 1:
        return None
    return best_model


def list_healthy_candidate_models(
    failed_model: str,
    available_models: Optional[Iterable[str]] = None,
    preferred_tier: str = "",
    limit: int = 8,
) -> List[Dict[str, str]]:
    """Return a ranked list of candidate models (`id`, `label`, `health`, `family`) for the interactive 503 choice card."""
    raw_entries = list(available_models) if available_models is not None else _load_cached_model_entries()
    if not raw_entries:
        return []

    failed_norm = _norm(failed_model)
    failed_family = extract_model_family(failed_model)
    target_tier = (preferred_tier or extract_model_tier(failed_model) or "high").lower()

    scored: List[Tuple[Tuple[int, int, int, int], Dict[str, str]]] = []
    seen: Set[str] = set()
    for entry in raw_entries:
        m_id, m_label = _parse_model_label(entry)
        m_norm = _norm(m_id)
        if not m_id or m_norm in seen or m_norm == failed_norm:
            continue
        seen.add(m_norm)
        if "orchestrator" in m_norm:
            continue

        health_status = get_model_health(m_id).get("status", "unknown")
        is_unhealthy = health_status == "unhealthy"
        fam = extract_model_family(m_id)
        tier = extract_model_tier(m_id)

        unhealthy_penalty = 2 if is_unhealthy else 0
        same_family_penalty = 1 if (failed_family and fam == failed_family) else 0
        health_rank = 0 if health_status == "healthy" else (2 if is_unhealthy else 1)
        fam_rank = _family_priority(fam)
        tier_dist = _tier_distance(tier, target_tier)

        scored.append((
            (unhealthy_penalty + same_family_penalty, health_rank, fam_rank, tier_dist),
            {
                "id": m_id,
                "label": m_label or m_id,
                "health": health_status,
                "family": fam,
            },
        ))

    scored.sort(key=lambda item: item[0])
    return [item[1] for item in scored[: max(1, int(limit))]]


async def _kill_process_group(proc: Any) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception:
        pass


def _scan_transcript_for_probe_503(transcript_path: str) -> Optional[str]:
    """Return the first 503 capacity error text found in an ephemeral probe transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except Exception:
                    continue
                if obj.get("type") == "ERROR_MESSAGE":
                    err_txt = str(obj.get("error") or obj.get("content") or "").strip()
                    if err_txt and _CAPACITY_ERROR_RE.search(err_txt):
                        return err_txt
    except Exception:
        pass
    return None


def _scan_text_file_for_503(file_path: str) -> Optional[str]:
    """Return the first 503 capacity error line found in a probe CLI log file."""
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                if _CAPACITY_ERROR_RE.search(raw_line):
                    return raw_line.strip()[-260:]
    except Exception:
        pass
    return None


async def probe_model_capacity(
    model_id: str,
    timeout_seconds: float = 18.0,
    force: bool = False,
) -> Dict[str, Any]:
    """Run a lightweight singleflight capacity probe against `model_id`.

    Detects server-side 503 No capacity in ~5-8s by monitoring `stream-json` stdout,
    the probe's dedicated `--log-file`, and the ephemeral `transcript.jsonl` for the
    very first 503 `ERROR_MESSAGE`, terminating immediately once a verdict is known.

    Returns dict:
        {"status": "healthy"|"unhealthy"|"timeout"|"error", "model": str, "latency_ms": float, "error": str}
    """
    m_id = (model_id or "").strip()
    m_key = _norm(m_id)
    if not m_id or m_key in ("default", "默认"):
        return {"status": "unknown", "model": m_id, "latency_ms": 0.0, "error": ""}

    if not force:
        cached = get_model_health(m_id)
        if cached.get("status") in ("healthy", "unhealthy") and cached.get("age_seconds", 999.0) < DEFAULT_PROBE_CACHE_WINDOW:
            return {
                "status": cached["status"],
                "model": m_id,
                "latency_ms": cached.get("latency_ms", 0.0),
                "error": cached.get("reason", ""),
                "cached": True,
            }

    lock = _PROBE_LOCKS.setdefault(m_key, asyncio.Lock())
    async with lock:
        # Re-check cache after acquiring singleflight lock
        if not force:
            cached = get_model_health(m_id)
            if cached.get("status") in ("healthy", "unhealthy") and cached.get("age_seconds", 999.0) < DEFAULT_PROBE_CACHE_WINDOW:
                return {
                    "status": cached["status"],
                    "model": m_id,
                    "latency_ms": cached.get("latency_ms", 0.0),
                    "error": cached.get("reason", ""),
                    "cached": True,
                }

        bin_path = ANTIGRAVITY_BIN or find_antigravity_bin()
        if not bin_path:
            return {"status": "error", "model": m_id, "latency_ms": 0.0, "error": "agy binary not found"}

        start_ts = time.time()
        probe_log_fd, probe_log_path = tempfile.mkstemp(prefix="agy_probe_", suffix=".log")
        os.close(probe_log_fd)
        cmd_args = [
            bin_path,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", m_id,
            "--print-timeout", "30s",
            "--disable-slash-commands",
            "--log-file", probe_log_path,
        ]
        probe_env = os.environ.copy()
        probe_env["PYTHONUNBUFFERED"] = "1"
        probe_env["STDOUT_LINE_BUFFERED"] = "1"

        proc = None
        stderr_task = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
                cwd=tempfile.gettempdir(),
                env=probe_env,
            )

            async def _drain_stderr():
                try:
                    while proc.stderr:
                        chunk = await proc.stderr.read(4096)
                        if not chunk:
                            break
                except Exception:
                    pass

            stderr_task = asyncio.create_task(_drain_stderr())

            payload = json.dumps({"event": "user", "message": {"content": "Reply OK only."}}) + "\n"
            proc.stdin.write(payload.encode("utf-8"))
            await proc.stdin.drain()

            probe_tx_path = ""
            deadline = start_ts + max(4.0, float(timeout_seconds))
            buf = ""

            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    elapsed_ms = (time.time() - start_ts) * 1000.0
                    # Final scan before returning timeout
                    err_503 = _scan_text_file_for_503(probe_log_path) or _scan_transcript_for_probe_503(probe_tx_path)
                    if err_503:
                        mark_model_unhealthy(m_id, reason=err_503)
                        return {"status": "unhealthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": err_503}
                    log.info(f"[ModelHealth] Probe timed out for {m_id} after {elapsed_ms:.0f}ms")
                    return {"status": "timeout", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": "probe timeout"}

                # Check CLI log + ephemeral transcript for immediate Attempt-1 503
                err_503 = _scan_text_file_for_503(probe_log_path) or (
                    _scan_transcript_for_probe_503(probe_tx_path) if probe_tx_path else None
                )
                if err_503:
                    elapsed_ms = (time.time() - start_ts) * 1000.0
                    mark_model_unhealthy(m_id, reason=err_503)
                    return {"status": "unhealthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": err_503}

                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=min(0.4, remaining))
                except asyncio.TimeoutError:
                    continue

                if not chunk:
                    err_503 = _scan_text_file_for_503(probe_log_path) or (
                        _scan_transcript_for_probe_503(probe_tx_path) if probe_tx_path else None
                    )
                    if err_503:
                        elapsed_ms = (time.time() - start_ts) * 1000.0
                        mark_model_unhealthy(m_id, reason=err_503)
                        return {"status": "unhealthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": err_503}
                    elapsed_ms = (time.time() - start_ts) * 1000.0
                    return {"status": "error", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": "probe process exited without output"}

                buf += chunk.decode("utf-8", errors="ignore")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        if _CAPACITY_ERROR_RE.search(line):
                            elapsed_ms = (time.time() - start_ts) * 1000.0
                            mark_model_unhealthy(m_id, reason=line)
                            return {"status": "unhealthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": line}
                        continue

                    ev_type = ev.get("event")
                    if ev_type == "init":
                        cid = ev.get("conversation_id") or ""
                        if cid:
                            probe_tx_path = get_transcript_path(cid)
                    elif ev_type == "step_update":
                        step_up = ev.get("step_update") or {}
                        if step_up.get("step_type") == "agent_response" and step_up.get("text_delta"):
                            elapsed_ms = (time.time() - start_ts) * 1000.0
                            mark_model_healthy(m_id, latency_ms=elapsed_ms)
                            log.info(f"[ModelHealth] Probe HEALTHY for {m_id} in {elapsed_ms:.0f}ms")
                            return {"status": "healthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": ""}
                    elif ev_type == "result":
                        res_obj = ev.get("result") or {}
                        if res_obj.get("status") == "SUCCESS":
                            elapsed_ms = (time.time() - start_ts) * 1000.0
                            mark_model_healthy(m_id, latency_ms=elapsed_ms)
                            log.info(f"[ModelHealth] Probe HEALTHY (result) for {m_id} in {elapsed_ms:.0f}ms")
                            return {"status": "healthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": ""}
                        err_txt = str(res_obj.get("error") or res_obj.get("message") or "")
                        elapsed_ms = (time.time() - start_ts) * 1000.0
                        if _CAPACITY_ERROR_RE.search(err_txt):
                            mark_model_unhealthy(m_id, reason=err_txt)
                            return {"status": "unhealthy", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": err_txt}
                        return {"status": "error", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": err_txt}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed_ms = (time.time() - start_ts) * 1000.0
            log.warning(f"[ModelHealth] Probe exception for {m_id}: {e}")
            return {"status": "error", "model": m_id, "latency_ms": round(elapsed_ms, 1), "error": str(e)}
        finally:
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            await _kill_process_group(proc)
            try:
                if os.path.exists(probe_log_path):
                    os.remove(probe_log_path)
            except Exception:
                pass
