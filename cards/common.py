"""Shared card footer helpers."""
from datetime import datetime


def create_footer():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": f"⚡ Powered by Antigravity | 🕒 {now}"
            }
        ]
    }
