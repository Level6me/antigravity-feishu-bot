import json
import lark_oapi as lark
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody, PatchMessageRequest, PatchMessageRequestBody
from config import APP_ID, APP_SECRET

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
