import json
import os
import tempfile
import unittest

from agent_memory.store import (
    add_project_fact,
    check_and_prepare_session_relay,
    classify_task_scope_and_budget,
    get_project_facts,
    get_project_memory_summary,
    inspect_and_backfill_transcript,
    read_project_capsule,
    recall_for_turn,
    record_episodic_turn,
    search_episodic_memory,
    strip_injected_prompt_headers,
    write_turn_contract,
)
from card_builder import CardBuilder
from hooks.agy_hooks import handle_hook_payload


class TestLongHorizonMemoryAndDecisionGuard(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.mem_root = os.path.join(self.tmp_dir.name, "agent_memory")
        self.proj_dir = "/home/jiang/github/luyuan-k50-passport-key"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_strip_injected_prompt_headers(self):
        bloated = (
            "[System Rule: Reply ONLY in Simplified Chinese (简体中文). NEVER output internal chain-of-thought or English preambles.]\n"
            "[Active Project: /home/jiang/github/luyuan-k50-passport-key | Guardrail: terminal commands MUST prefix `timeout <秒数>`; never restart main.py]\n"
            "[Security & Egress Guard: 严禁在最终回复中明文回显任何 API Token、私钥或服务器密码；严禁对生产环境执行未授权破坏性操作。]\n"
            "[TASK_PLAN Rule: 若需调用工具执行操作，回复首行必须先输出 `[TASK_PLAN] 步骤1 | 步骤2 | 步骤3 [/TASK_PLAN]`]\n"
            "[User's Relevant Notes]\n"
            "- github令牌 ghp_1234567890abcdefghijklmnopqrstuvwx\n"
            "- 树莓派ip 43.108.18.47 用户名jiang 密码1314520jh 端口2314\n"
            "[System One Adaptive Reasoning: HIGH]\n"
            "当前任务判定为多步复杂工程或系统操作，请开启深度思考推理与严谨边界校验。\n\n"
            "去看看树莓派上有没有安装 Saleae Logic 2 软件"
        )
        cleaned = strip_injected_prompt_headers(bloated)
        self.assertEqual(cleaned, "去看看树莓派上有没有安装 Saleae Logic 2 软件")

    def test_classify_task_scope_and_budget(self):
        # 1. Pure inspection / query -> scope == inspect, 15 steps / 240s, allow_write == False
        s_inspect = classify_task_scope_and_budget(
            "去看看树莓派上有没有安装 Saleae Logic 2 软件", tier="Medium", is_operation=True
        )
        self.assertEqual(s_inspect["scope"], "inspect")
        self.assertEqual(s_inspect["max_steps"], 15)
        self.assertFalse(s_inspect["allow_write"])

        s_why = classify_task_scope_and_budget(
            "你去树莓派看看为什么当前执行的任务耗费时间这么长 是什么原因", tier="High", is_operation=True
        )
        self.assertEqual(s_why["scope"], "inspect")

        # 2. Modification task -> scope == modify
        s_mod = classify_task_scope_and_budget(
            "修复温池复用逻辑并编写单元测试", tier="High", is_operation=True
        )
        self.assertEqual(s_mod["scope"], "modify")
        self.assertTrue(s_mod["allow_write"])

        # 3. Hardware device operation -> scope == execute_device
        s_dev = classify_task_scope_and_budget(
            "使用 openocd 把编译好的固件烧录到芯片并复位测试", tier="High", is_operation=True
        )
        self.assertEqual(s_dev["scope"], "execute_device")

        # 4. Continuation task -> extended budget
        s_cont = classify_task_scope_and_budget(
            "[CONTINUE_PAUSED_TASK]\n请继续执行未完成的任务", tier="High", is_operation=True
        )
        self.assertEqual(s_cont["scope"], "modify")
        self.assertEqual(s_cont["max_steps"], 60)

    def test_capsule_fts5_and_credential_redaction(self):
        record_episodic_turn(
            project_dir=self.proj_dir,
            user_request="排查 PA2 引脚在不接外部电瓶时的电压表现",
            reply_text=(
                "实测发现 PA2 引脚在仅靠 ST-Link 3.3V 供电且未接电瓶时恒为 0V。"
                "已证伪路径：单纯拉高 PA0 无法让芯片进入蓝牙广播，因为 LDO 输入端无主电。"
                "最终方案：决定采用外接 12V 稳压电源到 VIN 引脚模拟电瓶上电。"
                "生成产物：/home/jiang/github/luyuan-k50-passport-key/firmware/main.c "
                "敏感令牌 ghp_1234567890abcdefghijklmnopqrstuvwx 密码1314520jh"
            ),
            chat_id="oc_test",
            conv_id="11111111-2222-3333-4444-555555555555",
            step_index=10,
            scope="inspect",
            custom_root=self.mem_root,
        )

        capsule = read_project_capsule(self.proj_dir, custom_root=self.mem_root)
        self.assertIn("项目长效记忆胶囊", capsule)
        self.assertIn("PA2", capsule)
        self.assertNotIn("ghp_1234567890", capsule)
        self.assertNotIn("1314520jh", capsule)

        dead_ends = get_project_facts(self.proj_dir, fact_type="dead_end", custom_root=self.mem_root)
        self.assertTrue(len(dead_ends) >= 1)

        hits = search_episodic_memory(self.proj_dir, "PA2 电瓶 蓝牙广播", custom_root=self.mem_root)
        self.assertEqual(len(hits), 1)
        self.assertIn("PA2", hits[0]["user_request"])

    def test_transcript_backfill_and_proactive_session_relay(self):
        tx_path = os.path.join(self.tmp_dir.name, "transcript.jsonl")
        conv_id = "d6b10ff5-7d50-4f30-ba9d-f44cb1a820e7"
        events = [
            {
                "step_index": 1,
                "type": "USER_INPUT",
                "content": "[System Rule: Reply ONLY in Simplified Chinese]\n分析固件的蓝牙鉴权协议",
                "created_at": "2026-11-05T10:00:00Z",
            },
            {
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "content": "已确认蓝牙鉴权采用 FE01 特征值写入 8 字节挑战帧。",
                "created_at": "2026-11-05T10:01:00Z",
            },
            {
                "step_index": 3,
                "type": "CHECKPOINT",
                "content": "This request was blocked by Gemini's filters.",
                "created_at": "2026-11-05T10:05:00Z",
            },
        ]
        with open(tx_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        session_data = {"conversation": conv_id, "project": self.proj_dir}
        relay_res = check_and_prepare_session_relay(
            chat_id="oc_test",
            session_data=session_data,
            project_dir=self.proj_dir,
            transcript_path_override=tx_path,
            custom_root=self.mem_root,
        )
        self.assertTrue(relay_res["relayed"])
        self.assertTrue(relay_res["is_corrupted"])
        self.assertEqual(session_data["conversation"], "")
        self.assertEqual(len(session_data["conversation_lineage"]), 1)
        self.assertEqual(session_data["conversation_lineage"][0]["conv_id"], conv_id)
        self.assertIn("Session Relay Handoff", session_data["_relay_handoff_context"])
        self.assertIn("FE01", session_data["_relay_handoff_context"])

    def test_agy_hooks_compaction_recovery_and_inspect_guard(self):
        # 1. Write an inspect contract
        scope_info = classify_task_scope_and_budget("去看看有没有安装 Saleae Logic 2", tier="Medium", is_operation=True)
        write_turn_contract(
            chat_id="oc_test",
            project_dir=self.proj_dir,
            user_request="去看看有没有安装 Saleae Logic 2",
            scope_info=scope_info,
            conv_id="conv-123",
            custom_root=self.mem_root,
        )

        # 2. PreToolUse should DENY wget downloading a large package or writing code in inspect mode
        deny_resp = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "conversationId": "conv-123",
                "cwd": self.proj_dir,
                "toolName": "write_file",
                "toolInput": {"Path": f"{self.proj_dir}/hack.py", "Content": "print(1)"},
            },
            custom_root=self.mem_root,
        )
        self.assertEqual(
            deny_resp.get("hookSpecificOutput", {}).get("permissionDecision"),
            "deny",
        )

        # 3. PreToolUse should ALLOW read-only commands in inspect mode
        allow_resp = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "conversationId": "conv-123",
                "cwd": self.proj_dir,
                "toolName": "run_command",
                "toolInput": {"CommandLine": "timeout 10 which logic2"},
            },
            custom_root=self.mem_root,
        )
        self.assertNotEqual(
            allow_resp.get("hookSpecificOutput", {}).get("permissionDecision"),
            "deny",
        )

        # 4. PreInvocation should inject systemMessage when a CHECKPOINT occurs
        add_project_fact(self.proj_dir, "fact", "MCU 型号为 GD32E230C8T6", custom_root=self.mem_root)
        tx_path = os.path.join(self.tmp_dir.name, "tx_hook.jsonl")
        with open(tx_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"step_index": 1, "type": "CHECKPOINT", "content": "A" * 400}) + "\n")

        pre_inv_resp = handle_hook_payload(
            {
                "hookEventName": "PreInvocation",
                "conversationId": "conv-123",
                "transcriptPath": tx_path,
                "cwd": self.proj_dir,
            },
            custom_root=self.mem_root,
        )
        self.assertIn("Post-Compaction Memory Recovery", pre_inv_resp.get("systemMessage", ""))
        self.assertIn("GD32E230C8T6", pre_inv_resp.get("systemMessage", ""))

    def test_budget_pause_card_and_memory_card(self):
        card = CardBuilder.build_budget_pause_card(
            user_text="全量逆向固件并完成自动化烧录测试",
            reason="单轮工具调用步数已达范围契约上限 (15/15 步)",
            completed_steps=["正在读取文件: main.c", "正在执行终端命令"],
            planned_steps=["读取固件源码", "分析寄存器配置", "验证烧录"],
            partial_reply="已初步定位到串口初始化位于 0x08001240。",
            elapsed_seconds=245,
            scope_label="只读探查 (Inspect Only)",
        )
        card_json = json.dumps(card, ensure_ascii=False)
        self.assertIn("continue_long_task", card_json)
        self.assertIn("▶️ 继续执行剩余步骤", card_json)
        self.assertIn("stop_paused_task", card_json)

        summary = get_project_memory_summary(self.proj_dir, custom_root=self.mem_root)
        mem_card = CardBuilder.build_memory_card(["喜欢精炼中文回复"], project_memory_summary=summary)
        mem_json = json.dumps(mem_card, ensure_ascii=False)
        self.assertIn("当前项目长效记忆引擎", mem_json)
        self.assertIn("backfill_project_memory", mem_json)

    def test_08_safety_filter_desensitization_and_python_inline_action(self):
        from executor import (
            desensitize_hardware_engineering_text,
            detect_and_format_agent_system_error,
            format_tool_action,
            is_gemini_safety_filter_block,
        )

        # 1. Inline python3 -c semantic extraction (prevents identical "正在执行 Python 脚本" labels)
        act1 = format_tool_action(
            "run_command",
            {"CommandLine": "timeout 15 python3 -c \"data = open('STLinkUpgrade.exe','rb').read(); print(data[0x1680:0x16a0])\""},
        )
        act2 = format_tool_action(
            "run_command",
            {"CommandLine": "timeout 15 python3 -c \"print(hex(0x2120))\""},
        )
        self.assertIn("STLinkUpgrade.exe", act1)
        self.assertIn("0x1680", act1)
        self.assertIn("0x2120", act2)
        self.assertNotEqual(act1, act2)

        # 2. Gemini filter detection & desensitization
        raw_block = (
            "This request was blocked by Gemini's filters. They can occasionally trigger by mistake "
            "on safe coding, security, or biology-related queries. Please try rephrasing your prompt."
        )
        self.assertTrue(is_gemini_safety_filter_block(raw_block))
        formatted_zh = detect_and_format_agent_system_error(raw_block)
        self.assertIsNotNone(formatted_zh)
        self.assertIn("Gemini Safety Filter", formatted_zh)
        self.assertIn("[CHOICE_CARD]", formatted_zh)

        desensitized = desensitize_hardware_engineering_text("我的选择是：方案一: 给原厂 ST-Link 固件去限制（最推荐 ⭐）")
        self.assertNotIn("去限制", desensitized)
        self.assertIn("跨芯片标准 SWD 兼容模式", desensitized)

        # 3. <USER_REQUEST> tag stripping in strip_injected_prompt_headers
        wrapped = (
            "<USER_REQUEST>\n[S1 scope=execute_device tier=High]\n"
            "我的选择是：方案一\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe current local time is..."
        )
        self.assertEqual(strip_injected_prompt_headers(wrapped), "我的选择是：方案一")


if __name__ == "__main__":
    unittest.main()
