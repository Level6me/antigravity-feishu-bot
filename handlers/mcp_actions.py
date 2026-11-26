"""Interactive Feishu card actions for MCP server management (`/mcp`)."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError
import os
import shlex
from typing import Any, Dict, Optional

from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

import app_state
from card_builder import CardBuilder
from database import get_session_async
from lark_client import patch_interactive_card_sdk
from logger import log
from utils.auth import is_admin
from utils.mcp_manager import (
    add_mcp_from_template,
    add_mcp_server,
    list_mcp_servers,
    parse_kv_lines,
    remove_mcp_server,
    set_mcp_server_enabled,
)

_MCP_READ_ACTIONS = frozenset({
    "mcp_tab",
    "mcp_filter",
    "mcp_page",
    "mcp_refresh",
    "mcp_detail",
})

_MCP_WRITE_ACTIONS = frozenset({
    "mcp_toggle",
    "mcp_remove",
    "mcp_quick_add",
    "mcp_add_submit",
    "mcp_reload_session",
})

_MCP_ALL_ACTIONS = _MCP_READ_ACTIONS | _MCP_WRITE_ACTIONS


async def _resolve_chat_workspace(chat_id: str) -> str:
    try:
        sess_data = await get_session_async(chat_id)
        proj = str((sess_data or {}).get("project") or "").strip()
        if proj and proj not in ("默认", "Default") and os.path.isdir(proj):
            return proj
    except Exception:
        pass
    return os.getcwd()


async def _render_and_patch_mcp_card(
    chat_id: str,
    card_message_id: str,
    *,
    active_tab: str = "list",
    filter_status: str = "all",
    page: int = 1,
    expanded_name: str = "",
    notice: str = "",
    notice_level: str = "info",
    needs_session_reload: bool = False,
    force_refresh: bool = False,
    do_patch: bool = True,
):
    try:
        servers, err = await list_mcp_servers(force_refresh=force_refresh)
    except TypeError:
        servers, err = await list_mcp_servers()
    ws_dir = await _resolve_chat_workspace(chat_id)
    card = CardBuilder.build_mcp_panel_card(
        servers,
        active_tab=active_tab,
        filter_status=filter_status,
        page=page,
        expanded_name=expanded_name,
        notice=notice,
        notice_level=notice_level,
        needs_session_reload=needs_session_reload,
        is_admin_user=is_admin(chat_id),
        workspace_dir=ws_dir,
        error_msg=err or "",
    )
    if do_patch and card_message_id:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: patch_interactive_card_sdk(card_message_id, card)
        )
    return card


def _smart_mcp_read_response(
    chat_id: str,
    card_message_id: str,
    toast_text: str,
    **render_kwargs,
) -> P2CardActionTriggerResponse:
    """Return synchronous in-place card update from WS callback thread (<30ms, 0 HTTP PATCH),
    while seamlessly falling back to async PATCH on timeout or in-loop calls."""
    if asyncio._get_running_loop() is None and app_state.main_loop and app_state.main_loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(
            _render_and_patch_mcp_card(chat_id, card_message_id, do_patch=False, **render_kwargs),
            app_state.main_loop,
        )
        try:
            card = fut.result(timeout=1.4)
            from cards.common import adapt_card_for_message
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": adapt_card_for_message(card, message_id=card_message_id)},
                "toast": {"type": "info", "content": toast_text},
            })
        except FuturesTimeoutError:
            async def _late_patch():
                c = await asyncio.wrap_future(fut)
                if card_message_id and c:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: patch_interactive_card_sdk(card_message_id, c)
                    )
            app_state.schedule_bg_coro(_late_patch, label="MCPReadLatePatch")
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": toast_text}})
        except Exception as exc:
            log.error(f"[MCPActions] Sync read failed: {exc}", exc_info=True)
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"加载失败：{str(exc)[:80]}"}})

    app_state.schedule_bg_coro(
        lambda: _render_and_patch_mcp_card(chat_id, card_message_id, do_patch=True, **render_kwargs),
        label="MCPReadLoopPatch",
    )
    return P2CardActionTriggerResponse({"toast": {"type": "info", "content": toast_text}})


def handle_mcp_card_action(
    action_value: Optional[Dict[str, Any]],
    chat_id: str,
    card_message_id: str,
    form_value: Optional[Dict[str, Any]] = None,
) -> Optional[P2CardActionTriggerResponse]:
    """Handle `/mcp` card interactions. Returns None if the action is not an MCP action."""
    val = action_value if isinstance(action_value, dict) else {}
    action = str(val.get("action") or "")
    if action not in _MCP_ALL_ACTIONS:
        return None

    if action in _MCP_WRITE_ACTIONS and not is_admin(chat_id):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 MCP 修改操作仅管理员可用！"}})

    if not (app_state.main_loop and app_state.main_loop.is_running()):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

    try:
        return _dispatch_mcp_action(action, val, chat_id, card_message_id, form_value or {})
    except Exception as e:
        log.error(f"[MCPActions] Error dispatching {action}: {e}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"处理失败：{e}"}})


def _dispatch_mcp_action(
    action: str,
    val: Dict[str, Any],
    chat_id: str,
    card_message_id: str,
    form_value: Dict[str, Any],
) -> P2CardActionTriggerResponse:
    if action == "mcp_tab":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = int(val.get("page") or 1)
        return _smart_mcp_read_response(
            chat_id, card_message_id, "已切换视图", active_tab=tab, filter_status=f_status, page=page
        )

    if action == "mcp_filter":
        f_status = str(val.get("filter") or "all")
        return _smart_mcp_read_response(
            chat_id, card_message_id, "已更新筛选", active_tab="list", filter_status=f_status, page=1
        )

    if action == "mcp_page":
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_mcp_read_response(
            chat_id, card_message_id, f"第 {page} 页", active_tab="list", filter_status=f_status, page=page
        )

    if action == "mcp_detail":
        name = str(val.get("name") or "")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_mcp_read_response(
            chat_id,
            card_message_id,
            f"已展开 {name} 详情" if name else "已收起详情",
            active_tab="list",
            filter_status=f_status,
            page=page,
            expanded_name=name,
        )

    if action == "mcp_refresh":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_mcp_read_response(
            chat_id,
            card_message_id,
            "已刷新 MCP 列表",
            active_tab=tab,
            filter_status=f_status,
            page=page,
            notice="已刷新最新 MCP 服务状态",
            notice_level="info",
            force_refresh=True,
        )

    if action == "mcp_toggle":
        name = str(val.get("name") or "").strip()
        enable = bool(val.get("enable", True))
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_toggle():
            ok, msg = await set_mcp_server_enabled(name, enable)
            await _render_and_patch_mcp_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_status=f_status,
                page=page,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        app_state.schedule_bg_coro(_do_toggle, label=f"MCPToggle:{name}")
        verb = "启用" if enable else "禁用"
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在{verb} {name}..."}})

    if action == "mcp_remove":
        name = str(val.get("name") or "").strip()
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_remove():
            ok, msg = await remove_mcp_server(name)
            await _render_and_patch_mcp_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_status=f_status,
                page=page,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        app_state.schedule_bg_coro(_do_remove, label=f"MCPRemove:{name}")
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在移除 {name}..."}})

    if action == "mcp_quick_add":
        tpl_id = str(val.get("template_id") or "").strip()

        async def _do_quick_add():
            ws_dir = await _resolve_chat_workspace(chat_id)
            ok, msg = await add_mcp_from_template(tpl_id, workspace_dir=ws_dir)
            await _render_and_patch_mcp_card(
                chat_id,
                card_message_id,
                active_tab="list" if ok else "add",
                filter_status="all",
                page=1,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        app_state.schedule_bg_coro(_do_quick_add, label=f"MCPQuickAdd:{tpl_id}")
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在安装模板 {tpl_id}..."}})

    if action == "mcp_add_submit":
        name = str(form_value.get("mcp_name") or "").strip()
        transport = str(form_value.get("mcp_transport") or "auto").strip().lower()
        target = str(form_value.get("mcp_target") or "").strip()
        raw_args = str(form_value.get("mcp_args") or "").strip()
        raw_kv = str(form_value.get("mcp_kv") or "")

        if not name or not target:
            return P2CardActionTriggerResponse(
                {"toast": {"type": "error", "content": "请填写必填项：服务名称与启动命令/URL"}}
            )

        parsed_args = []
        if raw_args:
            try:
                parsed_args = shlex.split(raw_args)
            except ValueError as ve:
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"额外参数解析失败：{ve}"}})

        effective_t = transport
        if effective_t == "auto":
            effective_t = "http" if target.lower().startswith(("http://", "https://")) else "stdio"

        env_pairs = parse_kv_lines(raw_kv, mode="env") if effective_t == "stdio" else []
        headers = parse_kv_lines(raw_kv, mode="header") if effective_t == "http" else []

        async def _do_add_submit():
            ok, msg = await add_mcp_server(
                name=name,
                target=target,
                transport=effective_t,
                args=parsed_args,
                env_pairs=env_pairs,
                headers=headers,
            )
            await _render_and_patch_mcp_card(
                chat_id,
                card_message_id,
                active_tab="list" if ok else "add",
                filter_status="all",
                page=1,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        app_state.schedule_bg_coro(_do_add_submit, label=f"MCPAdd:{name}")
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在添加 MCP 服务 {name}..."}})

    if action == "mcp_reload_session":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_reload():
            from session_pool import session_pool

            active = session_pool.get_active_session(chat_id)
            if active and active.lock.locked():
                await _render_and_patch_mcp_card(
                    chat_id,
                    card_message_id,
                    active_tab=tab,
                    filter_status=f_status,
                    page=page,
                    notice="当前会话正在执行任务，请等待完成后再重建连接（或先发送 `/stop`）。",
                    notice_level="warning",
                    needs_session_reload=True,
                )
                return

            await session_pool.reset_session(chat_id)
            await _render_and_patch_mcp_card(
                chat_id,
                card_message_id,
                active_tab=tab,
                filter_status=f_status,
                page=page,
                notice="已重建当前会话连接（对话历史保留），下一条消息将自动加载最新 MCP 工具。",
                notice_level="success",
                needs_session_reload=False,
            )

        app_state.schedule_bg_coro(_do_reload, label="MCPReloadSession")
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在重建会话以加载新 MCP..."}})

    return P2CardActionTriggerResponse()
