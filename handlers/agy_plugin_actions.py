"""Interactive Feishu card action callbacks for Antigravity native plugin management (`/agyplugin`)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

import app_state
from card_builder import CardBuilder
from database import get_session_async
from lark_client import patch_interactive_card_sdk
from logger import log
from utils.agy_plugin_manager import (
    import_agy_plugins,
    install_agy_plugin,
    list_agy_plugins,
    set_agy_plugin_enabled,
    uninstall_agy_plugin,
    validate_agy_plugin,
)
from utils.auth import is_admin

_AGY_PLUGIN_READ_ACTIONS = frozenset({
    "agy_plugin_tab",
    "agy_plugin_filter",
    "agy_plugin_page",
    "agy_plugin_refresh",
    "agy_plugin_detail",
})

_AGY_PLUGIN_WRITE_ACTIONS = frozenset({
    "agy_plugin_toggle",
    "agy_plugin_uninstall",
    "agy_plugin_validate",
    "agy_plugin_import",
    "agy_plugin_form_submit",
    "agy_plugin_reload_session",
})

_AGY_PLUGIN_ALL_ACTIONS = _AGY_PLUGIN_READ_ACTIONS | _AGY_PLUGIN_WRITE_ACTIONS


async def _resolve_chat_workspace(chat_id: str) -> str:
    try:
        sess_data = await get_session_async(chat_id)
        proj = str((sess_data or {}).get("project") or "").strip()
        if proj and proj not in ("默认", "Default") and os.path.isdir(proj):
            return proj
    except Exception:
        pass
    return os.getcwd()


async def _render_and_patch_agy_plugin_card(
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
    ws_dir = await _resolve_chat_workspace(chat_id)
    try:
        plugins, err = await list_agy_plugins(workspace_dir=ws_dir, force_refresh=force_refresh)
    except TypeError:
        plugins, err = await list_agy_plugins(workspace_dir=ws_dir)
    card = CardBuilder.build_agy_plugin_panel_card(
        plugins,
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


def _smart_agy_plugin_read_response(
    chat_id: str,
    card_message_id: str,
    toast_text: str,
    **render_kwargs,
) -> P2CardActionTriggerResponse:
    if asyncio._get_running_loop() is None and app_state.main_loop and app_state.main_loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(
            _render_and_patch_agy_plugin_card(chat_id, card_message_id, do_patch=False, **render_kwargs),
            app_state.main_loop,
        )
        try:
            card = fut.result(timeout=1.4)
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": card},
                "toast": {"type": "info", "content": toast_text},
            })
        except Exception:
            async def _late_patch():
                try:
                    c = await asyncio.wrap_future(fut)
                    if card_message_id and c:
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: patch_interactive_card_sdk(card_message_id, c)
                        )
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_late_patch(), app_state.main_loop)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": toast_text}})

    asyncio.run_coroutine_threadsafe(
        _render_and_patch_agy_plugin_card(chat_id, card_message_id, do_patch=True, **render_kwargs),
        app_state.main_loop,
    )
    return P2CardActionTriggerResponse({"toast": {"type": "info", "content": toast_text}})


def handle_agy_plugin_card_action(
    action_value: Optional[Dict[str, Any]],
    chat_id: str,
    card_message_id: str,
    form_value: Optional[Dict[str, Any]] = None,
) -> Optional[P2CardActionTriggerResponse]:
    """Handle `/agyplugin` card interactions. Returns None if not an agy_plugin action."""
    val = action_value if isinstance(action_value, dict) else {}
    action = str(val.get("action") or "")
    if action not in _AGY_PLUGIN_ALL_ACTIONS:
        return None

    if action in _AGY_PLUGIN_WRITE_ACTIONS and not is_admin(chat_id):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 原生插件修改与校验操作仅管理员可用！"}})

    if not (app_state.main_loop and app_state.main_loop.is_running()):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

    try:
        return _dispatch_agy_plugin_action(action, val, chat_id, card_message_id, form_value or {})
    except Exception as e:
        log.error(f"[AgyPluginActions] Error dispatching {action}: {e}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"处理失败：{e}"}})


def _dispatch_agy_plugin_action(
    action: str,
    val: Dict[str, Any],
    chat_id: str,
    card_message_id: str,
    form_value: Dict[str, Any],
) -> P2CardActionTriggerResponse:
    if action == "agy_plugin_tab":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_agy_plugin_read_response(
            chat_id, card_message_id, "已切换视图", active_tab=tab, filter_status=f_status, page=page
        )

    if action == "agy_plugin_filter":
        f_status = str(val.get("filter") or "all")
        return _smart_agy_plugin_read_response(
            chat_id, card_message_id, "已更新筛选", active_tab="list", filter_status=f_status, page=1
        )

    if action == "agy_plugin_page":
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_agy_plugin_read_response(
            chat_id, card_message_id, f"第 {page} 页", active_tab="list", filter_status=f_status, page=page
        )

    if action == "agy_plugin_detail":
        name = str(val.get("name") or "")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_agy_plugin_read_response(
            chat_id,
            card_message_id,
            f"已展开 {name} 详情" if name else "已收起详情",
            active_tab="list",
            filter_status=f_status,
            page=page,
            expanded_name=name,
        )

    if action == "agy_plugin_refresh":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        return _smart_agy_plugin_read_response(
            chat_id,
            card_message_id,
            "已刷新原生插件列表",
            active_tab=tab,
            filter_status=f_status,
            page=page,
            notice="已刷新最新 Antigravity 原生插件状态",
            notice_level="info",
            force_refresh=True,
        )

    if action == "agy_plugin_toggle":
        name = str(val.get("name") or "").strip()
        dir_name = str(val.get("dir_name") or name).strip()
        enable = bool(val.get("enable", True))
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_toggle():
            ok, msg = await set_agy_plugin_enabled(name, enable, dir_name=dir_name)
            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_status=f_status,
                page=page,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_toggle(), app_state.main_loop)
        verb = "启用" if enable else "禁用"
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在{verb} {name}..."}})

    if action == "agy_plugin_uninstall":
        name = str(val.get("name") or "").strip()
        p_path = str(val.get("path") or "").strip()
        scope = str(val.get("scope") or "").strip()
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_uninstall():
            ok, msg = await uninstall_agy_plugin(name, plugin_path=p_path, scope=scope)
            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_status=f_status,
                page=page,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_uninstall(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在卸载 {name}..."}})

    if action == "agy_plugin_validate":
        target = str(val.get("target") or "").strip()
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_validate():
            eff_target = target or await _resolve_chat_workspace(chat_id)
            ok, msg = await validate_agy_plugin(eff_target, strict=False)
            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_status=f_status,
                page=page,
                notice=msg,
                notice_level="success" if ok else "warning",
            )

        asyncio.run_coroutine_threadsafe(_do_validate(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在校验插件结构..."}})

    if action == "agy_plugin_import":
        source = str(val.get("source") or "claude").strip()

        async def _do_import():
            ok, msg = await import_agy_plugins(source)
            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab="list" if ok else "install",
                filter_status="all",
                page=1,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_import(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在从 {source} 导入插件..."}})

    if action == "agy_plugin_form_submit":
        op = str(form_value.get("plugin_op") or "install").strip().lower()
        target = str(form_value.get("plugin_target") or "").strip()
        if not target:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请填写插件路径、URL 或名称"}})

        async def _do_form():
            if op == "install":
                ok, msg = await install_agy_plugin(target)
                needs_reload = ok
                next_tab = "list" if ok else "install"
            elif op in ("validate", "validate_strict"):
                ok, msg = await validate_agy_plugin(target, strict=(op == "validate_strict"))
                needs_reload = False
                next_tab = "install"
            elif op == "import":
                ok, msg = await import_agy_plugins(target)
                needs_reload = ok
                next_tab = "list" if ok else "install"
            else:
                ok, msg = False, f"未知操作类型：`{op}`"
                needs_reload = False
                next_tab = "install"

            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab=next_tab,
                filter_status="all",
                page=1,
                notice=msg,
                notice_level="success" if ok else ("warning" if op.startswith("validate") else "error"),
                needs_session_reload=needs_reload,
            )

        asyncio.run_coroutine_threadsafe(_do_form(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在执行插件操作 ({op})..."}})

    if action == "agy_plugin_reload_session":
        tab = str(val.get("tab") or "list")
        f_status = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))

        async def _do_reload():
            from session_pool import session_pool

            active = session_pool.get_active_session(chat_id)
            if active and active.lock.locked():
                await _render_and_patch_agy_plugin_card(
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
            await _render_and_patch_agy_plugin_card(
                chat_id,
                card_message_id,
                active_tab=tab,
                filter_status=f_status,
                page=page,
                notice="已重建当前会话连接（对话历史保留），下一条消息将自动加载最新原生插件配置。",
                notice_level="success",
                needs_session_reload=False,
            )

        asyncio.run_coroutine_threadsafe(_do_reload(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在重建会话以加载新插件..."}})

    return P2CardActionTriggerResponse()
