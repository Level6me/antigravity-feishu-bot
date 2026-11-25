# -*- coding: utf-8 -*-
"""Unit & Regression Suite for Antigravity Feishu Bot v3.4 Interactive Card Upgrades."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app_state
import config
import database
from cards import CardBuilder
from commands import get_system_status_card_data, get_version_string, handle_slash_command, PendingCommand
from handlers.card_actions import do_p2_card_action_trigger
from utils import agy_plugin_manager, mcp_manager, quota, skill_manager


def _make_trigger_event(chat_id: str, action_val: dict, form_val: dict = None, open_id: str = "ou_test"):
    ev = MagicMock()
    ev.event.action.value = action_val
    ev.event.action.form_value = form_val or {}
    ev.event.context.open_chat_id = chat_id
    ev.event.context.open_message_id = "om_card_v34"
    ev.event.operator.open_id = open_id
    return ev


class TestV34InteractiveCards(unittest.IsolatedAsyncioTestCase):
    def test_01_version_bumped_to_v3_4(self):
        self.assertEqual(config.BASE_VERSION_PREFIX, "v3.4.")
        ver = get_version_string("HEAD")
        self.assertTrue(ver.startswith("v3.4."), f"Expected v3.4.x, got {ver}")

    def test_02_fast_status_proc_metrics_and_dual_buttons(self):
        real_run = __import__("subprocess").run
        called_cmds = []

        def _spy_run(cmd, *a, **kw):
            called_cmds.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
            return real_run(cmd, *a, **kw)

        with patch("commands.subprocess.run", side_effect=_spy_run):
            t0 = time.perf_counter()
            cpu, mem_mb, uptime_str, status, restarts, _, git_status, bot_stats = get_system_status_card_data(
                fetch_remote=False
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        for cmd_argv in called_cmds:
            self.assertNotIn("fetch", cmd_argv)
            self.assertNotIn("pm2", cmd_argv)
        self.assertEqual(status, "online")
        self.assertGreaterEqual(mem_mb, 0.0)
        self.assertLess(elapsed_ms, 300.0)
        card = CardBuilder.build_status_card(cpu, mem_mb, uptime_str, status, restarts, "", git_status, bot_stats)
        raw = json.dumps(card, ensure_ascii=False)
        self.assertIn("秒级刷新", raw)
        self.assertIn("检查远端更新", raw)

    def test_03_quota_preserves_warm_cache_and_context_confirm(self):
        quota._AGY_VERSION_CACHE = "1.5.99-cached"
        quota._PORT_CACHE = (time.time(), 45678)
        sample_quota = {
            "response": {
                "groups": [
                    {"displayName": "Gemini", "buckets": [{"displayName": "Pro", "remainingFraction": 0.92}]},
                    {"displayName": "Claude 3P", "buckets": [{"displayName": "Sonnet", "remainingFraction": 0.15}]},
                    {"displayName": "Argon", "buckets": [{"displayName": "Argon", "remainingFraction": 0.80}]},
                ]
            }
        }
        with patch("utils.quota._probe_lsp", return_value=sample_quota):
            res = quota.fetch_quota(force_refresh=True)
        self.assertEqual(res, sample_quota)
        self.assertEqual(quota._AGY_VERSION_CACHE, "1.5.99-cached")
        q_card = CardBuilder.build_quota_card(sample_quota)
        self.assertIn("一键切高余量", json.dumps(q_card, ensure_ascii=False))

        ctx_card = CardBuilder.build_context_card({"model": "gemini-3.8-flash-high", "total_pct": 25.0})
        self.assertIn('"confirm"', json.dumps(ctx_card, ensure_ascii=False))

    async def test_04_cron_inline_form_and_action_type_inference(self):
        create_card = CardBuilder.build_cron_panel_card([], active_tab="create")
        c_str = json.dumps(create_card, ensure_ascii=False)
        self.assertIn("cron_create_form", c_str)
        self.assertIn("cron_create_submit", c_str)

        sys_tasks = [{
            "id": "sys_1",
            "category": "system",
            "name": "系统心跳巡检",
            "task_type": "cron",
            "action_type": "ai_agent",
            "cron_expr": "0 * * * *",
            "prompt": "检查服务健康",
            "is_active": True,
        }]
        sys_card = CardBuilder.build_cron_panel_card(sys_tasks, active_tab="system")
        self.assertIn('"tab": "system"', json.dumps(sys_card, ensure_ascii=False))

        saved_tasks = []
        sess = {"pending_command": PendingCommand.CRON_ADD.value, "project": ROOT_DIR}
        with patch("database.save_cron_task", side_effect=lambda td: saved_tasks.append(td) or True), \
             patch("commands.save_session_async", AsyncMock()), \
             patch("commands.send_interactive_card_sdk", return_value="om_1"):
            handled, _ = await handle_slash_command(
                "日志分析 | 600s | 提取日志并分析报告", "om_msg", "oc_cron_t", sess, {}, {}, {}
            )
        self.assertTrue(handled)
        self.assertEqual(saved_tasks[0]["action_type"], "ai_agent")

    def test_05_project_browser_pinning_and_inline_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            for nm in ("alpha_proj", "beta_proj", "zeta_proj"):
                os.makedirs(os.path.join(tmp, nm))
            zeta_p = os.path.join(tmp, "zeta_proj")
            beta_p = os.path.join(tmp, "beta_proj")
            card = CardBuilder.build_dir_browser_card(
                active_project_path=beta_p,
                recent_projects=[zeta_p],
                workspace_root=tmp,
                active_form="create",
            )
            raw = json.dumps(card, ensure_ascii=False)
            self.assertIn("project_create_form", raw)
            self.assertIn("🙈 隐藏", raw)
            self.assertIn('"disabled": true', raw)
            # Active (beta) and recent (zeta) appear before alphabetical alpha
            self.assertLess(raw.index("beta_proj"), raw.index("zeta_proj"))
            self.assertLess(raw.index("zeta_proj"), raw.index("alpha_proj"))

    def test_06_s1_inline_form_auto_mode_and_slim_l3_payload(self):
        s1_card = CardBuilder.build_typesafe_config_card(
            api_key="ts_live_abcd1234efgh",
            enabled=True,
            model="jev-latest",
            tier="copilot",
            auto_mode=False,
            show_credentials_form=True,
        )
        s1_raw = json.dumps(s1_card, ensure_ascii=False)
        self.assertIn("typesafe_cred_form", s1_raw)
        self.assertIn("锁定手动模式", s1_raw)
        self.assertNotIn("ts_live_abcd1234efgh", s1_raw)  # Masked in display

        huge_cmd = "timeout 30 python3 -c '" + ("x = 1; " * 800) + "'"
        l3_card = CardBuilder.build_l3_high_risk_confirm_card(
            tool_name="run_command",
            command=huge_cmd,
            chat_id="oc_admin",
            project_dir=ROOT_DIR,
            call_id="call_99",
        )
        l3_raw = json.dumps(l3_card, ensure_ascii=False)
        self.assertLess(len(l3_raw), 4500)

    def test_07_note_chinese_multiline_inline_expand_and_memory_form(self):
        long_cn_note = (
            "服务器数据库备份规范说明文档与异地容灾恢复演练操作指南超长第一行测试\n"
            + ("第二行详细运维参数配置说明与故障切换步骤分解" * 3)
            + "\n第四行末尾隐藏详情锚点"
        )
        collapsed = CardBuilder.build_note_list_card([long_cn_note], expanded_idx=None, show_add_form=True)
        col_raw = json.dumps(collapsed, ensure_ascii=False)
        self.assertIn("note_add_form", col_raw)
        self.assertNotIn("第四行末尾隐藏详情锚点", col_raw)

        expanded = CardBuilder.build_note_list_card([long_cn_note], expanded_idx=0)
        exp_raw = json.dumps(expanded, ensure_ascii=False)
        self.assertIn("第四行末尾隐藏详情锚点", exp_raw)
        self.assertIn("🔼 收起", exp_raw)
        self.assertNotIn("\\\\n", exp_raw)

        mem_card = CardBuilder.build_memory_card(["偏好中文精炼输出"], show_add_form=True)
        self.assertIn("memory_add_form", json.dumps(mem_card, ensure_ascii=False))

    def test_08_model_matrix_help_tabs_and_choice_lock(self):
        models = [
            "gemini-3.8-flash-low Gemini 3.8 Flash (Low)",
            "gemini-3.8-flash-medium Gemini 3.8 Flash (Medium)",
            "gemini-3.8-flash-high Gemini 3.8 Flash (High)",
        ]
        m_card = CardBuilder.build_model_panel(models, "gemini-3.8-flash-high", agent_mode="plan")
        m_raw = json.dumps(m_card, ensure_ascii=False)
        self.assertIn("switch_agent_mode", m_raw)
        self.assertIn("✅ High", m_raw)

        h_common = CardBuilder.build_help_card(is_admin_user=False, active_tab="common")
        h_raw = json.dumps(h_common, ensure_ascii=False)
        h_md = "\n".join(el.get("content", "") for el in h_common["elements"] if el.get("tag") == "markdown")
        self.assertIn("help_switch_tab", h_raw)
        self.assertIn("/model", h_md)
        self.assertNotIn("/agyplugin", h_md)  # Filtered out in "common" tab markdown body

        ai_card = CardBuilder.build_ai_response(
            "请选择部署方案",
            choice_card_data={"question": "部署环境", "options": ["方案A: 灰度发布", "方案B: 全量发布"]},
        )
        self.assertIn('"from_choice_card": true', json.dumps(ai_card, ensure_ascii=False))
        locked = CardBuilder.build_choice_locked_card("方案A", "方案A: 灰度发布", question="部署环境")
        self.assertIn('"disabled": true', json.dumps(locked, ensure_ascii=False))

    async def test_09_snapshot_caches_guest_auth_and_sync_ws_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            sk_dir = os.path.join(tmp, ".agents", "skills")
            skill_manager.invalidate_skill_cache()
            ok, _ = skill_manager.create_custom_skill("demo-sk", "Demo trigger", "Step 1", scope="workspace", workspace_dir=tmp)
            self.assertTrue(ok)
            s1 = skill_manager.list_skills(workspace_dir=tmp)
            self.assertTrue(any(x["name"] == "demo-sk" for x in s1))
            with patch("utils.skill_manager._inspect_skill_dir") as mock_insp:
                s2 = skill_manager.list_skills(workspace_dir=tmp, search_query="demo")
                mock_insp.assert_not_called()  # Served from 15s snapshot cache
                self.assertEqual(len(s2), 1)

        hint_card = CardBuilder.build_auth_hint_card()
        self.assertIn("guest_request_auth", json.dumps(hint_card, ensure_ascii=False))

        # Verify worker-thread WS callback returns synchronous `card` payload (zero extra HTTP PATCH)
        old_loop = app_state.main_loop
        app_state.main_loop = asyncio.get_running_loop()
        try:
            holder = {}
            with patch("handlers.card_actions.get_role", return_value="admin"), \
                 patch("handlers.card_actions.is_admin", return_value=True), \
                 patch("handlers.card_actions.patch_interactive_card_sdk") as mock_patch:
                def _ws_worker():
                    ev = _make_trigger_event("oc_admin", {"action": "help_switch_tab", "tab": "project"})
                    holder["resp"] = do_p2_card_action_trigger(ev)

                t = threading.Thread(target=_ws_worker)
                t.start()
                while t.is_alive():
                    await asyncio.sleep(0.01)
                t.join()
                mock_patch.assert_not_called()
                self.assertIsNotNone(getattr(holder["resp"], "card", None))
        finally:
            app_state.main_loop = old_loop


if __name__ == "__main__":
    unittest.main()
