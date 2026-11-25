import atexit
import json
import os
import time
import asyncio
import weakref
import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody, PatchMessageRequest,
    PatchMessageRequestBody, GetMessageResourceRequest, CreateMessageRequest,
    CreateMessageRequestBody, CreateMessageReactionRequest, GetChatRequest,
    CreateMessageReactionRequestBody, Emoji, DeleteMessageReactionRequest,
    CreateFileRequest, CreateFileRequestBody
)
from lark_oapi.api.contact.v3 import GetUserRequest
import config
from config import APP_ID, APP_SECRET
from logger import log
from utils import with_retry, NonRetryableError


def _compact_json(payload) -> str:
    """Compact UTF-8 JSON serializer (eliminates 6-byte \\uXXXX CJK escaping & whitespace, ~45% smaller cards)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Native Async HTTP Connection Pool & Feishu CardKit 2.0 Streaming Engine
# ---------------------------------------------------------------------------
_ASYNC_HTTP_CLIENTS = {}  # {loop_id: (weakref(loop), httpx.AsyncClient)}
_TENANT_TOKEN_CACHE = {"token": "", "expire_at": 0.0}
_TENANT_TOKEN_LOCK_MAP = {}
_CARDKIT_CAPABILITY_STATE = {"supported": None}  # None=unprobed, True=ok, False=permission_missing


def _close_all_async_http_clients():
    for _, (_, client) in list(_ASYNC_HTTP_CLIENTS.items()):
        try:
            if not client.is_closed:
                # Best-effort underlying transport close at interpreter shutdown
                pass
        except Exception:
            pass
    _ASYNC_HTTP_CLIENTS.clear()


atexit.register(_close_all_async_http_clients)


def _get_async_http_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    # Prune dead loops
    for k, (lref, cl) in list(_ASYNC_HTTP_CLIENTS.items()):
        lobj = lref()
        if lobj is None or lobj.is_closed():
            _ASYNC_HTTP_CLIENTS.pop(k, None)
    entry = _ASYNC_HTTP_CLIENTS.get(lid)
    if entry is not None and not entry[1].is_closed:
        return entry[1]
    client = httpx.AsyncClient(
        base_url="https://open.feishu.cn",
        timeout=httpx.Timeout(10.0, connect=5.0),
        limits=httpx.Limits(max_keepalive_connections=16, max_connections=32, keepalive_expiry=60.0),
    )
    _ASYNC_HTTP_CLIENTS[lid] = (weakref.ref(loop), client)
    return client


async def _get_tenant_access_token_async() -> str:
    app_id = getattr(config, "APP_ID", "") or APP_ID
    app_secret = getattr(config, "APP_SECRET", "") or APP_SECRET
    if not app_id or not app_secret:
        return ""
    now = time.time()
    if _TENANT_TOKEN_CACHE["token"] and now < _TENANT_TOKEN_CACHE["expire_at"] - 120:
        return _TENANT_TOKEN_CACHE["token"]

    loop = asyncio.get_running_loop()
    lock = _TENANT_TOKEN_LOCK_MAP.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _TENANT_TOKEN_LOCK_MAP[id(loop)] = lock

    async with lock:
        now = time.time()
        if _TENANT_TOKEN_CACHE["token"] and now < _TENANT_TOKEN_CACHE["expire_at"] - 120:
            return _TENANT_TOKEN_CACHE["token"]
        client = _get_async_http_client()
        resp = await client.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        data = resp.json()
        if data.get("code") == 0 and data.get("tenant_access_token"):
            tok = data["tenant_access_token"]
            exp = float(data.get("expire") or 7200)
            _TENANT_TOKEN_CACHE["token"] = tok
            _TENANT_TOKEN_CACHE["expire_at"] = now + exp
            return tok
        log.warning(f"[AsyncFeishu] Failed fetching tenant_access_token: {data}")
        return ""


async def async_patch_interactive_card(message_id: str, card_content: dict, max_retries: int = 3) -> bool:
    """Non-blocking async Feishu card PATCH over persistent keep-alive pool (zero ThreadPoolExecutor blocking)."""
    token = await _get_tenant_access_token_async()
    if not token:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: patch_interactive_card_sdk(message_id, card_content))

    client = _get_async_http_client()
    body_bytes = _compact_json({"content": _compact_json(card_content)}).encode("utf-8")
    delay = 0.4
    for attempt in range(max_retries + 1):
        try:
            resp = await client.patch(
                f"/open-apis/im/v1/messages/{message_id}",
                content=body_bytes,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            )
            data = resp.json()
            code = data.get("code", -1)
            if code == 0:
                return True
            msg = data.get("msg", "")
            if code in (99991663, 99991661):  # token expired
                _TENANT_TOKEN_CACHE["expire_at"] = 0.0
                token = await _get_tenant_access_token_async()
            elif _is_non_retryable_card_error(code, msg):
                raise NonRetryableError(f"Feishu Patch API non-retryable error code {code}: {msg}")
            if attempt == max_retries:
                raise RuntimeError(f"Feishu Patch API error code {code}: {msg}")
        except NonRetryableError:
            raise
        except Exception as e:
            if attempt == max_retries:
                raise
            log.debug(f"[AsyncFeishu] patch retry {attempt + 1}/{max_retries}: {e}")
        await asyncio.sleep(delay)
        delay *= 1.8
    return False


class CardKitStreamController:
    """Feishu CardKit 2.0 Native 60fps Streaming Controller with zero-risk automatic fallback."""

    def __init__(self, message_id: str, chat_id: str = ""):
        self.message_id = message_id
        self.chat_id = chat_id
        self.card_id: str = ""
        self.bot_reply_msg_id: str = ""
        self.seq: int = 1
        self.active: bool = False

    @staticmethod
    def can_attempt() -> bool:
        if not getattr(config, "ENABLE_CARDKIT_STREAMING", True):
            return False
        if _CARDKIT_CAPABILITY_STATE["supported"] is False:
            return False
        app_id = getattr(config, "APP_ID", "") or APP_ID
        app_secret = getattr(config, "APP_SECRET", "") or APP_SECRET
        return bool(app_id and app_secret)

    async def create_and_send(self, init_card_v1: dict) -> str:
        """Create a CardKit 2.0 streaming card entity and reply to `message_id` (or send to `chat_id`).
        Returns `bot_reply_msg_id` on success, or empty string if unsupported/failed (triggering transparent fallback).
        """
        if not self.can_attempt():
            return ""
        try:
            from cards.common import convert_card_v1_to_v2
            token = await _get_tenant_access_token_async()
            if not token:
                return ""
            client = _get_async_http_client()
            v2_card = convert_card_v1_to_v2(init_card_v1, stream_element_id="stream_md", streaming_mode=True)
            create_resp = await client.post(
                "/open-apis/cardkit/v1/cards",
                json={"type": "card_json", "data": _compact_json(v2_card)},
                headers={"Authorization": f"Bearer {token}"},
            )
            c_data = create_resp.json()
            c_code = c_data.get("code", -1)
            if c_code != 0:
                # Permission missing (e.g. 99991672 cardkit:card:write not enabled) -> cache & fallback cleanly
                if c_code in (99991672, 99991668) or "permission" in str(c_data.get("msg", "")).lower():
                    _CARDKIT_CAPABILITY_STATE["supported"] = False
                    log.info(
                        "[CardKit] App lacks cardkit:card:write permission; falling back to async HTTP PATCH "
                        "(enable cardkit:card:write in Feishu console to unlock native 60fps streaming)."
                    )
                else:
                    log.debug(f"[CardKit] create_card returned code={c_code}: {c_data.get('msg')}")
                return ""

            card_id = (c_data.get("data") or {}).get("card_id") or ""
            if not card_id:
                return ""

            card_ref_content = _compact_json({"type": "card", "data": {"card_id": card_id}})
            if self.message_id and str(self.message_id).startswith("om_"):
                send_resp = await client.post(
                    f"/open-apis/im/v1/messages/{self.message_id}/reply",
                    json={"msg_type": "interactive", "content": card_ref_content},
                    headers={"Authorization": f"Bearer {token}"},
                )
            elif self.chat_id:
                send_resp = await client.post(
                    "/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json={"receive_id": self.chat_id, "msg_type": "interactive", "content": card_ref_content},
                    headers={"Authorization": f"Bearer {token}"},
                )
            else:
                return ""

            s_data = send_resp.json()
            if s_data.get("code") == 0:
                msg_id = (s_data.get("data") or {}).get("message_id") or ""
                if msg_id:
                    _CARDKIT_CAPABILITY_STATE["supported"] = True
                    self.card_id = card_id
                    self.bot_reply_msg_id = msg_id
                    self.active = True
                    return msg_id
        except Exception as e:
            log.debug(f"[CardKit] create_and_send fallback: {e}")
        return ""

    async def stream_card(self, indicator_card_v1: dict) -> bool:
        """Stream markdown delta/full text into `stream_md` element via CardKit Streaming API."""
        if not self.active or not self.card_id:
            return False
        try:
            from cards.common import extract_primary_markdown_from_card
            token = await _get_tenant_access_token_async()
            if not token:
                return False
            md_text = extract_primary_markdown_from_card(indicator_card_v1)
            self.seq += 1
            client = _get_async_http_client()
            resp = await client.put(
                f"/open-apis/cardkit/v1/cards/{self.card_id}/elements/stream_md/content",
                json={"content": md_text, "sequence": self.seq},
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.json().get("code") == 0
        except Exception as e:
            log.debug(f"[CardKit] stream_card warning: {e}")
            return False

    async def finalize(self, final_card_v1: dict) -> bool:
        """Close `streaming_mode` and commit full final Card 2.0 (including interactive buttons & context bar)."""
        if not self.active or not self.card_id:
            return False
        try:
            from cards.common import convert_card_v1_to_v2
            token = await _get_tenant_access_token_async()
            if not token:
                return False
            client = _get_async_http_client()
            self.seq += 1
            await client.patch(
                f"/open-apis/cardkit/v1/cards/{self.card_id}/settings",
                json={
                    "settings": _compact_json({"config": {"streaming_mode": False}}),
                    "sequence": self.seq,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            v2_final = convert_card_v1_to_v2(final_card_v1, stream_element_id="stream_md", streaming_mode=False)
            self.seq += 1
            upd_resp = await client.put(
                f"/open-apis/cardkit/v1/cards/{self.card_id}",
                json={
                    "card": {"type": "card_json", "data": _compact_json(v2_final)},
                    "sequence": self.seq,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            ok = upd_resp.json().get("code") == 0
            self.active = False
            return ok
        except Exception as e:
            log.warning(f"[CardKit] finalize failed, caller will send fallback reply: {e}")
            self.active = False
            return False


def _is_non_retryable_card_error(code, msg: str) -> bool:
    """Return True for deterministic Feishu card payload/schema validation errors."""
    msg_str = str(msg or "")
    msg_lower = msg_str.lower()
    return (
        code == 230099
        or "11310" in msg_str
        or "the card contains images" in msg_lower
        or "invalid card" in msg_lower
    )

api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).timeout(10.0).build()
upload_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).timeout(120.0).build()

@with_retry()
def set_emoji_sdk(message_id, emoji_type):
    candidates = [emoji_type]
    if emoji_type in ("PAUSE", "Pause", "⏸️", "⏸"):
        candidates = ["PAUSE", "Pause", "OnIt", "Coffee"]
    try:
        last_msg = ""
        for cand in candidates:
            req = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(CreateMessageReactionRequestBody.builder() \
                    .reaction_type(Emoji.builder().emoji_type(cand).build()) \
                    .build()) \
                .build()
            resp = api_client.im.v1.message_reaction.create(req)
            if resp.code == 0:
                return json.loads(resp.raw.content).get("data", {}).get("reaction_id")
            last_msg = resp.msg
        log.error(f"[set_emoji_sdk] Failed for {emoji_type}: {last_msg}")
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
        # 硬边界兜底：若单行无换行文本本身超过 max_chunk_size，按定长切片拆分，防止单块突破飞书 20KB 限制
        if len(line) > max_chunk_size:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            for start_idx in range(0, len(line), max_chunk_size):
                sub_line = line[start_idx:start_idx + max_chunk_size]
                if len(sub_line) == max_chunk_size:
                    chunks.append(sub_line)
                else:
                    current_chunk = [sub_line]
                    current_length = len(sub_line) + 1
            continue

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


def _build_paginated_text_chunks(text, max_chunk_size=20000):
    """Split long text into bounded chunks with page headers when paginated."""
    if not text:
        return []
    if len(text) <= max_chunk_size:
        return [text]
    header_reserve = min(64, max(16, max_chunk_size // 10))
    body_budget = max(16, max_chunk_size - header_reserve)
    raw_chunks = split_text_into_chunks(text, body_budget)
    total = len(raw_chunks)
    return [f"📄 **[第 {idx + 1}/{total} 页]**\n\n{c}" for idx, c in enumerate(raw_chunks)]


@with_retry(max_retries=1)
def send_reply_sdk(message_id, reply_text):
    if not reply_text:
        return

    chunks = _build_paginated_text_chunks(reply_text, 20000)

    for chunk in chunks:
        req = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder() \
                .msg_type("text") \
                .content(_compact_json({"text": chunk})) \
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
            .content(_compact_json(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[send_interactive_card_sdk] Failed: {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except Exception as e:
        log.warning(f"[send_interactive_card_sdk] Failed to parse response message_id: {e}")
        return None

@with_retry(max_retries=4, initial_delay=0.6)
def patch_interactive_card_sdk(message_id, card_content):
    req = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(PatchMessageRequestBody.builder() \
            .content(_compact_json(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.patch(req)
    if resp.code != 0:
        log.error(f"[patch_interactive_card_sdk] Failed (code {resp.code}): {resp.msg}")
        if _is_non_retryable_card_error(resp.code, resp.msg):
            raise NonRetryableError(f"Feishu Patch API non-retryable error code {resp.code}: {resp.msg}")
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
            .content(_compact_json(card_content))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_card_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def send_text_to_chat_sdk(chat_id, text):
    """Send a plain text message (paginated if >20k chars) as NEW message(s) to a chat."""
    if not text:
        return True
    all_ok = True
    for chunk in _build_paginated_text_chunks(text, 20000):
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(_compact_json({"text": chunk}))
                .build()) \
            .build()
        resp = api_client.im.v1.message.create(req)
        if resp.code != 0:
            log.error(f"[send_text_to_chat_sdk] Failed: {resp.msg}")
            all_ok = False
    return all_ok


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


# ---------------------------------------------------------------------------
# File & Voice transfer helpers using Lark OpenAPI
# ---------------------------------------------------------------------------

@with_retry(max_retries=2)
def upload_file_sdk(file_path, file_type=None, duration_ms=None):
    """上传本地文件到飞书，返回 file_key（失败返回 None）。
    支持 file_type: 'stream', 'pdf', 'doc', 'xls', 'ppt', 'mp4', 'opus'
    """
    if not os.path.exists(file_path):
        log.error(f"[upload_file_sdk] File not found: {file_path}")
        return None

    if not file_type:
        ext = os.path.splitext(file_path)[1].lower()
        ext_map = {
            ".opus": "opus",
            ".mp4": "mp4",
            ".pdf": "pdf",
            ".doc": "doc", ".docx": "doc",
            ".xls": "xls", ".xlsx": "xls",
            ".ppt": "ppt", ".pptx": "ppt"
        }
        file_type = ext_map.get(ext, "stream")

    try:
        with open(file_path, "rb") as f:
            builder = CreateFileRequestBody.builder() \
                .file_type(file_type) \
                .file_name(os.path.basename(file_path)) \
                .file(f)
            if duration_ms is not None:
                builder.duration(int(duration_ms))
            req = CreateFileRequest.builder().request_body(builder.build()).build()
            resp = upload_client.im.v1.file.create(req)

        if resp.code == 0:
            data = json.loads(resp.raw.content).get("data", {})
            return data.get("file_key")
        log.error(f"[upload_file_sdk] Failed (code {resp.code}): {resp.msg}")
        return None
    except Exception as e:
        log.error(f"[upload_file_sdk] Error: {e}")
        return None


@with_retry(max_retries=1)
def send_file_to_chat_sdk(chat_id, file_key):
    """向会话发送原生文件消息。"""
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_file_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def reply_file_sdk(message_id, file_key):
    """回复消息：发送原生文件卡片。"""
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[reply_file_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def send_voice_to_chat_sdk(chat_id, file_key):
    """向会话发送原生语音消息（可直接在聊天窗口播放的语音条）。"""
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("audio")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_voice_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def reply_voice_sdk(message_id, file_key):
    """回复消息：发送原生语音条消息（带波形与时长，点击直接播放）。"""
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
            .msg_type("audio")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[reply_voice_sdk] Failed: {resp.msg}")
        return False
    return True


def send_local_file_to_chat(chat_id, file_path, file_type=None, caption=None, duration_ms=None):
    """组合工具：直接上传本地文件并通过 Lark API 发送到指定会话。"""
    file_key = upload_file_sdk(file_path, file_type=file_type, duration_ms=duration_ms)
    if not file_key:
        return False
    is_audio = (file_type == "opus") or file_path.lower().endswith(".opus")
    ok = send_voice_to_chat_sdk(chat_id, file_key) if is_audio else send_file_to_chat_sdk(chat_id, file_key)
    if ok and caption:
        send_text_to_chat_sdk(chat_id, caption)
    return ok


def send_local_file_as_reply(message_id, file_path, file_type=None, caption=None, duration_ms=None):
    """组合工具：直接上传本地文件并通过 Lark API 回复指定消息。"""
    file_key = upload_file_sdk(file_path, file_type=file_type, duration_ms=duration_ms)
    if not file_key:
        return False
    is_audio = (file_type == "opus") or file_path.lower().endswith(".opus")
    ok = reply_voice_sdk(message_id, file_key) if is_audio else reply_file_sdk(message_id, file_key)
    if ok and caption:
        send_reply_sdk(message_id, caption)
    return ok

