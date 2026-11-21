"""Interactive Feishu card action callbacks for Antigravity CLI Skills management (`/skills`)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

import app_state
from card_builder import CardBuilder
from database import get_session_async, save_session_async
from lark_client import patch_interactive_card_sdk
from logger import log
from utils.auth import is_admin
from utils.skill_manager import (
    create_custom_skill,
    install_skill_from_source,
    list_skills,
    remove_custom_skill,
    set_skill_enabled,
)

_SKILL_USER_ACTIONS = frozenset({
    "skill_tab",
    "skill_filter",
    "skill_page",
    "skill_refresh",
    "skill_detail",
    "skill_search_submit",
    "skill_search_clear",
    "skill_pin_toggle",
    "skill_clear_pins",
})

_SKILL_ADMIN_ACTIONS = frozenset({
    "skill_toggle",
    "skill_remove",
    "skill_install_submit",
    "skill_create_submit",
    "skill_reload_session",
})

_SKILL_ALL_ACTIONS = _SKILL_USER_ACTIONS | _SKILL_ADMIN_ACTIONS


async def _resolve_chat_context(chat_id: str) -> tuple[str, List[str], Dict[str, Any]]:
    sess_data: Dict[str, Any] = {}
    ws_dir = os.getcwd()
    try:
        sess_data = await get_session_async(chat_id) or {}
        proj = str(sess_data.get("project") or "").strip()
        if proj and proj not in ("默认", "Default") and os.path.isdir(proj):
            ws_dir = proj
    except Exception:
        pass
    pinned = [str(x).strip() for x in (sess_data.get("active_skills") or []) if str(x).strip()]
    return ws_dir, pinned, sess_data


async def _render_and_patch_skill_card(
    chat_id: str,
    card_message_id: str,
    *,
    active_tab: str = "list",
    filter_scope: str = "all",
    page: int = 1,
    expanded_name: str = "",
    search_query: str = "",
    notice: str = "",
    notice_level: str = "info",
    needs_session_reload: bool = False,
):
    ws_dir, pinned, _ = await _resolve_chat_context(chat_id)
    skills = list_skills(workspace_dir=ws_dir, search_query=search_query)
    card = CardBuilder.build_skill_panel_card(
        skills,
        active_tab=active_tab,
        filter_scope=filter_scope,
        page=page,
        expanded_name=expanded_name,
        search_query=search_query,
        pinned_skills=pinned,
        notice=notice,
        notice_level=notice_level,
        needs_session_reload=needs_session_reload,
        is_admin_user=is_admin(chat_id),
        workspace_dir=ws_dir,
    )
    if card_message_id:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: patch_interactive_card_sdk(card_message_id, card)
        )
    return card


def handle_skill_card_action(
    action_value: Optional[Dict[str, Any]],
    chat_id: str,
    card_message_id: str,
    form_value: Optional[Dict[str, Any]] = None,
) -> Optional[P2CardActionTriggerResponse]:
    """Handle `/skills` card interactions. Returns None if not a skill action."""
    val = action_value if isinstance(action_value, dict) else {}
    action = str(val.get("action") or "")
    if action not in _SKILL_ALL_ACTIONS:
        return None

    if action in _SKILL_ADMIN_ACTIONS and not is_admin(chat_id):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 技能启停、安装与删改仅管理员可用！"}})

    if not (app_state.main_loop and app_state.main_loop.is_running()):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪，请稍后重试"}})

    try:
        return _dispatch_skill_action(action, val, chat_id, card_message_id, form_value or {})
    except Exception as e:
        log.error(f"[SkillActions] Error dispatching {action}: {e}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"处理失败：{e}"}})


def _dispatch_skill_action(
    action: str,
    val: Dict[str, Any],
    chat_id: str,
    card_message_id: str,
    form_value: Dict[str, Any],
) -> P2CardActionTriggerResponse:
    if action == "skill_tab":
        tab = str(val.get("tab") or "list")
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_tab():
            await _render_and_patch_skill_card(
                chat_id, card_message_id, active_tab=tab, filter_scope=f_scope, page=page, search_query=q
            )

        asyncio.run_coroutine_threadsafe(_do_tab(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已切换视图"}})

    if action == "skill_filter":
        f_scope = str(val.get("filter") or "all")
        q = str(val.get("q") or "")

        async def _do_filter():
            await _render_and_patch_skill_card(
                chat_id, card_message_id, active_tab="list", filter_scope=f_scope, page=1, search_query=q
            )

        asyncio.run_coroutine_threadsafe(_do_filter(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已更新筛选"}})

    if action == "skill_page":
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_page():
            await _render_and_patch_skill_card(
                chat_id, card_message_id, active_tab="list", filter_scope=f_scope, page=page, search_query=q
            )

        asyncio.run_coroutine_threadsafe(_do_page(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"第 {page} 页"}})

    if action == "skill_detail":
        name = str(val.get("name") or "")
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_detail():
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope=f_scope,
                page=page,
                expanded_name=name,
                search_query=q,
            )

        asyncio.run_coroutine_threadsafe(_do_detail(), app_state.main_loop)
        return P2CardActionTriggerResponse(
            {"toast": {"type": "info", "content": f"已展开 {name} 详情" if name else "已收起详情"}}
        )

    if action == "skill_search_submit":
        f_scope = str(val.get("filter") or "all")
        q = str(form_value.get("skill_query") or "").strip()

        async def _do_search():
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope=f_scope,
                page=1,
                search_query=q,
                notice=f"已按关键词「`{q}`」过滤技能列表" if q else "已显示全部技能",
                notice_level="info",
            )

        asyncio.run_coroutine_threadsafe(_do_search(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"搜索：{q}" if q else "已清除搜索"}})

    if action == "skill_search_clear":
        f_scope = str(val.get("filter") or "all")

        async def _do_clear_search():
            await _render_and_patch_skill_card(
                chat_id, card_message_id, active_tab="list", filter_scope=f_scope, page=1, search_query=""
            )

        asyncio.run_coroutine_threadsafe(_do_clear_search(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已清除搜索过滤"}})

    if action == "skill_refresh":
        tab = str(val.get("tab") or "list")
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_refresh():
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab=tab,
                filter_scope=f_scope,
                page=page,
                search_query=q,
                notice="已重新扫描并刷新技能库",
                notice_level="info",
            )

        asyncio.run_coroutine_threadsafe(_do_refresh(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在刷新技能库..."}})

    if action == "skill_pin_toggle":
        name = str(val.get("name") or "").strip()
        pin = bool(val.get("pin", True))
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_pin():
            _, pinned, sess_data = await _resolve_chat_context(chat_id)
            lower_map = {p.lower(): p for p in pinned}
            if pin:
                if name.lower() not in lower_map:
                    pinned.append(name)
                msg = f"已将技能 `{name}` 挂载到当前会话（后续对话将优先激活该技能）"
            else:
                pinned = [p for p in pinned if p.lower() != name.lower()]
                msg = f"已从当前会话取消挂载技能 `{name}`"
            sess_data["active_skills"] = pinned
            await save_session_async(chat_id, sess_data)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope=f_scope,
                page=page,
                search_query=q,
                notice=msg,
                notice_level="success",
            )

        asyncio.run_coroutine_threadsafe(_do_pin(), app_state.main_loop)
        toast_txt = f"📌 已挂载技能 {name}" if pin else f"已取消挂载 {name}"
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": toast_txt}})

    if action == "skill_clear_pins":
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_clear_pins():
            _, _, sess_data = await _resolve_chat_context(chat_id)
            sess_data["active_skills"] = []
            await save_session_async(chat_id, sess_data)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope="all" if f_scope == "pinned" else f_scope,
                page=1 if f_scope == "pinned" else page,
                search_query=q,
                notice="已清空当前会话显式挂载的所有技能",
                notice_level="info",
            )

        asyncio.run_coroutine_threadsafe(_do_clear_pins(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已清空挂载技能"}})

    if action == "skill_toggle":
        name = str(val.get("name") or "").strip()
        enable = bool(val.get("enable", True))
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_toggle():
            ws_dir, _, _ = await _resolve_chat_context(chat_id)
            ok, msg = set_skill_enabled(name, enable, workspace_dir=ws_dir)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope=f_scope,
                page=page,
                search_query=q,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_toggle(), app_state.main_loop)
        verb = "启用" if enable else "禁用"
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在{verb}技能 {name}..."}})

    if action == "skill_remove":
        name = str(val.get("name") or "").strip()
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_remove():
            ws_dir, pinned, sess_data = await _resolve_chat_context(chat_id)
            ok, msg = remove_custom_skill(name, workspace_dir=ws_dir)
            if ok and any(p.lower() == name.lower() for p in pinned):
                sess_data["active_skills"] = [p for p in pinned if p.lower() != name.lower()]
                await save_session_async(chat_id, sess_data)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list",
                filter_scope=f_scope,
                page=page,
                search_query=q,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_remove(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在移除技能 {name}..."}})

    if action == "skill_install_submit":
        source = str(form_value.get("skill_source") or "").strip()
        scope = str(form_value.get("skill_install_scope") or "global").strip().lower()
        if not source:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请填写 GitHub URL 或本地目录路径"}})

        async def _do_install():
            ws_dir, _, _ = await _resolve_chat_context(chat_id)
            ok, msg = await install_skill_from_source(source, scope=scope, workspace_dir=ws_dir)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list" if ok else "add",
                filter_scope=scope if (ok and scope in ("workspace", "global")) else "all",
                page=1,
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_install(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在安装技能，请稍候..."}})

    if action == "skill_create_submit":
        name = str(form_value.get("skill_name") or "").strip()
        scope = str(form_value.get("skill_scope") or "workspace").strip().lower()
        desc = str(form_value.get("skill_desc") or "").strip()
        body = str(form_value.get("skill_body") or "").strip()
        if not name or not desc:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请填写必填项：技能名称与触发描述"}})

        async def _do_create():
            ws_dir, _, _ = await _resolve_chat_context(chat_id)
            ok, msg = create_custom_skill(name, desc, body, scope=scope, workspace_dir=ws_dir)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab="list" if ok else "add",
                filter_scope=scope if (ok and scope in ("workspace", "global")) else "all",
                page=1,
                expanded_name=name.lower().replace(" ", "-") if ok else "",
                notice=msg,
                notice_level="success" if ok else "error",
                needs_session_reload=ok,
            )

        asyncio.run_coroutine_threadsafe(_do_create(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在生成技能 {name}..."}})

    if action == "skill_reload_session":
        tab = str(val.get("tab") or "list")
        f_scope = str(val.get("filter") or "all")
        page = max(1, int(val.get("page") or 1))
        q = str(val.get("q") or "")

        async def _do_reload():
            from session_pool import session_pool

            active = session_pool.get_active_session(chat_id)
            if active and active.lock.locked():
                await _render_and_patch_skill_card(
                    chat_id,
                    card_message_id,
                    active_tab=tab,
                    filter_scope=f_scope,
                    page=page,
                    search_query=q,
                    notice="当前会话正在执行任务，请等待完成后再重建连接（或先发送 `/stop`）。",
                    notice_level="warning",
                    needs_session_reload=True,
                )
                return

            await session_pool.reset_session(chat_id)
            await _render_and_patch_skill_card(
                chat_id,
                card_message_id,
                active_tab=tab,
                filter_scope=f_scope,
                page=page,
                search_query=q,
                notice="已重建当前会话连接（对话历史保留），下一条消息将自动加载最新技能库。",
                notice_level="success",
                needs_session_reload=False,
            )

        asyncio.run_coroutine_threadsafe(_do_reload(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在重建会话以刷新技能库..."}})

    return P2CardActionTriggerResponse()
