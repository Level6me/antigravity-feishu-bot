"""Feishu interactive card action callbacks."""
import asyncio
import json
import os
import re
import time

from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from config import ALLOWED_USERS, ALLOWED_CHATS, get_oauth_token_path
from database import get_session_async, get_profile_async, save_session_async, save_profile_async
from card_builder import CardBuilder
from lark_client import (
    send_reply_sdk,
    send_interactive_card_sdk,
    patch_interactive_card_sdk,
)
from commands import PendingCommand, handle_slash_command
from logger import log
import app_state
from handlers.messages import _handle_message_async_internal
from handlers.auth_actions import handle_auth_card_action
from handlers.mcp_actions import handle_mcp_card_action
from handlers.agy_plugin_actions import handle_agy_plugin_card_action
from handlers.skill_actions import handle_skill_card_action
from utils.auth import SCOPE_PROJECT, get_role, has_scope, is_admin

# Compatibility aliases for extracted body
running_processes = app_state.running_processes
chat_queues = app_state.chat_queues
chat_workers = app_state.chat_workers


def _respond_card_sync_or_patch(
    build_card_coro,
    card_message_id: str,
    toast_content: str,
    toast_type: str = "success",
    sync_timeout: float = 1.5,
) -> P2CardActionTriggerResponse:
    """Execute async state update + card build on main_loop.
    Returns card synchronously in callback response (<30ms, 0 HTTP PATCH) from WS worker thread,
    falling back to async patch_interactive_card_sdk on slow operations or in-loop test calls.
    """
    if not (app_state.main_loop and app_state.main_loop.is_running()):
        return P2CardActionTriggerResponse({"toast": {"type": toast_type, "content": toast_content}})

    if asyncio._get_running_loop() is None:
        fut = asyncio.run_coroutine_threadsafe(build_card_coro(), app_state.main_loop)
        try:
            res = fut.result(timeout=sync_timeout)
            if isinstance(res, tuple) and len(res) == 3:
                new_card, eff_type, eff_msg = res
            else:
                new_card, eff_type, eff_msg = res, toast_type, toast_content
            if new_card:
                return P2CardActionTriggerResponse({
                    "card": {"type": "raw", "data": new_card},
                    "toast": {"type": eff_type, "content": eff_msg},
                })
            return P2CardActionTriggerResponse({"toast": {"type": eff_type, "content": eff_msg}})
        except Exception:
            async def _fallback_patch():
                try:
                    res = await asyncio.wrap_future(fut)
                    new_card = res[0] if (isinstance(res, tuple) and len(res) == 3) else res
                    if new_card and card_message_id:
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: patch_interactive_card_sdk(card_message_id, new_card)
                        )
                except Exception as pe:
                    log.warning(f"[CardAction] Fallback patch failed: {pe}")
            asyncio.run_coroutine_threadsafe(_fallback_patch(), app_state.main_loop)
            return P2CardActionTriggerResponse({"toast": {"type": toast_type, "content": toast_content}})

    async def _loop_patch():
        try:
            res = await build_card_coro()
            new_card = res[0] if (isinstance(res, tuple) and len(res) == 3) else res
            if new_card and card_message_id:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, new_card)
                )
        except Exception as le:
            log.warning(f"[CardAction] Loop patch failed: {le}")

    asyncio.run_coroutine_threadsafe(_loop_patch(), app_state.main_loop)
    return P2CardActionTriggerResponse({"toast": {"type": toast_type, "content": toast_content}})


# Provide main_loop as dynamic attribute via module-level lookup in function
def do_p2_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    log.info(f"Card action received: {data.event.action.value}")

    action_value = data.event.action.value or {}
    form_value = getattr(data.event.action, "form_value", None) or {}
    chat_id = data.event.context.open_chat_id
    card_message_id = data.event.context.open_message_id

    # Check whitelist if configured
    is_allowed = True
    if ALLOWED_USERS or ALLOWED_CHATS:
        is_allowed = False
        sender_id = data.event.operator.open_id if data.event.operator else None
        if ALLOWED_USERS and sender_id in ALLOWED_USERS:
            is_allowed = True
        if ALLOWED_CHATS and chat_id in ALLOWED_CHATS:
            is_allowed = True

    if not is_allowed:
        log.warning(f"Unauthorized card action ignored. chat_id: {chat_id}, operator_id: {sender_id if 'sender_id' in locals() else None}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "您无权操作此卡片！"}})

    operator_open_id = data.event.operator.open_id if data.event.operator else None
    role = get_role(chat_id, operator_open_id or "")

    # Allow unauthorized guest to click "🔑 一键申请使用权限" directly on build_auth_hint_card
    if action_value.get("action") == "guest_request_auth":
        if role == "banned":
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "该会话已被限制访问。"}})
        if role in ("admin", "user"):
            return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "当前会话已获得授权，可直接发送消息使用！"}})
        from utils.auth import get_admin_chat_id, request_access
        from database import get_auth_session
        status = request_access(chat_id, "p2p" if operator_open_id else "group", operator_open_id or "", "卡片一键申请授权")
        if status == "ok" and app_state.main_loop and app_state.main_loop.is_running():
            async def _notify_admin_req():
                from lark_client import send_card_to_chat_async
                admin_cid = get_admin_chat_id()
                sess = get_auth_session(chat_id)
                if admin_cid and sess:
                    await send_card_to_chat_async(admin_cid, CardBuilder.build_auth_request_card(sess))
            asyncio.run_coroutine_threadsafe(_notify_admin_req(), app_state.main_loop)
        toast_msg = "已向管理员提交授权申请！" if status == "ok" else "申请已提交，请等待管理员审批"
        return P2CardActionTriggerResponse({
            "card": {"type": "raw", "data": CardBuilder.build_auth_hint_card(pending=True)},
            "toast": {"type": "success" if status == "ok" else "info", "content": toast_msg},
        })

    # Auth gate: guests / pending / banned chats cannot operate any other card.
    if role in ("guest", "pending", "banned"):
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "该会话未授权，无法操作。"}})

    # Admin gate for plugin write operations (prevents privilege escalation via card buttons)
    plugin_write_actions = {
        "reload_plugins", "update_plugin", "uninstall_plugin",
        "install_github_repo", "prompt_install_github", "prompt_add_source",
        "toggle_plugin_form", "plugin_install_submit", "plugin_source_submit",
    }
    if action_value.get("action") in plugin_write_actions:
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 此操作仅管理员可用！"}})
        
    if action_value.get("action") == "acp_permission_decision":
        perm_token = str(action_value.get("perm_token") or "")
        option_id = str(action_value.get("option_id") or "")
        chosen_label = str(action_value.get("label") or option_id or "已处理")
        req_title = str(action_value.get("title") or "工具权限审批")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_resolve_perm():
                from acp_client import resolve_pending_permission
                ok, msg = resolve_pending_permission(perm_token, chat_id, option_id)
                if ok and card_message_id:
                    res_card = CardBuilder.build_acp_permission_result_card(req_title, chosen_label)
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: patch_interactive_card_sdk(card_message_id, res_card)
                    )
                return ok, msg
            try:
                ok, msg = asyncio.run_coroutine_threadsafe(_do_resolve_perm(), app_state.main_loop).result(timeout=5)
                toast_type = "success" if ok else "warning"
                return P2CardActionTriggerResponse({"toast": {"type": toast_type, "content": msg}})
            except Exception as e:
                log.error(f"[CardAction] acp_permission_decision error: {e}")
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "权限审批提交失败"}})
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "事件循环未就绪"}})

    if action_value.get("action") == "switch_model":
        new_model = action_value.get("model")
        
        result_card = None
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_switch():
                session_data = await get_session_async(chat_id)
                old_model = session_data.get("model", "Default")
                session_data["model"] = new_model
                session_data["base_model"] = new_model
                from system_one_gate import infer_effort_from_model
                effort_to_set = infer_effort_from_model(new_model, session_data.get("reasoning_effort", "medium"))
                session_data["reasoning_effort"] = effort_to_set

                auto_relayed = False
                try:
                    from agent_memory import relay_conversation_for_model_switch
                    relay_res = relay_conversation_for_model_switch(
                        chat_id=chat_id,
                        session_data=session_data,
                        new_model=new_model,
                        old_model=old_model,
                        project_dir=session_data.get("project", "默认"),
                    )
                    auto_relayed = bool(relay_res.get("relayed"))
                except Exception as relay_err:
                    log.warning(f"[switch_model] Capacity auto-relay check failed: {relay_err}")

                await save_session_async(chat_id, session_data)
                log.info(
                    f"Switched model to {new_model} (effort={effort_to_set}, auto_relayed={auto_relayed}) in chat {chat_id}"
                )
                try:
                    # If the previous worker is still stuck retrying a 503 capacity error, abort it cleanly
                    if auto_relayed and chat_id in chat_workers and not chat_workers[chat_id].done():
                        chat_workers[chat_id].cancel()
                        chat_workers.pop(chat_id, None)
                    from session_pool import session_pool
                    proj_val = session_data.get("project")
                    cwd_dir = proj_val if (proj_val and os.path.isdir(proj_val) and proj_val not in ["默认", "Default"]) else None
                    conv_id = session_data.get("conversation", "")
                    agent_mode = session_data.get("agent_mode") or None
                    extra_dirs = list(session_data.get("extra_dirs") or [])
                    active_sess = session_pool.get_active_session(chat_id) if hasattr(session_pool, "get_active_session") else None
                    # Live ACP session: hot-switch model in-place when not auto-relaying (preserves subagents & warm state!)
                    if (
                        not auto_relayed
                        and active_sess
                        and getattr(active_sess, "transport_name", "") == "acp"
                        and active_sess.is_alive()
                        and not active_sess.lock.locked()
                    ):
                        switched_ok = await active_sess.set_model(new_model, effort=effort_to_set)
                        if not switched_ok:
                            await session_pool.reset_session(chat_id)
                            asyncio.create_task(session_pool.prewarm(
                                chat_id, new_model, cwd_dir, conversation_id=conv_id,
                                effort=effort_to_set, agent_mode=agent_mode, extra_dirs=extra_dirs,
                            ))
                    else:
                        await session_pool.reset_session(chat_id)
                        asyncio.create_task(session_pool.prewarm(
                            chat_id, new_model, cwd_dir, conversation_id=conv_id,
                            effort=effort_to_set, agent_mode=agent_mode, extra_dirs=extra_dirs,
                        ))
                except Exception:
                    pass
                initial_probe_status = "probing"
                initial_probe_latency = 0.0
                initial_fallback = None
                should_run_bg_probe = True
                try:
                    from utils.model_health import (
                        get_model_health,
                        pick_healthy_fallback_model,
                        probe_model_capacity,
                    )
                    cached_h = get_model_health(new_model)
                    if cached_h.get("status") == "healthy" and cached_h.get("age_seconds", 999.0) < 60.0:
                        initial_probe_status = "healthy"
                        initial_probe_latency = float(cached_h.get("latency_ms") or 0.0)
                        should_run_bg_probe = False
                    elif cached_h.get("status") == "unhealthy":
                        initial_probe_status = "unhealthy"
                        initial_fallback = pick_healthy_fallback_model(new_model, preferred_tier=effort_to_set)
                        # Still re-probe in background in case the model just recovered
                        should_run_bg_probe = True
                except Exception as mh_err:
                    log.debug(f"[switch_model] Model health check skipped: {mh_err}")
                    should_run_bg_probe = False
                    initial_probe_status = None

                res_card = CardBuilder.build_model_switch_result_card(
                    new_model,
                    old_model,
                    auto_relayed=auto_relayed,
                    probe_status=initial_probe_status,
                    probe_latency_ms=initial_probe_latency,
                    suggested_fallback=initial_fallback,
                )

                if should_run_bg_probe and card_message_id:
                    async def _probe_and_patch_card():
                        try:
                            import config as _cfg
                            from utils.model_health import pick_healthy_fallback_model, probe_model_capacity
                            probe_timeout = float(getattr(_cfg, "CAPACITY_PROBE_TIMEOUT_SECONDS", 18) or 18)
                            probe_res = await probe_model_capacity(
                                new_model,
                                timeout_seconds=probe_timeout,
                                force=(initial_probe_status == "unhealthy"),
                            )
                            # Only patch if the user hasn't already switched to another model
                            latest_session = await get_session_async(chat_id)
                            if latest_session.get("model") != new_model and latest_session.get("base_model") != new_model:
                                return
                            p_status = probe_res.get("status")
                            if p_status not in ("healthy", "unhealthy", "timeout"):
                                p_status = None
                            fb_model = (
                                pick_healthy_fallback_model(new_model, preferred_tier=effort_to_set)
                                if p_status in ("unhealthy", "timeout")
                                else None
                            )
                            updated_card = CardBuilder.build_model_switch_result_card(
                                new_model,
                                old_model,
                                auto_relayed=auto_relayed,
                                probe_status=p_status,
                                probe_latency_ms=float(probe_res.get("latency_ms") or 0.0),
                                suggested_fallback=fb_model,
                            )
                            await asyncio.get_running_loop().run_in_executor(
                                None, lambda: patch_interactive_card_sdk(card_message_id, updated_card)
                            )
                        except Exception as pe:
                            log.warning(f"[switch_model] Background probe card patch failed: {pe}")

                    asyncio.create_task(_probe_and_patch_card())

                return res_card

            future = asyncio.run_coroutine_threadsafe(do_switch(), app_state.main_loop)
            try:
                result_card = future.result(timeout=8)
            except Exception as e:
                log.error(f"[switch_model] do_switch failed or timed out: {e}")

        if result_card:
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": result_card},
                "toast": {"type": "success", "content": f"模型已切换为 {new_model}"}
            })
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"模型已切换为 {new_model}"}})

    elif action_value.get("action") == "capacity_choice_decision":
        decision = str(action_value.get("decision") or "switch_and_continue")
        failed_model = str(action_value.get("failed_model") or "")
        target_model = str(action_value.get("target_model") or "")
        if decision == "switch_form_and_continue":
            target_model = str(form_value.get("capacity_target_model") or target_model or "").strip()

        if decision == "cancel":
            if app_state.main_loop and app_state.main_loop.is_running():
                async def _do_cancel_capacity():
                    session_data = await get_session_async(chat_id)
                    paused = session_data.pop("paused_capacity_state", None) or {}
                    await save_session_async(chat_id, session_data)
                    cancelled_card = CardBuilder.build_capacity_choice_card(
                        failed_model=failed_model or paused.get("failed_model") or session_data.get("model", "Default"),
                        user_text=paused.get("user_text", ""),
                        current_project=paused.get("project_dir") or session_data.get("project", "默认"),
                        resolved_status="cancelled",
                    )
                    if card_message_id:
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda: patch_interactive_card_sdk(card_message_id, cancelled_card)
                        )
                asyncio.run_coroutine_threadsafe(_do_cancel_capacity(), app_state.main_loop)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已取消本次任务重试"}})

        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_capacity_resume():
                session_data = await get_session_async(chat_id)
                paused = session_data.pop("paused_capacity_state", None) or {}
                orig_q = paused.get("user_text") or session_data.get("last_user_query") or "继续执行刚才的任务"
                err_m = failed_model or paused.get("failed_model") or session_data.get("model", "Default")
                proj_dir = paused.get("project_dir") or session_data.get("project", "默认")
                chosen_model = (err_m if decision == "retry_current" else (target_model or paused.get("recommended_model") or err_m)).strip()

                from system_one_gate import infer_effort_from_model
                effort_to_set = infer_effort_from_model(chosen_model, session_data.get("reasoning_effort", "medium"))

                if decision == "retry_current":
                    try:
                        from utils.model_health import clear_model_health
                        clear_model_health(chosen_model)
                    except Exception:
                        pass
                else:
                    session_data["model"] = chosen_model
                    session_data["base_model"] = chosen_model
                    session_data["reasoning_effort"] = effort_to_set

                try:
                    from agent_memory import relay_conversation_for_model_switch
                    relay_conversation_for_model_switch(
                        chat_id=chat_id,
                        session_data=session_data,
                        new_model=chosen_model,
                        old_model=err_m,
                        project_dir=proj_dir,
                        force=bool(paused.get("tainted_conv") or session_data.get("_capacity_tainted_conv")),
                    )
                except Exception as relay_err:
                    log.warning(f"[capacity_choice_decision] Relay check failed: {relay_err}")

                comp_steps = list(paused.get("completed_steps") or [])
                plan_steps = list(paused.get("planned_steps") or [])
                if comp_steps or plan_steps:
                    session_data["_resumed_task_progress"] = {
                        "user_text": orig_q,
                        "elapsed_seconds": int(paused.get("elapsed_seconds") or 0),
                        "completed_steps": comp_steps,
                        "planned_steps": plan_steps,
                        "current_plan_idx": int(paused.get("current_plan_idx") or 0),
                        "max_progress_pct": int(paused.get("max_progress_pct") or 0),
                    }
                await save_session_async(chat_id, session_data)

                try:
                    from session_pool import session_pool
                    await session_pool.reset_session(chat_id)
                except Exception:
                    pass

                resolved_card = CardBuilder.build_capacity_choice_card(
                    failed_model=err_m,
                    user_text=orig_q,
                    current_project=proj_dir,
                    resolved_status="retry_current" if decision == "retry_current" else "switched",
                    resolved_model=chosen_model,
                )
                if card_message_id:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: patch_interactive_card_sdk(card_message_id, resolved_card)
                    )

                simulated_content = json.dumps({"text": orig_q}, ensure_ascii=False)
                try:
                    await _handle_message_async_internal(
                        card_message_id, chat_id, "text", simulated_content, trusted_internal_resume=True
                    )
                except TypeError:
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(_do_capacity_resume(), app_state.main_loop)

        toast_msg = (
            f"🔄 正在继续使用 {failed_model or '当前模型'} 重试任务..."
            if decision == "retry_current"
            else f"⚡ 已切换至 {target_model}，正在继续执行任务..."
        )
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": toast_msg}})

    elif action_value.get("action") == "switch_agent_mode":
        target_mode = str(action_value.get("mode") or "auto").strip().lower()
        if target_mode not in ("auto", "accept-edits", "plan", "default"):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"无效模式：{target_mode}"}})
        if target_mode != "plan" and not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 当前会话仅允许使用 plan 只读模式"}})

        async def _do_switch_mode():
            from commands import DEFAULT_FALLBACK_MODELS, get_available_models_cached
            session_data = await get_session_async(chat_id)
            session_data["agent_mode"] = target_mode
            await save_session_async(chat_id, session_data)
            try:
                from session_pool import session_pool
                active_sess = session_pool.get_active_session(chat_id)
                if active_sess and getattr(active_sess, "transport_name", "") == "acp" and active_sess.is_alive():
                    await active_sess.set_mode(target_mode)
                elif active_sess:
                    await session_pool.reset_session(chat_id)
            except Exception:
                pass
            avail = await get_available_models_cached(force_refresh=False) or DEFAULT_FALLBACK_MODELS
            card = CardBuilder.build_model_panel(avail, session_data.get("model", "Default"), agent_mode=target_mode)
            return card, "success", f"执行模式已热切为 {target_mode}"

        return _respond_card_sync_or_patch(_do_switch_mode, card_message_id, f"执行模式已热切为 {target_mode}")

    elif action_value.get("action") == "help_switch_tab":
        tab = str(action_value.get("tab") or "all").strip().lower()
        new_help_card = CardBuilder.build_help_card(is_admin_user=is_admin(chat_id), active_tab=tab)
        if asyncio._get_running_loop() is None:
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": new_help_card},
                "toast": {"type": "info", "content": "已切换帮助分类"},
            })
        if app_state.main_loop and app_state.main_loop.is_running() and card_message_id:
            asyncio.run_coroutine_threadsafe(
                asyncio.to_thread(patch_interactive_card_sdk, card_message_id, new_help_card),
                app_state.main_loop,
            )
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已切换帮助分类"}})

    elif action_value.get("action") == "user_choice":
        choice = action_value.get("choice", "")
        label = action_value.get("label", choice)
        from_choice_card = bool(action_value.get("from_choice_card"))
        log.info(f"User selected choice: {choice} (from_choice_card={from_choice_card})")

        locked_card = None
        if from_choice_card and not str(choice).startswith("/"):
            locked_card = CardBuilder.build_choice_locked_card(
                chosen_label=label,
                chosen_value=choice,
                question=str(action_value.get("question") or "已确认选项"),
                reply_preview=str(action_value.get("reply_preview") or ""),
            )

        if app_state.main_loop and app_state.main_loop.is_running():
            async def notify_and_process():
                handled = False
                if choice and choice.startswith("/"):
                    session_data = await get_session_async(chat_id)
                    handled_res, _ = await handle_slash_command(choice, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                    handled = bool(handled_res)
                    if not handled:
                        clean_cmd = choice.split()[0].strip()
                        if clean_cmd != choice:
                            handled_res2, _ = await handle_slash_command(clean_cmd, card_message_id, chat_id, session_data, running_processes, chat_queues, chat_workers)
                            handled = bool(handled_res2)

                if not handled:
                    if locked_card is not None:
                        # 原卡片已原地锁定展示用户所选选项，无需再发多余刷屏文本消息
                        if asyncio._get_running_loop() is not None and card_message_id:
                            pass
                    else:
                        user_display_text = f"✅ **您已选择：{label}**\n*(选项内容已提交 AI 进行下一步处理...)*"
                        await asyncio.get_running_loop().run_in_executor(None, lambda: send_reply_sdk(card_message_id, user_display_text))
                    simulated_content = json.dumps({"text": f"我的选择是：{choice}"}, ensure_ascii=False)
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            if locked_card is not None and asyncio._get_running_loop() is not None and card_message_id:
                asyncio.run_coroutine_threadsafe(
                    asyncio.to_thread(patch_interactive_card_sdk, card_message_id, locked_card),
                    app_state.main_loop,
                )
            asyncio.run_coroutine_threadsafe(notify_and_process(), app_state.main_loop)

        if locked_card is not None and asyncio._get_running_loop() is None:
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": locked_card},
                "toast": {"type": "success", "content": f"已确认选择：{label}"},
            })
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"已选择：{label}"}})
            
    elif action_value.get("action") == "l3_confirm_action":
        decision = action_value.get("decision", "reject")
        scope = action_value.get("scope", "once")  # "once" or "turn"
        command = action_value.get("command", "")
        tool = action_value.get("tool", "")
        cwd = action_value.get("cwd", "")
        call_id = action_value.get("call_id", "")
        operator_open_id = data.event.operator.open_id if data.event.operator else ""

        # 1. 管理员权限校验：仅管理员有权审批高危敏感指令
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 仅系统管理员有权审批高危指令！"}})

        # 2. 拒绝分支
        if decision == "reject":
            log.info(f"[L3 Guard] Admin {operator_open_id} rejected command in chat {chat_id}: {command}")
            if app_state.main_loop and app_state.main_loop.is_running():
                async def do_cleanup_paused_l3():
                    try:
                        session_data = await get_session_async(chat_id)
                        session_data.pop("paused_l3_state", None)
                        session_data.pop("_l3_authorize_all_turn", None)
                        await save_session_async(chat_id, session_data)
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(do_cleanup_paused_l3(), app_state.main_loop)

            res_card = CardBuilder.build_l3_confirm_result_card(
                status="rejected",
                command=command,
                operator_id=operator_open_id,
                scope=scope
            )
            return P2CardActionTriggerResponse({
                "card": {"type": "raw", "data": res_card},
                "toast": {"type": "info", "content": "已拒绝执行该高危敏感指令"}
            })

        # 3. 授权执行分支 (支持仅单次授权 / 本轮后续所有高危指令授权)
        is_turn_scope = (scope == "turn")
        log.info(f"[L3 Guard] Admin {operator_open_id} approved command in chat {chat_id} (scope={scope}): {command} (cwd={cwd})")
        running_card = CardBuilder.build_l3_confirm_result_card(
            status="running",
            command=command,
            operator_id=operator_open_id,
            scope=scope
        )

        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_execute_approved():
                nonlocal command, tool, cwd
                t0 = time.time()
                ret_code = 0
                out_text = ""
                p_l3 = {}

                # 若用户选择授权本轮所有，立即标记当前 session 状态
                if is_turn_scope:
                    try:
                        cur_sess = await get_session_async(chat_id)
                        cur_sess["_l3_authorize_all_turn"] = True
                        await save_session_async(chat_id, cur_sess)
                    except Exception as s_err:
                        log.warning(f"[L3 Guard] Failed saving _l3_authorize_all_turn: {s_err}")

                # 多级恢复完整指令：优先从 session_data 提取完整未截断的指令，若破损则从 transcript_full.jsonl 找回
                try:
                    s_data = await get_session_async(chat_id)
                    p_l3 = s_data.get("paused_l3_state") or {}
                    saved_cmd = (p_l3.get("command") or "").strip()
                    if not tool and p_l3.get("tool"):
                        tool = p_l3.get("tool")
                    if not cwd and p_l3.get("cwd"):
                        cwd = p_l3.get("cwd")
                    if saved_cmd and not (saved_cmd.endswith("…") or saved_cmd.endswith("...")):
                        command = saved_cmd
                    elif not command or command.endswith("…") or command.endswith("..."):
                        c_id = p_l3.get("conv_id") or s_data.get("conversation")
                        if c_id:
                            from config import recover_full_tool_call
                            rec_cmd = recover_full_tool_call(
                                c_id,
                                tool or "run_command",
                                prefix_hint=saved_cmd or command,
                            )
                            if rec_cmd:
                                log.info(f"[L3 Guard] Recovered full un-truncated command ({len(rec_cmd)} chars) from transcript_full.jsonl")
                                command = rec_cmd
                except Exception as rec_err:
                    log.warning(f"[L3 Guard] Failed while resolving full command: {rec_err}")

                # 解析前置 timeout <N>[smhd] 参数，兼顾 shell 关键字兼容性与真实超时保护
                raw_cmd_stripped = command.strip()
                wait_timeout_sec = 300.0
                m_timeout = re.match(
                    r"^timeout\s+(?:-k\s+\S+\s+)?(\d+(?:\.\d+)?)([smhd])?\s+(.*)",
                    raw_cmd_stripped,
                    re.DOTALL,
                )
                if m_timeout:
                    val_num = float(m_timeout.group(1))
                    unit = (m_timeout.group(2) or "s").lower()
                    mult = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}.get(unit, 1.0)
                    wait_timeout_sec = min(1800.0, max(20.0, val_num * mult + 10.0))
                    clean_cmd = m_timeout.group(3).strip()
                else:
                    clean_cmd = re.sub(r"^timeout\s+\d+\s+", "", raw_cmd_stripped).strip()

                eff_tool = (tool or "run_command").strip()
                is_non_shell_tool = (
                    eff_tool not in ("", "run_command")
                    or bool(re.match(r"^(?:write_to_file|replace_file_content|edit_file|write_file|tool:|tool_action:)\b", clean_cmd))
                )

                shell_bin = "/bin/bash" if os.path.exists("/bin/bash") else "sh"

                # 语法预检与截断熔断防护：严防将未闭合引号或带省略号的破损命令直接交付 shell 执行
                is_truncated = clean_cmd.endswith("…") or clean_cmd.endswith("...")
                syntax_err = ""
                if not is_non_shell_tool and not is_truncated and clean_cmd:
                    sh_check = None
                    try:
                        sh_check = await asyncio.create_subprocess_exec(
                            shell_bin, "-n", "-c", clean_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        _, chk_err = await asyncio.wait_for(sh_check.communicate(), timeout=3.0)
                        if sh_check.returncode != 0:
                            syntax_err = chk_err.decode('utf-8', errors='replace').strip()
                    except Exception as e:
                        if sh_check is not None and sh_check.returncode is None:
                            try:
                                sh_check.kill()
                                await asyncio.wait_for(sh_check.wait(), timeout=1.0)
                            except Exception:
                                pass
                        log.warning(f"[L3 Guard] Syntax check helper exception: {e}")

                is_unterminated_quote = bool(
                    syntax_err and (
                        "Unterminated quoted string" in syntax_err
                        or "unexpected EOF while looking for matching" in syntax_err
                        or "syntax error: unexpected end of file" in syntax_err.lower()
                    )
                )

                if is_non_shell_tool:
                    out_text = f"已确认授权工具操作 ({eff_tool})，正在唤醒 AI 继续完成该文件/工具操作..."
                    ret_code = 0
                elif is_truncated or is_unterminated_quote:
                    log.warning(
                        f"[L3 Guard] Intercepted truncated/broken command on approval in chat {chat_id} "
                        f"(is_truncated={is_truncated}, syntax_err={syntax_err})"
                    )
                    out_text = (
                        "⚠️【安全拆解保护】待执行的单行系统指令因超长在上报流中发生截断（检测到省略号或未闭合引号），"
                        "已拦截直接执行并自动唤醒 AI 将该脚本拆解写入临时文件后安全重试。\n"
                    )
                    if syntax_err:
                        out_text += f"语法预检详情：{syntax_err}"
                    ret_code = 2
                else:
                    try:
                        import signal
                        exec_env = os.environ.copy()
                        cur_path = exec_env.get("PATH", "")
                        extra_paths = [
                            os.path.expanduser("~/.local/bin"),
                            "/usr/local/sbin",
                            "/usr/local/bin",
                            "/usr/sbin",
                            "/usr/bin",
                            "/sbin",
                            "/bin",
                        ]
                        for ep in reversed(extra_paths):
                            if ep and ep not in cur_path.split(":"):
                                cur_path = f"{ep}:{cur_path}" if cur_path else ep
                        exec_env["PATH"] = cur_path

                        proc = await asyncio.create_subprocess_exec(
                            shell_bin, "-c", clean_cmd,
                            cwd=cwd if (cwd and os.path.isdir(cwd)) else None,
                            env=exec_env,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            start_new_session=True,
                        )
                        try:
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout_sec)
                            out_str = stdout.decode('utf-8', errors='replace')
                            err_str = stderr.decode('utf-8', errors='replace')
                            out_text = f"{out_str}\n{err_str}".strip()
                            ret_code = proc.returncode
                        except asyncio.TimeoutError:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except Exception:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                            try:
                                await asyncio.wait_for(proc.wait(), timeout=2.0)
                            except Exception:
                                pass
                            out_text = f"执行超时 ({int(wait_timeout_sec)}s)，已被系统安全截断中止"
                            ret_code = 124
                    except Exception as ex:
                        out_text = f"执行异常: {ex}"
                        ret_code = -1

                elapsed = time.time() - t0

                # 智能幂等清理容错：由于 L3 旁路审计存在网络延迟，毫秒级命令（如 kill/rm）可能已在拦截前由 agy 执行完毕
                is_idempotent_success = False
                clean_cmd_lower = clean_cmd.lower().strip()
                if any(clean_cmd_lower.startswith(p) for p in ("kill ", "kill -", "pkill ", "killall ", "rm ", "rm -")):
                    out_lower = out_text.lower()
                    if "no such process" in out_lower or "no such file or directory" in out_lower or (ret_code == 1 and not out_lower):
                        is_idempotent_success = True

                if ret_code == 0 or is_idempotent_success:
                    final_status = "success"
                    if is_idempotent_success and ret_code != 0:
                        out_text = f"{out_text}\n(注：目标进程或资源在审批前已自然释放，符合幂等清理预期，判定为成功放行)".strip()
                        ret_code = 0
                else:
                    # 非零退出码或截断保护：更新卡片为自愈接力状态，并将报错反馈给 AI 自动修正，避免任务直接卡死
                    log.warning(
                        f"[L3 Guard] Command returned non-zero ({ret_code}) in chat {chat_id}, triggering AI self-healing resume: {out_text[:200]}"
                    )
                    final_status = "warning"

                final_card = CardBuilder.build_l3_confirm_result_card(
                    status=final_status,
                    command=command,
                    operator_id=operator_open_id,
                    output=out_text,
                    returncode=ret_code,
                    elapsed_seconds=elapsed,
                    scope=scope
                )
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: patch_interactive_card_sdk(card_message_id, final_card)
                    )
                except Exception as patch_err:
                    log.warning(f"[L3 Guard] Failed to patch result card (continuing to auto-resume): {patch_err}")

                # 自动接力推进原任务（成功时直接继续；非零退出或截断时携带诊断信息唤醒 AI 自愈修正，杜绝任务停摆）
                try:
                    try:
                        from math_decision_model import math_decision_engine
                        math_decision_engine.reset_session_risk(chat_id)
                    except Exception:
                        pass

                    session_data = await get_session_async(chat_id)
                    paused_l3 = session_data.pop("paused_l3_state", None) or p_l3 or {}
                    orig_q = paused_l3.get("user_text") or session_data.get("last_user_query", "继续执行未完成的任务")
                    if (orig_q or "").lstrip().startswith(("[AUTO_RESUME_AFTER_L3_APPROVAL]", "[CONTINUE_PAUSED_TASK]")):
                        m_orig_q = re.search(r"-\s*前序任务目标：(.+)", orig_q)
                        orig_q = m_orig_q.group(1).strip() if m_orig_q else (session_data.get("last_user_query") or "继续执行未完成的任务")

                    comp_steps = list(paused_l3.get("completed_steps") or [])
                    plan_steps = list(paused_l3.get("planned_steps") or [])
                    prior_elapsed_s = int(paused_l3.get("elapsed_seconds") or 0) + max(1, int(round(elapsed)))
                    approved_list = list(session_data.get("_approved_l3_commands") or [])
                    if final_status == "success":
                        for _c in (command.strip(), clean_cmd.strip()):
                            if _c and _c not in approved_list:
                                approved_list.append(_c)
                    session_data["_approved_l3_commands"] = approved_list[-10:]
                    if is_turn_scope:
                        session_data["_l3_authorize_all_turn"] = True
                    session_data["_is_authorized_l3_resume"] = True
                    step_tag = f"授权执行: {clean_cmd[:24]}" if final_status == "success" else f"授权自愈: {clean_cmd[:24]}"
                    session_data["_resumed_task_progress"] = {
                        "user_text": orig_q,
                        "elapsed_seconds": prior_elapsed_s,
                        "completed_steps": comp_steps + [step_tag],
                        "planned_steps": plan_steps,
                        "current_plan_idx": int(paused_l3.get("current_plan_idx") or 0),
                        "max_progress_pct": int(paused_l3.get("max_progress_pct") or 0),
                    }
                    await save_session_async(chat_id, session_data)

                    steps_str = " -> ".join(comp_steps[-8:]) if comp_steps else "无"
                    scope_str = "⚡ 本轮后续所有高危指令免密放行 (本轮结束自动失效)" if is_turn_scope else "仅限本次指令"

                    if final_status == "success":
                        if is_non_shell_tool:
                            action_guide = (
                                "管理员已批准该高危工具操作权限，请直接继续执行刚才被暂缓的操作并推进完成用户最初交办的任务，完成后向用户汇报最终结果！"
                            )
                        else:
                            action_guide = (
                                "请直接基于上述指令执行输出结果继续推进完成用户最初交办的任务，严禁重复执行刚刚已获批的指令，完成后向用户汇报最终结果！"
                            )
                        status_line = f"成功完成 (退出码: {ret_code})"
                    elif is_truncated or is_unterminated_quote:
                        status_line = f"单行指令超长截断/引号未闭合保护 (状态码: {ret_code})"
                        action_guide = (
                            "管理员已批准执行权限，但原单行命令因超长在流式上报中被截断（或引号未闭合）未能直接执行。"
                            "请勿再次拼接超长单行命令（如长 python3 -c / node -e），请改为先将脚本写入临时文件再运行，继续推进并完成用户最初交办的任务！"
                        )
                    else:
                        status_line = f"命令返回非零退出码 (退出码: {ret_code})"
                        action_guide = (
                            "管理员已批准操作权限，但该指令在宿主机执行时返回了非零状态码。"
                            "请根据上述终端报错信息分析原因并自愈修正（如补充工作目录参数、修正语法或调整实现方案，严禁原封不动盲目重复同一条失败指令），继续完成用户最初交办的任务！"
                        )

                    resume_prompt = (
                        f"[AUTO_RESUME_AFTER_L3_APPROVAL]\n"
                        f"管理员已在飞书审批卡片中【确认授权】并已在宿主机完成该高危敏感指令的调度：\n"
                        f"- 授权执行指令：`{command[:500]}`\n"
                        f"- 授权范围：{scope_str}\n"
                        f"- 执行状态：{status_line}\n"
                        f"- 终端输出结果：\n```text\n{out_text[:1200]}\n```\n"
                        f"- 前序任务目标：{orig_q}\n"
                        f"- 已完成步骤：{steps_str}\n\n"
                        f"{action_guide}"
                    )
                    simulated_content = json.dumps({"text": resume_prompt}, ensure_ascii=False)
                    try:
                        await _handle_message_async_internal(
                            card_message_id, chat_id, "text", simulated_content, trusted_internal_resume=True
                        )
                    except TypeError:
                        await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)
                    log.info(
                        f"[L3 Guard] Successfully triggered auto-resume for chat {chat_id} "
                        f"after approved execution (scope={scope}, status={final_status}, ret={ret_code})"
                    )
                except Exception as resume_err:
                    log.error(f"[L3 Guard] Failed to trigger auto-resume after approval: {resume_err}")

            asyncio.run_coroutine_threadsafe(do_execute_approved(), app_state.main_loop)

        toast_msg = "已确认授权：本轮后续高危指令将自动放行，正在后台调度执行..." if is_turn_scope else "已确认授权单次执行，正在后台调度执行..."
        return P2CardActionTriggerResponse({
            "card": {"type": "raw", "data": running_card},
            "toast": {"type": "success", "content": toast_msg}
        })

    elif action_value.get("action") == "extend_wait":
        log.info(f"User requested extend_wait in chat {chat_id}")
        current_extend = app_state.extended_wait_chats.get(chat_id, 0)
        app_state.extended_wait_chats[chat_id] = max(current_extend, time.time()) + 900
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已确认继续等待，后台保持执行并已延长 15 分钟等待。"}})

    elif action_value.get("action") == "continue_long_task":
        log.info(f"User requested continue_long_task in chat {chat_id}")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_continue_long_task():
                try:
                    from math_decision_model import math_decision_engine
                    math_decision_engine.reset_session_risk(chat_id)
                except Exception:
                    pass
                session_data = await get_session_async(chat_id)
                paused = session_data.pop("paused_task_state", None) or {}

                orig_q = paused.get("user_text") or session_data.get("last_user_query") or "继续执行未完成的任务"
                comp_steps = paused.get("completed_steps") or []
                plan_steps = paused.get("planned_steps") or []
                part_reply = paused.get("partial_reply") or ""
                elapsed_s = int(paused.get("elapsed_seconds") or 0)
                reason_s = paused.get("reason") or ""
                scope_lbl = paused.get("scope_label") or ""
                proj_dir = paused.get("project_dir") or session_data.get("project", "默认")

                session_data["_resumed_task_progress"] = {
                    "user_text": orig_q,
                    "elapsed_seconds": elapsed_s,
                    "completed_steps": list(comp_steps),
                    "planned_steps": list(plan_steps),
                    "current_plan_idx": int(paused.get("current_plan_idx") or 0),
                    "max_progress_pct": int(paused.get("max_progress_pct") or 0),
                }
                await save_session_async(chat_id, session_data)

                resumed_card = CardBuilder.build_budget_pause_card(
                    user_text=orig_q,
                    reason=reason_s,
                    completed_steps=comp_steps,
                    planned_steps=plan_steps,
                    partial_reply=part_reply,
                    elapsed_seconds=elapsed_s,
                    scope_label=scope_lbl,
                    current_model=session_data.get("model", "Default"),
                    current_project=proj_dir,
                    resumed_status="continued",
                    current_step_idx=int(paused.get("current_plan_idx") or 0),
                    min_pct=int(paused.get("max_progress_pct") or 0),
                )
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, resumed_card)
                )

                from executor import desensitize_hardware_engineering_text
                safe_orig_q = desensitize_hardware_engineering_text(orig_q)
                steps_str = " -> ".join(comp_steps[-12:]) if comp_steps else "无"
                cont_prompt = (
                    f"[CONTINUE_PAUSED_TASK]\n"
                    f"用户已点击「▶️ 继续执行剩余步骤」。请继续推进此前因步数/耗时预算暂停的任务：\n"
                    f"- 原始指令：{safe_orig_q}\n"
                    f"- 已完成步骤 ({len(comp_steps)}步)：{steps_str}\n"
                )
                if part_reply:
                    cont_prompt += f"- 前序阶段性进展：{desensitize_hardware_engineering_text(part_reply[:600])}\n"
                cont_prompt += "请直接基于项目记忆胶囊与上述已完成进展继续推进剩余未完成的步骤，完成后输出完整结论，严禁从头重复已完成的探查！"

                simulated_content = json.dumps({"text": cont_prompt}, ensure_ascii=False)
                try:
                    await _handle_message_async_internal(
                        card_message_id, chat_id, "text", simulated_content, trusted_internal_resume=True
                    )
                except TypeError:
                    await _handle_message_async_internal(card_message_id, chat_id, "text", simulated_content)

            asyncio.run_coroutine_threadsafe(do_continue_long_task(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "🚀 已确认继续执行！正在为您接力推进剩余步骤..."}})

    elif action_value.get("action") == "stop_paused_task":
        log.info(f"User requested stop_paused_task in chat {chat_id}")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_stop_paused_task():
                session_data = await get_session_async(chat_id)
                paused = session_data.pop("paused_task_state", None) or {}
                session_data.pop("_paused_message_reaction", None)
                session_data["last_execution_paused"] = False
                await save_session_async(chat_id, session_data)
                stopped_card = CardBuilder.build_budget_pause_card(
                    user_text=paused.get("user_text", "阶段性任务"),
                    reason=paused.get("reason", ""),
                    completed_steps=paused.get("completed_steps", []),
                    planned_steps=paused.get("planned_steps", []),
                    partial_reply=paused.get("partial_reply", ""),
                    elapsed_seconds=int(paused.get("elapsed_seconds") or 0),
                    scope_label=paused.get("scope_label", ""),
                    current_model=session_data.get("model", "Default"),
                    current_project=paused.get("project_dir") or session_data.get("project", "默认"),
                    resumed_status="stopped",
                    current_step_idx=int(paused.get("current_plan_idx") or 0),
                    min_pct=int(paused.get("max_progress_pct") or 0),
                )
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, stopped_card)
                )

            asyncio.run_coroutine_threadsafe(do_stop_paused_task(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已结束追加执行，当前阶段性进度已保存在项目记忆胶囊中。"}})
        
    elif action_value.get("action") == "browse_dir":
        if not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 无目录浏览权限"}})
        target_path = action_value.get("path")

        async def _do_browse_dir():
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_dir_browser_card(
                target_path,
                session_data.get("recent_projects", []),
                workspace_root=session_data.get("workspace_root"),
                ignored_projects=session_data.get("ignored_projects", []),
            )

        return _respond_card_sync_or_patch(_do_browse_dir, card_message_id, "已载入目录")

    elif action_value.get("action") in ("toggle_project_form", "create_project_prompt", "prompt_custom_workspace_root", "prompt_custom_project_path"):
        if not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 无工作区设置权限"}})
        act_name = action_value.get("action")
        if act_name == "create_project_prompt":
            target_form = "create"
        elif act_name in ("prompt_custom_workspace_root", "prompt_custom_project_path"):
            target_form = "root"
        else:
            target_form = str(action_value.get("form") or "")
        page_num = int(action_value.get("page") or 1)

        async def _do_toggle_proj_form():
            session_data = await get_session_async(chat_id)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            return CardBuilder.build_dir_browser_card(
                session_data.get("project", "默认"),
                session_data.get("recent_projects", []),
                recent_page=page_num,
                workspace_root=session_data.get("workspace_root"),
                ignored_projects=session_data.get("ignored_projects", []),
                active_form=target_form,
            )

        return _respond_card_sync_or_patch(
            _do_toggle_proj_form,
            card_message_id,
            "请在卡片表单中填写" if target_form else "已收起表单",
            toast_type="info",
        )

    elif action_value.get("action") == "project_root_submit":
        if not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 无工作区根目录设置权限"}})
        raw_root = str(form_value.get("workspace_root_input") or "").strip()
        if not raw_root:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请输入根目录绝对路径"}})
        target_root = os.path.abspath(os.path.expanduser(raw_root))
        forbidden_sys_dirs = ("/etc", "/root", "/proc", "/sys", "/boot", "/dev", "/var/log")
        if not is_admin(chat_id) and any(target_root == d or target_root.startswith(d + "/") for d in forbidden_sys_dirs):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 禁止非管理员绑定系统敏感目录"}})
        if not os.path.isdir(target_root):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": f"目录不存在：{target_root}"}})

        async def _do_save_root():
            session_data = await get_session_async(chat_id)
            session_data["workspace_root"] = target_root
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            return CardBuilder.build_dir_browser_card(
                session_data.get("project", "默认"),
                session_data.get("recent_projects", []),
                recent_page=1,
                workspace_root=target_root,
                ignored_projects=session_data.get("ignored_projects", []),
                notice=f"公共项目根目录已更新为 `{target_root}`",
            )

        return _respond_card_sync_or_patch(_do_save_root, card_message_id, "根目录已保存")

    elif action_value.get("action") == "project_create_submit":
        if not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 无创建项目权限"}})
        raw_target = str(form_value.get("project_target") or "").strip()
        if not raw_target or raw_target.startswith("-"):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请输入有效的项目名称或 Git 仓库地址"}})

        is_git = bool(re.match(r"^(https?://|git@|git://)[^\s]+$", raw_target, re.IGNORECASE)) or (raw_target.endswith(".git") and "/" in raw_target)
        if is_git:
            if app_state.main_loop and app_state.main_loop.is_running():
                async def _do_git_clone_proj():
                    from commands import _handle_create_project
                    session_data = await get_session_async(chat_id)
                    await _handle_create_project(raw_target, card_message_id, chat_id, session_data)
                    session_data = await get_session_async(chat_id)
                    updated_card = CardBuilder.build_dir_browser_card(
                        session_data.get("project", "默认"),
                        session_data.get("recent_projects", []),
                        recent_page=1,
                        workspace_root=session_data.get("workspace_root"),
                        ignored_projects=session_data.get("ignored_projects", []),
                        notice=f"已完成仓库克隆并切换工作区",
                    )
                    if card_message_id:
                        await asyncio.to_thread(patch_interactive_card_sdk, card_message_id, updated_card)
                asyncio.run_coroutine_threadsafe(_do_git_clone_proj(), app_state.main_loop)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在后台克隆 Git 仓库并切换工作区..."}})

        async def _do_local_create_proj():
            from config import WORKSPACE_ROOT
            session_data = await get_session_async(chat_id)
            ws_root = session_data.get("workspace_root")
            parent_p = os.path.abspath(os.path.expanduser(ws_root if ws_root and os.path.isdir(ws_root) else WORKSPACE_ROOT))
            expanded = os.path.abspath(os.path.expanduser(raw_target))
            forbidden_sys = ("/etc", "/root", "/proc", "/sys", "/boot", "/dev", "/var/log")
            if not is_admin(chat_id) and any(expanded == d or expanded.startswith(d + "/") for d in forbidden_sys):
                return None, "error", "🔒 禁止绑定系统敏感目录"
            if os.path.isdir(expanded):
                new_path = expanded
                notice_txt = f"已切换至现有目录 `{os.path.basename(new_path) or new_path}`"
            else:
                clean_name = re.sub(r"[^a-zA-Z0-9._\-\u4e00-\u9fa5]", "_", raw_target)[:32].strip(".") or "project"
                new_path = os.path.abspath(os.path.join(parent_p, clean_name))
                os.makedirs(new_path, exist_ok=True)
                notice_txt = f"已新建项目并切换至 `{clean_name}`"
            session_data["project"] = new_path
            recent = list(session_data.get("recent_projects", []))
            if new_path in recent:
                recent.remove(new_path)
            recent.insert(0, new_path)
            session_data["recent_projects"] = recent[:8]
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            card = CardBuilder.build_dir_browser_card(
                new_path,
                recent,
                recent_page=1,
                workspace_root=ws_root,
                ignored_projects=session_data.get("ignored_projects", []),
                notice=notice_txt,
            )
            return card, "success", notice_txt[:40]

        return _respond_card_sync_or_patch(_do_local_create_proj, card_message_id, "项目已创建并切换")

    elif action_value.get("action") == "select_project":
        if not (is_admin(chat_id) or has_scope(chat_id, SCOPE_PROJECT)):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 无工作区切换权限"}})
        target_path = action_value.get("path")
        if target_path and not is_admin(chat_id):
            abs_tp = os.path.abspath(target_path)
            if any(abs_tp == d or abs_tp.startswith(d + "/") for d in ("/etc", "/root", "/proc", "/sys", "/boot", "/dev")):
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 禁止绑定系统敏感目录"}})

        async def _do_select_project():
            session_data = await get_session_async(chat_id)
            session_data["project"] = target_path
            recent = list(session_data.get("recent_projects", []))
            if target_path in recent:
                recent.remove(target_path)
            recent.insert(0, target_path)
            session_data["recent_projects"] = recent[:8]
            await save_session_async(chat_id, session_data)
            try:
                from session_pool import session_pool
                from system_one_gate import infer_effort_from_model
                model_to_warm = session_data.get("base_model") or session_data.get("model", "Default")
                effort_to_warm = infer_effort_from_model(model_to_warm, session_data.get("reasoning_effort", "medium"))
                cwd_dir = target_path if (target_path and os.path.isdir(target_path) and target_path not in ["默认", "Default"]) else None
                conv_id = session_data.get("conversation", "")
                asyncio.create_task(session_pool.prewarm(
                    chat_id, model_to_warm, cwd_dir, conversation_id=conv_id, effort=effort_to_warm
                ))
            except Exception:
                pass
            proj_name = os.path.basename(str(target_path)) or target_path
            card = CardBuilder.build_dir_browser_card(
                target_path,
                recent,
                recent_page=1,
                workspace_root=session_data.get("workspace_root"),
                ignored_projects=session_data.get("ignored_projects", []),
                notice=f"当前工作区已切换为 `{proj_name}`（后台已预热会话）",
            )
            return card, "success", f"已切换至 {proj_name}"

        return _respond_card_sync_or_patch(_do_select_project, card_message_id, "项目切换成功！")

    elif action_value.get("action") == "remove_project_from_list":
        target_path = action_value.get("path")
        page_num = int(action_value.get("page") or 1)

        async def _do_remove_project():
            session_data = await get_session_async(chat_id)
            ignored = list(session_data.get("ignored_projects", []))
            if target_path not in ignored:
                ignored.append(target_path)
            session_data["ignored_projects"] = ignored
            recent = [p for p in session_data.get("recent_projects", []) if p != target_path]
            session_data["recent_projects"] = recent
            await save_session_async(chat_id, session_data)
            return CardBuilder.build_dir_browser_card(
                session_data.get("project", "默认"),
                recent,
                recent_page=page_num,
                workspace_root=session_data.get("workspace_root"),
                ignored_projects=ignored,
                notice=f"已从列表隐藏 `{os.path.basename(str(target_path)) or target_path}`",
            )

        return _respond_card_sync_or_patch(_do_remove_project, card_message_id, "已从列表中隐藏")

    elif action_value.get("action") == "browse_recent_page":
        target_path = action_value.get("current_path")
        target_page = int(action_value.get("page", 1))

        async def _do_browse_recent_page():
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_dir_browser_card(
                target_path or session_data.get("project", "默认"),
                session_data.get("recent_projects", []),
                target_page,
                workspace_root=session_data.get("workspace_root"),
                ignored_projects=session_data.get("ignored_projects", []),
            )

        return _respond_card_sync_or_patch(_do_browse_recent_page, card_message_id, f"第 {target_page} 页")

    elif action_value.get("action") in ("toggle_note_form", "prompt_add_note"):
        show_form = True if action_value.get("action") == "prompt_add_note" else bool(action_value.get("show", True))
        page_num = int(action_value.get("page") or 1)

        async def _do_toggle_note_form():
            session_data = await get_session_async(chat_id)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            return CardBuilder.build_note_list_card(
                session_data.get("notes", []), show_add_form=show_form, page=page_num
            )

        return _respond_card_sync_or_patch(
            _do_toggle_note_form, card_message_id, "请在卡片内输入笔记内容" if show_form else "已收起输入框", toast_type="info"
        )

    elif action_value.get("action") == "note_add_submit":
        note_content = str(form_value.get("note_content") or "").strip()
        if not note_content:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请输入笔记内容"}})

        async def _do_add_note():
            session_data = await get_session_async(chat_id)
            notes = list(session_data.get("notes", []))
            notes.append(note_content)
            session_data["notes"] = notes
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            last_page = max(1, (len(notes) + 7) // 8)
            return CardBuilder.build_note_list_card(notes, page=last_page, notice="已新增一条笔记")

        return _respond_card_sync_or_patch(_do_add_note, card_message_id, "笔记已保存！")

    elif action_value.get("action") == "note_page":
        page_num = int(action_value.get("page") or 1)

        async def _do_note_page():
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_note_list_card(session_data.get("notes", []), page=page_num)

        return _respond_card_sync_or_patch(_do_note_page, card_message_id, f"第 {page_num} 页", toast_type="info")

    elif action_value.get("action") == "view_note_detail":
        idx = int(action_value.get("index", -1))
        page_num = int(action_value.get("page") or 1)

        async def _do_view_note():
            session_data = await get_session_async(chat_id)
            notes = session_data.get("notes", [])
            exp = idx if (0 <= idx < len(notes)) else None
            return CardBuilder.build_note_list_card(notes, expanded_idx=exp, page=page_num)

        return _respond_card_sync_or_patch(
            _do_view_note,
            card_message_id,
            "已在卡片内展开详情" if idx >= 0 else "已收起详情",
            toast_type="info",
        )

    elif action_value.get("action") == "delete_note":
        idx = int(action_value.get("index"))
        page_num = int(action_value.get("page") or 1)

        async def _do_delete_note():
            session_data = await get_session_async(chat_id)
            notes = list(session_data.get("notes", []))
            if 0 <= idx < len(notes):
                notes.pop(idx)
                session_data["notes"] = notes
                await save_session_async(chat_id, session_data)
            return CardBuilder.build_note_list_card(notes, page=page_num)

        return _respond_card_sync_or_patch(_do_delete_note, card_message_id, "已删除该条笔记！")

    elif action_value.get("action") == "clear_notes":
        async def _do_clear_notes():
            session_data = await get_session_async(chat_id)
            session_data["notes"] = []
            await save_session_async(chat_id, session_data)
            return CardBuilder.build_note_list_card([])

        return _respond_card_sync_or_patch(_do_clear_notes, card_message_id, "记事本已全部清空！")

    elif action_value.get("action") == "refresh_status":
        if "chat_id" in action_value or action_value.get("plugin"):
            # 允许插件专属状态刷新逻辑穿透分发至 plugin_manager
            pass
        else:
            if not is_admin(chat_id):
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "仅管理员可查看系统状态。"}})
            fetch_remote = bool(action_value.get("fetch_remote", False))

            async def _do_refresh_status():
                from commands import get_system_status_card_data
                cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats = await asyncio.to_thread(
                    get_system_status_card_data, fetch_remote
                )
                return CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, err_logs, git_status, bot_stats)

            return _respond_card_sync_or_patch(
                _do_refresh_status,
                card_message_id,
                "已完成远端版本检查与刷新" if fetch_remote else "状态已秒级刷新！",
                sync_timeout=0.8 if fetch_remote else 1.5,
            )

    elif action_value.get("action") == "refresh_feishu_perm":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 仅管理员可执行飞书权限体检！"}})

        async def _do_refresh_perm():
            from utils.feishu_perm_checker import check_feishu_permissions
            rep = await check_feishu_permissions(
                chat_id=chat_id,
                sender_open_id=operator_open_id or "",
                force_token_refresh=True,
            )
            card = CardBuilder.build_perm_check_card(rep)
            toast_msg = (
                "🎉 权限已就绪，CardKit 2.0 流式引擎已自动热激活！"
                if rep.get("cardkit_newly_activated")
                else "飞书应用权限体检已刷新！"
            )
            return card, "success" if rep.get("ok") else "warning", toast_msg

        return _respond_card_sync_or_patch(
            _do_refresh_perm, card_message_id, "飞书应用权限体检已刷新！", sync_timeout=1.6
        )

    elif action_value.get("action") == "refresh_quota":
        async def _do_refresh_quota():
            from utils.quota import fetch_quota
            from cards.stats_cards import get_antigravity_account
            await asyncio.to_thread(get_antigravity_account, True)
            quota_data = await asyncio.to_thread(fetch_quota, True)
            return CardBuilder.build_quota_card(quota_data)

        return _respond_card_sync_or_patch(_do_refresh_quota, card_message_id, "额度已刷新！", sync_timeout=1.4)

    elif action_value.get("action") in ("toggle_memory_form", "prompt_add_memory"):
        show_form = True if action_value.get("action") == "prompt_add_memory" else bool(action_value.get("show", True))
        page_num = int(action_value.get("page") or 1)

        async def _do_toggle_mem_form():
            session_data = await get_session_async(chat_id)
            session_data.pop("pending_command", None)
            await save_session_async(chat_id, session_data)
            memories = await get_profile_async(chat_id)
            proj_summary = None
            try:
                from agent_memory import get_project_memory_summary
                proj_summary = get_project_memory_summary((session_data or {}).get("project", "默认"), session_data=session_data)
            except Exception:
                pass
            return CardBuilder.build_memory_card(memories, project_memory_summary=proj_summary, show_add_form=show_form, page=page_num)

        return _respond_card_sync_or_patch(
            _do_toggle_mem_form, card_message_id, "请在卡片内输入个人偏好" if show_form else "已收起输入框", toast_type="info"
        )

    elif action_value.get("action") == "memory_add_submit":
        mem_txt = str(form_value.get("memory_text") or "").strip()
        if not mem_txt:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请输入偏好内容"}})

        async def _do_add_mem():
            memories = list(await get_profile_async(chat_id))
            memories.append(mem_txt)
            await save_profile_async(chat_id, memories)
            session_data = await get_session_async(chat_id)
            if session_data.pop("pending_command", None) is not None:
                await save_session_async(chat_id, session_data)
            proj_summary = None
            try:
                from agent_memory import get_project_memory_summary
                proj_summary = get_project_memory_summary((session_data or {}).get("project", "默认"), session_data=session_data)
            except Exception:
                pass
            last_page = max(1, (len(memories) + 9) // 10)
            return CardBuilder.build_memory_card(
                memories, project_memory_summary=proj_summary, page=last_page, notice="已保存新的个人偏好"
            )

        return _respond_card_sync_or_patch(_do_add_mem, card_message_id, "偏好已保存！")

    elif action_value.get("action") == "memory_page":
        page_num = int(action_value.get("page") or 1)

        async def _do_mem_page():
            memories = await get_profile_async(chat_id)
            session_data = await get_session_async(chat_id)
            proj_summary = None
            try:
                from agent_memory import get_project_memory_summary
                proj_summary = get_project_memory_summary((session_data or {}).get("project", "默认"), session_data=session_data)
            except Exception:
                pass
            return CardBuilder.build_memory_card(memories, project_memory_summary=proj_summary, page=page_num)

        return _respond_card_sync_or_patch(_do_mem_page, card_message_id, f"第 {page_num} 页", toast_type="info")

    elif action_value.get("action") == "forget_single_memory":
        idx = int(action_value.get("index"))
        page_num = int(action_value.get("page") or 1)

        async def _do_forget():
            memories = list(await get_profile_async(chat_id))
            if 0 <= idx < len(memories):
                removed = memories.pop(idx)
                await save_profile_async(chat_id, memories)
                log.info(f"Removed memory preference: '{removed}' in chat {chat_id}")
            proj_summary = None
            try:
                from agent_memory import get_project_memory_summary
                session_data = await get_session_async(chat_id)
                proj_summary = get_project_memory_summary(
                    (session_data or {}).get("project", "默认"), session_data=session_data
                )
            except Exception:
                pass
            return CardBuilder.build_memory_card(memories, project_memory_summary=proj_summary, page=page_num)

        return _respond_card_sync_or_patch(_do_forget, card_message_id, "已成功擦除该偏好记录！")

    elif action_value.get("action") == "backfill_project_memory":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_backfill_mem():
                from agent_memory import get_project_memory_summary, inspect_and_backfill_transcript
                import config
                session_data = await get_session_async(chat_id)
                memories = await get_profile_async(chat_id)
                proj_dir = (session_data or {}).get("project", "默认")
                conv_id = (session_data or {}).get("conversation", "")
                if conv_id:
                    tx_path = config.get_transcript_path(conv_id)
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: inspect_and_backfill_transcript(tx_path, project_dir=proj_dir, chat_id=chat_id, conv_id=conv_id),
                    )
                proj_summary = get_project_memory_summary(proj_dir, session_data=session_data)
                new_card = CardBuilder.build_memory_card(memories, project_memory_summary=proj_summary)
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, new_card)
                )

            asyncio.run_coroutine_threadsafe(do_backfill_mem(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在扫描并同步当前会话长效记忆..."}})

    elif action_value.get("action") in ("switch_cron_tab", "open_cron_create", "cron_page"):
        tab = "create" if action_value.get("action") == "open_cron_create" else str(action_value.get("tab", "user"))
        page_num = int(action_value.get("page") or 1)

        async def _do_switch_cron():
            from database import get_all_cron_tasks, get_cron_logs
            loop = asyncio.get_running_loop()
            tasks = await loop.run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
            logs = await loop.run_in_executor(None, lambda: get_cron_logs(chat_id=chat_id, limit=15)) if tab == "history" else None
            session_data = await get_session_async(chat_id)
            if session_data.pop("pending_command", None) is not None:
                await save_session_async(chat_id, session_data)
            return CardBuilder.build_cron_panel_card(tasks, active_tab=tab, session_data=session_data, logs=logs, page=page_num)

        tab_labels = {"history": "历史记录", "user": "用户任务", "system": "系统任务", "create": "新建任务表单"}
        return _respond_card_sync_or_patch(
            _do_switch_cron, card_message_id, f"已切换至 {tab_labels.get(tab, tab)}", toast_type="info"
        )

    elif action_value.get("action") == "cron_create_submit":
        import uuid
        raw_name = str(form_value.get("cron_name") or "").strip() or "未命名任务"
        act_type = str(form_value.get("cron_action_type") or "ai_agent").strip()
        if act_type == "shell" and not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 Shell 脚本定时任务仅管理员可创建"}})
        if act_type not in ("ai_agent", "reminder", "shell"):
            act_type = "ai_agent"
        custom_expr = str(form_value.get("cron_custom_expr") or "").strip()
        preset_expr = str(form_value.get("cron_preset") or "0 9 * * *").strip()
        expr = custom_expr or preset_expr
        prompt_txt = str(form_value.get("cron_prompt") or "").strip()
        if not prompt_txt:
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请填写执行 Prompt 或提醒内容"}})

        async def _do_cron_submit():
            from cron_engine import compute_next_run
            from database import get_all_cron_tasks, save_cron_task
            task_type = "delay" if re.match(r"^\d+\s*[smhd]?$", expr.lower()) else "cron"
            now_ts = int(time.time())
            next_run = compute_next_run(expr, task_type, now_ts)
            if not next_run or next_run <= 0:
                return None, "error", f"无效的 Cron 或倒计时表达式：{expr}"
            session_data = await get_session_async(chat_id)
            t_id = f"task_usr_{now_ts}_{uuid.uuid4().hex[:6]}"
            task_data = {
                "id": t_id,
                "chat_id": chat_id,
                "category": "user",
                "name": raw_name,
                "task_type": task_type,
                "action_type": act_type,
                "command": prompt_txt if act_type == "shell" else "",
                "cron_expr": expr,
                "prompt": prompt_txt,
                "project_path": session_data.get("project", ""),
                "is_active": True,
                "created_by": chat_id,
                "created_at": now_ts,
                "next_run_at": next_run,
            }
            await asyncio.to_thread(save_cron_task, task_data)
            tasks = await asyncio.to_thread(get_all_cron_tasks, chat_id)
            card = CardBuilder.build_cron_panel_card(
                tasks,
                active_tab="user",
                session_data=session_data,
                notice=f"已创建任务「**{raw_name}**」（`{expr}`）",
            )
            return card, "success", f"已创建任务：{raw_name}"

        return _respond_card_sync_or_patch(_do_cron_submit, card_message_id, "计划任务已创建！")

    elif action_value.get("action") == "open_cron_panel":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_open_cron():
                from database import get_all_cron_tasks
                tasks = await asyncio.get_running_loop().run_in_executor(None, lambda: get_all_cron_tasks(chat_id))
                session_data = await get_session_async(chat_id)
                new_card = CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)
                from lark_client import send_card_to_chat_sdk
                await asyncio.get_running_loop().run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, new_card))
            asyncio.run_coroutine_threadsafe(do_open_cron(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在打开计划任务中心..."}})

    elif action_value.get("action") == "toggle_cron_active":
        task_id = action_value.get("task_id")
        is_active = bool(action_value.get("is_active", True))
        cur_tab = str(action_value.get("tab") or "user")
        page_num = int(action_value.get("page") or 1)

        async def _do_toggle_cron():
            from database import get_all_cron_tasks, update_cron_task_status
            await asyncio.to_thread(update_cron_task_status, task_id, is_active)
            tasks = await asyncio.to_thread(get_all_cron_tasks, chat_id)
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_cron_panel_card(tasks, active_tab=cur_tab, session_data=session_data, page=page_num)

        return _respond_card_sync_or_patch(
            _do_toggle_cron, card_message_id, f"任务已{'启用' if is_active else '暂停'}"
        )

    elif action_value.get("action") == "delete_cron_task":
        task_id = action_value.get("task_id")
        cur_tab = str(action_value.get("tab") or "user")
        page_num = int(action_value.get("page") or 1)

        async def _do_delete_cron():
            from database import delete_cron_task, get_all_cron_tasks
            await asyncio.to_thread(delete_cron_task, task_id, False)
            tasks = await asyncio.to_thread(get_all_cron_tasks, chat_id)
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_cron_panel_card(tasks, active_tab=cur_tab, session_data=session_data, page=page_num)

        return _respond_card_sync_or_patch(_do_delete_cron, card_message_id, "计划任务已删除！")

    elif action_value.get("action") == "clear_completed_cron":
        async def _do_clear_completed_cron():
            from database import cleanup_completed_delay_tasks, get_all_cron_tasks
            await asyncio.to_thread(cleanup_completed_delay_tasks, chat_id, True)
            tasks = await asyncio.to_thread(get_all_cron_tasks, chat_id)
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_cron_panel_card(tasks, active_tab="user", session_data=session_data)

        return _respond_card_sync_or_patch(_do_clear_completed_cron, card_message_id, "已清理所有已完成/已暂停任务！")

    elif action_value.get("action") == "clear_cron_logs":
        async def _do_clear_cron_logs():
            from database import clear_cron_logs, get_all_cron_tasks
            await asyncio.to_thread(clear_cron_logs, chat_id)
            tasks = await asyncio.to_thread(get_all_cron_tasks, chat_id)
            session_data = await get_session_async(chat_id)
            return CardBuilder.build_cron_panel_card(tasks, active_tab="history", session_data=session_data, logs=[])

        return _respond_card_sync_or_patch(_do_clear_cron_logs, card_message_id, "已清空历史执行记录！")

    elif action_value.get("action") == "run_cron_now":
        task_id = action_value.get("task_id")
        from database import get_cron_task
        task_snapshot = get_cron_task(task_id) if task_id else None
        if not task_snapshot:
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "该一次性任务已完成并自动移除"}})
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_run_now():
                from cron_engine import cron_engine
                asyncio.create_task(cron_engine._run_task_wrapper(task_snapshot))
            asyncio.run_coroutine_threadsafe(do_run_now(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已触发即刻运行计划任务！"}})

    elif action_value.get("action") == "reload_plugins":
        async def _do_reload_plugins():
            from plugin_manager import plugin_manager
            plugin_manager.reload_plugins()
            return CardBuilder.build_plugin_panel_card(plugin_manager.get_plugin_list(), active_tab="installed", notice="已热重载全部插件")

        return _respond_card_sync_or_patch(_do_reload_plugins, card_message_id, "已成功热重载插件库！")

    elif action_value.get("action") in ("switch_plugin_tab", "toggle_plugin_form", "prompt_install_github", "prompt_add_source"):
        act = action_value.get("action")
        if act == "prompt_install_github":
            tab, form_mode = "installed", "install"
        elif act == "prompt_add_source":
            tab, form_mode = "sources", "source"
        else:
            tab = str(action_value.get("tab") or "installed")
            form_mode = str(action_value.get("form") or "")

        async def _do_switch_plugin():
            from plugin_manager import plugin_manager
            return CardBuilder.build_plugin_panel_card(
                plugin_manager.get_plugin_list(), active_tab=tab, active_form=form_mode
            )

        return _respond_card_sync_or_patch(
            _do_switch_plugin,
            card_message_id,
            "已切换插件视图" if not form_mode else "请在表单中填写仓库信息",
            toast_type="info",
        )

    elif action_value.get("action") == "plugin_install_submit":
        repo_url = str(form_value.get("plugin_repo_url") or "").strip()
        if not repo_url or repo_url.startswith("-"):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请输入合法的 GitHub 仓库地址"}})
        if app_state.main_loop and app_state.main_loop.is_running():
            async def _do_form_install_plugin():
                from plugin_store import install_plugin_from_github
                from plugin_manager import plugin_manager
                ok, msg = await asyncio.to_thread(install_plugin_from_github, repo_url)
                if ok:
                    plugin_manager.reload_plugins()
                new_card = CardBuilder.build_plugin_panel_card(
                    plugin_manager.get_plugin_list(), active_tab="installed", notice=msg[:240]
                )
                if card_message_id:
                    await asyncio.to_thread(patch_interactive_card_sdk, card_message_id, new_card)
            asyncio.run_coroutine_threadsafe(_do_form_install_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在后台克隆并安装插件..."}})

    elif action_value.get("action") == "plugin_source_submit":
        s_name = str(form_value.get("source_name") or "").strip()
        s_url = str(form_value.get("source_url") or "").strip()
        s_desc = str(form_value.get("source_desc") or "").strip()
        if not s_name or not s_url or s_url.startswith("-"):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "请填写合法的插件源名称与仓库 URL"}})

        async def _do_add_plugin_src():
            from plugin_store import add_plugin_source
            from plugin_manager import plugin_manager
            await asyncio.to_thread(add_plugin_source, s_name, s_url, s_desc)
            return CardBuilder.build_plugin_panel_card(
                plugin_manager.get_plugin_list(), active_tab="sources", notice=f"已添加插件源「{s_name}」"
            )

        return _respond_card_sync_or_patch(_do_add_plugin_src, card_message_id, "插件源已保存！")

    elif action_value.get("action") == "refresh_store_plugins":
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_refresh_store():
                from plugin_store import fetch_remote_store_plugins
                await asyncio.get_running_loop().run_in_executor(None, lambda: fetch_remote_store_plugins(force_refresh=True))
                from plugin_manager import plugin_manager
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="sources")
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_refresh_store(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "正在刷新 GitHub 插件列表..."}})

    elif action_value.get("action") == "update_plugin":
        plugin_id = action_value.get("plugin_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_update_plugin():
                from plugin_store import update_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: update_plugin(plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed", notice=msg[:200])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_update_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": f"正在从 Git 拉取插件 {plugin_id} 最新代码..."}})

    elif action_value.get("action") == "uninstall_plugin":
        plugin_id = action_value.get("plugin_id")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_uninstall_plugin():
                from plugin_store import uninstall_plugin
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: uninstall_plugin(plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed", notice=msg[:200])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_uninstall_plugin(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": f"正在卸载插件 {plugin_id}..."}})

    elif action_value.get("action") == "install_github_repo":
        repo_url = action_value.get("repo_url")
        plugin_id = action_value.get("plugin_id", "")
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_install_repo():
                from plugin_store import install_plugin_from_github
                ok, msg = await asyncio.get_running_loop().run_in_executor(None, lambda: install_plugin_from_github(repo_url, plugin_id))
                from plugin_manager import plugin_manager
                plugin_manager.reload_plugins()
                p_list = plugin_manager.get_plugin_list()
                new_card = CardBuilder.build_plugin_panel_card(p_list, active_tab="installed", notice=msg[:200])
                await asyncio.get_running_loop().run_in_executor(None, lambda: patch_interactive_card_sdk(card_message_id, new_card))
            asyncio.run_coroutine_threadsafe(do_install_repo(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在一键安装 GitHub 插件仓库..."}})

    elif action_value.get("action") in ("toggle_typesafe_form", "prompt_typesafe_key", "prompt_typesafe_base_url", "prompt_typesafe_model"):
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该设置仅管理员可用！"}})
        show_f = True if action_value.get("action") != "toggle_typesafe_form" else bool(action_value.get("show", True))

        async def _do_toggle_ts_form():
            from system_one_gate import get_typesafe_config_state
            session_data = await get_session_async(chat_id)
            if session_data.pop("pending_command", None) is not None:
                await save_session_async(chat_id, session_data)
            cur = get_typesafe_config_state()
            return CardBuilder.build_typesafe_config_card(
                api_key=cur["api_key"],
                enabled=cur["enabled"],
                model=cur["model"],
                base_url=cur["base_url"],
                tier=cur.get("tier"),
                auto_mode=cur.get("auto_mode"),
                show_credentials_form=show_f,
            )

        return _respond_card_sync_or_patch(
            _do_toggle_ts_form,
            card_message_id,
            "请在卡片安全表单中填写凭据（不在聊天留痕）" if show_f else "已收起凭据表单",
            toast_type="info",
        )

    elif action_value.get("action") == "typesafe_form_submit":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该设置仅管理员可用！"}})
        new_key = str(form_value.get("ts_api_key") or "").strip()
        raw_url = form_value.get("ts_base_url")
        raw_model = str(form_value.get("ts_model") or "").strip()

        async def _do_ts_form_submit():
            from system_one_gate import get_typesafe_config_state, update_typesafe_env
            cur = get_typesafe_config_state()
            kw = {}
            changed = []
            if new_key:
                kw["api_key"] = new_key
                changed.append("API Key")
            if raw_url is not None:
                u_clean = str(raw_url).strip()
                if u_clean.lower() in ("clear", "reset", "default", "默认", "官方"):
                    u_clean = ""
                if u_clean != (cur.get("base_url") or ""):
                    kw["base_url"] = u_clean
                    changed.append("Base URL")
            if raw_model and raw_model != cur.get("model"):
                kw["model"] = raw_model
                changed.append(f"模型 ({raw_model})")
            updated = update_typesafe_env(**kw) if kw else cur
            notice_msg = f"已安全更新：{', '.join(changed)}" if changed else "未检测到字段变更"
            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier"),
                auto_mode=updated.get("auto_mode"),
                show_credentials_form=False,
                notice=notice_msg,
            )

        return _respond_card_sync_or_patch(_do_ts_form_submit, card_message_id, "System One 凭据与配置已热生效！")

    elif action_value.get("action") == "toggle_typesafe_enabled":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})

        async def _do_toggle_ts():
            from system_one_gate import get_typesafe_config_state, update_typesafe_env
            cur = get_typesafe_config_state()
            updated = update_typesafe_env(enabled=not cur["enabled"])
            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier"),
                auto_mode=updated.get("auto_mode"),
            )

        return _respond_card_sync_or_patch(_do_toggle_ts, card_message_id, "System One 网关开关状态已更新！")

    elif action_value.get("action") == "toggle_typesafe_auto_mode":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})

        async def _do_toggle_auto():
            from system_one_gate import get_typesafe_config_state, infer_effort_from_model, update_typesafe_env
            cur = get_typesafe_config_state()
            updated = update_typesafe_env(auto_mode=not cur.get("auto_mode", True))
            try:
                session_data = await get_session_async(chat_id)
                cur_model = session_data.get("base_model") or session_data.get("model", "")
                if cur_model:
                    session_data["model"] = cur_model
                    session_data["reasoning_effort"] = infer_effort_from_model(cur_model, session_data.get("reasoning_effort", "medium"))
                    session_data.pop("typesafe_adaptive_tier", None)
                    await save_session_async(chat_id, session_data)
            except Exception as _sync_err:
                log.warning(f"[toggle_typesafe_auto_mode] Sync session effort failed: {_sync_err}")

            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier"),
                auto_mode=updated.get("auto_mode", True),
            )

        return _respond_card_sync_or_patch(_do_toggle_auto, card_message_id, "自适应思考模式状态已更新！")

    elif action_value.get("action") == "open_typesafe_tier_menu":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})

        async def _do_open_tier_menu():
            from system_one_gate import get_typesafe_config_state
            cur = get_typesafe_config_state()
            return CardBuilder.build_typesafe_tier_menu_card(tier=cur.get("tier", "gateway"))

        return _respond_card_sync_or_patch(_do_open_tier_menu, card_message_id, "已载入决策级别配置", toast_type="info")

    elif action_value.get("action") == "open_typesafe_main_card":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})

        async def _do_open_main_card():
            from system_one_gate import get_typesafe_config_state
            cur = get_typesafe_config_state()
            return CardBuilder.build_typesafe_config_card(
                api_key=cur["api_key"],
                enabled=cur["enabled"],
                model=cur["model"],
                base_url=cur["base_url"],
                tier=cur.get("tier", "gateway"),
                auto_mode=cur.get("auto_mode"),
            )

        return _respond_card_sync_or_patch(_do_open_main_card, card_message_id, "已返回主控制台", toast_type="info")

    elif action_value.get("action") == "set_typesafe_tier":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})
        target_tier = action_value.get("tier", "gateway")
        from_sub_menu = action_value.get("from_sub_menu", False)
        tier_names = {
            "gateway": "L1 · 入口决策",
            "sentry": "L2 · 双向决策",
            "copilot": "L3 · 全流程决策",
        }
        disp_name = tier_names.get(target_tier, target_tier)

        async def _do_set_ts_tier():
            from system_one_gate import update_typesafe_env
            updated = update_typesafe_env(tier=target_tier)
            if from_sub_menu:
                return CardBuilder.build_typesafe_tier_menu_card(tier=updated.get("tier", target_tier))
            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier", target_tier),
                auto_mode=updated.get("auto_mode"),
            )

        return _respond_card_sync_or_patch(_do_set_ts_tier, card_message_id, f"决策级别已切换为 {disp_name}")

    elif action_value.get("action") == "set_typesafe_model":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})
        target_model = action_value.get("model", "jev-latest")

        async def _do_set_ts_model():
            from system_one_gate import update_typesafe_env
            updated = update_typesafe_env(model=target_model)
            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier"),
                auto_mode=updated.get("auto_mode"),
            )

        return _respond_card_sync_or_patch(_do_set_ts_model, card_message_id, f"System One 模型已切换为 {target_model}")

    elif action_value.get("action") == "test_typesafe_ping":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})
        if app_state.main_loop and app_state.main_loop.is_running():
            async def do_ts_ping():
                from system_one_gate import get_typesafe_config_state, test_typesafe_connectivity
                cur = get_typesafe_config_state()
                res = await test_typesafe_connectivity()
                new_card = CardBuilder.build_typesafe_config_card(
                    api_key=cur["api_key"],
                    enabled=cur["enabled"],
                    model=cur["model"],
                    base_url=cur["base_url"],
                    test_result=res,
                    tier=cur.get("tier"),
                    auto_mode=cur.get("auto_mode"),
                )
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: patch_interactive_card_sdk(card_message_id, new_card)
                )
            asyncio.run_coroutine_threadsafe(do_ts_ping(), app_state.main_loop)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "正在发起连通性测试，请查看卡片反馈..."}})

    elif action_value.get("action") == "clear_typesafe_key":
        if not is_admin(chat_id):
            return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "🔒 该操作仅管理员可用！"}})

        async def _do_clear_ts_key():
            from system_one_gate import update_typesafe_env
            updated = update_typesafe_env(api_key="")
            return CardBuilder.build_typesafe_config_card(
                api_key=updated["api_key"],
                enabled=updated["enabled"],
                model=updated["model"],
                base_url=updated["base_url"],
                tier=updated.get("tier"),
                auto_mode=updated.get("auto_mode"),
            )

        return _respond_card_sync_or_patch(_do_clear_ts_key, card_message_id, "已清除 API Key，恢复降级兜底模式")

    # Interactive MCP management panel actions (read/write permissions checked inside handler)
    mcp_resp = handle_mcp_card_action(action_value, chat_id, card_message_id, form_value=form_value)
    if mcp_resp is not None:
        return mcp_resp

    # Interactive Antigravity native plugin management panel actions (/agyplugin)
    agy_plugin_resp = handle_agy_plugin_card_action(action_value, chat_id, card_message_id, form_value=form_value)
    if agy_plugin_resp is not None:
        return agy_plugin_resp

    # Interactive Antigravity CLI Skills management panel actions (/skills)
    skill_resp = handle_skill_card_action(action_value, chat_id, card_message_id, form_value=form_value)
    if skill_resp is not None:
        return skill_resp

    # Dispatch to plugin manager
    action_name = action_value.get("action", "")
    if action_name and app_state.main_loop and app_state.main_loop.is_running():
        async def do_dispatch_plugin_card():
            from plugin_manager import plugin_manager
            await plugin_manager.dispatch_card_action(action_name, action_value, chat_id, card_message_id)
        asyncio.run_coroutine_threadsafe(do_dispatch_plugin_card(), app_state.main_loop)

    resp = handle_auth_card_action(action_value, chat_id, card_message_id)
    if resp is not None:
        return resp

    return P2CardActionTriggerResponse()
