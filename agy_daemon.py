#!/usr/bin/env python3
"""
Antigravity (agy) keep-alive daemon.

Purpose:
  Keep a long-lived `agy` process warm so quota/LSP probing and first-token
  latency stay stable. The main bot (main.py) still launches one-shot
  `agy -p ...` subprocesses per request; this daemon is optional infrastructure.

Configuration (env / .env, via config.py):
  ANTIGRAVITY_BIN   – absolute path to agy binary (auto-detected if empty)
  ANTIGRAVITY_HOME  – antigravity-cli data root

Run:
  python agy_daemon.py
  # or via PM2 (install.sh): pm2 start venv/bin/python3 --name agy-daemon -- agy_daemon.py
"""

from __future__ import annotations

import os
import signal
import sys
import time

from config import ANTIGRAVITY_BIN, get_antigravity_home
from logger import log

# Restart policy
RESTART_DELAY_SEC = float(os.environ.get("AGY_DAEMON_RESTART_DELAY", "3"))
MAX_BACKOFF_SEC = float(os.environ.get("AGY_DAEMON_MAX_BACKOFF", "60"))


class AgyDaemon:
    def __init__(self):
        self._child = None
        self._stopping = False
        self._backoff = RESTART_DELAY_SEC

    def _resolve_bin(self) -> str:
        bin_path = ANTIGRAVITY_BIN
        if not bin_path or not os.path.exists(bin_path):
            # Late re-detect in case PATH was updated after import
            from config import find_antigravity_bin
            bin_path = find_antigravity_bin()
        if not bin_path or not os.path.exists(bin_path):
            raise FileNotFoundError(
                "agy/antigravity binary not found. "
                "Install Antigravity CLI or set ANTIGRAVITY_BIN in .env"
            )
        return bin_path

    def _spawn(self):
        import pexpect

        bin_path = self._resolve_bin()
        home = get_antigravity_home()
        os.makedirs(home, exist_ok=True)

        env = os.environ.copy()
        env.setdefault("HOME", os.path.expanduser("~"))
        # Prefer non-interactive behavior when possible
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["DEBIAN_FRONTEND"] = "noninteractive"

        log.info(f"[agy-daemon] spawning: {bin_path} (ANTIGRAVITY_HOME={home})")
        self._child = pexpect.spawn(
            bin_path,
            env=env,
            encoding="utf-8",
            timeout=None,
            echo=False,
        )
        self._backoff = RESTART_DELAY_SEC
        return self._child

    def _terminate_child(self):
        child = self._child
        if not child:
            return
        try:
            if child.isalive():
                child.terminate(force=False)
                time.sleep(0.5)
            if child.isalive():
                child.terminate(force=True)
        except Exception as e:
            log.warning(f"[agy-daemon] terminate error: {e}")
        finally:
            self._child = None

    def stop(self, signum=None, frame=None):
        log.info(f"[agy-daemon] received signal {signum}, shutting down...")
        self._stopping = True
        self._terminate_child()

    def run(self):
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        log.info("[agy-daemon] starting keep-alive loop")
        while not self._stopping:
            try:
                child = self._spawn()
            except FileNotFoundError as e:
                log.error(f"[agy-daemon] {e}")
                log.error("[agy-daemon] will retry after backoff")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, MAX_BACKOFF_SEC)
                continue
            except Exception as e:
                log.error(f"[agy-daemon] spawn failed: {e}")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, MAX_BACKOFF_SEC)
                continue

            try:
                # Drain stdout/stderr so the child never blocks on a full pipe.
                while not self._stopping and child.isalive():
                    try:
                        line = child.readline()
                    except Exception:
                        break
                    if not line:
                        # EOF or idle tick
                        if not child.isalive():
                            break
                        time.sleep(0.2)
                        continue
                    # Keep logs light — only surface non-empty useful lines
                    stripped = line.strip()
                    if stripped:
                        log.debug(f"[agy] {stripped[:500]}")
            except KeyboardInterrupt:
                self._stopping = True

            exit_status = None
            try:
                if child and child.isalive():
                    self._terminate_child()
                elif child is not None:
                    exit_status = child.exitstatus
            except Exception:
                pass

            if self._stopping:
                break

            log.warning(
                f"[agy-daemon] child exited (status={exit_status}), "
                f"restarting in {self._backoff:.1f}s"
            )
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SEC)

        log.info("[agy-daemon] exited cleanly")
        return 0


def main():
    daemon = AgyDaemon()
    code = daemon.run()
    sys.exit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    main()
