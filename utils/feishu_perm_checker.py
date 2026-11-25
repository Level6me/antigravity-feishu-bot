"""Non-intrusive concurrent Feishu Open Platform permission & capability diagnostics (`/perm`).

Probes all required and optional Feishu scopes used by `antigravity-feishu-bot` via
side-effect-free API calls, detects `99991672` / `99991668` scope violations, and
hot-activates `CardKit 2.0` streaming if `cardkit:card:write` has been newly granted.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import config
from lark_client import (
    _CARDKIT_CAPABILITY_STATE,
    _TENANT_TOKEN_CACHE,
    _get_async_http_client,
    _get_tenant_access_token_async,
)
from logger import log

PERM_DENIED_CODES = {99991672, 99991668, 99991671, 99991679}


def _extract_missing_scopes(resp_json: Dict[str, Any], default_scopes: List[str]) -> List[str]:
    """Extract missing scope identifiers from Feishu 99991672 error payload if present."""
    found: List[str] = []
    err_obj = resp_json.get("error")
    if isinstance(err_obj, dict):
        for v in (err_obj.get("permission_violations") or []):
            if isinstance(v, dict) and v.get("subject"):
                subj = str(v["subject"]).strip()
                if subj and subj not in found:
                    found.append(subj)
    if not found:
        msg = str(resp_json.get("msg") or "")
        # Feishu often formats: "...scopes is required: [im:resource, ...]"
        if "[" in msg and "]" in msg:
            inside = msg.split("[", 1)[1].split("]", 1)[0]
            for part in inside.split(","):
                s = part.strip().strip("'\"")
                if ":" in s and s not in found:
                    found.append(s)
    return found or list(default_scopes)


def _is_perm_denied(code: int, msg: str) -> bool:
    if code in PERM_DENIED_CODES:
        return True
    low = (msg or "").lower()
    return "99991672" in low or "scopes is required" in low or ("access denied" in low and "scope" in low)


async def _probe_single(
    client,
    token: str,
    *,
    item_id: str,
    title: str,
    scopes: List[str],
    required: bool,
    impact: str,
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await client.request(method, path, json=json_body, headers=headers)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        try:
            data = resp.json()
        except Exception:
            data = {"code": -1, "msg": f"HTTP {resp.status_code}"}

        code = int(data.get("code", -1))
        msg = str(data.get("msg") or "")

        if _is_perm_denied(code, msg):
            missing = _extract_missing_scopes(data, scopes)
            return {
                "id": item_id,
                "title": title,
                "scopes": scopes,
                "missing_scopes": missing,
                "required": required,
                "status": "missing" if required else "degraded",
                "code": code,
                "latency_ms": latency_ms,
                "detail": impact,
            }

        # Special contact visibility range check (scope granted, but user outside app contact range)
        if item_id == "contact_user" and code in (41002, 41003, 99991670) or "no user authority" in msg.lower():
            return {
                "id": item_id,
                "title": title,
                "scopes": scopes,
                "missing_scopes": [],
                "required": required,
                "status": "warning",
                "code": code,
                "latency_ms": latency_ms,
                "detail": "权限已开通，但应用「可用性 / 通讯录权限范围」未包含该成员（可在飞书后台『版本管理与发布-可用范围』调整）。",
            }

        return {
            "id": item_id,
            "title": title,
            "scopes": scopes,
            "missing_scopes": [],
            "required": required,
            "status": "ok",
            "code": code,
            "latency_ms": latency_ms,
            "detail": "已就绪",
        }
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.warning(f"[PermCheck] Probe {item_id} error: {e}")
        return {
            "id": item_id,
            "title": title,
            "scopes": scopes,
            "missing_scopes": [],
            "required": required,
            "status": "warning",
            "code": -1,
            "latency_ms": latency_ms,
            "detail": f"网络探测超时或异常：{type(e).__name__}",
        }


async def check_feishu_permissions(
    chat_id: str = "",
    sender_open_id: str = "",
    force_token_refresh: bool = False,
) -> Dict[str, Any]:
    """Run concurrent, side-effect-free permission probes against Feishu Open Platform."""
    t_start = time.perf_counter()
    app_id = (getattr(config, "APP_ID", "") or "").strip()
    app_secret = (getattr(config, "APP_SECRET", "") or "").strip()
    console_url = f"https://open.feishu.cn/app/{app_id}/auth" if app_id else "https://open.feishu.cn/app"

    if not app_id or not app_secret:
        return {
            "ok": False,
            "app_id": app_id or "未配置",
            "console_url": console_url,
            "elapsed_ms": 0.0,
            "token_ok": False,
            "items": [],
            "missing_required": ["APP_ID / APP_SECRET"],
            "missing_optional": [],
            "error": "本地 `.env` 未配置完整的 `FEISHU_APP_ID` 或 `FEISHU_APP_SECRET`。",
        }

    if force_token_refresh:
        _TENANT_TOKEN_CACHE["expire_at"] = 0.0

    token = await _get_tenant_access_token_async()
    if not token:
        return {
            "ok": False,
            "app_id": app_id,
            "console_url": console_url,
            "elapsed_ms": round((time.perf_counter() - t_start) * 1000.0, 1),
            "token_ok": False,
            "items": [],
            "missing_required": ["tenant_access_token"],
            "missing_optional": [],
            "error": "无法获取飞书 `tenant_access_token`，请核对 App ID / App Secret 是否正确且应用已启用。",
        }

    client = _get_async_http_client()
    probe_chat = chat_id if (chat_id and chat_id.startswith("oc_")) else "oc_perm_probe_000000"
    probe_user = sender_open_id if (sender_open_id and sender_open_id.startswith("ou_")) else "ou_perm_probe_000000"

    # Side-effect-free concurrent probes:
    # 1. im_message: PATCH non-existent message -> checks `im:message` / `im:message:send_as_bot` without touching real messages
    # 2. im_resource: GET non-existent message resource -> checks `im:resource` without uploading/polluting files
    # 3. cardkit_write: POST /open-apis/cardkit/v1/cards -> creates unattached server-side card entity (never sent to chat!)
    # 4. im_reaction: DELETE non-existent reaction -> checks `im:message.reactions:write_only` with zero UI effect
    # 5. im_chat: GET chat metadata -> checks `im:chat:readonly`
    # 6. contact_user: GET user info -> checks `contact:user.base:readonly`
    probes = [
        _probe_single(
            client,
            token,
            item_id="im_message",
            title="消息收发与交互卡片更新",
            scopes=["im:message", "im:message:send_as_bot"],
            required=True,
            impact="核心能力：缺少此权限将无法发送回复或原地刷新交互式卡片",
            method="PATCH",
            path="/open-apis/im/v1/messages/om_perm_probe_000000",
            json_body={"content": '{"elements":[]}'},
        ),
        _probe_single(
            client,
            token,
            item_id="im_resource",
            title="多模态文件、图片与语音收发",
            scopes=["im:resource"],
            required=True,
            impact="核心多模态：缺少此权限将无法解析用户发送的图片/PDF/语音，也无法回传附件与语音条",
            method="GET",
            path="/open-apis/im/v1/messages/om_perm_probe_000000/resources/file_perm_probe_000?type=file",
        ),
        _probe_single(
            client,
            token,
            item_id="cardkit_write",
            title="CardKit 2.0 原生 60fps 流式卡片",
            scopes=["cardkit:card:write"],
            required=False,
            impact="可选加速：未开通时已自动降级为异步 HTTP PATCH 更新；开通后可解锁丝滑流式打字机效果",
            method="POST",
            path="/open-apis/cardkit/v1/cards",
            json_body={
                "type": "card_json",
                "data": '{"schema":"2.0","body":{"elements":[{"tag":"markdown","content":"ok"}]}}',
            },
        ),
        _probe_single(
            client,
            token,
            item_id="im_reaction",
            title="消息表情回应状态指示",
            scopes=["im:message.reactions:write_only"],
            required=False,
            impact="可选体验：未开通时无法在用户消息旁自动贴『⏳处理中 / ✅已完成』状态表情",
            method="DELETE",
            path="/open-apis/im/v1/messages/om_perm_probe_000000/reactions/ZVcCIj_perm_probe",
        ),
        _probe_single(
            client,
            token,
            item_id="im_chat",
            title="会话与群聊名称读取",
            scopes=["im:chat:readonly"],
            required=False,
            impact="可选管理：未开通时 `/user` 授权面板无法自动显示飞书群聊名称（将回退显示 `oc_xxx`）",
            method="GET",
            path=f"/open-apis/im/v1/chats/{probe_chat}",
        ),
        _probe_single(
            client,
            token,
            item_id="contact_user",
            title="用户真实姓名读取",
            scopes=["contact:user.base:readonly"],
            required=False,
            impact="可选管理：未开通时 `/user` 与权限审批卡无法显示申请人姓名（将回退显示 `ou_xxx`）",
            method="GET",
            path=f"/open-apis/contact/v3/users/{probe_user}?user_id_type=open_id",
        ),
    ]

    results = list(await asyncio.gather(*probes))

    # Hot-heal CardKit 2.0 capability cache immediately if `cardkit:card:write` is now granted!
    cardkit_item = next((r for r in results if r["id"] == "cardkit_write"), None)
    cardkit_newly_activated = False
    if cardkit_item:
        if cardkit_item["status"] == "ok":
            if _CARDKIT_CAPABILITY_STATE.get("supported") is False:
                cardkit_newly_activated = True
            _CARDKIT_CAPABILITY_STATE["supported"] = True
            cardkit_item["detail"] = (
                "已开通（🎉 已自动热激活 CardKit 2.0 流式引擎，无需重启！）"
                if cardkit_newly_activated
                else "已就绪（CardKit 2.0 原生流式输出已启用）"
            )
        elif cardkit_item["status"] in ("missing", "degraded"):
            _CARDKIT_CAPABILITY_STATE["supported"] = False

    missing_required: List[str] = []
    missing_optional: List[str] = []
    for r in results:
        if r["status"] == "missing":
            for s in r["missing_scopes"]:
                if s not in missing_required:
                    missing_required.append(s)
        elif r["status"] == "degraded":
            for s in r["missing_scopes"]:
                if s not in missing_optional:
                    missing_optional.append(s)

    elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
    return {
        "ok": len(missing_required) == 0,
        "app_id": app_id,
        "console_url": console_url,
        "elapsed_ms": elapsed_ms,
        "token_ok": True,
        "cardkit_newly_activated": cardkit_newly_activated,
        "items": results,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "error": "",
    }
