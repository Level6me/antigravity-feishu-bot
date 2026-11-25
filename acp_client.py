"""Agent Client Protocol (ACP) client for Antigravity CLI (`agy 1.5.x`).

Provides a long-lived JSON-RPC 2.0 stdio transport (`agy acp --stdio`) that unlocks:
  - In-place dynamic model & thinking-level switching (`session/set_model`, `session/set_config_option`)
    without killing the process or terminating running subagents/background tasks.
  - Native execution mode switching (`session/set_mode`: default | accept-edits | plan).
  - Native multimodal prompt blocks (inline base64 images) without an extra read_file round-trip.
  - Untruncated structured tool_call / tool_call_update events (eliminating stream-json 512-char ellipsis truncation).
  - Mid-turn steering (`steer()`) to redirect a running turn without `/stop`.
  - Interactive permission / `ask_question` RPC callback bridge to Feishu interactive cards.
  - Background subagent / task wake-up notifications outside active turns.
"""
from __future__ import annotations

import asyncio
import base64
import codecs
import json
import mimetypes
import os
import re
import signal
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import config
from config import ANTIGRAVITY_BIN, DANGEROUSLY_SKIP_PERMISSIONS, find_antigravity_bin
from logger import log
from math_decision_model import snapshot_process_group_pids
from utils.auth import is_admin
from utils.model_tiers import has_embedded_effort_suffix
import app_state

# Maximum inline image attachment limits for ACP prompt blocks
_MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_INLINE_IMAGES_PER_TURN = 6
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Regex to locate downloaded Feishu image paths referenced inside prompt text
_DOWNLOADED_IMG_PATH_RE = re.compile(
    r"(?:图片路径(?:\s*#\d+)?\s*[:：]\s*|文件路径\s*[:：]\s*`?)"
    r"(/[^\s\]`\"'<>]+?\.(?:jpg|jpeg|png|webp|gif|bmp))",
    re.IGNORECASE,
)

# Pending interactive permission requests keyed by unique token
# Each entry: {"future": asyncio.Future, "chat_id": str, "options": list, "created_at": float}
_PENDING_PERMISSIONS: Dict[str, Dict[str, Any]] = {}


def normalize_acp_mode(mode: Optional[str]) -> str:
    """Map bot execution mode names to valid ACP modeIds (`default`, `accept-edits`, `plan`)."""
    m = (mode or "default").strip().lower()
    if m in ("plan", "readonly", "read-only"):
        return "plan"
    if m in ("accept-edits", "accept_edits", "edits"):
        return "accept-edits"
    # Both "auto" and "default" map to ACP "default" mode
    return "default"


def resolve_pending_permission(perm_token: str, chat_id: str, option_id: str) -> Tuple[bool, str]:
    """Resolve an awaiting ACP permission request from a Feishu card button click."""
    entry = _PENDING_PERMISSIONS.get(perm_token)
    if not entry:
        return False, "该权限审批请求已过期或已处理。"
    if entry.get("chat_id") != chat_id:
        return False, "无权操作其他会话的权限审批卡片。"
    fut: asyncio.Future = entry["future"]
    if fut.done():
        _PENDING_PERMISSIONS.pop(perm_token, None)
        return False, "该权限审批已完成。"
    fut.set_result(option_id)
    _PENDING_PERMISSIONS.pop(perm_token, None)
    return True, "已提交审批决定。"


def build_acp_prompt_blocks(prompt_text: str, extra_image_paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Build ACP content blocks: primary text block + native inline image blocks."""
    blocks: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]

    candidate_paths: List[str] = []
    if extra_image_paths:
        candidate_paths.extend(extra_image_paths)
    for m in _DOWNLOADED_IMG_PATH_RE.finditer(prompt_text or ""):
        candidate_paths.append(m.group(1))

    seen = set()
    attached = 0
    downloads_root = os.path.realpath(os.path.abspath(os.path.join(config.BASE_DIR, "downloads")))
    real_tmp_root = os.path.realpath("/tmp")
    for raw_p in candidate_paths:
        if attached >= _MAX_INLINE_IMAGES_PER_TURN:
            break
        if not raw_p:
            continue
        abs_p = os.path.abspath(raw_p)
        real_p = os.path.realpath(abs_p)
        if real_p in seen or not os.path.isfile(real_p):
            continue
        # Security boundary: resolve symlinks so /tmp/link -> /outside/secret.png cannot bypass directory check
        if not (
            real_p.startswith(downloads_root + os.sep)
            or real_p.startswith(real_tmp_root + os.sep)
            or real_p.startswith("/tmp/")
        ):
            continue
        ext = os.path.splitext(real_p)[1].lower()
        if ext not in _IMAGE_EXTS:
            continue
        try:
            fsize = os.path.getsize(real_p)
            if fsize <= 0 or fsize > _MAX_INLINE_IMAGE_BYTES:
                continue
            with open(real_p, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("ascii")
            mime_type, _ = mimetypes.guess_type(real_p)
            if not mime_type or not mime_type.startswith("image/"):
                mime_type = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
            blocks.append({
                "type": "image",
                "data": b64_data,
                "mimeType": mime_type,
            })
            seen.add(real_p)
            attached += 1
        except Exception as e:
            log.debug(f"[ACP] Skipping inline image {real_p}: {e}")

    return blocks


def _map_acp_tool_state(status: str) -> str:
    st = (status or "").strip().lower()
    if st in ("completed", "done", "success", "succeeded"):
        return "DONE"
    if st in ("failed", "error", "denied", "cancelled"):
        return "ERROR"
    return "RUNNING"


_ACP_KIND_TO_TOOL_NAME = {
    "execute": "run_command",
    "read": "read_file",
    "edit": "edit_file",
    "delete": "edit_file",
    "move": "edit_file",
    "search": "grep_search",
    "fetch": "read_url_content",
}

_KNOWN_TOOL_NAMES = (
    "run_command",
    "read_file",
    "view_file",
    "edit_file",
    "replace_file_content",
    "write_file",
    "write_to_file",
    "grep_search",
    "find_by_name",
    "list_dir",
    "search_web",
    "read_url_content",
    "invoke_subagent",
    "manage_subagents",
    "define_subagent",
    "send_message",
    "manage_task",
    "run_workflow",
    "swarm",
    "schedule",
    "ask_question",
    "grep",
    "glob",
)


def _extract_acp_params(raw_input: Any, args: Any) -> Dict[str, Any]:
    if isinstance(raw_input, dict):
        return dict(raw_input)
    if isinstance(raw_input, str) and raw_input.strip().startswith("{"):
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    if isinstance(args, dict):
        return dict(args)
    return {}


def _infer_acp_tool_name(raw_name: str, title: str, kind: str, params: Dict[str, Any]) -> str:
    name = (raw_name or "").strip()
    if name:
        return name

    # 1. Infer from structured rawInput / args keys
    if isinstance(params, dict) and params:
        if any(k in params for k in ("CommandLine", "command", "cmd")):
            return "run_command"
        if any(k in params for k in ("Target", "Replacement", "TargetContent", "ReplacementContent")):
            return "edit_file"
        if any(k in params for k in ("Content", "CodeContent")) and any(
            k in params for k in ("Path", "path", "TargetFile")
        ):
            return "write_file"
        if "questions" in params:
            return "ask_question"
        if "CronExpression" in params or "DurationSeconds" in params:
            return "schedule"
        if "TypeName" in params and "Prompt" in params:
            return "invoke_subagent"
        if any(k in params for k in ("Query", "query")):
            title_l = (title or "").lower()
            if (kind or "").lower() == "fetch" or "web" in title_l or "search_web" in title_l:
                return "search_web"
            return "grep_search"
        if any(k in params for k in ("Pattern", "pattern")):
            return "find_by_name"
        if any(k in params for k in ("Path", "path", "AbsolutePath")):
            if (kind or "").lower() == "edit":
                return "edit_file"
            return "read_file"

    # 2. Match known canonical tool name embedded in ACP title
    title_l = (title or "").strip().lower()
    if title_l:
        for known in _KNOWN_TOOL_NAMES:
            if title_l == known or title_l.startswith(known + " ") or title_l.startswith(known + ":"):
                return known

    # 3. Fallback to ACP standard ToolKind mapping
    mapped = _ACP_KIND_TO_TOOL_NAME.get((kind or "").strip().lower(), "")
    if mapped:
        return mapped
    return ""


_BG_TASK_STARTED_RE = re.compile(
    r'Tool is running as a background task with task id:\s*["\']?([^\s\n"\'<>]+)',
    re.IGNORECASE,
)
_BG_TASK_FINISHED_RE = re.compile(
    r'Task id\s*["\']([^"\']+)["\']\s+(?:finished with result|completed|failed|cancelled)',
    re.IGNORECASE,
)


def _collect_acp_text_fragments(value: Any) -> List[str]:
    """Recursively collect plain text strings from ACP rawOutput / content structures."""
    out: List[str] = []
    if isinstance(value, str):
        if value:
            out.append(value)
    elif isinstance(value, dict):
        for k in ("text", "output", "stdout", "stderr", "message", "content"):
            if k in value and value[k] is not None:
                out.extend(_collect_acp_text_fragments(value[k]))
    elif isinstance(value, list):
        for item in value:
            out.extend(_collect_acp_text_fragments(item))
    return out


def _extract_bg_task_transitions(update: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Extract (started_task_ids, finished_task_ids) from an ACP tool_call_update payload."""
    if not isinstance(update, dict):
        return [], []
    fragments: List[str] = []
    for key in ("rawOutput", "content", "output"):
        if key in update and update[key] is not None:
            fragments.extend(_collect_acp_text_fragments(update[key]))
    if not fragments:
        return [], []
    joined = "\n".join(fragments)
    started = [m.group(1).strip() for m in _BG_TASK_STARTED_RE.finditer(joined) if m.group(1).strip()]
    finished = [m.group(1).strip() for m in _BG_TASK_FINISHED_RE.finditer(joined) if m.group(1).strip()]
    return started, finished


def _acp_update_to_stream_event(
    update: Dict[str, Any],
    tool_call_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Convert an ACP `session/update` payload into the bot's unified stream event schema."""
    if not isinstance(update, dict):
        return None
    u_type = update.get("sessionUpdate") or ""

    if u_type == "agent_message_chunk":
        content = update.get("content") or {}
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text", "")
            if text:
                return {"event": "message", "role": "assistant", "content": text}
        return None

    if u_type == "agent_thought_chunk":
        content = update.get("content") or {}
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            return {"event": "thought", "content": text}
        return None

    if u_type in ("tool_call", "tool_call_update"):
        call_id = str(update.get("toolCallId") or "")
        cached = (tool_call_cache.get(call_id) or {}) if (tool_call_cache is not None and call_id) else {}

        raw_name = update.get("name") or cached.get("tool_name") or ""
        title = update.get("title") or cached.get("action") or ""
        kind = update.get("kind") or cached.get("kind") or ""

        params = dict(cached.get("parameters") or {})
        new_params = _extract_acp_params(update.get("rawInput"), update.get("args"))
        if new_params:
            params.update(new_params)

        locations = update.get("locations") or cached.get("locations") or []
        if isinstance(locations, list) and locations and not any(
            params.get(k) for k in ("Path", "path", "TargetFile", "AbsolutePath")
        ):
            first_loc = locations[0]
            if isinstance(first_loc, dict) and first_loc.get("path"):
                params["Path"] = first_loc["path"]

        tool_name = _infer_acp_tool_name(raw_name, title, kind, params)
        action_title = title or tool_name
        raw_status = update.get("status")
        if raw_status:
            mapped_state = _map_acp_tool_state(raw_status)
        else:
            mapped_state = cached.get("state") or "RUNNING"

        if tool_call_cache is not None and call_id:
            if mapped_state in ("DONE", "ERROR"):
                tool_call_cache.pop(call_id, None)
            else:
                tool_call_cache[call_id] = {
                    "tool_name": tool_name,
                    "action": action_title,
                    "kind": kind,
                    "parameters": params,
                    "locations": locations,
                    "state": mapped_state,
                }

        return {
            "event": "step_update",
            "step_update": {
                "step_type": "tool",
                "tool_name": tool_name,
                "parameters": params,
                "tool_info": {"parameters": params},
                "action": action_title,
                "state": mapped_state,
                "call_id": call_id,
                "kind": kind,
            },
        }

    if u_type == "plan":
        entries = update.get("entries") or []
        if isinstance(entries, list) and entries:
            return {"event": "plan_update", "entries": entries}
        return None

    if u_type == "usage_update":
        return {
            "event": "usage",
            "used": update.get("used", 0),
            "size": update.get("size", 0),
        }

    if u_type == "current_mode_update":
        return {
            "event": "mode_update",
            "mode": update.get("currentModeId", ""),
        }

    return None


class AcpTurnEventStream:
    """Drop-in compatible counterpart of `TurnEventStream` backed by ACP JSON-RPC."""

    _TERMINAL_EVENTS = {"result", "error", "turn_error", "cancelled", "fatal", "process_exit", "read_error"}

    def __init__(self, queue: asyncio.Queue, cleanup_cb: Optional[Callable[[], None]] = None):
        self._queue = queue
        self._cleanup_cb = cleanup_cb
        self.is_done = False

    async def get_event(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if self.is_done:
            return None
        try:
            if timeout is not None:
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                event = await self._queue.get()
            if event.get("event") in self._TERMINAL_EVENTS or event.get("type") in self._TERMINAL_EVENTS:
                self.is_done = True
            return event
        except asyncio.TimeoutError:
            return None

    def drain_all(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        while not self.is_done and not self._queue.empty():
            try:
                ev = self._queue.get_nowait()
                events.append(ev)
                if ev.get("event") in self._TERMINAL_EVENTS or ev.get("type") in self._TERMINAL_EVENTS:
                    self.is_done = True
                    break
            except asyncio.QueueEmpty:
                break
        return events

    def resume_for_background_tasks(self):
        """Keep consuming `session/update` events after `end_turn` while background tasks finish."""
        self.is_done = False

    async def aclose(self):
        self.is_done = True
        if self._cleanup_cb:
            try:
                self._cleanup_cb()
            except Exception:
                pass


class AcpPersistentSession:
    """Long-lived ACP stdio session compatible with SessionPool & executor.py."""

    transport_name = "acp"

    def __init__(
        self,
        chat_id: str,
        model: str,
        project_dir: Optional[str] = None,
        conversation_id: str = "",
        effort: str = "medium",
        sandbox_enabled: bool = False,
        agent_mode: str = "default",
        extra_dirs: Optional[List[str]] = None,
        permission_notifier: Optional[Callable[[str, str, Dict[str, Any]], Awaitable[bool]]] = None,
        async_update_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        owner_chat_id: Optional[str] = None,
    ):
        self.chat_id = chat_id
        self.owner_chat_id = owner_chat_id or chat_id
        self.model = model
        self.effort = effort or "medium"
        self.sandbox_enabled = sandbox_enabled
        self.agent_mode = normalize_acp_mode(agent_mode)
        self.project_dir = project_dir
        self.extra_dirs: List[str] = [os.path.abspath(d) for d in (extra_dirs or []) if d and os.path.isdir(d)]
        self.conversation_id: str = conversation_id
        self.process: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self.spawn_time = time.time()
        self.last_active_time = time.time()
        self._closing = False
        self._recent_stderr_buf = ""
        self._stderr_drain_task: Optional[asyncio.Task] = None
        self._stdout_reader_task: Optional[asyncio.Task] = None
        self._next_rpc_id = 1
        self._pending_rpc: Dict[int, asyncio.Future] = {}
        self._active_turn_queue: Optional[asyncio.Queue] = None
        self._active_prompt_rpc_id: Optional[int] = None
        self._active_turn_text_chunks: List[str] = []
        self._active_turn_tool_calls: Dict[str, Dict[str, Any]] = {}
        self._active_turn_pending_bg_tasks: set = set()
        self._queued_steer_rpc_ids: Dict[int, str] = {}
        self._steer_turn_text_chunks: List[str] = []
        self._idle_text_chunks: List[str] = []
        self._idle_flush_task: Optional[asyncio.Task] = None
        self.agent_capabilities: Dict[str, Any] = {}
        self.available_models: List[Dict[str, Any]] = []
        self.last_usage: Dict[str, Any] = {}
        self._permission_notifier = permission_notifier
        self._async_update_callback = async_update_callback
        self._in_turn = False

    def get_recent_stderr(self) -> str:
        return self._recent_stderr_buf

    def clear_recent_stderr(self):
        self._recent_stderr_buf = ""

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def is_dead_or_closing(self) -> bool:
        return self._closing or (self.process is not None and self.process.returncode is not None)

    def is_turn_running(self) -> bool:
        return self._in_turn and self.is_alive()

    async def start(self):
        async with self._start_lock:
            if self._closing or self.is_alive():
                return
            await self._start_process()

    async def _start_process(self):
        if self._closing or self.is_alive():
            return

        bin_path = ANTIGRAVITY_BIN or find_antigravity_bin()
        if not bin_path:
            raise FileNotFoundError("Antigravity / agy binary not found.")

        cmd_args = [bin_path, "acp", "--stdio"]
        if getattr(config, "ACP_PROCESS_PER_SESSION", True):
            cmd_args.append("--process-per-session")
        if self.sandbox_enabled:
            cmd_args.append("--sandbox")

        is_admin_chat = is_admin(self.owner_chat_id)
        if DANGEROUSLY_SKIP_PERMISSIONS and is_admin_chat:
            cmd_args.append("--dangerously-skip-permissions")

        cwd_dir = None
        all_add_dirs: List[str] = []
        if self.project_dir and os.path.isdir(self.project_dir):
            cwd_dir = self.project_dir
            all_add_dirs.append(os.path.abspath(self.project_dir))
        for d in self.extra_dirs:
            if d not in all_add_dirs and os.path.isdir(d):
                all_add_dirs.append(d)
        for d in all_add_dirs:
            cmd_args.extend(["--add-dir", d])

        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        custom_env["PYTHONUNBUFFERED"] = "1"
        custom_env["STDOUT_LINE_BUFFERED"] = "1"

        log.info(
            f"[ACP] Spawning agy acp for chat {self.chat_id} "
            f"(model={self.model}, mode={self.agent_mode}, sandbox={self.sandbox_enabled}, cwd={cwd_dir}, extra_dirs={len(self.extra_dirs)})"
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
            cwd=cwd_dir,
            env=custom_env,
        )
        if self._closing:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return

        self.process = proc
        self.spawn_time = time.time()
        self.last_active_time = time.time()
        if hasattr(app_state, "register_subprocess"):
            app_state.register_subprocess(self.chat_id, self.process)
        else:
            app_state.running_processes[self.chat_id] = self.process

        self._stderr_drain_task = asyncio.create_task(self._drain_stderr())
        self._stdout_reader_task = asyncio.create_task(self._read_stdout_loop())

        try:
            await self._initialize_and_open_session(cwd_dir or os.getcwd())
        except Exception:
            await self._terminate_process_only()
            raise

    async def _initialize_and_open_session(self, cwd_dir: str):
        init_res = await self._rpc_call(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "antigravity-feishu-bot", "version": "1.5"},
            },
            timeout=20.0,
        )
        self.agent_capabilities = (init_res or {}).get("agentCapabilities") or {}

        opened = None
        if self.conversation_id:
            try:
                opened = await self._rpc_call(
                    "session/resume",
                    {
                        "sessionId": self.conversation_id,
                        "cwd": cwd_dir,
                        "mcpServers": [],
                    },
                    timeout=20.0,
                )
            except Exception as e:
                log.warning(f"[ACP] session/resume({self.conversation_id}) failed ({e}), falling back to session/new.")
                self.conversation_id = ""

        if not opened:
            opened = await self._rpc_call(
                "session/new",
                {
                    "cwd": cwd_dir,
                    "mcpServers": [],
                },
                timeout=20.0,
            )

        sid = (opened or {}).get("sessionId") or ""
        if not sid:
            raise RuntimeError(f"ACP session open returned no sessionId: {opened}")
        self.conversation_id = sid
        self.available_models = ((opened or {}).get("models") or {}).get("availableModels") or []

        # Synchronize target model & mode in-place
        await self.set_model(self.model, effort=self.effort)
        if self.agent_mode and self.agent_mode != "default":
            await self.set_mode(self.agent_mode)

    async def _send_json(self, payload: Dict[str, Any]):
        if not self.is_alive() or not self.process or not self.process.stdin:
            raise RuntimeError("ACP process is not running")
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.process.stdin.write(raw)
            await self.process.stdin.drain()

    async def _rpc_call(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        rpc_id = self._next_rpc_id
        self._next_rpc_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_rpc[rpc_id] = fut
        try:
            await self._send_json({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params})
            if timeout and timeout > 0:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        finally:
            self._pending_rpc.pop(rpc_id, None)

    async def set_model(self, model_id: str, effort: Optional[str] = None) -> bool:
        """Dynamically switch model (and thinking effort) without restarting the ACP process.
        Includes singleflight lock + applied-state deduplication so concurrent prewarm & executor calls never send duplicate RPCs.
        """
        if not self.is_alive() or not self.conversation_id or not model_id:
            self.model = model_id
            if effort:
                self.effort = effort
            return False

        switch_lock = getattr(self, "_model_switch_lock", None)
        if switch_lock is None:
            switch_lock = asyncio.Lock()
            self._model_switch_lock = switch_lock

        async with switch_lock:
            target_effort = effort or self.effort
            if (
                getattr(self, "_applied_acp_conv_id", None) == self.conversation_id
                and getattr(self, "_applied_acp_model", None) == model_id
                and getattr(self, "_applied_acp_effort", None) == target_effort
            ):
                self.model = model_id
                if effort:
                    self.effort = effort
                return True

            try:
                await self._rpc_call(
                    "session/set_model",
                    {"sessionId": self.conversation_id, "modelId": model_id},
                    timeout=12.0,
                )
                self.model = model_id
                if effort:
                    self.effort = effort
                    if not has_embedded_effort_suffix(model_id):
                        try:
                            await self._rpc_call(
                                "session/set_config_option",
                                {
                                    "sessionId": self.conversation_id,
                                    "configId": "thought_level",
                                    "value": effort,
                                },
                                timeout=8.0,
                            )
                        except Exception as eff_err:
                            log.debug(f"[ACP] set thought_level={effort} skipped: {eff_err}")
                self._applied_acp_conv_id = self.conversation_id
                self._applied_acp_model = model_id
                self._applied_acp_effort = self.effort
                log.info(f"[ACP] Dynamically switched model in-place for {self.chat_id}: {model_id} (effort={self.effort})")
                return True
            except Exception as e:
                log.warning(f"[ACP] session/set_model({model_id}) failed for {self.chat_id}: {e}")
                return False

    async def set_mode(self, mode: str) -> bool:
        """Dynamically switch execution mode (`default` | `accept-edits` | `plan`) in-place."""
        acp_mode = normalize_acp_mode(mode)
        self.agent_mode = acp_mode
        if not self.is_alive() or not self.conversation_id:
            return False
        try:
            await self._rpc_call(
                "session/set_mode",
                {"sessionId": self.conversation_id, "modeId": acp_mode},
                timeout=10.0,
            )
            log.info(f"[ACP] Dynamically switched mode in-place for {self.chat_id}: {acp_mode}")
            return True
        except Exception as e:
            log.warning(f"[ACP] session/set_mode({acp_mode}) failed for {self.chat_id}: {e}")
            return False

    async def steer(self, steering_text: str) -> bool:
        """Queue a follow-up ACP prompt on the active session (executes right after the current turn finishes)."""
        if not self.is_turn_running() or not self.conversation_id or not (steering_text or "").strip():
            return False
        rpc_id = self._next_rpc_id
        self._next_rpc_id += 1
        preview = steering_text.strip()[:120]
        self._queued_steer_rpc_ids[rpc_id] = preview
        try:
            blocks = build_acp_prompt_blocks(steering_text)
            await self._send_json({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "session/prompt",
                "params": {"sessionId": self.conversation_id, "prompt": blocks},
            })
            log.info(f"[ACP] Queued follow-up prompt (rpc_id={rpc_id}) on {self.chat_id} (len={len(steering_text)})")
            return True
        except Exception as e:
            self._queued_steer_rpc_ids.pop(rpc_id, None)
            log.warning(f"[ACP] Failed to queue follow-up prompt for {self.chat_id}: {e}")
            return False

    async def cancel_active_turn(self):
        """Send ACP `session/cancel` notification to gracefully interrupt the current turn."""
        if not self.is_alive() or not self.conversation_id:
            return
        try:
            await self._send_json({
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": self.conversation_id},
            })
        except Exception as e:
            log.debug(f"[ACP] session/cancel error: {e}")

    async def send_prompt_and_stream(
        self,
        prompt_text: str,
        extra_image_paths: Optional[List[str]] = None,
    ) -> AcpTurnEventStream:
        """Send a turn via `session/prompt` and return a stream-json compatible event stream."""
        if not self.is_alive():
            await self.start()

        self.clear_recent_stderr()
        self.last_active_time = time.time()

        queue: asyncio.Queue = asyncio.Queue()
        self._active_turn_queue = queue
        self._active_turn_text_chunks = []
        self._active_turn_tool_calls = {}
        self._active_turn_pending_bg_tasks = set()
        self._in_turn = True

        # Emit synthetic `init` event so executor immediately persists conversation_id
        if self.conversation_id:
            await queue.put({"event": "init", "conversation_id": self.conversation_id})

        rpc_id = self._next_rpc_id
        self._next_rpc_id += 1
        self._active_prompt_rpc_id = rpc_id

        blocks = build_acp_prompt_blocks(prompt_text, extra_image_paths=extra_image_paths)
        await self._send_json({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "session/prompt",
            "params": {
                "sessionId": self.conversation_id,
                "prompt": blocks,
            },
        })

        def _cleanup():
            if self._active_turn_queue is queue:
                self._active_turn_queue = None
                self._active_prompt_rpc_id = None
                self._active_turn_tool_calls.clear()
                self._active_turn_pending_bg_tasks.clear()
                # Keep `_in_turn = True` if follow-up `steer()` prompts are still in flight on stdin
                if self._queued_steer_rpc_ids:
                    self._turn_waiting_for_steer = True
                    self._in_turn = True
                else:
                    self._turn_waiting_for_steer = False
                    self._in_turn = False

        return AcpTurnEventStream(queue, cleanup_cb=_cleanup)

    async def _handle_server_request(self, msg: Dict[str, Any]):
        """Handle incoming JSON-RPC requests from `agy acp` (e.g. `session/request_permission`)."""
        req_id = msg.get("id")
        method = msg.get("method") or ""
        params = msg.get("params") or {}

        if method == "session/request_permission":
            options = params.get("options") or []
            chosen_option_id = await self._decide_permission(params, options)
            if chosen_option_id:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"outcome": {"outcome": "selected", "optionId": chosen_option_id}},
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"outcome": {"outcome": "cancelled"}},
                }
            try:
                await self._send_json(resp)
            except Exception as e:
                log.warning(f"[ACP] Failed to send permission response: {e}")
            return

        # Unknown server request: return JSON-RPC method-not-found error cleanly
        try:
            await self._send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unsupported client method: {method}"},
            })
        except Exception:
            pass

    async def _decide_permission(self, params: Dict[str, Any], options: List[Dict[str, Any]]) -> Optional[str]:
        """Resolve an ACP permission request via auto-policy or Feishu interactive card."""
        def _pick_option(preferred_kinds: Tuple[str, ...], fallback_first: bool = False) -> Optional[str]:
            for kind in preferred_kinds:
                for opt in options:
                    if (opt.get("kind") or "").lower() == kind and opt.get("optionId"):
                        return opt["optionId"]
            if fallback_first and options:
                return options[0].get("optionId")
            return None

        target_owner_chat = self.owner_chat_id or self.chat_id
        # Admin with DANGEROUSLY_SKIP_PERMISSIONS auto-approves unless interactive override is forced
        if DANGEROUSLY_SKIP_PERMISSIONS and is_admin(target_owner_chat):
            return _pick_option(("allow_once", "allow_always", "allow"), fallback_first=True)

        reject_kinds = ("reject_once", "reject_always", "deny", "deny_once", "deny_always", "cancel", "reject")

        # Interactive Feishu card bridge when notifier is configured
        if getattr(config, "ACP_INTERACTIVE_PERMISSIONS", True) and self._permission_notifier:
            perm_token = uuid.uuid4().hex[:16]
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            _PENDING_PERMISSIONS[perm_token] = {
                "future": fut,
                "chat_id": target_owner_chat,
                "options": options,
                "created_at": time.time(),
            }
            try:
                sent = await self._permission_notifier(target_owner_chat, perm_token, params)
                if sent:
                    try:
                        return await asyncio.wait_for(fut, timeout=180.0)
                    except asyncio.TimeoutError:
                        log.warning(f"[ACP] Permission card timed out for {target_owner_chat}, rejecting safely.")
                        return _pick_option(reject_kinds, fallback_first=False)
            except Exception as e:
                log.warning(f"[ACP] Permission notifier error: {e}")
            finally:
                _PENDING_PERMISSIONS.pop(perm_token, None)

        # Safe fallback: deny when non-admin & no interactive approval occurred (returns None -> outcome='cancelled' if no explicit reject kind)
        return _pick_option(reject_kinds, fallback_first=False)

    async def _flush_idle_updates(self):
        await asyncio.sleep(1.2)
        if not self._idle_text_chunks or not self._async_update_callback:
            self._idle_text_chunks.clear()
            return
        payload = "".join(self._idle_text_chunks).strip()
        self._idle_text_chunks.clear()
        if payload:
            try:
                await self._async_update_callback(self.owner_chat_id or self.chat_id, payload)
            except Exception as e:
                log.warning(f"[ACP] Async background update callback failed: {e}")

    async def _read_stdout_loop(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        _MAX_STDOUT_LINE_BUF = 16 * 1024 * 1024
        try:
            while self.process and self.process.stdout:
                chunk = await self.process.stdout.read(8192)
                if not chunk:
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        buf += tail
                    break
                buf += decoder.decode(chunk)
                if len(buf) > _MAX_STDOUT_LINE_BUF and "\n" not in buf:
                    log.warning(f"[ACP] Dropping oversized single-line stdout buffer ({len(buf)} bytes) for {self.chat_id}")
                    buf = ""
                    continue
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        if self._active_turn_queue is not None:
                            await self._active_turn_queue.put({"event": "raw_log", "text": line})
                        continue
                    await self._dispatch_incoming_message(msg)
            if buf.strip():
                try:
                    msg = json.loads(buf.strip())
                    await self._dispatch_incoming_message(msg)
                except Exception:
                    if self._active_turn_queue is not None:
                        await self._active_turn_queue.put({"event": "raw_log", "text": buf.strip()})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[ACP] stdout reader error for {self.chat_id}: {e}")
            if self._active_turn_queue is not None:
                await self._active_turn_queue.put({"event": "read_error", "error": str(e)})
        finally:
            rc = getattr(self.process, "returncode", -1) if self.process else -1
            for fut in list(self._pending_rpc.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(f"ACP process exited (rc={rc})"))
            self._pending_rpc.clear()
            if self._active_turn_queue is not None:
                await self._active_turn_queue.put({"event": "process_exit", "returncode": rc})
            self._in_turn = False

    def _spawn_tracked_task(self, coro) -> asyncio.Task:
        bg = getattr(self, "_bg_tasks", None)
        if bg is None:
            bg = set()
            self._bg_tasks = bg
        t = asyncio.create_task(coro)
        bg.add(t)
        def _on_done(done_t: asyncio.Task):
            bg.discard(done_t)
            if not done_t.cancelled():
                exc = done_t.exception()
                if exc:
                    log.warning(f"[ACP] Background task error for {self.chat_id}: {exc}")
        t.add_done_callback(_on_done)
        return t

    async def _dispatch_incoming_message(self, msg: Dict[str, Any]):
        if not isinstance(msg, dict):
            return

        # 1. Server-initiated RPC request (has both `method` and `id`)
        if "method" in msg and "id" in msg:
            self._spawn_tracked_task(self._handle_server_request(msg))
            return

        # 2. Server notification (`method` without `id`)
        if "method" in msg and "id" not in msg:
            if msg.get("method") == "session/update":
                params = msg.get("params") or {}
                update = params.get("update") or {}
                if update.get("sessionUpdate") == "usage_update":
                    self.last_usage = {
                        "used": update.get("used", 0),
                        "size": update.get("size", 0),
                        "updated_at": time.time(),
                    }
                if update.get("sessionUpdate") in ("tool_call", "tool_call_update"):
                    bg_started, bg_finished = _extract_bg_task_transitions(update)
                    for tid in bg_started:
                        self._active_turn_pending_bg_tasks.add(tid)
                    for tid in bg_finished:
                        self._active_turn_pending_bg_tasks.discard(tid)
                stream_ev = _acp_update_to_stream_event(update, tool_call_cache=self._active_turn_tool_calls)
                if not stream_ev:
                    return
                if self._active_turn_queue is not None:
                    if stream_ev.get("event") == "message":
                        self._active_turn_text_chunks.append(stream_ev.get("content", ""))
                    await self._active_turn_queue.put(stream_ev)
                elif self._queued_steer_rpc_ids and stream_ev.get("event") == "message":
                    # Buffer cohesive text for an in-flight queued `steer()` prompt until its RPC result arrives
                    self._steer_turn_text_chunks.append(stream_ev.get("content", ""))
                elif stream_ev.get("event") == "message" and self._async_update_callback:
                    # Background subagent / task wake-up outside active turn
                    self._idle_text_chunks.append(stream_ev.get("content", ""))
                    if self._idle_flush_task and not self._idle_flush_task.done():
                        self._idle_flush_task.cancel()
                    self._idle_flush_task = asyncio.create_task(self._flush_idle_updates())
            return

        # 3. RPC Response (has `id` without `method`)
        if "id" in msg:
            rpc_id = msg["id"]
            if rpc_id == self._active_prompt_rpc_id and self._active_turn_queue is not None:
                q = self._active_turn_queue
                self._active_prompt_rpc_id = None
                if "error" in msg and msg["error"]:
                    self._active_turn_queue = None
                    self._in_turn = bool(self._queued_steer_rpc_ids)
                    err = msg["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    await q.put({
                        "event": "result",
                        "result": {"status": "ERROR", "error": err_msg},
                    })
                else:
                    res = msg.get("result") or {}
                    if isinstance(res.get("usage"), dict):
                        self.last_usage.update(res["usage"])
                    final_text = "".join(self._active_turn_text_chunks)
                    stop_reason = res.get("stopReason", "end_turn")
                    if stop_reason == "cancelled":
                        self._active_turn_queue = None
                        self._in_turn = bool(self._queued_steer_rpc_ids)
                        await q.put({"event": "cancelled", "message": "Turn cancelled"})
                    else:
                        # Keep `_active_turn_queue` attached until `turn_stream.aclose()` so
                        # if executor detects pending background tasks, no wake-up notifications are lost.
                        await q.put({
                            "event": "result",
                            "result": {
                                "status": "SUCCESS",
                                "response": final_text,
                                "stopReason": stop_reason,
                                "usage": res.get("usage") or {},
                                "pending_bg_tasks": sorted(self._active_turn_pending_bg_tasks),
                            },
                        })
                return

            if rpc_id in self._queued_steer_rpc_ids:
                steer_preview = self._queued_steer_rpc_ids.pop(rpc_id, "")
                steer_body = "".join(self._steer_turn_text_chunks).strip()
                self._steer_turn_text_chunks.clear()
                if not self._queued_steer_rpc_ids and getattr(self, "_turn_waiting_for_steer", False):
                    self._turn_waiting_for_steer = False
                    self._in_turn = False
                target_cb_chat = self.owner_chat_id or self.chat_id
                if "error" in msg and msg["error"]:
                    err = msg["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    log.warning(
                        f"[ACP] Queued follow-up prompt (rpc_id={rpc_id}) failed for {self.chat_id}: {err_msg}"
                    )
                    if self._async_update_callback and self._active_turn_queue is None:
                        try:
                            self._spawn_tracked_task(
                                self._async_update_callback(
                                    target_cb_chat,
                                    f"⚠️ 排队追加指令执行失败（{steer_preview[:60]}）：{err_msg}",
                                )
                            )
                        except Exception as cb_err:
                            log.debug(f"[ACP] steer error callback failed: {cb_err}")
                else:
                    res = msg.get("result") or {}
                    if isinstance(res.get("usage"), dict):
                        self.last_usage.update(res["usage"])
                    stop_reason = res.get("stopReason", "end_turn")
                    log.info(
                        f"[ACP] Queued follow-up prompt (rpc_id={rpc_id}) completed for {self.chat_id} "
                        f"(stopReason={stop_reason})"
                    )
                    if steer_body and self._async_update_callback and self._active_turn_queue is None:
                        try:
                            self._spawn_tracked_task(
                                self._async_update_callback(target_cb_chat, steer_body)
                            )
                        except Exception as cb_err:
                            log.debug(f"[ACP] steer completion callback failed: {cb_err}")
                return

            fut = self._pending_rpc.get(rpc_id)
            if fut and not fut.done():
                if "error" in msg and msg["error"]:
                    err = msg["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    fut.set_exception(RuntimeError(err_msg))
                else:
                    fut.set_result(msg.get("result") or {})

    async def _drain_stderr(self):
        try:
            while self.process and self.process.returncode is None:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                if text:
                    self._recent_stderr_buf = (self._recent_stderr_buf + text)[-32768:]
                    log.debug(f"[ACP:{self.chat_id}] stderr: {text[:200].strip()}")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _terminate_process_only(self):
        for t in (self._stderr_drain_task, self._stdout_reader_task, self._idle_flush_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._stderr_drain_task = None
        self._stdout_reader_task = None
        self._idle_flush_task = None

        proc = self.process
        self.process = None
        if proc:
            try:
                descendant_pids = [p for p in snapshot_process_group_pids(proc.pid) if p != proc.pid]
                if proc.returncode is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
                    for d_pid in descendant_pids:
                        try:
                            os.kill(d_pid, signal.SIGTERM)
                        except Exception:
                            pass
                    await asyncio.sleep(0.15)
                if proc.returncode is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
                    for d_pid in descendant_pids:
                        try:
                            os.kill(d_pid, signal.SIGKILL)
                        except Exception:
                            pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    pass
            except Exception as e:
                log.warning(f"[ACP] Error terminating process for {self.chat_id}: {e}")
            finally:
                if hasattr(app_state, "unregister_subprocess"):
                    app_state.unregister_subprocess(self.chat_id, proc)
                elif app_state.running_processes.get(self.chat_id) is proc:
                    app_state.running_processes.pop(self.chat_id, None)

    async def close(self):
        self._closing = True
        self._in_turn = False
        self._queued_steer_rpc_ids.clear()
        async with self._start_lock:
            if self.is_alive() and self.conversation_id:
                try:
                    await self._rpc_call("session/close", {"sessionId": self.conversation_id}, timeout=2.0)
                except Exception:
                    pass
            await self._terminate_process_only()
