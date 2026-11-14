import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


class TestDynamicModelTiers(unittest.TestCase):
    def setUp(self):
        from utils.model_tiers import (
            FALLBACK_MODELS_CATALOG,
            get_effort_rank,
            split_model_id_suffix,
            has_embedded_effort_suffix,
            get_family_ordered_models,
            resolve_adaptive_model_dynamic,
            infer_effort_from_model_dynamic,
        )
        self.catalog = list(FALLBACK_MODELS_CATALOG) + [
            "gemini-pro-orchestrator-g1-pro\tGemini Pro Orchestrator G1",
        ]
        self.get_effort_rank = get_effort_rank
        self.split_model_id_suffix = split_model_id_suffix
        self.has_embedded_effort_suffix = has_embedded_effort_suffix
        self.get_family_ordered_models = get_family_ordered_models
        self.resolve_adaptive_model_dynamic = resolve_adaptive_model_dynamic
        self.infer_effort_from_model_dynamic = infer_effort_from_model_dynamic

    def test_01_four_tier_hydrogen_up_and_down_from_xhigh(self):
        # Even if user picked gemini-4-hydrogen-xhigh manually, Low/Medium/High/XHigh all route dynamically
        base = "gemini-4-hydrogen-xhigh"
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "Low", available_models=self.catalog),
            "gemini-4-hydrogen-low",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "Medium", available_models=self.catalog),
            "gemini-4-hydrogen-medium",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "High", available_models=self.catalog),
            "gemini-4-hydrogen-high",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "XHigh", available_models=self.catalog),
            "gemini-4-hydrogen-xhigh",
        )
        # High maps to -high, XHigh maps to -xhigh cleanly on 4-tier models
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "High", complexity_score=1.50, available_models=self.catalog),
            "gemini-4-hydrogen-high",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic(base, "XHigh", complexity_score=1.80, available_models=self.catalog),
            "gemini-4-hydrogen-xhigh",
        )
        self.assertEqual(self.infer_effort_from_model_dynamic("gemini-4-hydrogen-xhigh"), "xhigh")

    def test_02_three_tier_caps_xhigh_safely_to_high(self):
        for base in ("gemini-4-argon-medium", "claude-sonnet-5-5-high", "gemini-3.8-flash-low"):
            resolved = self.resolve_adaptive_model_dynamic(base, "XHigh", available_models=self.catalog)
            self.assertTrue(resolved.endswith("-high"), f"Expected -high for {base}, got {resolved}")

    def test_03_two_tier_nearest_rank_mapping(self):
        two_tier_catalog = [
            "future-mini-low\tFuture Mini (Low)",
            "future-mini-high\tFuture Mini (High)",
        ]
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("future-mini-low", "Low", available_models=two_tier_catalog),
            "future-mini-low",
        )
        # Medium=40 is equidistant to Low=20 and High=60; tie-breaker favors higher tier
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("future-mini-low", "Medium", available_models=two_tier_catalog),
            "future-mini-high",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("future-mini-low", "High", available_models=two_tier_catalog),
            "future-mini-high",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("future-mini-low", "XHigh", available_models=two_tier_catalog),
            "future-mini-high",
        )

    def test_04_one_tier_fixed_models_never_switch(self):
        for fixed_model in ("gpt-oss-120b-medium", "gemini-pro-orchestrator-g1-pro"):
            for tier in ("Low", "Medium", "High", "XHigh"):
                self.assertEqual(
                    self.resolve_adaptive_model_dynamic(fixed_model, tier, available_models=self.catalog),
                    fixed_model,
                )

    def test_05_future_five_tier_and_display_name_clustering(self):
        future_catalog = [
            "gemini-5-nova-minimal\tGemini 5 Nova (Minimal)",
            "gemini-5-nova-fast\tGemini 5 Nova (Fast)",
            "gemini-5-nova-medium\tGemini 5 Nova (Medium)",
            "gemini-5-nova-high\tGemini 5 Nova (High)",
            "gemini-5-nova-ultra\tGemini 5 Nova (Ultra)",
        ]
        ordered = [m[0] for m in self.get_family_ordered_models("gemini-5-nova-medium", available_models=future_catalog)]
        self.assertEqual(
            ordered,
            [
                "gemini-5-nova-minimal",
                "gemini-5-nova-fast",
                "gemini-5-nova-medium",
                "gemini-5-nova-high",
                "gemini-5-nova-ultra",
            ],
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("gemini-5-nova-ultra", "Low", available_models=future_catalog),
            "gemini-5-nova-fast",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("gemini-5-nova-ultra", "Low", complexity_score=0.15, available_models=future_catalog),
            "gemini-5-nova-minimal",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("gemini-5-nova-minimal", "Medium", available_models=future_catalog),
            "gemini-5-nova-medium",
        )
        self.assertEqual(
            self.resolve_adaptive_model_dynamic("gemini-5-nova-minimal", "XHigh", complexity_score=1.80, available_models=future_catalog),
            "gemini-5-nova-ultra",
        )
        self.assertTrue(self.has_embedded_effort_suffix("gemini-5-nova-ultra"))
        self.assertTrue(self.has_embedded_effort_suffix("gemini-4-hydrogen-xhigh"))
        self.assertFalse(self.has_embedded_effort_suffix("gemini-pro-orchestrator-g1-pro"))
        self.assertGreater(self.get_effort_rank("xxhigh"), self.get_effort_rank("xhigh"))

    def test_06_hmdm_xhigh_escalation_and_system_one_gate(self):
        from math_decision_model import HybridMathDecisionEngine
        from system_one_gate import evaluate_adaptive_tier, TypeSafeDecision

        engine = HybridMathDecisionEngine()
        extreme_prompt = (
            "请全面架构重构整个分布式微服务项目，跨模块排查并发竞态和数据库死锁，"
            "然后设计迁移方案、编写完整脚本并执行全量测试验证上线"
        )
        d1 = engine.evaluate_decision("chat_xhigh_test", extreme_prompt)
        engine.update_post_turn_state(
            "chat_xhigh_test",
            had_error=False,
            tier_used="High",
            needs_plan_used=True,
            complexity_used=1.8,
        )
        d2 = engine.evaluate_decision("chat_xhigh_test", extreme_prompt)
        self.assertEqual(d2.effort_tier, "XHigh")

        # Multi-error recovery (consecutive_errors >= 2) with high complexity escalates to XHigh
        engine.update_post_turn_state("chat_xhigh_test", had_error=True, tier_used="High")
        engine.update_post_turn_state("chat_xhigh_test", had_error=True, tier_used="High")
        d_err = engine.evaluate_decision(
            "chat_xhigh_test",
            "报错了还是失败，继续深入排查并发死锁根因并修复所有关联模块",
        )
        self.assertEqual(d_err.effort_tier, "XHigh")

        # System One tier evaluation accepts XHigh from math_tier or extreme complexity
        dec = TypeSafeDecision(
            is_dangerous=False,
            danger_prob=0.0,
            intent="code_agent",
            intent_confidence=0.95,
            complexity_score=1.9,
            needs_terminal=True,
            math_tier="XHigh",
        )
        self.assertEqual(evaluate_adaptive_tier(dec), "XHigh")

    def test_07_session_pool_high_to_xhigh_upgrade(self):
        import config
        from session_pool import SessionPool

        pool = SessionPool()
        with patch.object(config, "TYPESAFE_AUTO_MODE", True):
            s_high = pool.get_or_create("chat_xhigh_pool", "gemini-4-hydrogen-high", "/tmp", "conv-x1", effort="high")
            s_med = pool.get_or_create("chat_xhigh_pool", "gemini-4-hydrogen-medium", "/tmp", "conv-x1", effort="medium")
            self.assertIs(s_high, s_med)

            s_xhigh = pool.get_or_create("chat_xhigh_pool", "gemini-4-hydrogen-xhigh", "/tmp", "conv-x1", effort="xhigh")
            self.assertIsNot(s_high, s_xhigh)
            self.assertEqual(s_xhigh.model, "gemini-4-hydrogen-xhigh")
            self.assertEqual(s_xhigh.effort, "xhigh")


if __name__ == "__main__":
    unittest.main()
