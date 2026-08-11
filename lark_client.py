import json
import os
import asyncio
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody, PatchMessageRequest,
    PatchMessageRequestBody, GetMessageResourceRequest, CreateMessageRequest,
    CreateMessageRequestBody, CreateMessageReactionRequest, GetChatRequest,
    CreateMessageReactionRequestBody, Emoji, DeleteMessageReactionRequest
)
from lark_oapi.api.contact.v3 import GetUserRequest
from config import APP_ID, APP_SECRET
from logger import log
from utils import with_retry

api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).timeout(10.0).build()

@with_retry()
def set_emoji_sdk(message_id, emoji_type):
    try:
        req = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(CreateMessageReactionRequestBody.builder() \
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build()) \
                .build()) \
            .build()
        resp = api_client.im.v1.message_reaction.create(req)
        if resp.code == 0:
            return json.loads(resp.raw.content).get("data", {}).get("reaction_id")
        else:
            log.error(f"[set_emoji_sdk] Failed: {resp.msg}")
            return None
    except Exception as e:
        log.error(f"[set_emoji_sdk] Error: {e}")
        return None

@with_retry()
def delete_emoji_sdk(message_id, reaction_id):
    if not reaction_id:
        return
    try:
        req = DeleteMessageReactionRequest.builder() \
            .message_id(message_id) \
            .reaction_id(reaction_id) \
            .build()
        resp = api_client.im.v1.message_reaction.delete(req)
        if resp.code != 0:
            log.error(f"[delete_emoji_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[delete_emoji_sdk] Error: {e}")


def split_text_into_chunks(text, max_chunk_size=20000):
    if not text or len(text) <= max_chunk_size:
        return [text]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    lines = text.split("\n")
    for line in lines:
        line_len = len(line) + 1
        if current_length + line_len > max_chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_len
        else:
            current_chunk.append(line)
            current_length += line_len
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    return chunks


@with_retry(max_retries=1)
def send_reply_sdk(message_id, reply_text):
    if not reply_text:
        return
        
    max_text_chars = 20000
    if len(reply_text) <= max_text_chars:
        chunks = [reply_text]
    else:
        raw_chunks = split_text_into_chunks(reply_text, max_text_chars)
        total = len(raw_chunks)
        chunks = [f"📄 **[第 {idx+1}/{total} 页]**\n\n{c}" for idx, c in enumerate(raw_chunks)]

    for chunk in chunks:
        req = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder() \
                .msg_type("text") \
                .content(json.dumps({"text": chunk})) \
                .build()) \
            .build()
        resp = api_client.im.v1.message.reply(req)
        if resp.code != 0:
            log.error(f"[send_reply_sdk] Failed: {resp.msg}")

@with_retry(max_retries=1)
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
        log.error(f"[send_interactive_card_sdk] Failed: {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except:
        return None

@with_retry(max_retries=4, initial_delay=0.6)
def patch_interactive_card_sdk(message_id, card_content):
    req = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(PatchMessageRequestBody.builder() \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.patch(req)
    if resp.code != 0:
        log.error(f"[patch_interactive_card_sdk] Failed (code {resp.code}): {resp.msg}")
        raise Exception(f"Feishu Patch API error code {resp.code}: {resp.msg}")
    return True

@with_retry(max_retries=5, initial_delay=2.0)
def download_message_resource_sdk(message_id, file_key, resource_type, output_path):
    """
    Downloads a message resource (image, file, audio, media) using the official SDK.
    Writes to a temp file first, then atomically renames on success so a failed
    download never leaves a partial file at output_path.
    """
    import tempfile
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    req = GetMessageResourceRequest.builder() \
        .message_id(message_id) \
        .file_key(file_key) \
        .type(resource_type) \
        .build()

    resp = api_client.im.v1.message_resource.get(req)

    if resp.code != 0:
        log.error(f"[download_message_resource_sdk] Failed: {resp.msg}")
        return False

    tmp_fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".dl_tmp_")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            while True:
                chunk = resp.file.read(8192)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        log.error(f"[download_message_resource_sdk] Error saving file: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Outbound messages to a chat (used by auth flows / admin notifications)
# ---------------------------------------------------------------------------

@with_retry(max_retries=1)
def send_card_to_chat_sdk(chat_id, card_content):
    """Send an interactive card as a NEW message to a chat (not a reply)."""
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card_content))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_card_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def send_text_to_chat_sdk(chat_id, text):
    """Send a plain text message as a NEW message to a chat."""
    if text and len(text) > 28000:
        text = text[:28000] + "\n\n（内容过长已截断）"
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_text_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


def get_chat_name_sdk(chat_id):
    """Resolve a group chat display name (empty on failure)."""
    try:
        req = GetChatRequest.builder().chat_id(chat_id).build()
        resp = api_client.im.v1.chat.get(req)
        # GetChatResponse.data 本身就是群信息对象，name 为其直接字段
        if resp.code == 0 and resp.data:
            return resp.data.name or ""
        log.error(f"[get_chat_name_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[get_chat_name_sdk] Error: {e}")
    return ""


def get_user_name_sdk(open_id):
    """Resolve a user display name by open_id (empty on failure)."""
    if not open_id:
        return ""
    try:
        req = GetUserRequest.builder().user_id_type("open_id").user_id(open_id).build()
        resp = api_client.contact.v3.user.get(req)
        if resp.code == 0 and resp.data and resp.data.user:
            return resp.data.user.name or ""
        log.error(f"[get_user_name_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[get_user_name_sdk] Error: {e}")
    return ""


async def send_card_to_chat_async(chat_id, card_content):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, card_content))


async def send_text_to_chat_async(chat_id, text):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: send_text_to_chat_sdk(chat_id, text))


async def get_chat_name_async(chat_id):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_chat_name_sdk(chat_id))


async def get_user_name_async(open_id):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_user_name_sdk(open_id))
