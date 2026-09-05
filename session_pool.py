"""Persistent session pool for Antigravity (agy) interactive CLI processes.

Maintains warm, long-lived `agy --input-format stream-json --output-format stream-json`
processes per chat/project to eliminate cold-start overhead and enable instant 1-2s responses.
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
from typing import Optional, Dict, Any

from config import (
    ANTIGRAVITY_BIN,
    find_antigravity_bin,
    BASE_DIR,
    DANGEROUSLY_SKIP_PERMISSIONS,
)
from utils.auth import is_admin, has_scope, SCOPE_PROJECT
from logger import log
import app_state

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

            if event.get("event") in ["result", "process_exit", "read_error"]:
                self.is_done = True
            return event
        except asyncio.TimeoutError:
            return None

    async def aclose(self):
        """Clean up the turn reader task if still active."""
        if not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

class PersistentSession:
    """Represents a long-lived interactive agy CLI process."""
    def __init__(self, chat_id: str, model: str, project_dir: Optional[str] = None, conversation_id: str = ""):
        self.chat_id = chat_id
        self.model = model
        self.project_dir = project_dir
        self.process: Optional[asyncio.subprocess.Process] = None
        self.conversation_id: str = conversation_id
        self.lock = asyncio.Lock()
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

    async def start(self):
        """Spawn the background agy process in stream-json mode."""
        if self.is_alive():
            return

        bin_path = ANTIGRAVITY_BIN or find_antigravity_bin()
        if not bin_path:
            raise FileNotFoundError("Antigravity / agy binary not found.")

        cmd_args = [
            bin_path,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", self.model,
            "--print-timeout", "60m"
        ]

        if self.conversation_id:
            cmd_args.extend(["--conversation", self.conversation_id])

        is_admin_chat = is_admin(self.chat_id)
        if DANGEROUSLY_SKIP_PERMISSIONS and is_admin_chat:
            cmd_args.append("--dangerously-skip-permissions")

        cwd_dir = None
        if self.project_dir and os.path.isdir(self.project_dir):
            cwd_dir = self.project_dir
            cmd_args.extend(["--add-dir", self.project_dir])

        custom_env = os.environ.copy()
        custom_env["GIT_TERMINAL_PROMPT"] = "0"
        custom_env["DEBIAN_FRONTEND"] = "noninteractive"
        custom_env["GIT_ASKPASS"] = "echo"
        custom_env["PYTHONUNBUFFERED"] = "1"
        custom_env["STDOUT_LINE_BUFFERED"] = "1"

        log.info(f"[SessionPool] Spawning warm agy process for chat {self.chat_id} (model={self.model}, cwd={cwd_dir})")
        self.process = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
            cwd=cwd_dir,
            env=custom_env
        )
        self._decoder = codecs.getincrementaldecoder('utf-8')()
        self._line_buffer = ""
        self.last_active_time = time.time()
        app_state.running_processes[self.chat_id] = self.process

        # Start background stderr drain to prevent OS pipe buffer deadlock.
        # If stderr fills up (~64KB), the process blocks on write and stdout stalls too.
        self._stderr_drain_task = asyncio.create_task(self._drain_stderr())

    async def send_prompt_and_stream(self, prompt_text: str) -> TurnEventStream:
        """Send a prompt turn to the process stdin and return a TurnEventStream."""
        if not self.is_alive():
            await self.start()

        self.clear_recent_stderr()
        self.last_active_time = time.time()
        req = json.dumps({"event": "user", "message": {"content": prompt_text}}) + "\n"
        self.process.stdin.write(req.encode("utf-8"))
        await self.process.stdin.drain()

        queue = asyncio.Queue()
        reader_task = asyncio.create_task(self._read_turn_stdout(queue))
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

                    event_type = event_data.get("event")
                    if event_type == "init":
                        self.conversation_id = event_data.get("conversation_id", "")

                    await queue.put(event_data)

                    if event_type == "result":
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
        """Gracefully terminate the warm process."""
        self._closing = True
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
        app_state.running_processes.pop(self.chat_id, None)
        if proc:
            try:
                # Collect descendant pids to prevent orphaned commands (e.g. brew, shell tasks) from lingering
                descendant_pids = []
                try:
                    out = subprocess.check_output(["pgrep", "-P", str(proc.pid)], text=True).strip()
                    if out:
                        for p in out.split():
                            try:
                                descendant_pids.append(int(p))
                                sub_out = subprocess.check_output(["pgrep", "-P", p], text=True).strip()
                                if sub_out:
                                    descendant_pids.extend([int(sp) for sp in sub_out.split()])
                            except Exception:
                                pass
                except Exception:
                    pass

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
                    await asyncio.sleep(0.2)
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
            except Exception as e:
                log.warning(f"[SessionPool] Error terminating process for {self.chat_id}: {e}")

class SessionPool:
    """Manages active persistent sessions across chats."""
    def __init__(self):
        self._sessions: Dict[str, PersistentSession] = {}

    def get_or_create(self, chat_id: str, model: str, project_dir: Optional[str] = None, conversation_id: str = "") -> PersistentSession:
        sess = self._sessions.get(chat_id)
        # conversation_id 比对：只在"双方均非空且不同"时才视作需要重建
        # 避免传入空串（新会话/clear后）与已有真实 UUID 的 sess 不匹配，导致每轮都重建进程
        conv_mismatch = bool(conversation_id and sess and sess.conversation_id and sess.conversation_id != conversation_id)
        if sess is None or sess.model != model or sess.project_dir != project_dir or conv_mismatch or not sess.is_alive():
            if sess:
                asyncio.create_task(sess.close())
            sess = PersistentSession(chat_id, model, project_dir, conversation_id)
            self._sessions[chat_id] = sess
        return sess

    def update_conversation_id(self, chat_id: str, conversation_id: str):
        """Sync runtime assigned conversation ID into memory to maintain persistent session continuity."""
        if not chat_id or not conversation_id:
            return
        sess = self._sessions.get(chat_id)
        if sess:
            sess.conversation_id = conversation_id

    async def prewarm(self, chat_id: str, model: str, project_dir: Optional[str] = None, conversation_id: str = ""):
        """Asynchronously pre-spawns a warm process in the background so next message has 0s start latency."""
        try:
            sess = self.get_or_create(chat_id, model, project_dir, conversation_id)
            if not sess.is_alive():
                await sess.start()
        except Exception as e:
            log.warning(f"[SessionPool] Prewarm failed for chat {chat_id}: {e}")

    async def reset_session(self, chat_id: str):
        """Close and purge a chat's session."""
        sess = self._sessions.pop(chat_id, None)
        if sess:
            await sess.close()

session_pool = SessionPool()

