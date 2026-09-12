#!/usr/bin/env python3
"""通过飞书 Lark API 将本地文件发送到指定飞书会话（默认最近活跃会话）。

用法：
    python3 send_to_feishu.py <file_path> [chat_id] [--caption "说明文字"]

示例：
    python3 send_to_feishu.py docs/README.md
    python3 send_to_feishu.py report.pdf oc_xxxx --caption "这是导出的验收报告"
    python3 send_to_feishu.py speech.opus oc_xxxx   # 自动以原生语音条发送

chat_id 省略时自动取数据库中最近活跃的会话。
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db
from lark_client import send_local_file_to_chat


def resolve_default_chat_id():
    """返回最近活跃会话 chat_id：优先 auth_sessions，回退 chat_sessions。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT chat_id FROM auth_sessions ORDER BY last_request_at DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                return row[0]
            row = conn.execute("SELECT chat_id FROM chat_sessions LIMIT 1").fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"[send_to_feishu] 读取默认会话失败: {e}", file=sys.stderr)
        return None


def send_file(file_path, chat_id=None, caption=None):
    """发送本地文件到会话；chat_id 省略时自动定位最近活跃会话。
    返回 (ok, message)。
    """
    if not os.path.isfile(file_path):
        return False, f"文件不存在: {file_path}"

    chat_id = chat_id or resolve_default_chat_id()
    if not chat_id:
        return False, "未找到目标会话，请显式传入 chat_id"

    ok = send_local_file_to_chat(chat_id, file_path, caption=caption)
    if ok:
        return True, f"✅ 已通过 Lark API 成功发送文件: {file_path} -> {chat_id}"
    return False, f"❌ 发送失败: {file_path} -> {chat_id}"


def main():
    parser = argparse.ArgumentParser(description="使用飞书 Lark API 发送本地文件/语音到飞书会话")
    parser.add_argument("file_path", help="要发送的本地文件路径")
    parser.add_argument("chat_id", nargs="?", default=None, help="目标会话 ID（省略则自动使用最近活跃会话）")
    parser.add_argument("--caption", default=None, help="可选：文件附带说明文字")
    args = parser.parse_args()

    ok, msg = send_file(args.file_path, args.chat_id, args.caption)
    print(f"[send_to_feishu] {msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
