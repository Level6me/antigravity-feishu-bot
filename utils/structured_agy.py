"""Structured JSON output helper backed by `agy 1.5.x` CLI.

Leverages official `--output-format json --json-schema <schema> --disable-slash-commands`
for deterministic, schema-validated extraction (intent classification, memory summarization,
status distillation) without fragile regex parsing.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Dict, Optional

import config
from logger import log


def _extract_json_payload(raw_stdout: str) -> Optional[Dict[str, Any]]:
    """Extract structured JSON object from `agy -p --output-format json` stdout."""
    text = (raw_stdout or "").strip()
    if not text:
        return None

    # 1. Whole stdout may be a JSON envelope from `agy --output-format json`
    try:
        envelope = json.loads(text)
    except Exception:
        envelope = None

    if isinstance(envelope, dict):
        # agy json envelope commonly wraps response text under "response" / "result" / "output"
        for key in ("structured_output", "result", "response", "output", "text"):
            val = envelope.get(key)
            if isinstance(val, dict):
                if key == "result" and isinstance(val.get("response"), str):
                    inner = _extract_json_payload(val["response"])
                    if inner is not None:
                        return inner
                elif key != "result":
                    return val
                elif "status" not in val:
                    return val
            elif isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val.strip())
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        # If the envelope itself looks like the user's schema object (no CLI metadata wrapper)
        cli_meta_keys = {"conversation_id", "session_id", "status", "usage", "stats", "error"}
        if not cli_meta_keys.intersection(envelope.keys()):
            return envelope

    # 2. Fallback: find last valid JSON object block in output
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        sub = text[start : end + 1]
        if sub != text:
            return _extract_json_payload(sub)
        try:
            parsed = json.loads(sub)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


async def run_structured_prompt(
    prompt: str,
    json_schema: Dict[str, Any],
    *,
    model: Optional[str] = None,
    timeout_seconds: float = 30.0,
    cwd: Optional[str] = None,
    effort: str = "low",
) -> Optional[Dict[str, Any]]:
    """Run a single lightweight non-interactive structured-output call via `agy`.

    Uses `--disable-slash-commands`, `--output-format json`, and `--json-schema`
    so the CLI enforces valid JSON conforming to `json_schema`.
    """
    bin_path = config.ANTIGRAVITY_BIN or config.find_antigravity_bin()
    if not bin_path:
        log.warning("[StructuredAgy] agy binary not found.")
        return None

    target_model = (model or config.DEFAULT_MODEL or "gemini-3.8-flash-low").strip()
    schema_str = json.dumps(json_schema, ensure_ascii=False)

    # Use a temporary schema file if schema string is large to avoid argv length issues
    schema_arg = schema_str
    tmp_schema_file = None
    if len(schema_str) > 2048:
        fd, tmp_schema_file = tempfile.mkstemp(prefix="agy_schema_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(schema_str)
        schema_arg = tmp_schema_file

    cmd = [
        bin_path,
        "-p", prompt,
        "--model", target_model,
        "--output-format", "json",
        "--json-schema", schema_arg,
        "--disable-slash-commands",
        "--mode", "plan",
        "--print-timeout", f"{max(5, int(timeout_seconds))}s",
    ]
    from utils.model_tiers import has_embedded_effort_suffix
    if effort and not has_embedded_effort_suffix(target_model):
        cmd.extend(["--effort", effort])

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONUNBUFFERED"] = "1"

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd if (cwd and os.path.isdir(cwd)) else None,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds + 5.0)
        stdout_str = (stdout_b or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err_snip = (stderr_b or b"").decode("utf-8", errors="replace").strip()[:240]
            log.warning(f"[StructuredAgy] Non-zero exit ({proc.returncode}): {err_snip}")
            return None
        return _extract_json_payload(stdout_str)
    except asyncio.TimeoutError:
        log.warning(f"[StructuredAgy] Timed out after {timeout_seconds}s (model={target_model})")
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        return None
    except Exception as e:
        log.warning(f"[StructuredAgy] Execution failed: {e}")
        return None
    finally:
        if tmp_schema_file and os.path.exists(tmp_schema_file):
            try:
                os.remove(tmp_schema_file)
            except Exception:
                pass
