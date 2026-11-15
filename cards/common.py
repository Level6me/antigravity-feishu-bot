"""Shared card footer and Markdown sanitization helpers."""
import re
from datetime import datetime

# Feishu card Markdown blocks validate every `![alt](target)` globally—even inside
# inline code backticks—and fail with code 230099 / ErrCode 11310 unless `target`
# is an uploaded Feishu image_key (`img_v2_...` / `img_v3_...`).
_FEISHU_IMAGE_KEY_RE = re.compile(r"^img_(?:v\d+_)?[A-Za-z0-9_-]{8,}$")
_MD_IMAGE_RE = re.compile(r"!\[([^\n]*?)\]\(([^)\n]*)\)")


def sanitize_feishu_markdown(text):
    """Break non-image_key `![alt](target)` syntax in Feishu card markdown.

    Inserts a zero-width space between `!` and `[` when `target` is not a Feishu
    `image_key`, preserving literal rendering inside backticks/prose while
    preventing Feishu card validation error 230099 / 11310.
    """
    if not text or not isinstance(text, str):
        return text or ""

    def _replace(match):
        alt, target = match.group(1), match.group(2)
        if _FEISHU_IMAGE_KEY_RE.match(target.strip()):
            return match.group(0)
        return f"!\u200b[{alt}]({target})"

    return _MD_IMAGE_RE.sub(_replace, text)


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
