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


def extract_primary_markdown_from_card(card: dict) -> str:
    """Extract concatenated markdown content from a Card 1.0 dict for CardKit element streaming."""
    if not isinstance(card, dict):
        return ""
    md_blocks = [
        str(el.get("content") or "")
        for el in (card.get("elements") or [])
        if isinstance(el, dict) and el.get("tag") == "markdown" and el.get("content")
    ]
    return "\n\n---\n\n".join(md_blocks) if md_blocks else "..."


def convert_card_v1_to_v2(
    card_v1: dict,
    stream_element_id: str = "stream_md",
    streaming_mode: bool = False,
) -> dict:
    """Convert a Feishu Card JSON 1.0 payload into a spec-compliant Card JSON 2.0 payload.
    - Translates `note` blocks into grey notation markdown (Card 2.0 removed `note` tag).
    - Translates `action` blocks into Card 2.0 `column_set` / `button` with `behaviors` callbacks.
    - Tags the primary `markdown` element with `element_id=stream_element_id` for 60fps CardKit streaming.
    """
    if not isinstance(card_v1, dict):
        card_v1 = {}
    if card_v1.get("schema") == "2.0":
        return card_v1

    v2_config = dict(card_v1.get("config") or {"wide_screen_mode": True})
    v2_config["update_multi"] = True
    if streaming_mode:
        v2_config["streaming_mode"] = True
        v2_config.setdefault("summary", {"content": "[生成中...]"})
    else:
        v2_config["streaming_mode"] = False

    v2_elements = []
    tagged_primary_md = False

    for el in (card_v1.get("elements") or []):
        if not isinstance(el, dict):
            continue
        tag = el.get("tag")
        if tag == "markdown":
            new_md = {"tag": "markdown", "content": str(el.get("content") or "")}
            if not tagged_primary_md and stream_element_id:
                new_md["element_id"] = stream_element_id
                tagged_primary_md = True
            v2_elements.append(new_md)
        elif tag == "hr":
            v2_elements.append({"tag": "hr"})
        elif tag == "note":
            note_parts = []
            for sub in (el.get("elements") or []):
                if isinstance(sub, dict) and sub.get("content"):
                    note_parts.append(str(sub["content"]))
            if note_parts:
                v2_elements.append({
                    "tag": "markdown",
                    "text_size": "notation",
                    "content": f"<font color='grey'>{' '.join(note_parts)}</font>",
                })
        elif tag == "action":
            cols = []
            for btn in (el.get("actions") or []):
                if not isinstance(btn, dict) or btn.get("tag") != "button":
                    continue
                v2_btn = {
                    "tag": "button",
                    "text": dict(btn.get("text") or {"tag": "plain_text", "content": "选择"}),
                    "type": btn.get("type", "default"),
                    "width": "fill",
                }
                if "value" in btn:
                    v2_btn["behaviors"] = [{"type": "callback", "value": btn["value"]}]
                if "url" in btn:
                    v2_btn["behaviors"] = [{"type": "open_url", "default_url": btn["url"]}]
                cols.append({
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [v2_btn],
                })
            if cols:
                v2_elements.append({
                    "tag": "column_set",
                    "flex_mode": "flow",
                    "horizontal_spacing": "small",
                    "columns": cols,
                })

    if not tagged_primary_md and stream_element_id:
        v2_elements.insert(0, {"tag": "markdown", "element_id": stream_element_id, "content": "..."})

    result = {
        "schema": "2.0",
        "config": v2_config,
        "body": {"elements": v2_elements},
    }
    if card_v1.get("header"):
        result["header"] = dict(card_v1["header"])
    return result
