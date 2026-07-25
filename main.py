"""Antigravity Feishu Bot entrypoint."""
import asyncio
import json
import os
import signal
import sys

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


async def main():
    app_state.main_loop = asyncio.get_running_loop()
    log.info("Starting Lark WS Client...")

    # Send post-update notification if applicable
    pending_file = os.path.join(BASE_DIR, ".update_pending.json")
    if os.path.exists(pending_file):
        try:
            with open(pending_file, "r") as f:
                data = json.load(f)
            os.remove(pending_file)
            msg_id = data.get("message_id")
            if msg_id:
                from commands import get_version_string
                v_str = get_version_string("HEAD")
                text = f"✨ 升级完毕！系统已成功重新上线。\n当前运行版本：{v_str}"
                asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(msg_id, text))
                log.info(f"Sent post-update notification to {msg_id}")
        except Exception as e:
            log.error(f"Failed to process post-update notification: {e}")

    # Start background GC task
    asyncio.create_task(garbage_collector())

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
        log_level=lark.LogLevel.DEBUG,
    )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)


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
