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


async def _patched_handle_data_frame(self, frame):
    hs = frame.headers
    msg_id = _ws_client_mod._get_by_key(hs, HEADER_MESSAGE_ID)
    trace_id = _ws_client_mod._get_by_key(hs, HEADER_TRACE_ID)
    sum_ = _ws_client_mod._get_by_key(hs, HEADER_SUM)
    seq = _ws_client_mod._get_by_key(hs, HEADER_SEQ)
    type_ = _ws_client_mod._get_by_key(hs, HEADER_TYPE)

    pl = frame.payload
    if int(sum_) > 1:
        pl = self._combine(msg_id, int(sum_), int(seq), pl)
        if pl is None:
            return

    message_type = _WsMessageType(type_)
    resp = _WsResponse(code=http.HTTPStatus.OK)
    try:
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
        _ws_client_mod.logger.error(
            self._fmt_log(
                "handle message failed, message_type: {}, message_id: {}, trace_id: {}, err: {}",
                message_type.value,
                msg_id,
                trace_id,
                e,
            )
        )
        # 输出 payload 摘要，便于定位 CARD 帧结构与 SDK 预期不一致的问题
        try:
            preview = pl.decode("utf-8", errors="replace")[:300]
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
    from plugin_manager import plugin_manager
    plugin_manager.register_system_commands([
        "/help", "/model", "/card", "/menu", "/project", "/note", "/notes",
        "/status", "/context", "/quota", "/clear", "/stop", "/update", "/ping",
        "/newproj_resolve", "/cron", "/schedule", "/plugin", "/plugins", "/user"
    ])
    plugin_manager.load_all_plugins()

    # Start background GC task
    asyncio.create_task(garbage_collector())

    # Start background Cron scheduler engine
    from cron_engine import cron_engine
    cron_engine.start()

    # Restore queued tasks persisted across a previous process lifetime
    await restore_pending_tasks()

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

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)


async def restore_pending_tasks():
    """Re-queue tasks persisted in SQLite after a restart/crash.
    Tasks whose message was already processed (dedup table) are dropped."""
    from database import delete_pending_task, is_message_seen, load_pending_tasks
    from handlers.pipeline import process_chat_queue

    tasks = load_pending_tasks()
    if not tasks:
        return

    recovered = {}
    for chat_id, task, created_at in tasks:
        if is_message_seen(task.get("message_id", "")):
            delete_pending_task(chat_id, created_at)
            continue
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

    log.info(f"[restore] recovered {total} pending tasks across {len(recovered)} chats")


def cleanup(signum, frame):
    log.warning("Gracefully shutting down... killing zombie processes")
    for process in app_state.running_processes.values():
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.error(f"Failed to kill process group {process.pid}: {e}")
            try:
                process.kill()
            except Exception:
                pass
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    asyncio.run(main())
