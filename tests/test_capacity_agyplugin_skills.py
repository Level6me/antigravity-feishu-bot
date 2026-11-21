"""Unit tests for:
1. Interactive 503 capacity choice card (`build_capacity_choice_card` & `capacity_choice_decision`).
2. Interactive Antigravity native plugin management panel (`/agyplugin`).
3. Antigravity CLI Skills management & interactive panel (`/skills`).
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class TestCapacityChoiceAndPanels(unittest.TestCase):
    def setUp(self):
        from utils.model_health import clear_health_cache
        clear_health_cache()

    def test_default_auto_fallback_is_disabled_and_candidate_models_ranked(self):
        import config
        from utils.model_health import (
            clear_model_health,
            is_model_circuit_open,
            list_healthy_candidate_models,
            mark_model_healthy,
            mark_model_unhealthy,
        )

        self.assertFalse(config.AUTO_FALLBACK_ON_CAPACITY_ERROR)

        mark_model_unhealthy("gemini-4-argon-high", reason="503 No capacity")
        self.assertTrue(is_model_circuit_open("gemini-4-argon-high"))

        mark_model_healthy("gemini-4-neon-high", latency_ms=320.0)
        catalog = [
            "gemini-4-argon-high\tGemini 4 Argon (High)",
            "gemini-4-argon-medium\tGemini 4 Argon (Medium)",
            "gemini-4-neon-high\tGemini 4 Neon (High)",
            "claude-sonnet-4-6-high\tClaude Sonnet 4.6 (High)",
        ]
        candidates = list_healthy_candidate_models(
            "gemini-4-argon-high",
            available_models=catalog,
            preferred_tier="high",
            limit=6,
        )
        self.assertGreaterEqual(len(candidates), 3)
        # Healthy cross-family model must rank first
        self.assertEqual(candidates[0]["id"], "gemini-4-neon-high")
        self.assertEqual(candidates[0]["health"], "healthy")

        # Clearing health record allows retrying current model
        clear_model_health("gemini-4-argon-high")
        self.assertFalse(is_model_circuit_open("gemini-4-argon-high"))

    def test_build_capacity_choice_card_and_resolved_states(self):
        from card_builder import CardBuilder

        candidates = [
            {"id": "gemini-4-neon-high", "label": "Gemini 4 Neon (High)", "health": "healthy", "family": "gemini-4-neon"},
            {"id": "claude-sonnet-4-6-high", "label": "Claude Sonnet 4.6 (High)", "health": "unknown", "family": "claude-sonnet-4-6"},
        ]
        card = CardBuilder.build_capacity_choice_card(
            failed_model="gemini-4-argon-high",
            user_text="重构数据库层并运行全部测试",
            candidate_models=candidates,
            recommended_model="gemini-4-neon-high",
            current_project="/home/jiang/github/antigravity-feishu-bot",
        )
        raw = json.dumps(card, ensure_ascii=False)
        self.assertIn("模型算力不足", raw)
        self.assertIn("capacity_choice_decision", raw)
        self.assertIn("retry_current", raw)
        self.assertIn("switch_and_continue", raw)
        self.assertIn("switch_form_and_continue", raw)
        self.assertIn("gemini-4-neon-high", raw)
        self.assertIn("claude-sonnet-4-6-high", raw)

        retry_card = CardBuilder.build_capacity_choice_card(
            failed_model="gemini-4-argon-high",
            user_text="重构数据库层",
            resolved_status="retry_current",
            resolved_model="gemini-4-argon-high",
        )
        self.assertIn("已选择继续使用当前模型重试", json.dumps(retry_card, ensure_ascii=False))

        switched_card = CardBuilder.build_capacity_choice_card(
            failed_model="gemini-4-argon-high",
            user_text="重构数据库层",
            resolved_status="switched",
            resolved_model="gemini-4-neon-high",
        )
        self.assertIn("已切换至 gemini-4-neon-high 并继续执行", json.dumps(switched_card, ensure_ascii=False))

    def test_capacity_choice_card_action_retry_and_switch(self):
        import app_state
        from handlers.card_actions import do_p2_card_action_trigger

        patched_cards = []
        dispatched_messages = []

        def _make_event(action_val, form_val=None):
            ev = MagicMock()
            ev.event.action.value = action_val
            ev.event.action.form_value = form_val or {}
            ev.event.context.open_chat_id = "oc_test_cap"
            ev.event.context.open_message_id = "om_cap_card"
            ev.event.operator.open_id = "ou_admin"
            return ev

        async def run():
            loop = asyncio.get_running_loop()
            old_loop = app_state.main_loop
            app_state.main_loop = loop
            try:
                sess_store = {
                    "model": "gemini-4-argon-high",
                    "base_model": "gemini-4-argon-high",
                    "project": ROOT_DIR,
                    "paused_capacity_state": {
                        "user_text": "继续分析当前代码架构",
                        "failed_model": "gemini-4-argon-high",
                        "recommended_model": "gemini-4-neon-high",
                        "completed_steps": ["正在读取文件: main.py"],
                        "planned_steps": ["读取代码", "输出结论"],
                        "current_plan_idx": 1,
                        "max_progress_pct": 50,
                        "elapsed_seconds": 12,
                        "project_dir": ROOT_DIR,
                    },
                }

                with patch("handlers.card_actions.ALLOWED_USERS", []), \
                     patch("handlers.card_actions.ALLOWED_CHATS", []), \
                     patch("handlers.card_actions.get_role", return_value="admin"), \
                     patch("handlers.card_actions.get_session_async", AsyncMock(side_effect=lambda cid: sess_store)), \
                     patch("handlers.card_actions.save_session_async", AsyncMock()), \
                     patch("handlers.card_actions.patch_interactive_card_sdk", side_effect=lambda mid, c: patched_cards.append(c) or True), \
                     patch("handlers.card_actions._handle_message_async_internal", AsyncMock(side_effect=lambda mid, cid, mtype, content: dispatched_messages.append(json.loads(content)["text"]))), \
                     patch("session_pool.session_pool.reset_session", AsyncMock()):
                    # Test switch via dropdown form
                    resp = do_p2_card_action_trigger(
                        _make_event(
                            {
                                "action": "capacity_choice_decision",
                                "decision": "switch_form_and_continue",
                                "failed_model": "gemini-4-argon-high",
                            },
                            form_val={"capacity_target_model": "claude-sonnet-4-6-high"},
                        )
                    )
                    self.assertIsNotNone(resp)
                    await asyncio.sleep(0.08)

                self.assertEqual(sess_store["model"], "claude-sonnet-4-6-high")
                self.assertEqual(dispatched_messages, ["继续分析当前代码架构"])
                self.assertGreaterEqual(len(patched_cards), 1)
                self.assertIn("claude-sonnet-4-6-high", json.dumps(patched_cards[-1], ensure_ascii=False))
            finally:
                app_state.main_loop = old_loop

        asyncio.run(run())

    def test_agy_plugin_manager_and_interactive_card(self):
        from card_builder import CardBuilder
        from utils.agy_plugin_manager import list_agy_plugins, _update_local_plugin_config_enabled

        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = os.path.join(tmpdir, "plugins")
            sample_p = os.path.join(p_root, "demo-bundle")
            os.makedirs(os.path.join(sample_p, "skills", "my-skill"), exist_ok=True)
            os.makedirs(os.path.join(sample_p, "rules"), exist_ok=True)
            with open(os.path.join(sample_p, "plugin.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "name": "demo-bundle",
                        "displayName": "Demo Bundle Plugin",
                        "version": "1.2.0",
                        "description": "Bundled skills and rules for testing",
                        "suggestedPrompts": ["Run demo workflow"],
                    },
                    f,
                )
            with open(os.path.join(sample_p, "skills", "my-skill", "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: my-skill\ndescription: test\n---\nBody\n")
            with open(os.path.join(sample_p, "rules", "coding.md"), "w", encoding="utf-8") as f:
                f.write("# Rule\n")

            cfg_file = os.path.join(tmpdir, "config.json")
            _update_local_plugin_config_enabled("demo-bundle", False, config_path=cfg_file)

            async def run():
                with patch("utils.agy_plugin_manager._exec_agy_plugin", AsyncMock(return_value=(True, "[]", ""))):
                    plugins, err = await list_agy_plugins(
                        workspace_dir=tmpdir,
                        plugin_roots=[("global", p_root)],
                        config_paths=[cfg_file],
                    )
                    self.assertIsNone(err)
                    self.assertEqual(len(plugins), 1)
                    self.assertEqual(plugins[0]["name"], "demo-bundle")
                    self.assertFalse(plugins[0]["enabled"])
                    self.assertEqual(plugins[0]["components"]["skills"], 1)
                    self.assertEqual(plugins[0]["components"]["rules"], 1)

                    card = CardBuilder.build_agy_plugin_panel_card(
                        plugins,
                        active_tab="list",
                        expanded_name="demo-bundle",
                        needs_session_reload=True,
                        is_admin_user=True,
                    )
                    raw_card = json.dumps(card, ensure_ascii=False)
                    self.assertIn("Antigravity 原生插件管理中心", raw_card)
                    self.assertIn("Demo Bundle Plugin", raw_card)
                    self.assertIn("my-skill", raw_card)
                    self.assertIn("agy_plugin_toggle", raw_card)
                    self.assertIn("agy_plugin_reload_session", raw_card)

                    card_install = CardBuilder.build_agy_plugin_panel_card(
                        plugins,
                        active_tab="install",
                        is_admin_user=True,
                    )
                    raw_install = json.dumps(card_install, ensure_ascii=False)
                    self.assertIn("从 Claude Code 导入插件", raw_install)
                    self.assertIn("agy_plugin_form_submit", raw_install)

            asyncio.run(run())

    def test_skills_manager_card_and_slash_commands(self):
        from card_builder import CardBuilder
        from commands import handle_slash_command
        from utils.skill_manager import (
            build_active_skills_prompt_block,
            create_custom_skill,
            list_skills,
            remove_custom_skill,
            set_skill_enabled,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_dir = os.path.join(tmpdir, "workspace")
            global_dir = os.path.join(tmpdir, "global_skills")
            os.makedirs(ws_dir, exist_ok=True)
            os.makedirs(global_dir, exist_ok=True)

            # 1. Create custom workspace skill
            ok, msg = create_custom_skill(
                "firmware-check",
                "Verify STM32 firmware CRC and headers before flashing",
                "## Steps\n1. Check header\n2. Compute CRC32",
                scope="workspace",
                workspace_dir=ws_dir,
            )
            self.assertTrue(ok, msg)

            roots = [
                ("workspace", os.path.join(ws_dir, ".agents", "skills")),
                ("global", global_dir),
            ]
            skills = list_skills(workspace_dir=ws_dir, extra_roots=roots)
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0]["name"], "firmware-check")
            self.assertTrue(skills[0]["enabled"])

            # 2. Prompt block generation for pinned skill
            prompt_blk = build_active_skills_prompt_block(["firmware-check"], workspace_dir=ws_dir, extra_roots=roots)
            self.assertIn("Active Mounted Antigravity Skills", prompt_blk)
            self.assertIn("firmware-check", prompt_blk)

            # 3. Disable and re-enable skill
            ok_dis, _ = set_skill_enabled("firmware-check", False, workspace_dir=ws_dir, extra_roots=roots)
            self.assertTrue(ok_dis)
            self.assertFalse(list_skills(workspace_dir=ws_dir, extra_roots=roots)[0]["enabled"])

            ok_en, _ = set_skill_enabled("firmware-check", True, workspace_dir=ws_dir, extra_roots=roots)
            self.assertTrue(ok_en)
            self.assertTrue(list_skills(workspace_dir=ws_dir, extra_roots=roots)[0]["enabled"])

            # 4. Build interactive skill card (list & add tabs)
            card_list = CardBuilder.build_skill_panel_card(
                list_skills(workspace_dir=ws_dir, extra_roots=roots),
                active_tab="list",
                expanded_name="firmware-check",
                pinned_skills=["firmware-check"],
                is_admin_user=True,
                workspace_dir=ws_dir,
            )
            raw_list = json.dumps(card_list, ensure_ascii=False)
            self.assertIn("Antigravity Skills 技能管理中心", raw_list)
            self.assertIn("当前会话已显式挂载技能", raw_list)
            self.assertIn("skill_pin_toggle", raw_list)
            self.assertIn("skill_toggle", raw_list)

            card_add = CardBuilder.build_skill_panel_card(
                [],
                active_tab="add",
                is_admin_user=True,
                workspace_dir=ws_dir,
            )
            raw_add = json.dumps(card_add, ensure_ascii=False)
            self.assertIn("skill_install_submit", raw_add)
            self.assertIn("skill_create_submit", raw_add)

            # 5. Test `/skills` and `/agyplugin` slash commands
            sent_cards = []
            session_data = {"project": ws_dir}

            async def run_cmds():
                with patch("commands.is_admin", return_value=True), \
                     patch("commands.save_session_async", AsyncMock()), \
                     patch("commands.send_interactive_card_sdk", side_effect=lambda mid, c: sent_cards.append(c) or "om_1"), \
                     patch("utils.skill_manager.list_skills", return_value=skills), \
                     patch("utils.agy_plugin_manager.list_agy_plugins", AsyncMock(return_value=([], None))):
                    h1, _ = await handle_slash_command("/skills use firmware-check", "m1", "oc_sk", session_data, {}, {}, {})
                    self.assertTrue(h1)
                    self.assertEqual(session_data.get("active_skills"), ["firmware-check"])

                    h2, _ = await handle_slash_command("/agyplugin", "m2", "oc_sk", session_data, {}, {}, {})
                    self.assertTrue(h2)

            asyncio.run(run_cmds())
            self.assertEqual(len(sent_cards), 2)
            self.assertIn("Antigravity Skills 技能管理中心", json.dumps(sent_cards[0], ensure_ascii=False))
            self.assertIn("Antigravity 原生插件管理中心", json.dumps(sent_cards[1], ensure_ascii=False))

            # 6. Remove custom skill
            ok_rm, _ = remove_custom_skill("firmware-check", workspace_dir=ws_dir, extra_roots=roots)
            self.assertTrue(ok_rm)
            self.assertEqual(len(list_skills(workspace_dir=ws_dir, extra_roots=roots)), 0)

    def test_pause_card_and_resumed_card_stage_progress_consistency_and_pause_emoji(self):
        from card_builder import CardBuilder
        from executor import format_tool_action
        from handlers.pipeline import set_emoji

        # 1. Verify ACP "File ..." tool titles are formatted into canonical Chinese actions
        self.assertEqual(
            format_tool_action("read_file", {"Path": "/tmp/a.py"}, raw_action="File a.py"),
            "正在读取文件: a.py",
        )
        self.assertEqual(
            format_tool_action("edit_file", {"Path": "/tmp/b.py"}, raw_action="File b.py"),
            "正在编辑文件: b.py",
        )
        self.assertEqual(
            format_tool_action("write_file", {"Path": "/tmp/c.py"}, raw_action="File c.py"),
            "正在写入文件: c.py",
        )
        self.assertEqual(
            format_tool_action(
                "run_command",
                {"CommandLine": "timeout 20 python3 -m unittest tests/test_x.py", "toolSummary": "Run existing unit test suite"},
            ),
            "正在运行单元测试验证",
        )

        # 2. Verify pause card and resumed execution card have 100% identical stage progress, percentage, bar, and checklist
        planned_steps = [
            "排查现有模块与代码结构",
            "实现核心逻辑与数据模型",
            "编写交互式飞书卡片组件",
            "对接卡片事件与斜杠命令路由",
            "编写并执行回归单元测试",
            "整理代码并输出交付报告",
        ]
        completed_steps = ["正在读取文件: a.py"] * 10 + ["正在编辑文件: b.py"] * 45
        current_plan_idx = 2  # Stage 3/6
        locked_pct = CardBuilder._compute_monotonic_progress_pct(
            current_plan_idx, len(planned_steps), False, len(completed_steps), min_pct=0
        )

        pause_card = CardBuilder.build_budget_pause_card(
            user_text="实现完整的多模块功能",
            reason="单轮工具调用步数已达范围契约上限 (55/55 步)",
            completed_steps=completed_steps,
            planned_steps=planned_steps,
            elapsed_seconds=125,
            current_step_idx=current_plan_idx,
            min_pct=locked_pct,
        )
        resumed_card = CardBuilder.build_tool_indicator(
            "继续推进剩余步骤",
            "实现完整的多模块功能",
            think_seconds=125,
            completed_steps=completed_steps,
            planned_steps=planned_steps,
            current_step_idx=current_plan_idx,
            min_pct=locked_pct,
        )

        pause_md = json.dumps(pause_card, ensure_ascii=False)
        resumed_md = json.dumps(resumed_card, ensure_ascii=False)

        # Both cards must display identical percentage, stage counter (3/6), and tool count (55)
        self.assertIn(f"**{locked_pct}%**", pause_md)
        self.assertIn(f"**{locked_pct}%**", resumed_md)
        self.assertIn("阶段 3/6", pause_md)
        self.assertIn("阶段 3/6", resumed_md)
        self.assertIn("第 3/6 阶段", pause_md)
        self.assertIn("第 3/6 阶段", resumed_md)
        self.assertIn("已调用 55 次工具", pause_md)
        self.assertIn("已调用 55 次工具", resumed_md)

        # Steps 1 & 2 must be completed (✅), Step 3 active (⏸️ on pause card, ⏳ on resumed card), Steps 4~6 pending (⚪)
        self.assertIn("✅ **1. 已排查现有模块与代码结构**", pause_md)
        self.assertIn("✅ **2. 已实现核心逻辑与数据模型**", pause_md)
        self.assertIn("⏸️ **3. 正在编写交互式飞书卡片组件**", pause_md)
        self.assertIn("⚪ 4. 对接卡片事件与斜杠命令路由", pause_md)

        self.assertIn("✅ **1. 已排查现有模块与代码结构**", resumed_md)
        self.assertIn("✅ **2. 已实现核心逻辑与数据模型**", resumed_md)
        self.assertIn("⏳ **3. 正在编写交互式飞书卡片组件**", resumed_md)
        self.assertIn("⚪ 4. 对接卡片事件与斜杠命令路由", resumed_md)

        # 3. Verify set_emoji maps "PAUSE" / "⏸️" to "PAUSE"
        captured_emojis = []

        async def run_emoji():
            with patch("handlers.pipeline.set_emoji_sdk", side_effect=lambda mid, et: captured_emojis.append((mid, et)) or "react_pause_1"):
                rid = await set_emoji("om_msg_1", "PAUSE")
                self.assertEqual(rid, "react_pause_1")
                rid2 = await set_emoji("om_msg_2", "⏸️")
                self.assertEqual(rid2, "react_pause_1")

        asyncio.run(run_emoji())
        self.assertEqual(captured_emojis, [("om_msg_1", "PAUSE"), ("om_msg_2", "PAUSE")])


if __name__ == "__main__":
    unittest.main()
