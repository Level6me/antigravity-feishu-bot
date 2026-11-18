import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import multimodal  # noqa: E402
import send_to_feishu  # noqa: E402
from agent_memory.store import strip_injected_prompt_headers  # noqa: E402


class TestMultimodalFileDeliveryPrecision(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj_dir = os.path.join(self.tmpdir.name, "my_proj")
        os.makedirs(self.proj_dir, exist_ok=True)
        os.makedirs(os.path.join(self.proj_dir, "src"), exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _touch(self, rel_path, content="hello"):
        p = os.path.join(self.proj_dir, rel_path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_recent_edited_code_files_and_dirs_are_never_auto_uploaded(self):
        py_file = self._touch("file_utils.py", "print(1)")
        send_file = self._touch("send_to_feishu.py", "print(2)")
        yml_file = self._touch("docker-compose.yml", "version: '3'")
        md_file = self._touch("README.md", "# docs")
        html_file = self._touch("index.html", "<html></html>")
        csv_file = self._touch("partitions.csv", "a,b\n1,2\n")
        no_ext = self._touch("Dockerfile", "FROM python:3.13")
        src_dir = os.path.join(self.proj_dir, "src")

        reply = (
            "已完成任务并生成了完整测试文档，结果保存在工作区，请查看：\n"
            f"- 修改了 [file_utils.py](file://{py_file})\n"
            f"- 修改了 [send_to_feishu.py](file://{send_file}#L10-L20)\n"
            f"- 更新了 [docker-compose.yml](file://{yml_file})\n"
            f"- 更新了 [README.md](file://{md_file})\n"
            f"- 更新了 [index.html](file://{html_file})\n"
            f"- 检查了 [partitions.csv](file://{csv_file})\n"
            f"- 检查了 [Dockerfile](file://{no_ext})\n"
            f"- 扫描了目录 [src/](file://{src_dir})\n"
        )

        with patch("multimodal.upload_file_sdk") as mock_upload, \
             patch("multimodal.reply_file_sdk") as mock_reply:
            summary = multimodal.extract_and_upload_resources(
                reply, "om_1", MagicMock(), additional_safe_dirs=[self.proj_dir], chat_id="oc_1"
            )

        mock_upload.assert_not_called()
        mock_reply.assert_not_called()
        self.assertEqual(summary["uploaded"], [])

    def test_explicit_delivery_markers_and_deliverable_paths_are_uploaded(self):
        md_doc = self._touch("summary.md", "# summary")
        pdf_file = self._touch("report v1.pdf", "%PDF-1.4")
        zip_file = self._touch("dist/release.zip", "PK\x03\x04")
        txt_log = self._touch("logs/run.log", "line1")

        reply = (
            "已为您准备好以下文件：\n"
            f"- 📄 [总结文档](file://{md_doc})\n"
            f"- `[导出报告](file://{os.path.join(self.proj_dir, 'report%20v1.pdf')})`\n"
            f"- 打包输出：`{zip_file}`\n"
            f"- 附件：{txt_log}\n"
            f"- 重复引用同一文件：📄 [总结文档2](file://{md_doc})\n"
        )

        with patch("multimodal.upload_file_sdk", side_effect=["fk_1", "fk_2", "fk_3", "fk_4"]) as mock_upload, \
             patch("multimodal.reply_file_sdk", return_value=True) as mock_reply:
            summary = multimodal.extract_and_upload_resources(
                reply, "om_2", MagicMock(), additional_safe_dirs=[self.proj_dir], chat_id="oc_2"
            )

        self.assertEqual(mock_upload.call_count, 4)
        self.assertEqual(mock_reply.call_count, 4)
        uploaded_set = set(summary["uploaded"])
        self.assertEqual(
            uploaded_set,
            {
                os.path.abspath(md_doc),
                os.path.abspath(pdf_file),
                os.path.abspath(zip_file),
                os.path.abspath(txt_log),
            },
        )

    def test_sensitive_files_always_blocked_even_with_explicit_marker_and_admin(self):
        env_file = self._touch(".env", "SECRET=123")
        key_file = self._touch("server.key", "PRIVATE")
        db_file = self._touch("antigravity_bot.db", "sqlite")

        reply = (
            f"📎 [env]({env_file})\n"
            f"📎 [key]({key_file})\n"
            f"📎 [db]({db_file})\n"
        )

        with patch("multimodal.upload_file_sdk") as mock_upload:
            summary = multimodal.extract_and_upload_resources(
                reply,
                "om_3",
                MagicMock(),
                additional_safe_dirs=[self.proj_dir],
                chat_id="oc_3",
                allow_home_deliverables=True,
            )

        mock_upload.assert_not_called()
        self.assertEqual(len(summary["failed"]), 3)

    def test_admin_home_deliverable_allowed_while_non_admin_blocked(self):
        home_dir = os.path.abspath(os.path.expanduser("~"))
        fake_home_export = os.path.join(home_dir, "_test_nonexistent_check_dir", "out.pdf")
        with patch("multimodal.os.path.isfile", return_value=True), \
             patch("multimodal.os.path.getsize", return_value=128), \
             patch("multimodal.upload_file_sdk", return_value="fk_home") as mock_upload, \
             patch("multimodal.reply_file_sdk", return_value=True):
            res_non_admin = multimodal.extract_and_upload_resources(
                f"📎 [out.pdf]({fake_home_export})",
                "om_4",
                MagicMock(),
                additional_safe_dirs=[],
                chat_id="oc_4",
                allow_home_deliverables=False,
            )
            mock_upload.assert_not_called()
            self.assertEqual(len(res_non_admin["failed"]), 1)

            res_admin = multimodal.extract_and_upload_resources(
                f"📎 [out.pdf]({fake_home_export})",
                "om_4",
                MagicMock(),
                additional_safe_dirs=[],
                chat_id="oc_4",
                allow_home_deliverables=True,
            )
            mock_upload.assert_called_once()
            self.assertEqual(len(res_admin["uploaded"]), 1)

    def test_send_to_feishu_blocks_sensitive_files(self):
        env_file = self._touch(".env", "SECRET=123")
        ok, msg = send_to_feishu.send_file(env_file, chat_id="oc_test")
        self.assertFalse(ok)
        self.assertIn("安全策略", msg)

    def test_strip_injected_prompt_headers_strips_new_feishu_file_rules(self):
        raw = (
            "[S1 scope=modify tier=High budget=35步/10m | Proj: /tmp | 仅用简体中文回复]\n"
            "[Feishu File Rule: 主动发文件运行 `python3 /app/send_to_feishu.py \"<路径>\" oc_1` 或用 `📎 [文件名](/路径)`；普通代码引用勿加 📎/📄]\n"
            "[System Feishu Resource Delivery / 飞书文件传送规范]\n"
            "1. 规则一\n"
            "2. 规则二\n\n"
            "请帮我检查一下代码"
        )
        stripped = strip_injected_prompt_headers(raw)
        self.assertEqual(stripped, "请帮我检查一下代码")


if __name__ == "__main__":
    unittest.main()
