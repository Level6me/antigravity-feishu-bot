import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_lark_oapi_stub():
    try:
        import lark_oapi  # noqa: F401
        return
    except ImportError:
        pass
    lark_mod = types.ModuleType("lark_oapi")

    class _DummyBuilder:
        def __getattr__(self, _name):
            def _method(*_args, **_kwargs):
                return self
            return _method

        def build(self):
            return MagicMock()

    class _DummyReq:
        @classmethod
        def builder(cls):
            return _DummyBuilder()

    lark_mod.Client = _DummyReq
    im_v1 = types.ModuleType("lark_oapi.api.im.v1")
    for name in (
        "ReplyMessageRequest",
        "ReplyMessageRequestBody",
        "PatchMessageRequest",
        "PatchMessageRequestBody",
        "GetMessageResourceRequest",
        "CreateMessageRequest",
        "CreateMessageRequestBody",
        "CreateMessageReactionRequest",
        "GetChatRequest",
        "CreateMessageReactionRequestBody",
        "Emoji",
        "DeleteMessageReactionRequest",
        "CreateFileRequest",
        "CreateFileRequestBody",
        "CreateImageRequest",
        "CreateImageRequestBody",
    ):
        setattr(im_v1, name, _DummyReq)

    contact_v3 = types.ModuleType("lark_oapi.api.contact.v3")
    contact_v3.GetUserRequest = _DummyReq

    api_pkg = types.ModuleType("lark_oapi.api")
    im_pkg = types.ModuleType("lark_oapi.api.im")
    contact_pkg = types.ModuleType("lark_oapi.api.contact")
    api_pkg.im = im_pkg
    im_pkg.v1 = im_v1
    api_pkg.contact = contact_pkg
    contact_pkg.v3 = contact_v3
    lark_mod.api = api_pkg

    sys.modules["lark_oapi"] = lark_mod
    sys.modules["lark_oapi.api"] = api_pkg
    sys.modules["lark_oapi.api.im"] = im_pkg
    sys.modules["lark_oapi.api.im.v1"] = im_v1
    sys.modules["lark_oapi.api.contact"] = contact_pkg
    sys.modules["lark_oapi.api.contact.v3"] = contact_v3


_ensure_lark_oapi_stub()

from cards.common import sanitize_feishu_markdown  # noqa: E402
from cards.response import build_ai_response, build_budget_pause_card  # noqa: E402
from cards.indicators import build_streaming_indicator, build_planned_steps_indicator  # noqa: E402
from cards.memory import build_memory_card, build_note_list_card  # noqa: E402
from utils import NonRetryableError, with_retry  # noqa: E402
import lark_client  # noqa: E402
import multimodal  # noqa: E402


class TestFeishuCardMarkdownSanitization(unittest.TestCase):
    def test_sanitizes_literal_and_url_images_while_preserving_feishu_keys(self):
        raw = (
            "检测逻辑为 `![alt](url)`，外部图为 ![chart](https://example.com/a.png)，"
            "飞书图 ![ok](img_v3_02ab_1234567890) 应保留。"
        )
        sanitized = sanitize_feishu_markdown(raw)
        self.assertIn("`!\u200b[alt](url)`", sanitized)
        self.assertIn("!\u200b[chart](https://example.com/a.png)", sanitized)
        self.assertIn("![ok](img_v3_02ab_1234567890)", sanitized)
        self.assertEqual(sanitized, sanitize_feishu_markdown(sanitized))

    def test_card_builders_sanitize_markdown_elements(self):
        sample = "示例：`![alt](url)`"
        resp_card = build_ai_response(sample)
        self.assertIn("!\u200b[alt](url)", resp_card["elements"][0]["content"])

        stream_card = build_streaming_indicator(sample, think_seconds=5)
        self.assertIn("!\u200b[alt](url)", stream_card["elements"][0]["content"])

        pause_card = build_budget_pause_card(
            user_text=sample,
            completed_steps=[sample],
            planned_steps=[sample],
            partial_reply=sample,
            elapsed_seconds=10,
        )
        markdown_texts = [e["content"] for e in pause_card["elements"] if e.get("tag") == "markdown"]
        self.assertTrue(any("!\u200b[alt](url)" in t for t in markdown_texts))

        plan_card = build_planned_steps_indicator(
            ["分析 `![alt](url)`"],
            current_step_idx=0,
        )
        self.assertNotIn("![alt](url)", plan_card["elements"][0]["content"])

        mem_card = build_memory_card(
            [sample],
            project_memory_summary={"capsule_markdown": sample},
        )
        self.assertNotIn("![alt](url)", str(mem_card))

        note_card = build_note_list_card([f"标题 {sample}"])
        self.assertNotIn("![alt](url)", str(note_card))


class TestNonRetryableCardPatchAndFallback(unittest.TestCase):
    def test_non_retryable_error_does_not_sleep_or_retry(self):
        calls = {"count": 0}

        @with_retry(max_retries=4, initial_delay=5.0)
        def _always_bad_card():
            calls["count"] += 1
            raise NonRetryableError("code 230099 ErrCode: 11310")

        with patch("utils.time.sleep") as mock_sleep:
            with self.assertRaises(NonRetryableError):
                _always_bad_card()

        self.assertEqual(calls["count"], 1)
        mock_sleep.assert_not_called()

    def test_patch_interactive_card_raises_non_retryable_on_230099(self):
        fake_resp = MagicMock()
        fake_resp.code = 230099
        fake_resp.msg = "Failed to create card content, ext=ErrCode: 11310; ErrMsg: the card contains images"

        with patch.object(lark_client.api_client.im.v1.message, "patch", return_value=fake_resp) as mock_patch, \
             patch("utils.time.sleep") as mock_sleep:
            with self.assertRaises(NonRetryableError):
                lark_client.patch_interactive_card_sdk("om_123", {"elements": []})

        self.assertEqual(mock_patch.call_count, 1)
        mock_sleep.assert_not_called()

    def test_send_text_to_chat_paginates_long_text_without_truncation(self):
        long_text = "\n".join(f"line-{i}: " + ("a" * 100) for i in range(250))
        self.assertGreater(len(long_text), 20000)
        fake_resp = MagicMock()
        fake_resp.code = 0

        with patch.object(lark_client.api_client.im.v1.message, "create", return_value=fake_resp) as mock_create:
            ok = lark_client.send_text_to_chat_sdk("oc_chat", long_text)

        self.assertTrue(ok)
        self.assertGreaterEqual(mock_create.call_count, 2)


class TestMultimodalPlaceholderGuard(unittest.TestCase):
    def test_ignores_code_span_placeholders_and_logs_no_warning(self):
        text = "Markdown 解析会检查 `![alt](url)` 和 `[file](/tmp/fake.txt)`，但不应自动上传占位符。"
        with patch.object(multimodal.log, "warning") as mock_warn:
            multimodal.extract_and_upload_resources(text, "om_123", MagicMock())
        mock_warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
