# -*- coding: utf-8 -*-
"""
Unit tests for model caching and fast /model retrieval in commands.py.
"""

import os
import sys
import time
import json
import asyncio
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import commands
from cards.models import build_model_panel, _parse_model_entry


class TestModelCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset memory cache state for testing isolation
        self.tmp_cache_file = os.path.join(tempfile.gettempdir(), f".test_model_cache_{os.getpid()}.json")
        self.orig_cache_file = commands._MODEL_CACHE_FILE
        commands._MODEL_CACHE_FILE = self.tmp_cache_file
        commands._MODEL_CACHE = []
        commands._MODEL_CACHE_TIME = 0.0
        commands._MODEL_FETCH_LOCK = None

    def tearDown(self):
        commands._MODEL_CACHE_FILE = self.orig_cache_file
        if os.path.exists(self.tmp_cache_file):
            try:
                os.remove(self.tmp_cache_file)
            except Exception:
                pass

    def test_parse_model_entry(self):
        m_id, label = _parse_model_entry("gemini-4-argon-medium\tGemini 4 Argon (Medium)")
        self.assertEqual(m_id, "gemini-4-argon-medium")
        self.assertEqual(label, "Gemini 4 Argon (Medium)")

        m_id2, label2 = _parse_model_entry("claude-sonnet-5-5-high Claude Sonnet 5.5 (High)")
        self.assertEqual(m_id2, "claude-sonnet-5-5-high")
        self.assertEqual(label2, "Claude Sonnet 5.5 (High)")

    def test_build_model_panel_with_defaults(self):
        card = build_model_panel(commands.DEFAULT_FALLBACK_MODELS, "gemini-3.8-flash-high")
        self.assertIn("elements", card)
        self.assertEqual(card["header"]["title"]["content"], "🎛️ 大模型切换控制台")

    async def test_get_available_models_cached_hit(self):
        # Warm memory cache
        dummy_models = ["model-a Model A", "model-b Model B"]
        commands._MODEL_CACHE = list(dummy_models)
        commands._MODEL_CACHE_TIME = time.time()

        t0 = time.time()
        res = await commands.get_available_models_cached(force_refresh=False)
        duration = time.time() - t0

        self.assertEqual(res, dummy_models)
        self.assertLess(duration, 0.05, "Cached retrieval should be sub-50ms")

    async def test_stale_while_revalidate_returns_stale_and_refreshes(self):
        # Cache is older than TTL
        old_models = ["old-model-1 Old Model 1"]
        commands._MODEL_CACHE = list(old_models)
        commands._MODEL_CACHE_TIME = time.time() - (commands._MODEL_CACHE_TTL + 100)

        new_models = ["new-model-1 New Model 1", "new-model-2 New Model 2"]
        with patch.object(commands, "_fetch_models_from_cli", new=AsyncMock(return_value=new_models)) as mock_fetch:
            res = await commands.get_available_models_cached(force_refresh=False)
            # Should immediately return stale models to avoid blocking
            self.assertEqual(res, old_models)
            # Allow background task to complete
            await asyncio.sleep(0.05)
            self.assertEqual(commands._MODEL_CACHE, new_models)
            mock_fetch.assert_awaited()

    async def test_fallback_when_cli_fails(self):
        commands._MODEL_CACHE = []
        commands._MODEL_CACHE_TIME = 0.0

        with patch.object(commands, "load_model_cache_from_disk", return_value=[]), \
             patch.object(commands, "_fetch_models_from_cli", new=AsyncMock(return_value=[])):
            res = await commands.get_available_models_cached(force_refresh=True)
            self.assertEqual(res, commands.DEFAULT_FALLBACK_MODELS)


if __name__ == "__main__":
    unittest.main()
