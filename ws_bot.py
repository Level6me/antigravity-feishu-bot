import asyncio
import json
import subprocess
import os
import uuid
import re

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse

APP_ID = "***REDACTED_APPID***"
APP_SECRET = "***REDACTED***"

SESSION_FILE = "chat_sessions.json"
main_loop = None

api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def send_reply_sdk(message_id, reply_text):
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder() \
            .msg_type("text") \
            .content(json.dumps({"text": reply_text})) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        print(f"[Error send_reply_sdk] {resp.msg}", flush=True)

def send_interactive_card_sdk(message_id, card_content):
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder() \
            .msg_type("interactive") \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        print(f"[Error send_interactive_card_sdk] {resp.msg}", flush=True)
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except:
        return None

def patch_interactive_card_sdk(message_id, card_content):
    req = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(PatchMessageRequestBody.builder() \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.patch(req)
    if resp.code != 0:
        print(f"[Error patch_interactive_card_sdk] {resp.msg}", flush=True)


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

# emoji_spinner removed

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

async def send_interactive_card(message_id, card_content):
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

async def handle_message_async(message_id, chat_id, message_type, content_raw):
    try:
        await _handle_message_async_internal(message_id, chat_id, message_type, content_raw)
    except Exception as e:
        import traceback
        print(f"[FATAL ERROR in handle_message_async]: {e}")
        traceback.print_exc()

async def _handle_message_async_internal(message_id, chat_id, message_type, content_raw):
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
    elif message_type in ["file", "audio", "media"]:
        file_key = content_json.get("file_key", "")
        file_name = content_json.get("file_name", "")
        
        if file_key:
            if not file_name:
                if message_type == "audio":
                    file_name = f"audio_{file_key}.ogg"
                elif message_type == "media":
                    file_name = f"video_{file_key}.mp4"
                else:
                    file_name = f"file_{file_key}"
            
            # Force .mp4 for media to fix Gemini API unsupported mime type
            if message_type == "media" and not file_name.lower().endswith(".mp4"):
                file_name = file_key + ".mp4"
            if message_type == "audio" and "." not in file_name:
                file_name = file_key + ".ogg"
            
            output_path = os.path.abspath(file_name)
            dl_proc = await asyncio.create_subprocess_exec(
                "lark-cli", "im", "+messages-resources-download",
                "--message-id", message_id,
                "--file-key", file_key,
                "--type", "file",
                "--output", file_name,
                "--as", "bot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await dl_proc.communicate()
            
            if message_type == "file":
                user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
            elif message_type == "audio":
                user_text = f"请仔细听这段语音内容（语音文件路径: {output_path}），并做出响应。"
            elif message_type == "media":
                user_text = f"请仔细观看这段视频内容（视频文件路径: {output_path}），并做出响应。"
        else:
            user_text = f"[未获取到{message_type}的资源键]"
    else:
        user_text = f"[暂不支持的消息类型: {message_type}]"

    if not user_text:
        return

    print(f"\n[User {chat_id}]: {user_text}", flush=True)

    sessions = load_sessions()
    if chat_id not in sessions:
        sessions[chat_id] = {"conversation": "", "model": "Gemini 3.5 Flash"}
    
    session_data = sessions[chat_id]

    if user_text.startswith("/clear"):
        sessions[chat_id]["conversation"] = uuid.uuid4().hex
        save_sessions(sessions)
        reply_text = "🔄 上下文已清空，开启新对话！"
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return
    elif user_text.startswith("/role "):
        new_role = user_text.split(" ", 1)[1].strip()
        sessions[chat_id]["role"] = new_role
        save_sessions(sessions)
        
        # Override the user_text to let the normal flow handle it, ensuring conversation ID is captured!
        user_text = f"请记住以下设定，并在接下来的对话中始终扮演这个角色：{new_role}。收到请回复：'好的，角色设定已生效！'"
        # Continue to standard flow instead of returning early
    elif user_text.startswith("/help"):
        reply_text = """💡 **Antigravity 机器人使用指南**

🔹 `/model` : 弹出交互式控制面板，自由切换大模型
🔹 `/role <角色设定>` : 让机器人扮演特定角色 (例如: `/role 资深Python工程师`)
🔹 `/clear` : 清空当前对话的上下文记忆，重新开始
🔹 `/help` : 显示此帮助菜单

*提示: 机器人会自动下载您发送的图片，您可以直接发图并提问！*"""
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(message_id, reply_text))
        return
    elif user_text.startswith("/model") or user_text.startswith("/card") or user_text.startswith("/menu"):
        fetch_proc = await asyncio.create_subprocess_exec(
            "/Users/YOUR_USERNAME/.local/bin/antigravity", "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        stdout, _ = await fetch_proc.communicate()
        models_output = stdout.decode().strip()
        
        available_models = [line.strip() for line in models_output.split('\n') if line.strip()]
        if not available_models:
            available_models = ["Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)", "GPT-OSS 120B (Medium)"]
            
        actions = []
        for i, model_name in enumerate(available_models[:10]):
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
                    "layout": "flow",
                    "actions": actions
                }
            ]
        }
        await asyncio.get_running_loop().run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))
        return

    save_sessions(sessions)

    r_id = await set_emoji(message_id, "StatusReading")
    await asyncio.sleep(1)
    await delete_emoji(message_id, r_id)

    # Spinner removed; rely on typing indicator stream

    # Inject protocol into prompt
    system_instruction = "[System Rule: If you need the user to make a choice, format your options inside [CHOICE_CARD] Q: <Question> \n - <Option1> \n - <Option2> [/CHOICE_CARD] tags. NEVER ask normal text multi-choice questions.]\n\n"
    final_prompt = system_instruction + user_text

    log_file_path = f"agy_log_{uuid.uuid4().hex}.txt"
    cmd_args = [
        "/Users/YOUR_USERNAME/.local/bin/antigravity", 
        "-p", final_prompt, 
        "--dangerously-skip-permissions", 
        "--model", session_data["model"],
        "--log-file", log_file_path
    ]
    if session_data.get("conversation"):
        cmd_args.extend(["--conversation", session_data["conversation"]])
    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=subprocess.DEVNULL
    )
    
    init_card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "✨ AI 思考中...", "tag": "plain_text"}
        },
        "elements": [{"tag": "markdown", "content": "正在为您生成回复..."}]
    }
    loop = asyncio.get_running_loop()
    bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, init_card))
    
    accumulated_text = ""
    stderr_text = ""
    
    async def read_stdout():
        nonlocal accumulated_text
        while True:
            chunk = await process.stdout.read(64)
            if not chunk:
                break
            accumulated_text += chunk.decode(errors='ignore')
            
    async def read_stderr():
        nonlocal stderr_text
        while True:
            chunk = await process.stderr.read(64)
            if not chunk:
                break
            stderr_text += chunk.decode(errors='ignore')

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    
    last_update_text = ""
    
    while process.returncode is None:
        await asyncio.sleep(0.5)
        if accumulated_text != last_update_text:
            last_update_text = accumulated_text
            clean_text = re.sub(r'\[CHOICE_CARD\].*', '', accumulated_text, flags=re.DOTALL)
            clean_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', clean_text, flags=re.DOTALL)
            clean_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', clean_text)
            if clean_text.strip():
                patch_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "blue",
                        "title": {"content": "✨ AI 输出中...", "tag": "plain_text"}
                    },
                    "elements": [{"tag": "markdown", "content": clean_text + " ✍️"}]
                }
                if bot_reply_msg_id:
                    await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, patch_card))
        if stdout_task.done() and stderr_task.done():
            break

    await process.wait()
    await stdout_task
    await stderr_task
    
    # Spinner cancellation removed
    
    reply_text = accumulated_text.strip()
    reply_text = re.sub(r'^Warning: conversation ".*?" not found\.?\r?\n*', '', reply_text).strip()
    reply_text = re.sub(r'\[Message\] timestamp=.*?content=.*?(?=\n\n|\Z)', '', reply_text, flags=re.DOTALL).strip()
    
    # Parse log file for conversation ID
    if os.path.exists(log_file_path):
        with open(log_file_path, "r") as f:
            log_content = f.read()
        match = re.search(r'(?:Created|found) conversation ([0-9a-fA-F-]+)', log_content)
        if match:
            new_conv_id = match.group(1)
            if session_data.get("conversation") != new_conv_id:
                sessions[chat_id]["conversation"] = new_conv_id
                save_sessions(sessions)
        os.remove(log_file_path)

    
    is_error = False
    if not reply_text:
        reply_text = stderr_text.strip() or "Sorry, I couldn't generate a response."
        is_error = True

    # Parse for [CHOICE_CARD]
    choice_card_data = None
    if not is_error:
        choice_pattern = re.compile(r'\[CHOICE_CARD\]\s*Q:\s*(.*?)\n(.*?)\s*\[/CHOICE_CARD\]', re.DOTALL | re.IGNORECASE)
        match = choice_pattern.search(reply_text)
        if match:
            question = match.group(1).strip()
            options_text = match.group(2).strip()
            options = [opt.strip()[1:].strip() if opt.strip().startswith('-') else opt.strip() for opt in options_text.split('\n') if opt.strip()]
            
            # Remove the block from the text reply
            reply_text = choice_pattern.sub('', reply_text).strip()
            choice_card_data = {
                "question": question,
                "options": options
            }

    elements = []
    
    if reply_text:
        print(f"[Agent text]: {reply_text[:100]}...", flush=True)
        elements.append({
            "tag": "markdown",
            "content": reply_text
        })
        
    if choice_card_data and choice_card_data["options"]:
        if reply_text:
            elements.append({"tag": "hr"})
            
        actions = []
        markdown_options = []
        is_long_options = any(len(opt) > 15 for opt in choice_card_data["options"])
        
        for i, opt in enumerate(choice_card_data["options"][:10]):
            prefix_match = re.match(r'^([a-zA-Z0-9\u4e00-\u9fa5]+)[:：.、]\s*(.*)$', opt)
            
            if prefix_match:
                prefix = prefix_match.group(1).strip()
                rest_text = prefix_match.group(2).strip()
                
                if len(prefix) == 1 and prefix.encode('utf-8').isalpha():
                    btn_label = f"选项 {prefix}"
                elif len(prefix) <= 4:
                    btn_label = prefix
                else:
                    btn_label = f"选项 {i+1}"
            else:
                btn_label = f"选项 {i+1}"
                rest_text = opt
            
            if not is_long_options:
                btn_label = opt[:50]
                
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn_label},
                "type": "default",
                "value": {"action": "user_choice", "choice": opt, "label": btn_label}
            })
            if is_long_options:
                markdown_options.append(f"- **{btn_label}**: {rest_text}")

        question_text = f"**{choice_card_data['question']}**"
        if is_long_options:
            question_text += "\n\n" + "\n".join(markdown_options)
            
        elements.append({
            "tag": "markdown",
            "content": question_text
        })
        elements.append({
            "tag": "action",
            "layout": "flow",
            "actions": actions
        })

    if not is_error:
        current_model = session_data.get('model', 'Default')
        current_role = session_data.get('role', '无')
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"🤖 模型: {current_model} | 🎭 角色: {current_role} | 💡 键入 /help 查看指令"
                }
            ]
        })

    if elements:
        card_content = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue" if not is_error else "red",
                "title": {"content": "✨ AI 回复" if not is_error else "❌ 发生错误", "tag": "plain_text"}
            },
            "elements": elements
        }
        if bot_reply_msg_id:
            await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(bot_reply_msg_id, card_content))
        else:
            await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, card_content))

    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")

def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    message_id = data.event.message.message_id
    chat_id = data.event.message.chat_id
    message_type = data.event.message.message_type
    content_raw = data.event.message.content
    
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(handle_message_async(message_id, chat_id, message_type, content_raw), main_loop)
    else:
        print("Error: main_loop is not running!")

def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    print(f"Card action received: {data.event.action.value}")
    
    action_value = data.event.action.value
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id
    
    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        sessions = load_sessions()
        if chat_id not in sessions:
            sessions[chat_id] = {"model": new_model}
        else:
            sessions[chat_id]["model"] = new_model
        save_sessions(sessions)

        print(f"Switched model to {new_model} in chat {chat_id}")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已成功切换为 {new_model}！"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice")
        label = action_value.get("label", choice)
        print(f"User selected choice: {choice}")
        
        if main_loop and main_loop.is_running():
            async def notify_and_process():
                # Notify the user visually in the chat
                user_display_text = f"👉 您已选择：**{label}**\n*(详细内容: {choice})*"
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                
                # Send the choice to the LLM backend
                simulated_content = json.dumps({"text": f"我的选择是：{choice}"})
                await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(notify_and_process(), main_loop)
            
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已确认：{label[:15]}"}})
    
    return P2CardActionTriggerResponse()

async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("Starting Lark WS Client...", flush=True)
    
    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .register_p2_card_action_trigger(do_p2_card_action_trigger) \
        .build()

    cli = lark.ws.Client(
        APP_ID, 
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG
    )
    
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cli.start)

if __name__ == "__main__":
    asyncio.run(main())
