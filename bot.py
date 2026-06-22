import asyncio
import json
import subprocess
import sys
import os
import uuid

os.environ["PATH"] += os.pathsep + "/Users/YOUR_USERNAME/.npm-global/bin" + os.pathsep + "/Users/YOUR_USERNAME/.local/bin"

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

async def handle_message(message_id, chat_id, message_type, content_raw):
    # Parse content
    print(f"DEBUG message_type={message_type} content_raw={content_raw}", flush=True)
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
        # Handle rich text post (extract both text and images)
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
            image_key = image_keys[0] # Grab first image for now
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

    # State management
    sessions = load_sessions()
    if chat_id not in sessions:
        sessions[chat_id] = {"conversation": uuid.uuid4().hex, "model": "Gemini 3.5 Flash"}
    
    session_data = sessions[chat_id]

    # Slash commands
    if user_text.startswith("/clear"):
        sessions[chat_id]["conversation"] = uuid.uuid4().hex
        save_sessions(sessions)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await send_reply(message_id, reply_text)
        return
    elif user_text.startswith("/model "):
        new_model = user_text.split(" ", 1)[1].strip()
        sessions[chat_id]["model"] = new_model
        save_sessions(sessions)
        reply_text = f"⚙️ 模型已切换为: {new_model}"
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

🔹 `/model <模型名>` : 切换当前大模型 (例如: `/model Claude 3.5 Sonnet`)
🔹 `/role <角色设定>` : 让机器人扮演特定角色 (例如: `/role 资深Python工程师`)
🔹 `/clear` : 清空当前对话的上下文记忆，重新开始
🔹 `/help` : 显示此帮助菜单

*提示: 机器人会自动下载您发送的图片，您可以直接发图并提问！*"""
        await send_reply(message_id, reply_text)
        return

    save_sessions(sessions)

    # State 1: Received
    r_id = await set_emoji(message_id, "StatusReading")
    await asyncio.sleep(1) # Show it briefly
    await delete_emoji(message_id, r_id)

    # State 2: Spinner (Thinking -> Typing -> Mac -> Coffee)
    spinner_task = asyncio.create_task(emoji_spinner(message_id, ["THINKING", "Typing", "Mac", "Communicate"]))

    # Call antigravity CLI non-interactively
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
        # Add footer (小尾巴)
        current_model = session_data.get('model', 'Default')
        current_role = session_data.get('role', '无')
        reply_text += f"\n\n---\n*🤖 模型: {current_model} | 🎭 角色: {current_role} | 💡 键入 /help 查看指令*"

    print(f"[Agent]: {reply_text[:100]}...", flush=True)
    
    # State 3: Done or Error
    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")

    await send_reply(message_id, reply_text)

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

async def process_lark_events():
    print("Starting Lark Event Consumer...", flush=True)
    process = await asyncio.create_subprocess_exec(
        "lark-cli", "event", "consume", "im.message.receive_v1", "--as", "bot",
        stdin=subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    print("Bot is ready and listening!", flush=True)
    
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        
        line = line.decode().strip()
        if not line:
            continue
            
        try:
            event_payload = json.loads(line)
            
            # Handle both wrapped and unwrapped event structures
            event_obj = event_payload.get("event", event_payload)
            msg_obj = event_obj.get("message", event_obj)
            
            message_id = msg_obj.get("message_id")
            chat_id = msg_obj.get("chat_id")
            message_type = msg_obj.get("message_type", "text")
            content_raw = msg_obj.get("content", "{}")

            if not message_id or not chat_id:
                continue

            # Run message handling fully asynchronously!
            asyncio.create_task(handle_message(message_id, chat_id, message_type, content_raw))

        except Exception as e:
            print(f"Error processing event: {e}", flush=True)

    # If we broke out, check if process exited
    stderr_output = await process.stderr.read()
    print(f"Lark Event Consumer exited. Stderr: {stderr_output.decode()}", flush=True)

if __name__ == "__main__":
    asyncio.run(process_lark_events())
