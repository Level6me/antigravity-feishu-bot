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


def _inject_v2_schema_marker(val):
    """Attach stateless `_card_schema: "2.0"` marker into callback value dicts without mutating input."""
    if isinstance(val, dict):
        copied = dict(val)
        copied.setdefault("_card_schema", "2.0")
        return copied
    return val


def _convert_button_v1_to_v2(btn: dict) -> dict:
    """Convert a Card 1.0 button element to a spec-compliant Card 2.0 button."""
    v2_btn = {
        "tag": "button",
        "text": dict(btn.get("text") or {"tag": "plain_text", "content": "选择"}),
        "type": btn.get("type", "default"),
    }
    if "width" in btn:
        v2_btn["width"] = btn["width"]
    else:
        v2_btn["width"] = "fill"
    if "name" in btn:
        v2_btn["name"] = btn["name"]
    if "disabled" in btn:
        v2_btn["disabled"] = bool(btn["disabled"])
    if "confirm" in btn and isinstance(btn["confirm"], dict):
        v2_btn["confirm"] = dict(btn["confirm"])

    action_type = btn.get("action_type") or btn.get("form_action_type")
    if action_type == "form_submit" or btn.get("form_action_type") == "submit":
        v2_btn["form_action_type"] = "submit"
        v2_btn.setdefault("name", "form_submit_btn")
    elif action_type == "form_reset" or btn.get("form_action_type") == "reset":
        v2_btn["form_action_type"] = "reset"
        v2_btn.setdefault("name", "form_reset_btn")

    if "behaviors" in btn and isinstance(btn["behaviors"], list):
        behaviors = []
        for bh in btn["behaviors"]:
            if isinstance(bh, dict):
                bh_copy = dict(bh)
                if bh_copy.get("type") == "callback" and "value" in bh_copy:
                    bh_copy["value"] = _inject_v2_schema_marker(bh_copy["value"])
                behaviors.append(bh_copy)
            else:
                behaviors.append(bh)
        v2_btn["behaviors"] = behaviors
    elif "value" in btn:
        v2_btn["behaviors"] = [{"type": "callback", "value": _inject_v2_schema_marker(btn["value"])}]
    elif "url" in btn:
        v2_btn["behaviors"] = [{"type": "open_url", "default_url": btn["url"]}]
    return v2_btn


def _convert_element_v1_to_v2(el: dict, stream_state: dict) -> list:
    """Recursively convert a single Card 1.0 element into one or more Card 2.0 elements."""
    if not isinstance(el, dict):
        return []
    tag = el.get("tag")
    stream_element_id = stream_state.get("stream_element_id", "")

    if tag == "markdown":
        new_md = {"tag": "markdown", "content": str(el.get("content") or "")}
        if el.get("text_size"):
            new_md["text_size"] = el["text_size"]
        if el.get("element_id"):
            new_md["element_id"] = el["element_id"]
        elif not stream_state["tagged"] and stream_element_id:
            new_md["element_id"] = stream_element_id
            stream_state["tagged"] = True
        return [new_md]

    if tag == "hr":
        return [{"tag": "hr"}]

    if tag == "note":
        note_parts = []
        for sub in (el.get("elements") or []):
            if isinstance(sub, dict) and sub.get("content"):
                note_parts.append(str(sub["content"]))
        if note_parts:
            return [{
                "tag": "markdown",
                "text_size": "notation",
                "content": f"<font color='grey'>{' '.join(note_parts)}</font>",
            }]
        return []

    if tag == "div":
        out = []
        text_obj = el.get("text")
        if isinstance(text_obj, dict) and text_obj.get("content"):
            md_el = {"tag": "markdown", "content": str(text_obj["content"])}
            if not stream_state["tagged"] and stream_element_id:
                md_el["element_id"] = stream_element_id
                stream_state["tagged"] = True
            out.append(md_el)
        fields = el.get("fields") or []
        if fields:
            cols = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                ft = f.get("text") if isinstance(f.get("text"), dict) else {}
                content = str(ft.get("content") or "")
                if not content:
                    continue
                cols.append({
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [{"tag": "markdown", "content": content}],
                })
            if cols:
                out.append({
                    "tag": "column_set",
                    "flex_mode": "flow",
                    "horizontal_spacing": "small",
                    "columns": cols,
                })
        extra = el.get("extra")
        if isinstance(extra, dict):
            extra_tag = extra.get("tag")
            if extra_tag == "button":
                out.append(_convert_button_v1_to_v2(extra))
            elif extra_tag in ("select_static", "input"):
                copied_extra = dict(extra)
                if "value" in copied_extra:
                    copied_extra["value"] = _inject_v2_schema_marker(copied_extra["value"])
                out.append(copied_extra)
        return out

    if tag == "button":
        return [_convert_button_v1_to_v2(el)]

    if tag == "action":
        cols = []
        standalone = []
        for item in (el.get("actions") or []):
            if not isinstance(item, dict):
                continue
            item_tag = item.get("tag")
            if item_tag == "button":
                cols.append({
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [_convert_button_v1_to_v2(item)],
                })
            elif item_tag in ("select_static", "input"):
                copied = dict(item)
                if "value" in copied:
                    copied["value"] = _inject_v2_schema_marker(copied["value"])
                standalone.append(copied)
        out = []
        if cols:
            out.append({
                "tag": "column_set",
                "flex_mode": "flow",
                "horizontal_spacing": "small",
                "columns": cols,
            })
        out.extend(standalone)
        return out

    if tag == "column_set":
        v2_cols = []
        for col in (el.get("columns") or []):
            if not isinstance(col, dict):
                continue
            col_copy = {k: v for k, v in col.items() if k != "elements"}
            col_copy["tag"] = "column"
            converted_children = []
            for child in (col.get("elements") or []):
                converted_children.extend(_convert_element_v1_to_v2(child, stream_state))
            col_copy["elements"] = converted_children
            if converted_children:
                v2_cols.append(col_copy)
        if not v2_cols:
            return []
        cs_copy = {k: v for k, v in el.items() if k != "columns"}
        cs_copy["tag"] = "column_set"
        cs_copy["columns"] = v2_cols
        return [cs_copy]

    if tag == "form":
        form_children = []
        for child in (el.get("elements") or []):
            form_children.extend(_convert_element_v1_to_v2(child, stream_state))
        if not form_children:
            return []
        form_copy = {k: v for k, v in el.items() if k != "elements"}
        form_copy["tag"] = "form"
        form_copy.setdefault("name", "auto_form")
        form_copy["elements"] = form_children
        return [form_copy]

    if tag in ("select_static", "input", "img", "collapsible_panel"):
        copied = dict(el)
        if "value" in copied:
            copied["value"] = _inject_v2_schema_marker(copied["value"])
        if "behaviors" in copied and isinstance(copied["behaviors"], list):
            new_bhs = []
            for bh in copied["behaviors"]:
                if isinstance(bh, dict):
                    bh_c = dict(bh)
                    if bh_c.get("type") == "callback" and "value" in bh_c:
                        bh_c["value"] = _inject_v2_schema_marker(bh_c["value"])
                    new_bhs.append(bh_c)
                else:
                    new_bhs.append(bh)
            copied["behaviors"] = new_bhs
        if tag == "collapsible_panel" and isinstance(el.get("elements"), list):
            inner = []
            for child in el["elements"]:
                inner.extend(_convert_element_v1_to_v2(child, stream_state))
            copied["elements"] = inner
        return [copied]

    return []


def convert_card_v1_to_v2(
    card_v1: dict,
    stream_element_id: str = "stream_md",
    streaming_mode: bool = False,
) -> dict:
    """Convert a Feishu Card JSON 1.0 payload into a spec-compliant Card JSON 2.0 payload.
    - Translates `note` blocks into grey notation markdown (Card 2.0 removed `note` tag).
    - Translates `action` blocks into Card 2.0 `column_set` / `button` with `behaviors` callbacks.
    - Preserves `div`, `column_set`, `form`, `select_static`, `input`, and `confirm` dialogs.
    - Injects stateless `_card_schema: "2.0"` into interactive callback values so post-restart
      callbacks still know the message is Schema 2.0.
    - Tags the primary `markdown` element with `element_id=stream_element_id` for 60fps CardKit streaming.
    """
    if not isinstance(card_v1, dict):
        card_v1 = {}
    if card_v1.get("schema") == "2.0":
        return card_v1

    v2_config = dict(card_v1.get("config") or {})
    # Card JSON 2.0 deprecated `wide_screen_mode` (wide layout is default in Schema 2.0)
    v2_config.pop("wide_screen_mode", None)
    v2_config["update_multi"] = True
    if streaming_mode:
        v2_config["streaming_mode"] = True
        v2_config.setdefault("summary", {"content": "[生成中...]"})
    else:
        v2_config["streaming_mode"] = False

    v2_elements = []
    stream_state = {"stream_element_id": stream_element_id, "tagged": False}

    for el in (card_v1.get("elements") or []):
        v2_elements.extend(_convert_element_v1_to_v2(el, stream_state))

    if not stream_state["tagged"] and stream_element_id:
        v2_elements.insert(0, {"tag": "markdown", "element_id": stream_element_id, "content": "..."})

    result = {
        "schema": "2.0",
        "config": v2_config,
        "body": {"elements": v2_elements},
    }
    if card_v1.get("header"):
        result["header"] = dict(card_v1["header"])
    return result


def adapt_card_for_message(card: dict, message_id: str = "", force_v2: bool = False) -> dict:
    """Ensure a card payload matches the target message's schema (upgrade V1 -> V2 when needed)."""
    if not isinstance(card, dict):
        return card
    if card.get("schema") == "2.0":
        return card
    is_v2 = bool(force_v2)
    if not is_v2 and message_id:
        try:
            import app_state
            is_v2 = app_state.is_v2_card_message(message_id)
        except Exception:
            is_v2 = False
    if is_v2:
        return convert_card_v1_to_v2(card, stream_element_id="", streaming_mode=False)
    return card
