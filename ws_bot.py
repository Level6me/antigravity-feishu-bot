import asyncio
import json
import subprocess
import os
import uuid

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse

APP_ID = "***REDACTED_APPID***"
APP_SECRET = "***REDACTED***"

SESSION_FILE = "chat_sessions.json"

def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_sessions(sessions):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

async def set_emoji(message_id, emoji_type):
    process = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "reactions", "create", 
        "--message-id", message_id,
        "--data", f'{{"reaction_type":{{"emoji_type":"{emoji_type}"}}}}',
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    try:
        data = json.loads(stdout.decode())
        return data.get("data", {}).get("reaction_id")
    except:
        return None

async def delete_emoji(message_id, reaction_id):
    if not reaction_id: return
    process = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "reactions", "delete", 
        "--message-id", message_id,
        "--reaction-id", reaction_id,
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

async def emoji_spinner(message_id, emojis):
    current_idx = 0
    r_id = await set_emoji(message_id, emojis[current_idx])
    try:
        while True:
            await asyncio.sleep(4)
            await delete_emoji(message_id, r_id)
            current_idx = (current_idx + 1) % len(emojis)
            r_id = await set_emoji(message_id, emojis[current_idx])
    except asyncio.CancelledError:
        await delete_emoji(message_id, r_id)
        raise

async def send_reply(message_id, reply_text):
    reply_proc = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "+messages-reply", 
        "--message-id", message_id,
        "--text", reply_text,
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await reply_proc.communicate()
    if reply_proc.returncode != 0:
        print(f"[Error send_reply] {stderr.decode()}", flush=True)

async def handle_message_async(message_id, chat_id, message_type, content_raw):
    try:
        content_json = json.loads(content_raw)
    except:
        content_json = {}

    user_text = ""
    if message_type == "text":
        user_text = content_json.get("text", "") if content_json.get("text") else content_raw
        user_text = user_text.strip()
    elif message_type == "image":
        image_key = content_json.get("image_key", "")
        if not image_key:
            import re
            match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
            if match:
                image_key = match.group(0)

        if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
            image_key = content_raw[8:-1]
        
        if image_key:
            output_filename = f"img_{image_key}.jpg"
            output_path = os.path.abspath(output_filename)
            dl_proc = await asyncio.create_subprocess_exec(
                "lark-cli", "im", "+messages-resources-download",
                "--message-id", message_id,
                "--file-key", image_key,
                "--type", "image",
                "--output", output_filename,
                "--as", "bot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await dl_proc.communicate()
            user_text = f"请查看这张图片并做出回应。图片路径: {output_path}"
        else:
            user_text = "[未获取到图片]"
    elif message_type == "post":
        texts = []
        image_keys = []
        for line in content_json.get("content", []):
            for elem in line:
                if elem.get("tag") == "text":
                    texts.append(elem.get("text", ""))
                elif elem.get("tag") == "img":
                    image_keys.append(elem.get("image_key", ""))
        
        user_text = " ".join(texts)
        if image_keys:
            image_key = image_keys[0]
            output_filename = f"img_{image_key}.jpg"
            output_path = os.path.abspath(output_filename)
            dl_proc = await asyncio.create_subprocess_exec(
                "lark-cli", "im", "+messages-resources-download",
                "--message-id", message_id,
                "--file-key", image_key,
                "--type", "image",
                "--output", output_filename,
                "--as", "bot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await dl_proc.communicate()
            user_text += f"\n[附加图片路径: {output_path}]"
    else:
        user_text = f"[暂不支持的消息类型: {message_type}]"

    if not user_text:
        return

    print(f"\n[User {chat_id}]: {user_text}", flush=True)

    sessions = load_sessions()
    if chat_id not in sessions:
        sessions[chat_id] = {"conversation": uuid.uuid4().hex, "model": "Gemini 3.5 Flash"}
    
    session_data = sessions[chat_id]

    if user_text.startswith("/clear"):
        sessions[chat_id]["conversation"] = uuid.uuid4().hex
        save_sessions(sessions)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await send_reply(message_id, reply_text)
        return
    elif user_text.startswith("/role "):
        new_role = user_text.split(" ", 1)[1].strip()
        sessions[chat_id]["role"] = new_role
        save_sessions(sessions)
        
        role_prompt = f"[System Instruction: For the rest of this conversation, you must adopt the following persona/role: {new_role}. Please just say 'Role accepted: {new_role}']"
        await asyncio.create_subprocess_exec(
            "/Users/YOUR_USERNAME/.local/bin/antigravity", "-p", role_prompt, 
            "--dangerously-skip-permissions", 
            "--model", session_data["model"],
            "--conversation", session_data["conversation"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
        )
        
        reply_text = f"🎭 角色设定成功！我将以「{new_role}」的身份与您对话。"
        await send_reply(message_id, reply_text)
        return
    elif user_text.startswith("/help"):
        reply_text = """💡 **Antigravity 机器人使用指南**

🔹 `/model` : 弹出交互式控制面板，自由切换大模型
🔹 `/role <角色设定>` : 让机器人扮演特定角色 (例如: `/role 资深Python工程师`)
🔹 `/clear` : 清空当前对话的上下文记忆，重新开始
🔹 `/help` : 显示此帮助菜单

*提示: 机器人会自动下载您发送的图片，您可以直接发图并提问！*"""
        await send_reply(message_id, reply_text)
        return
    elif user_text.startswith("/model") or user_text.startswith("/card") or user_text.startswith("/menu"):
        # Fetch available models dynamically
        fetch_proc = await asyncio.create_subprocess_exec(
            "/Users/YOUR_USERNAME/.local/bin/antigravity", "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        stdout, _ = await fetch_proc.communicate()
        models_output = stdout.decode().strip()
        
        # Parse models (assuming one model per line)
        available_models = [line.strip() for line in models_output.split('\n') if line.strip()]
        
        # Fallback if command fails
        if not available_models:
            available_models = ["Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)", "GPT-OSS 120B (Medium)"]
            
        actions = []
        for i, model_name in enumerate(available_models[:10]): # Limit to 10 buttons to prevent payload overflow
            button_type = "primary" if i == 0 else "default"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": model_name},
                "type": button_type,
                "value": {"action": "switch_model", "model": model_name}
            })

        card_content = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"content": "🤖 机器人控制面板", "tag": "plain_text"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**当前正在使用的模型**: {session_data.get('model', 'Default')}\n**可用模型列表**（点击下方按钮快速切换）："
                },
                {
                    "tag": "action",
                    "actions": actions
                }
            ]
        }
        
        reply_proc = await asyncio.create_subprocess_exec(
            "lark-cli", "im", "+messages-reply", 
            "--message-id", message_id,
            "--msg-type", "interactive",
            "--content", json.dumps(card_content),
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await reply_proc.communicate()
        return

    save_sessions(sessions)

    r_id = await set_emoji(message_id, "StatusReading")
    await asyncio.sleep(1)
    await delete_emoji(message_id, r_id)

    spinner_task = asyncio.create_task(emoji_spinner(message_id, ["THINKING", "Typing", "Mac", "Communicate"]))

    cmd_args = [
        "/Users/YOUR_USERNAME/.local/bin/antigravity", 
        "-p", user_text, 
        "--dangerously-skip-permissions", 
        "--model", session_data["model"],
        "--conversation", session_data["conversation"]
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    stdout, stderr = await process.communicate()
    
    spinner_task.cancel()
    try:
        await spinner_task
    except asyncio.CancelledError:
        pass
    
    reply_text = stdout.decode().strip()
    import re
    reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
    
    is_error = False
    if not reply_text:
        reply_text = stderr.decode().strip() or "Sorry, I couldn't generate a response."
        is_error = True

    if not is_error:
        current_model = session_data.get('model', 'Default')
        current_role = session_data.get('role', '无')
        reply_text += f"\n\n---\n*🤖 模型: {current_model} | 🎭 角色: {current_role} | 💡 键入 /help 查看指令*"

    print(f"[Agent]: {reply_text[:100]}...", flush=True)
    
    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")

    await send_reply(message_id, reply_text)


main_loop = None

def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    
    # Offload to asyncio event loop
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), main_loop)
    else:
        print("Error: main_loop is not running!")


def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    print(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    
    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        sessions = load_sessions()
        if chat_id not in sessions:
            sessions[chat_id] = {"model": new_model}
        else:
            sessions[chat_id]["model"] = new_model
        save_sessions(sessions)

        print(f"Switched model to {new_model} in chat {chat_id}")

        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"模型已成功切换为 {new_model}！"}})
    
    return P2CardActionTriggerResponse()


async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("Starting Lark WS Client...", flush=True)
    
    # Initialize dispatcher
    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .register_p2_card_action_trigger(do_p2_card_action_trigger) \
        .build()

    # Start WebSocket client
    cli = lark.ws.Client(
        APP_ID, 
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG
    )
    
    # Start cli in a separate thread so it doesn't block the async event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)

if __name__ == "__main__":
    asyncio.run(main())
