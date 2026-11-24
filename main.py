"""Antigravity Feishu Bot entrypoint."""
import asyncio
import base64
import http
import json
import os
import signal
import sys
import time

# Ensure common user-local bin paths are available under PM2 / non-login shells
_home = os.path.expanduser("~")
os.environ["PATH"] += (
    os.pathsep + os.path.join(_home, ".npm-global/bin")
    + os.pathsep + os.path.join(_home, ".local/bin")
)

# Strip transient Antigravity CLI session env vars that may have been leaked into
# PM2's saved environment if `pm2 restart --update-env` was run inside an agy session.
for _stale_env in (
    "ANTIGRAVITY_LS_VERSION",
    "ANTIGRAVITY_LS_ADDRESS",
    "ANTIGRAVITY_CSRF_TOKEN",
    "ANTIGRAVITY_CONVERSATION_ID",
    "ANTIGRAVITY_TRAJECTORY_ID",
    "ANTIGRAVITY_SOURCE_METADATA",
    "ANTIGRAVITY_AGENT",
):
    os.environ.pop(_stale_env, None)

import lark_oapi as lark

from config import APP_ID, APP_SECRET, BASE_DIR
from logger import log
from lark_client import send_reply_sdk
from garbage_collection import garbage_collector
import app_state
from handlers import do_p2_im_message_receive_v1, do_p2_card_action_trigger

# ---------------------------------------------------------------------------
# Patch: lark-oapi 1.6.8 ws client drops card-action frames.
# The WebSocket client returns early for MessageType.CARD, so card button
# callbacks (card.action.trigger) get no response and Feishu reports
# "出错了，请稍候重试 code:200671". Route CARD frames through the same event
# dispatcher as EVENT frames. Safe on newer SDK versions too (same envelope).
# ---------------------------------------------------------------------------
from lark_oapi.ws import client as _ws_client_mod
from lark_oapi.ws.const import (
    HEADER_BIZ_RT,
    HEADER_MESSAGE_ID,
    HEADER_SEQ,
    HEADER_SUM,
    HEADER_TRACE_ID,
    HEADER_TYPE,
)
from lark_oapi.ws.enum import MessageType as _WsMessageType
from lark_oapi.ws.model import Response as _WsResponse
from lark_oapi.core.json import JSON as _WsJSON
from lark_oapi.core.const import UTF_8 as _UTF8


def _safe_ws_header(headers, key: str, default: str = "") -> str:
    try:
        val = _ws_client_mod._get_by_key(headers, key)
        return val if val is not None else default
    except Exception:
        return default


async def _patched_handle_data_frame(self, frame):
    hs = getattr(frame, "headers", None) or []
    msg_id = _safe_ws_header(hs, HEADER_MESSAGE_ID, "")
    trace_id = _safe_ws_header(hs, HEADER_TRACE_ID, "")
    sum_ = _safe_ws_header(hs, HEADER_SUM, "1")
    seq = _safe_ws_header(hs, HEADER_SEQ, "0")
    type_ = _safe_ws_header(hs, HEADER_TYPE, "")

    pl = frame.payload
    resp = _WsResponse(code=http.HTTPStatus.OK)
    message_type = None
    try:
        total_parts = int(sum_) if (sum_ is not None and str(sum_).strip() != "") else 1
        seq_num = int(seq) if (seq is not None and str(seq).strip() != "") else 0
        if total_parts > 1:
            pl = self._combine(msg_id, total_parts, seq_num, pl)
            if pl is None:
                return

        try:
            message_type = _WsMessageType(type_)
        except ValueError:
            _ws_client_mod.logger.debug(
                self._fmt_log("Ignoring unsupported WS frame type: {}, msg_id: {}", type_, msg_id)
            )
            return

        start = int(round(time.time() * 1000))
        if message_type == _WsMessageType.CARD:
            _ws_client_mod.logger.info(
                self._fmt_log("CARD frame received, dispatching card callback, msg_id: {}", msg_id)
            )
            result = self._event_handler._do_without_validation(pl)
        elif message_type == _WsMessageType.EVENT:
            result = self._event_handler._do_without_validation(pl)
        else:
            return
        end = int(round(time.time() * 1000))
        header = hs.add()
        header.key = HEADER_BIZ_RT
        header.value = str(end - start)
        if result is not None:
            resp.data = base64.b64encode(_WsJSON.marshal(result).encode(_UTF8))
    except Exception as e:
        m_type_val = message_type.value if message_type is not None else str(type_)
        _ws_client_mod.logger.error(
            self._fmt_log(
                "handle message failed, message_type: {}, message_id: {}, trace_id: {}, err: {}",
                m_type_val,
                msg_id,
                trace_id,
                e,
            )
        )
        # 输出 payload 摘要，便于定位 CARD 帧结构与 SDK 预期不一致的问题
        try:
            preview = (pl or b"").decode("utf-8", errors="replace")[:300]
            _ws_client_mod.logger.error(f"payload preview: {preview}")
        except Exception:
            pass
        resp = _WsResponse(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)

    frame.payload = _WsJSON.marshal(resp).encode(_UTF8)
    await self._write_message(frame.SerializeToString())


_ws_client_mod.Client._handle_data_frame = _patched_handle_data_frame


async def main():
    app_state.main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")

    # Send post-update notification if applicable
    try:
        from database import get_and_clear_pending_update_notice
        from commands import get_version_string
        from cards.common import create_footer
        from lark_client import send_interactive_card_sdk, send_reply_sdk
        
        data = None
        pending_file = os.path.join(BASE_DIR, ".update_pending.json")
        if os.path.exists(pending_file):
            try:
                with open(pending_file, "r") as f:
                    data = json.load(f)
                os.remove(pending_file)
                try:
                    with open(os.path.join(BASE_DIR, ".update_buzzer_pending"), "w") as bf:
                        bf.write("pending")
                except Exception:
                    pass
            except Exception as e:
                log.error(f"Error reading .update_pending.json: {e}")

        if not data:
            data = await asyncio.get_running_loop().run_in_executor(None, get_and_clear_pending_update_notice)

        if data:
            message_id = data.get("message_id")
            old_ver = data.get("old_version", "")
            if message_id:
                new_ver = await asyncio.get_running_loop().run_in_executor(None, lambda: get_version_string("HEAD"))
                ver_info = f"从 ~`{old_ver}`~ 升级至 **`{new_ver}`**" if old_ver else f"**`{new_ver}`**"
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "green",
                        "title": {"content": "🎉 系统升级成功！", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"✅ **核心代码与服务组件已成功热升级！**\n\n> 🔖 **当前版本**：{ver_info}\n> 🔄 **运行状态**：后台 PM2 进程已完成自动重启与加载。\n\n系统所有功能与指令插件均已恢复，欢迎继续使用！"
                        },
                        create_footer()
                    ]
                }
                res = await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card))
                if not res:
                    fallback_text = f"🎉 系统升级成功！当前运行版本：{new_ver}"
                    await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, fallback_text))
                log.info(f"[PostUpdate] Notification sent to message_id {message_id}")
    except Exception as e:
        log.error(f"Failed to process post-update notification: {e}")

    # Register system commands & load plugins
    from plugin_manager import DEFAULT_SYSTEM_COMMANDS, plugin_manager
    plugin_manager.register_system_commands(DEFAULT_SYSTEM_COMMANDS)
    plugin_manager.load_all_plugins()

    # Start background GC task (keep strong reference to avoid premature GC)
    app_state._gc_task = asyncio.create_task(garbage_collector())

    # Start background Cron scheduler engine
    from cron_engine import cron_engine
    cron_engine.start()

    # Restore queued tasks persisted across a previous process lifetime
    await restore_pending_tasks()

    loop = asyncio.get_running_loop()
    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                _sig,
                lambda s=_sig: asyncio.create_task(_async_graceful_shutdown(s)),
            )
        except (NotImplementedError, RuntimeError):
            signal.signal(_sig, cleanup)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .register_p2_card_action_trigger(do_p2_card_action_trigger)
        .build()
    )

    cli = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    await loop.run_in_executor(None, cli.start)


async def restore_pending_tasks():
    """Re-queue tasks persisted in SQLite after a restart/crash.
    Interrupted tasks will be resumed with retry protection."""
    from database import delete_pending_task, save_pending_task, load_pending_tasks
    from handlers.pipeline import process_chat_queue
    from card_builder import CardBuilder
    from lark_client import send_interactive_card_sdk, patch_interactive_card_sdk

    tasks = await asyncio.get_running_loop().run_in_executor(None, load_pending_tasks)
    if not tasks:
        return

    recovered = {}
    loop = asyncio.get_running_loop()

    for chat_id, task, created_at in tasks:
        retry_count = task.get("retry_count", 0)
        message_id = task.get("message_id", "")
        bot_reply_msg_id = task.get("bot_reply_msg_id")

        if retry_count >= 2:
            # 连续多次重启仍然失败的任务，丢弃以防崩溃循环，并发送友好卡片通知
            await loop.run_in_executor(None, lambda c=chat_id, ts=created_at: delete_pending_task(c, ts))
            log.warning(f"[restore] Task for chat {chat_id} (msg {message_id}) exceeded max retry count ({retry_count}), dropping.")
            err_card = CardBuilder.build_ai_response(
                "⚠️ **任务未能恢复**\n\n该任务在执行过程中导致服务异常重启超过上限，系统已自动保护并终止该任务。如需继续请重新发送指令。",
                is_error=True
            )
            try:
                if bot_reply_msg_id:
                    await loop.run_in_executor(None, lambda m=bot_reply_msg_id, c=err_card: patch_interactive_card_sdk(m, c))
                elif message_id:
                    await loop.run_in_executor(None, lambda m=message_id, c=err_card: send_interactive_card_sdk(m, c))
            except Exception as e:
                log.error(f"[restore] Failed to send abort notification: {e}")
            continue

        # 标记为断点恢复任务，保持原始 created_at 主键稳定并递增重试计数后持久化
        task["created_at"] = created_at
        task["retry_count"] = retry_count + 1
        task["resumed"] = True
        await loop.run_in_executor(None, lambda c=chat_id, t=dict(task): save_pending_task(c, t))
        recovered.setdefault(chat_id, []).append(task)

    total = 0
    for chat_id, items in recovered.items():
        if chat_id not in app_state.chat_queues:
            app_state.chat_queues[chat_id] = asyncio.Queue()
        for t in items:
            await app_state.chat_queues[chat_id].put(t)
            total += 1
        if chat_id not in app_state.chat_workers or app_state.chat_workers[chat_id].done():
            app_state.chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))

    log.info(f"[restore] recovered {total} pending tasks across {len(recovered)} chats for automatic resumption")


_shutdown_in_progress = False


def _kill_tracked_subprocesses_snapshot():
    """Safely terminate all tracked subprocess groups over a snapshot copy without logging reentrancy."""
    procs = (
        app_state.snapshot_all_tracked_subprocesses()
        if hasattr(app_state, "snapshot_all_tracked_subprocesses")
        else list(app_state.running_processes.values())
    )
    for process in procs:
        if not process:
            continue
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


async def _async_graceful_shutdown(signum):
    """Event-loop safe graceful shutdown handler (avoids POSIX signal logging lock deadlocks)."""
    global _shutdown_in_progress
    if _shutdown_in_progress:
        return
    _shutdown_in_progress = True
    log.warning(f"Gracefully shutting down on signal {signum}... stopping schedulers and subprocesses")
    try:
        from cron_engine import cron_engine
        cron_engine.stop()
    except Exception:
        pass
    try:
        from plugin_manager import plugin_manager
        await asyncio.to_thread(plugin_manager.dispatch_service_restarting)
    except Exception:
        pass
    try:
        from session_pool import session_pool
        for cid in list(session_pool._sessions.keys()):
            try:
                await asyncio.wait_for(session_pool.reset_session(cid), timeout=2.0)
            except Exception:
                pass
    except Exception:
        pass
    _kill_tracked_subprocesses_snapshot()
    os._exit(0)


def cleanup(signum, frame):
    """Async-signal-safe fallback handler: never calls Python logging locks inside signal frame."""
    global _shutdown_in_progress
    if _shutdown_in_progress:
        os._exit(0)
    _shutdown_in_progress = True
    try:
        os.write(2, b"[FeishuBot] Signal received, terminating subprocess groups...\n")
    except Exception:
        pass
    _kill_tracked_subprocesses_snapshot()
    os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    asyncio.run(main())
