"""System Updater Plugin for antigravity-feishu-bot."""

import sys
import os
import glob
import json
import tempfile
import threading
import asyncio
import subprocess

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from plugin_base import BasePlugin
from cards import CardBuilder
from config import BASE_DIR, GITEE_MIRROR_URL
from lark_client import send_reply_sdk, send_interactive_card_sdk
from logger import log


def _atomic_write_text(filepath: str, content: str) -> None:
    parent = os.path.dirname(os.path.abspath(filepath)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".upd_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def _spawn_detached_pm2_restart(service_name: str = "feishu-bot") -> None:
    """Spawn pm2 restart in its own session and reap exit status on a daemon thread to prevent zombies."""
    proc = subprocess.Popen(
        ["pm2", "restart", service_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    def _reap():
        try:
            proc.wait(timeout=15)
        except Exception:
            pass
    threading.Thread(target=_reap, name="pm2-restart-reaper", daemon=True).start()


class SystemUpdaterPlugin(BasePlugin):

    def initialize(self):
        log.info(f"[Plugin:{self.plugin_id}] System Updater plugin initialized.")

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        if command.lower() == "/update":
            clean_args = (args or "").strip().lower()

            if clean_args in ["confirm", "restart", "force"]:
                from commands import get_version_string
                from database import save_pending_update_notice

                old_version = await asyncio.to_thread(get_version_string, "HEAD")

                reply_text = "⬇️ 正在执行核心系统升级，请勿中断..."
                await asyncio.to_thread(send_reply_sdk, message_id, reply_text)

                custom_env = os.environ.copy()
                custom_env["GIT_TERMINAL_PROMPT"] = "0"
                custom_env["DEBIAN_FRONTEND"] = "noninteractive"
                custom_env["GIT_ASKPASS"] = "echo"

                def _perform_sync_upgrade() -> str:
                    orig_head = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True, timeout=5, env=custom_env, cwd=BASE_DIR
                    ).stdout.strip()

                    status_res = subprocess.run(
                        ["git", "status", "--porcelain", "-uno"],
                        capture_output=True, text=True, timeout=10, env=custom_env, cwd=BASE_DIR
                    )
                    has_local_changes = bool(status_res.stdout.strip())
                    stashed = False
                    if has_local_changes:
                        stash_res = subprocess.run(
                            ["git", "-c", "user.name=bot", "-c", "user.email=bot@local", "stash", "push", "-m", "system_updater_auto_stash"],
                            capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR
                        )
                        if stash_res.returncode != 0:
                            raise RuntimeError(
                                f"检测到本地未提交修改但自动暂存 (git stash) 失败，为防止丢失本地代码已中止升级：{(stash_res.stderr or stash_res.stdout).strip()[:200]}"
                            )
                        stashed = "Saved working directory" in (stash_res.stdout + stash_res.stderr)

                    def _rollback_to_orig():
                        subprocess.run(
                            ["git", "reset", "--hard", orig_head],
                            capture_output=True, text=True, check=False, timeout=10, env=custom_env, cwd=BASE_DIR
                        )
                        if stashed:
                            subprocess.run(
                                ["git", "stash", "pop"],
                                capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR
                            )

                    try:
                        try:
                            subprocess.run(
                                ["git", "pull", "--rebase", "origin", "main"],
                                capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR
                            )
                        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as pull_err:
                            log.warning(f"Pull from origin failed, trying Gitee fallback: {pull_err}")
                            subprocess.run(
                                ["git", "rebase", "--abort"],
                                capture_output=True, text=True, check=False, timeout=5, env=custom_env, cwd=BASE_DIR
                            )
                            if not GITEE_MIRROR_URL:
                                raise
                            subprocess.run(
                                ["git", "pull", "--rebase", GITEE_MIRROR_URL, "main"],
                                capture_output=True, text=True, check=True, timeout=30, env=custom_env, cwd=BASE_DIR
                            )
                    except Exception:
                        _rollback_to_orig()
                        raise

                    conflict_hint = ""
                    if stashed:
                        pop_res = subprocess.run(
                            ["git", "stash", "pop"],
                            capture_output=True, text=True, check=False, timeout=15, env=custom_env, cwd=BASE_DIR
                        )
                        if pop_res.returncode != 0:
                            log.warning(f"git stash pop encountered conflicts: {pop_res.stderr}; keeping stash and clean upstream HEAD")
                            subprocess.run(
                                ["git", "reset", "--hard", "HEAD"],
                                capture_output=True, text=True, check=False, timeout=10, env=custom_env, cwd=BASE_DIR
                            )
                            conflict_hint = "\n\n⚠️ 本地未提交修改与新版本冲突，原改动已安全保留在 `git stash` 中，当前采用最新官方代码启动。"

                    py_targets = [
                        p for p in glob.glob(os.path.join(BASE_DIR, "**", "*.py"), recursive=True)
                        if not os.path.relpath(p, BASE_DIR).startswith(("venv" + os.sep, ".git" + os.sep, "tests" + os.sep))
                    ]
                    compile_res = subprocess.run(
                        [sys.executable, "-m", "py_compile", *py_targets],
                        capture_output=True, text=True, timeout=25, cwd=BASE_DIR
                    )
                    if compile_res.returncode != 0:
                        err_detail = (compile_res.stderr or compile_res.stdout or "syntax error").strip()[:300]
                        log.error(f"py_compile failed after update: {err_detail}, rolling back to {orig_head}")
                        _rollback_to_orig()
                        raise RuntimeError(f"新版本语法校验未通过，已安全回滚至原版本 `{orig_head[:7]}`：{err_detail}")

                    pip_bin = os.path.join(BASE_DIR, "venv", "bin", "pip")
                    if os.path.exists(pip_bin):
                        pip_cmd = [pip_bin, "install", "-r", os.path.join(BASE_DIR, "requirements.txt")]
                        pip_res = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=60, cwd=BASE_DIR)
                        if pip_res.returncode != 0:
                            pip_err = (pip_res.stderr or pip_res.stdout or "pip install error").strip()[:300]
                            log.error(f"pip install failed after update: {pip_err}, rolling back to {orig_head}")
                            _rollback_to_orig()
                            raise RuntimeError(f"新版本依赖安装失败，已安全回滚至原版本 `{orig_head[:7]}`：{pip_err}")

                    save_pending_update_notice(chat_id, message_id, old_version)
                    pending_file = os.path.join(BASE_DIR, ".update_pending.json")
                    try:
                        _atomic_write_text(
                            pending_file,
                            json.dumps({"chat_id": chat_id, "message_id": message_id, "old_version": old_version}, ensure_ascii=False),
                        )
                    except Exception as ex:
                        log.error(f"Failed to write .update_pending.json: {ex}")

                    buzzer_pending_file = os.path.join(BASE_DIR, ".update_buzzer_pending")
                    try:
                        _atomic_write_text(buzzer_pending_file, "pending")
                    except Exception as ex:
                        log.error(f"Failed to write .update_buzzer_pending: {ex}")

                    return conflict_hint

                try:
                    conflict_hint = await asyncio.to_thread(_perform_sync_upgrade)
                    reply_text = "🔄 系统升级就绪，正在触发自启进程，预计 3 秒后重新上线..." + conflict_hint
                    await asyncio.to_thread(send_reply_sdk, message_id, reply_text)
                    _spawn_detached_pm2_restart("feishu-bot")
                except Exception as e:
                    log.error(f"Failed to apply update: {e}")
                    error_text = f"❌ 升级执行失败: {e}"
                    await asyncio.to_thread(send_reply_sdk, message_id, error_text)
                return True

            reply_text = "🔍 正在从云端拉取最新版本信息，请稍候..."
            await asyncio.to_thread(send_reply_sdk, message_id, reply_text)

            custom_env = os.environ.copy()
            custom_env["GIT_TERMINAL_PROMPT"] = "0"
            custom_env["DEBIAN_FRONTEND"] = "noninteractive"
            custom_env["GIT_ASKPASS"] = "echo"

            def _check_update_sync():
                try:
                    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=True, timeout=10, env=custom_env, cwd=BASE_DIR)
                    remote_ref = "origin/main"
                except Exception as e:
                    if GITEE_MIRROR_URL:
                        subprocess.run(["git", "fetch", GITEE_MIRROR_URL, "main"], capture_output=True, text=True, check=True, timeout=15, env=custom_env, cwd=BASE_DIR)
                        remote_ref = "FETCH_HEAD"
                    else:
                        raise e

                from commands import get_version_string
                local_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()
                remote_hash = subprocess.run(["git", "rev-parse", "--short", remote_ref], capture_output=True, text=True, timeout=5, env=custom_env, cwd=BASE_DIR).stdout.strip()

                local_version_str = get_version_string("HEAD")
                remote_version_str = get_version_string(remote_ref)

                if local_hash == remote_hash:
                    return CardBuilder.build_no_update_card(local_version_str)
                changelog_cmd = ["git", "log", f"{local_hash}..{remote_ref}", "--pretty=format:- %s"]
                changelog = subprocess.run(changelog_cmd, capture_output=True, text=True, timeout=10, cwd=BASE_DIR).stdout.strip() or "- 未知更新"
                return CardBuilder.build_update_card(local_version_str, remote_version_str, changelog)

            try:
                card = await asyncio.to_thread(_check_update_sync)
                await asyncio.to_thread(send_interactive_card_sdk, message_id, card)
            except Exception as ex:
                log.error(f"System update error: {ex}")
                await asyncio.to_thread(send_reply_sdk, message_id, f"❌ 检查更新异常: {ex}")
            return True
        return False
