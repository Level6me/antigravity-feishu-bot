"""Persistent session pool for Antigravity (agy) CLI processes.

Supports dual transport modes:
  - `acp` (default on agy 1.5.x): Agent Client Protocol JSON-RPC stdio server with in-place
    model/mode hot-switching, native multimodal image blocks, mid-turn steering, and interactive
    permission approval callbacks.
  - `stream-json`: Legacy warm `agy --input-format stream-json --output-format stream-json`
    with `--mode`, `--add-dir` (multi-dir), and `--settings` session-scoped isolation,
    also serving as automatic fallback if ACP fails to initialize.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import os
import signal
import subprocess
import time
import uuid
from typing import Optional, Dict, Any, List, Callable, Awaitable

import config
from config import (
    ANTIGRAVITY_BIN,
    find_antigravity_bin,
    BASE_DIR,
    DANGEROUSLY_SKIP_PERMISSIONS,
)
from utils.auth import is_admin, has_scope, SCOPE_PROJECT, SCOPE_SHELL
from math_decision_model import snapshot_process_group_pids
from utils.model_tiers import (
    split_model_id_suffix,
    get_effort_rank,
    has_embedded_effort_suffix,
)
from logger import log
import app_state

_TERMINAL_TURN_EVENTS = {"result", "error", "turn_error", "cancelled", "fatal", "process_exit", "read_error"}
_ALLOWED_EFFORTS = ("minimal", "fast", "low", "medium", "high", "xhigh", "max", "ultra")
_ALLOWED_CLI_MODES = ("auto", "accept-edits", "plan")


def normalize_cli_mode(mode: Optional[str]) -> str:
    """Normalize bot mode string to valid `agy --mode` value (`auto`, `accept-edits`, `plan`)."""
    m = (mode or "auto").strip().lower()
    if m in ("plan", "readonly", "read-only"):
        return "plan"
    if m in ("accept-edits", "accept_edits", "edits"):
        return "accept-edits"
    return "auto"


def build_session_settings_json(agent_mode: str, sandbox_enabled: bool, project_dir: Optional[str]) -> str:
    """Build ephemeral per-session `--settings` JSON without mutating global settings.json."""
    settings_obj: Dict[str, Any] = {
        "agentMode": normalize_cli_mode(agent_mode),
        # NOTE: `artifactReviewMode` is rejected by agy 1.5.12 `--settings` ("unknown setting"), do not add it here.
        "allowNonWorkspaceAccess": not sandbox_enabled,
    }
    if project_dir and os.path.isdir(project_dir):
        settings_obj["trustedWorkspaces"] = [os.path.abspath(project_dir)]
    return json.dumps(settings_obj, ensure_ascii=False, separators=(",", ":"))


def _extract_model_family(model_name: str) -> str:
    """Return base model family name with adaptive effort suffix stripped."""
    stem, _, _ = split_model_id_suffix(model_name)
    return stem


def _extract_model_tier_rank(model_name: str, effort: str = "medium") -> int:
    """Return numeric reasoning tier rank from model suffix or effort (e.g. 20=low, 40=medium, 60=high, 80=xhigh)."""
    _, _, rank = split_model_id_suffix(model_name)
    if rank is not None:
        return rank
    eff_rank = get_effort_rank(effort or "medium")
    return eff_rank if eff_rank is not None else 40

class TurnEventStream:
    """A thread-safe, non-cancellable stream of parsed JSON events for a single dialogue turn.
    
    Reading stdout is decoupled from consumer polling timeouts.
    If consumer timeouts occur while waiting for an event (e.g. to refresh cards),
    the underlying stdout reader continues reading unaffected.
    """
    def __init__(self, queue: asyncio.Queue, reader_task: asyncio.Task):
        self._queue = queue
        self._reader_task = reader_task
        self.is_done = False

    async def get_event(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Fetch the next parsed event from the queue.
        Returns None on timeout without interrupting or cancelling the reader.
        """
        if self.is_done:
            return None
        try:
            if timeout is not None:
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                event = await self._queue.get()

            if event.get("event") in _TERMINAL_TURN_EVENTS or event.get("type") in ("result", "error", "turn_error", "cancelled", "fatal"):
                self.is_done = True
            return event
        except asyncio.TimeoutError:
            return None

    def drain_all(self) -> list[Dict[str, Any]]:
        """Drain all currently available events in the queue non-blockingly."""
        events = []
        while not self.is_done and not self._queue.empty():
            try:
                ev = self._queue.get_nowait()
                events.append(ev)
                if ev.get("event") in _TERMINAL_TURN_EVENTS or ev.get("type") in ("result", "error", "turn_error", "cancelled", "fatal"):
                    self.is_done = True
                    break
            except asyncio.QueueEmpty:
                break
        return events

    async def aclose(self):
        """Clean up the turn reader task if still active."""
        if not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

class PersistentSession:
    """Represents a long-lived interactive agy CLI process (`stream-json` transport)."""

    transport_name = "stream-json"

    def __init__(
        self,
        chat_id: str,
        model: str,
        project_dir: Optional[str] = None,
        conversation_id: str = "",
        effort: str = "medium",
        sandbox_enabled: bool = False,
        agent_mode: str = "auto",
        extra_dirs: Optional[List[str]] = None,
        owner_chat_id: Optional[str] = None,
    ):
        self.chat_id = chat_id
        self.owner_chat_id = owner_chat_id or chat_id
        self.model = model
        self.effort = effort if effort in _ALLOWED_EFFORTS else "medium"
        self.sandbox_enabled = sandbox_enabled
        self.agent_mode = normalize_cli_mode(agent_mode)
        self.project_dir = project_dir
        self.extra_dirs: List[str] = [os.path.abspath(d) for d in (extra_dirs or []) if d and os.path.isdir(d)]
        self.process: Optional[asyncio.subprocess.Process] = None
        self.conversation_id: str = conversation_id
        self.lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self.spawn_time = time.time()
        self.last_active_time = time.time()
        self._closing = False
        self._decoder = codecs.getincrementaldecoder('utf-8')()
        self._line_buffer = ""
        self._recent_stderr_buf = ""
        self._stderr_drain_task: Optional[asyncio.Task] = None

    def get_recent_stderr(self) -> str:
        return self._recent_stderr_buf

    def clear_recent_stderr(self):
        self._recent_stderr_buf = ""

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def is_dead_or_closing(self) -> bool:
        """True only when this session was explicitly closed or its spawned process has exited.
        Returns False for a newly created session that is about to start or currently spawning,
        preventing get_or_create() from overwriting a prewarming session and leaking an orphan process.
        """
        return self._closing or (self.process is not None and self.process.returncode is not None)

    async def start(self):
        # prewarm and request execution may race to start the same process.
        async with self._start_lock:
            if self._closing or self.is_alive():
                return
            await self._start_process()

    async def _start_process(self):
        """Spawn the background agy process in stream-json mode."""
        if self._closing or self.is_alive():
            return

        bin_path = ANTIGRAVITY_BIN or find_antigravity_bin()
        if not bin_path:
            raise FileNotFoundError("Antigravity / agy binary not found.")

        cmd_args = [
            bin_path,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", self.model,
            "--mode", self.agent_mode,
            "--print-timeout", "60m"
        ]

        # 仅当模型名称未自带固定 effort 后缀时，才附加 --effort，杜绝 agy 参数冲突报错
        if not has_embedded_effort_suffix(self.model):
            if self.effort:
                cmd_args.extend(["--effort", self.effort])

        if self.conversation_id:
            cmd_args.extend(["--conversation", self.conversation_id])

        if self.sandbox_enabled:
            cmd_args.append("--sandbox")

        if getattr(config, "USE_SESSION_SETTINGS_ISOLATION", True):
            settings_json = build_session_settings_json(self.agent_mode, self.sandbox_enabled, self.project_dir)
            cmd_args.extend(["--settings", settings_json])

        is_admin_chat = is_admin(self.owner_chat_id)
        if DANGEROUSLY_SKIP_PERMISSIONS and is_admin_chat and self.agent_mode != "plan":
            cmd_args.append("--dangerously-skip-permissions")

        cwd_dir = None
        added_dirs: List[str] = []
        if self.project_dir and os.path.isdir(self.project_dir):
            cwd_dir = self.project_dir
            abs_proj = os.path.abspath(self.project_dir)
            added_dirs.append(abs_proj)
        for extra_d in self.extra_dirs:
            if extra_d not in added_dirs and os.path.isdir(extra_d):
                added_dirs.append(extra_d)
        for d in added_dirs:
            cmd_args.extend(["--add-dir", d])

        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        custom_env["PYTHONUNBUFFERED"] = "1"
        custom_env["STDOUT_LINE_BUFFERED"] = "1"

        log.info(
            f"[SessionPool] Spawning warm agy stream-json process for chat {self.chat_id} "
            f"(model={self.model}, effort={self.effort}, mode={self.agent_mode}, sandbox={self.sandbox_enabled}, cwd={cwd_dir}, extra_dirs={len(self.extra_dirs)})"
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
            cwd=cwd_dir,
            env=custom_env
        )
        # If close() was invoked while create_subprocess_exec was awaiting, terminate immediately so no orphan leaks
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
        self._decoder = codecs.getincrementaldecoder('utf-8')()
        self._line_buffer = ""
        self.last_active_time = time.time()
        if hasattr(app_state, "register_subprocess"):
            app_state.register_subprocess(self.chat_id, self.process)
        else:
            app_state.running_processes[self.chat_id] = self.process

        # Start background stderr drain to prevent OS pipe buffer deadlock.
        # If stderr fills up (~64KB), the process blocks on write and stdout stalls too.
        self._stderr_drain_task = asyncio.create_task(self._drain_stderr())

    async def send_prompt_and_stream(self, prompt_text: str) -> TurnEventStream:
        """Send a prompt turn to the process stdin and return a TurnEventStream."""
        if not self.is_alive():
            await self.start()

        prev_reader = getattr(self, "_active_reader_task", None)
        if prev_reader is not None and not prev_reader.done():
            prev_reader.cancel()
            try:
                await prev_reader
            except (asyncio.CancelledError, Exception):
                pass
            self._active_reader_task = None

        write_lock = getattr(self, "_write_lock", None)
        if write_lock is None:
            write_lock = asyncio.Lock()
            self._write_lock = write_lock

        self.clear_recent_stderr()
        self.last_active_time = time.time()
        req = json.dumps({"event": "user", "message": {"content": prompt_text}}) + "\n"
        async with write_lock:
            self.process.stdin.write(req.encode("utf-8"))
            await self.process.stdin.drain()

        queue = asyncio.Queue()
        reader_task = asyncio.create_task(self._read_turn_stdout(queue))
        self._active_reader_task = reader_task
        return TurnEventStream(queue, reader_task)

    async def _read_turn_stdout(self, queue: asyncio.Queue):
        """Background coroutine reading stdout until 'result' event or EOF.
        Guarantees that no JSON line or stream chunk is lost due to UI poll timeouts.
        """
        try:
            while True:
                if self.process is None or self.process.returncode is not None:
                    await queue.put({"event": "process_exit", "returncode": getattr(self.process, "returncode", -1)})
                    break
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    await queue.put({"event": "process_exit", "returncode": getattr(self.process, "returncode", -1)})
                    break
                text_chunk = self._decoder.decode(chunk)
                self._line_buffer += text_chunk
                while "\n" in self._line_buffer:
                    line, self._line_buffer = self._line_buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event_data = json.loads(line)
                    except Exception:
                        event_data = {"event": "raw_log", "text": line}

                    event_type = event_data.get("event") or event_data.get("type")
                    if event_type == "init":
                        self.conversation_id = event_data.get("conversation_id", "")

                    await queue.put(event_data)

                    if event_type in ("result", "error", "turn_error", "cancelled", "fatal"):
                        return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[SessionPool] Error in _read_turn_stdout for {self.chat_id}: {e}")
            await queue.put({"event": "read_error", "error": str(e)})

    async def _drain_stderr(self):
        """Background task: continuously read and buffer stderr to prevent pipe buffer deadlock."""
        try:
            while self.process and self.process.returncode is None:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                if text:
                    self._recent_stderr_buf = (self._recent_stderr_buf + text)[-32768:]
                    log.debug(f"[Session:{self.chat_id}] stderr: {text[:200].strip()}")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def close(self):
        """Gracefully terminate the warm process and reap its exit status."""
        self._closing = True
        async with self._start_lock:
            active_reader = getattr(self, "_active_reader_task", None)
            if active_reader and not active_reader.done():
                active_reader.cancel()
                try:
                    await active_reader
                except (asyncio.CancelledError, Exception):
                    pass
                self._active_reader_task = None
            # Cancel stderr drain first
            if self._stderr_drain_task and not self._stderr_drain_task.done():
                self._stderr_drain_task.cancel()
                try:
                    await self._stderr_drain_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._stderr_drain_task = None
            proc = self.process
            self.process = None
            if proc:
                try:
                    # Non-blocking pure /proc descendant PID discovery (never blocks asyncio event loop with subprocess.check_output)
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
                    log.warning(f"[SessionPool] Error terminating process for {self.chat_id}: {e}")
                finally:
                    if hasattr(app_state, "unregister_subprocess"):
                        app_state.unregister_subprocess(self.chat_id, proc)
                    elif app_state.running_processes.get(self.chat_id) is proc:
                        app_state.running_processes.pop(self.chat_id, None)

class SessionPool:
    """Manages active persistent sessions across chats (ACP + stream-json dual transport)."""

    def __init__(self):
        self._sessions: Dict[str, Any] = {}

    def get_active_session(self, chat_id: str) -> Optional[Any]:
        """Return currently pooled session for `chat_id` if alive and not closing."""
        sess = self._sessions.get(chat_id)
        if sess and not sess.is_dead_or_closing():
            return sess
        return None

    def _resolve_transport(self, transport: Optional[str]) -> str:
        t = (transport or getattr(config, "AGENT_TRANSPORT", "acp") or "acp").strip().lower()
        return "acp" if t == "acp" else "stream-json"

    def _create_session(
        self,
        *,
        transport: str,
        chat_id: str,
        model: str,
        project_dir: Optional[str],
        conversation_id: str,
        effort: str,
        sandbox_enabled: bool,
        agent_mode: str,
        extra_dirs: List[str],
        permission_notifier: Optional[Callable[[str, str, Dict[str, Any]], Awaitable[bool]]] = None,
        async_update_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        owner_chat_id: Optional[str] = None,
    ):
        if transport == "acp":
            from acp_client import AcpPersistentSession
            return AcpPersistentSession(
                chat_id=chat_id,
                model=model,
                project_dir=project_dir,
                conversation_id=conversation_id,
                effort=effort,
                sandbox_enabled=sandbox_enabled,
                agent_mode=agent_mode,
                extra_dirs=extra_dirs,
                permission_notifier=permission_notifier,
                async_update_callback=async_update_callback,
                owner_chat_id=owner_chat_id,
            )
        return PersistentSession(
            chat_id=chat_id,
            model=model,
            project_dir=project_dir,
            conversation_id=conversation_id,
            effort=effort,
            sandbox_enabled=sandbox_enabled,
            agent_mode=agent_mode,
            extra_dirs=extra_dirs,
            owner_chat_id=owner_chat_id,
        )

    def get_or_create(
        self,
        chat_id: str,
        model: str,
        project_dir: Optional[str] = None,
        conversation_id: str = "",
        effort: str = "medium",
        agent_mode: Optional[str] = None,
        extra_dirs: Optional[List[str]] = None,
        transport: Optional[str] = None,
        permission_notifier: Optional[Callable[[str, str, Dict[str, Any]], Awaitable[bool]]] = None,
        async_update_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        owner_chat_id: Optional[str] = None,
    ):
        sess = self._sessions.get(chat_id)
        desired_transport = self._resolve_transport(transport)
        # conversation_id 比对：只在"双方均非空且不同"时才视作需要重建
        conv_mismatch = bool(conversation_id and sess and sess.conversation_id and sess.conversation_id != conversation_id)

        normalized_effort = effort if effort in _ALLOWED_EFFORTS else "medium"
        raw_mode = agent_mode if agent_mode is not None else getattr(config, "DEFAULT_AGENT_MODE", "auto")
        normalized_cli_mode = normalize_cli_mode(raw_mode)
        normalized_extra_dirs = sorted({os.path.abspath(d) for d in (extra_dirs or []) if d and os.path.isdir(d)})

        # Enforce CLI sandbox both when Co-Pilot mode is active AND when a non-admin user lacks SCOPE_SHELL
        eff_owner_chat = owner_chat_id or (str(chat_id).split("cron_")[-1] if str(chat_id).startswith("cron_") else str(chat_id))
        user_lacks_shell = not is_admin(eff_owner_chat) and not has_scope(eff_owner_chat, SCOPE_SHELL)
        sandbox_enabled = bool((config.TYPESAFE_ENABLED and config.TYPESAFE_TIER == "copilot") or user_lacks_shell)

        sess_dead = sess is None or sess.is_dead_or_closing()
        sess_transport = getattr(sess, "transport_name", "stream-json") if sess else ""
        transport_mismatch = bool(sess and sess_transport != desired_transport)
        extra_dirs_mismatch = bool(
            sess and sorted(getattr(sess, "extra_dirs", []) or []) != normalized_extra_dirs
        )

        # Live ACP sessions support in-place model/effort/mode hot-switching without process restart!
        is_live_acp = bool(sess and not sess_dead and sess_transport == "acp" and sess.is_alive())
        if is_live_acp:
            model_mismatch = bool(sess.sandbox_enabled != sandbox_enabled)
            mode_mismatch = False
        else:
            auto_mode = bool(getattr(config, "TYPESAFE_AUTO_MODE", True))
            if sess and not sess_dead and auto_mode:
                sess_rank = _extract_model_tier_rank(sess.model, sess.effort)
                target_rank = _extract_model_tier_rank(model, normalized_effort)
                needs_high_upgrade = bool(target_rank >= 60 and sess_rank < target_rank)
                if needs_high_upgrade:
                    log.info(
                        f"[SessionPool] Upgrading warm session for {chat_id} from "
                        f"{sess.model} (effort={sess.effort}) to High-tier {model} (effort={normalized_effort})"
                    )
                model_mismatch = bool(
                    _extract_model_family(sess.model) != _extract_model_family(model)
                    or sess.sandbox_enabled != sandbox_enabled
                    or needs_high_upgrade
                )
            else:
                model_mismatch = bool(sess and (
                    sess.model != model
                    or sess.effort != normalized_effort
                    or sess.sandbox_enabled != sandbox_enabled
                ))
            sess_mode = normalize_cli_mode(getattr(sess, "agent_mode", "auto")) if sess else "auto"
            mode_mismatch = bool(sess and sess_mode != normalized_cli_mode)

        # Prevent warm sessions older than 40 minutes from hitting Google OAuth2 60m token expiry mid-turn
        now_ts = time.time()
        token_ttl_expired = bool(
            sess
            and not sess.lock.locked()
            and not sess._start_lock.locked()
            and (now_ts - getattr(sess, "spawn_time", sess.last_active_time) > 2400.0)
        )
        if token_ttl_expired:
            log.info(f"[SessionPool] Recycling warm session for {chat_id} (>40m age) to refresh OAuth token window.")

        if (
            sess_dead
            or transport_mismatch
            or model_mismatch
            or mode_mismatch
            or extra_dirs_mismatch
            or sess.project_dir != project_dir
            or conv_mismatch
            or token_ttl_expired
        ):
            bg_set = getattr(self, "_bg_close_tasks", None)
            if bg_set is None:
                bg_set = set()
                self._bg_close_tasks = bg_set

            def _schedule_close(s_obj):
                try:
                    t = asyncio.get_running_loop().create_task(s_obj.close())
                    bg_set.add(t)
                    t.add_done_callback(lambda done_t: (bg_set.discard(done_t), done_t.exception() if not done_t.cancelled() else None))
                except RuntimeError:
                    pass

            if sess:
                _schedule_close(sess)
            # Evict excess idle unlocked sessions to bound total memory footprint on low-RAM hosts
            max_warm = int(getattr(config, "MAX_WARM_SESSIONS", 2) or 2)
            idle_candidates = [
                (cid, s) for cid, s in list(self._sessions.items())
                if cid != chat_id and not s.lock.locked()
            ]
            if len(idle_candidates) >= max_warm:
                idle_candidates.sort(key=lambda item: item[1].last_active_time)
                for evict_cid, evict_sess in idle_candidates[:len(idle_candidates) - max_warm + 1]:
                    self._sessions.pop(evict_cid, None)
                    _schedule_close(evict_sess)
            sess = self._create_session(
                transport=desired_transport,
                chat_id=chat_id,
                model=model,
                project_dir=project_dir,
                conversation_id=conversation_id,
                effort=normalized_effort,
                sandbox_enabled=sandbox_enabled,
                agent_mode=raw_mode,
                extra_dirs=normalized_extra_dirs,
                permission_notifier=permission_notifier,
                async_update_callback=async_update_callback,
                owner_chat_id=eff_owner_chat,
            )
            self._sessions[chat_id] = sess
        else:
            sess.last_active_time = time.time()
            if conversation_id and not sess.conversation_id:
                sess.conversation_id = conversation_id
            if is_live_acp:
                if permission_notifier is not None:
                    sess._permission_notifier = permission_notifier
                if async_update_callback is not None:
                    sess._async_update_callback = async_update_callback
        return sess

    async def sync_live_session_config(
        self,
        sess: Any,
        model: str,
        effort: str = "medium",
        agent_mode: Optional[str] = None,
    ) -> None:
        """Apply in-place model/effort/mode changes on a live ACP session without restarting it."""
        if not sess or getattr(sess, "transport_name", "") != "acp" or not sess.is_alive():
            return
        normalized_effort = effort if effort in _ALLOWED_EFFORTS else "medium"
        if sess.model != model or sess.effort != normalized_effort:
            await sess.set_model(model, effort=normalized_effort)
        if agent_mode is not None:
            from acp_client import normalize_acp_mode
            target_acp_mode = normalize_acp_mode(agent_mode)
            if sess.agent_mode != target_acp_mode:
                await sess.set_mode(target_acp_mode)

    async def fallback_to_stream_json(
        self,
        chat_id: str,
        model: str,
        project_dir: Optional[str] = None,
        conversation_id: str = "",
        effort: str = "medium",
        agent_mode: Optional[str] = None,
        extra_dirs: Optional[List[str]] = None,
    ) -> PersistentSession:
        """Replace a failed ACP session with a stream-json session seamlessly."""
        old = self._sessions.pop(chat_id, None)
        if old:
            try:
                await old.close()
            except Exception:
                pass
        return self.get_or_create(
            chat_id,
            model,
            project_dir=project_dir,
            conversation_id=conversation_id,
            effort=effort,
            agent_mode=agent_mode,
            extra_dirs=extra_dirs,
            transport="stream-json",
        )

    def update_conversation_id(self, chat_id: str, conversation_id: str):
        """Sync runtime assigned conversation ID into memory to maintain persistent session continuity."""
        if not chat_id or not conversation_id:
            return
        sess = self._sessions.get(chat_id)
        if sess:
            sess.conversation_id = conversation_id

    async def prewarm(
        self,
        chat_id: str,
        model: str,
        project_dir: Optional[str] = None,
        conversation_id: str = "",
        effort: str = "medium",
        agent_mode: Optional[str] = None,
        extra_dirs: Optional[List[str]] = None,
        transport: Optional[str] = None,
    ):
        """Asynchronously pre-spawns a warm process in the background so next message has 0s start latency."""
        try:
            sess = self.get_or_create(
                chat_id,
                model,
                project_dir,
                conversation_id,
                effort=effort,
                agent_mode=agent_mode,
                extra_dirs=extra_dirs,
                transport=transport,
            )
            if not sess.is_alive():
                if not sess.lock.locked() and not sess._start_lock.locked():
                    await sess.start()
            elif not sess.lock.locked():
                await self.sync_live_session_config(sess, model, effort=effort, agent_mode=agent_mode)
        except Exception as e:
            log.warning(f"[SessionPool] Prewarm failed for chat {chat_id}: {e}")

    async def reap_idle_sessions(self, max_idle_seconds: float = 1800.0) -> int:
        """Close and remove idle unlocked sessions that have been inactive longer than max_idle_seconds."""
        now = time.time()
        reaped = 0
        for chat_id, sess in list(self._sessions.items()):
            if sess.lock.locked() or sess._start_lock.locked():
                continue
            if sess.is_dead_or_closing() or (now - sess.last_active_time > max_idle_seconds):
                self._sessions.pop(chat_id, None)
                try:
                    await sess.close()
                    reaped += 1
                except Exception as e:
                    log.debug(f"[SessionPool] Error closing idle session {chat_id}: {e}")
        if reaped > 0:
            log.info(f"[SessionPool] Reaped {reaped} idle/expired warm session(s).")
        return reaped

    async def reset_session(self, chat_id: str):
        """Close and purge a chat's session."""
        sess = self._sessions.pop(chat_id, None)
        if sess:
            await sess.close()

    async def probe_model_via_shared_acp(
        self,
        model_id: str,
        timeout_seconds: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """Fast warm-ACP capacity probe reusing a shared lightweight ACP daemon via `set_model`.
        Eliminates 3~5s CLI cold-start overhead on 503 health checks.
        Returns verdict dict or None if ACP probe is unavailable so caller can fall back to CLI probe.
        """
        if self._resolve_transport(None) != "acp":
            return None

        probe_lock = getattr(self, "_shared_probe_lock", None)
        if probe_lock is None:
            probe_lock = asyncio.Lock()
            self._shared_probe_lock = probe_lock

        import tempfile
        start_ts = time.time()
        async with probe_lock:
            probe_sess = getattr(self, "_shared_probe_acp_session", None)
            now_ts = time.time()
            # Recycle shared probe session if dead or older than 30m
            if (
                probe_sess is None
                or probe_sess.is_dead_or_closing()
                or not probe_sess.is_alive()
                or (now_ts - getattr(probe_sess, "spawn_time", probe_sess.last_active_time) > 1800.0)
            ):
                if probe_sess is not None:
                    try:
                        await probe_sess.close()
                    except Exception:
                        pass
                try:
                    from acp_client import AcpPersistentSession
                    probe_sess = AcpPersistentSession(
                        chat_id="__shared_model_probe__",
                        model=model_id,
                        project_dir=tempfile.gettempdir(),
                        conversation_id="",
                        effort="low",
                        sandbox_enabled=True,
                        agent_mode="auto",
                        extra_dirs=[],
                    )
                    await asyncio.wait_for(probe_sess.start(), timeout=min(8.0, max(4.0, timeout_seconds)))
                    self._shared_probe_acp_session = probe_sess
                except Exception as start_err:
                    log.debug(f"[SessionPool] Shared ACP probe start unavailable, falling back: {start_err}")
                    self._shared_probe_acp_session = None
                    return None
            else:
                try:
                    await asyncio.wait_for(probe_sess.set_model(model_id, effort="low"), timeout=3.0)
                except Exception as switch_err:
                    log.debug(f"[SessionPool] Shared ACP set_model failed for {model_id}: {switch_err}")
                    try:
                        await probe_sess.close()
                    except Exception:
                        pass
                    self._shared_probe_acp_session = None
                    return None

            turn_stream = None
            try:
                probe_start = time.time()
                turn_stream = await probe_sess.send_prompt_and_stream("Reply OK only.")
                deadline = probe_start + max(3.0, float(timeout_seconds))
                while True:
                    rem = deadline - time.time()
                    if rem <= 0:
                        return {
                            "status": "timeout",
                            "model": model_id,
                            "latency_ms": round((time.time() - start_ts) * 1000.0, 1),
                            "error": "shared acp probe timeout",
                        }
                    ev = await turn_stream.get_event(timeout=min(0.5, rem))
                    if ev is None:
                        continue
                    ev_type = ev.get("event") or ev.get("type")
                    if ev_type == "message" and ev.get("content"):
                        return {
                            "status": "healthy",
                            "model": model_id,
                            "latency_ms": round((time.time() - start_ts) * 1000.0, 1),
                            "error": "",
                        }
                    if ev_type == "step_update":
                        step_up = ev.get("step_update") or {}
                        if step_up.get("step_type") == "agent_response" and step_up.get("text_delta"):
                            return {
                                "status": "healthy",
                                "model": model_id,
                                "latency_ms": round((time.time() - start_ts) * 1000.0, 1),
                                "error": "",
                            }
                    if ev_type in ("result", "error", "turn_error", "fatal", "process_exit"):
                        res_obj = ev.get("result") or {}
                        if ev_type == "result" and isinstance(res_obj, dict) and res_obj.get("status") == "SUCCESS":
                            return {
                                "status": "healthy",
                                "model": model_id,
                                "latency_ms": round((time.time() - start_ts) * 1000.0, 1),
                                "error": "",
                            }
                        err_txt = str(
                            (res_obj.get("error") if isinstance(res_obj, dict) else "")
                            or ev.get("error")
                            or ev.get("message")
                            or probe_sess.get_recent_stderr()
                            or ""
                        ).strip()
                        if "503" in err_txt or "no capacity" in err_txt.lower() or "unavailable" in err_txt.lower():
                            return {
                                "status": "unhealthy",
                                "model": model_id,
                                "latency_ms": round((time.time() - start_ts) * 1000.0, 1),
                                "error": err_txt[-300:],
                            }
                        return None
            except Exception as probe_err:
                log.debug(f"[SessionPool] Shared ACP probe error for {model_id}: {probe_err}")
                try:
                    await probe_sess.close()
                except Exception:
                    pass
                self._shared_probe_acp_session = None
                return None
            finally:
                if turn_stream is not None:
                    try:
                        await turn_stream.aclose()
                    except Exception:
                        pass
                if probe_sess and getattr(probe_sess, "_in_turn", False) and hasattr(probe_sess, "cancel"):
                    try:
                        await asyncio.wait_for(probe_sess.cancel(), timeout=1.5)
                    except Exception:
                        pass

session_pool = SessionPool()
