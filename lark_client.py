import json
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody, PatchMessageRequest,
    PatchMessageRequestBody, GetMessageResourceRequest, CreateMessageReactionRequest,
    CreateMessageReactionRequestBody, Emoji, DeleteMessageReactionRequest
)
from config import APP_ID, APP_SECRET
from logger import log
from utils import with_retry

api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

@with_retry()
def set_emoji_sdk(message_id, emoji_type):
    try:
        req = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(CreateMessageReactionRequestBody.builder() \
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build()) \
                .build()) \
            .build()
        resp = api_client.im.v1.message.reaction.create(req)
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
        resp = api_client.im.v1.message.reaction.delete(req)
        if resp.code != 0:
            log.error(f"[delete_emoji_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[delete_emoji_sdk] Error: {e}")


@with_retry()
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
        log.error(f"[send_reply_sdk] Failed: {resp.msg}")

@with_retry()
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

@with_retry()
def patch_interactive_card_sdk(message_id, card_content):
    req = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(PatchMessageRequestBody.builder() \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.patch(req)
    if resp.code != 0:
        log.error(f"[patch_interactive_card_sdk] Failed: {resp.msg}")

@with_retry(max_retries=5, initial_delay=2.0)
def download_message_resource_sdk(message_id, file_key, resource_type, output_path):
    """
    Downloads a message resource (image, file, audio, media) using the official SDK.
    """
    req = GetMessageResourceRequest.builder() \
        .message_id(message_id) \
        .file_key(file_key) \
        .type(resource_type) \
        .build()
    
    resp = api_client.im.v1.message_resource.get(req)
    
    if resp.code == 0:
        try:
            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.file.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            return True
        except Exception as e:
            log.error(f"[download_message_resource_sdk] Error saving file: {e}")
            return False
    else:
        log.error(f"[download_message_resource_sdk] Failed: {resp.msg}")
        return False
