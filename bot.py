import asyncio
import json
import subprocess
import sys
import os

os.environ["PATH"] += os.pathsep + "/Users/YOUR_USERNAME/.npm-global/bin" + os.pathsep + "/Users/YOUR_USERNAME/.local/bin"

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

async def handle_message(message_id, user_text):
    print(f"\n[User]: {user_text}", flush=True)

    # State 1: Received
    r_id = await set_emoji(message_id, "StatusReading")
    await asyncio.sleep(1) # Show it briefly
    await delete_emoji(message_id, r_id)

    # State 2: Spinner (Thinking -> Typing -> Mac -> Coffee)
    # The user wanted a status showing "Operating, executing commands or coding"
    # We cycle through these emojis to show active work
    spinner_task = asyncio.create_task(emoji_spinner(message_id, ["THINKING", "Typing", "Mac", "Communicate"]))

    # Call antigravity CLI non-interactively
    process = await asyncio.create_subprocess_exec(
        "/Users/YOUR_USERNAME/.local/bin/antigravity", "-p", user_text, "--dangerously-skip-permissions", "--model", "Gemini 3.5 Flash",
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
    is_error = False
    if not reply_text:
        reply_text = stderr.decode().strip() or "Sorry, I couldn't generate a response."
        is_error = True

    print(f"[Agent]: {reply_text[:100]}...", flush=True)
    
    # State 3: Done or Error
    if is_error:
        await set_emoji(message_id, "CrossMark")
    else:
        await set_emoji(message_id, "DONE")

    # Send reply back via lark-cli
    reply_proc = await asyncio.create_subprocess_exec(
        "lark-cli", "im", "+messages-reply", 
        "--message-id", message_id,
        "--text", reply_text,
        "--as", "bot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await reply_proc.communicate()

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
            event = json.loads(line)
            message_id = event.get("message_id")
            content_raw = event.get("content", "{}")
            
            try:
                content_json = json.loads(content_raw)
                user_text = content_json.get("text", "")
            except:
                user_text = content_raw

            if not message_id or not user_text:
                continue

            # Run message handling fully asynchronously!
            asyncio.create_task(handle_message(message_id, user_text))

        except Exception as e:
            print(f"Error processing event: {e}", flush=True)

if __name__ == "__main__":
    asyncio.run(process_lark_events())
