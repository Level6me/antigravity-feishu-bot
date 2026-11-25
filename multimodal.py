import re
import os
import json
import asyncio
import tempfile
import subprocess
from urllib.parse import unquote
import lark_oapi as lark
from logger import log
from config import get_brain_dir
from lark_client import upload_file_sdk, reply_file_sdk, reply_voice_sdk, send_file_to_chat_sdk, send_voice_to_chat_sdk

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB Feishu image limit
MAX_FILE_UPLOAD_BYTES = 30 * 1024 * 1024    # 30 MB Feishu file limit


async def extract_and_upload_resources_async(
    text, message_id, api_client, additional_safe_dirs=None, chat_id=None, allow_home_deliverables=False
):
    """Non-blocking async wrapper for extract_and_upload_resources."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: extract_and_upload_resources(
            text,
            message_id,
            api_client,
            additional_safe_dirs,
            chat_id,
            allow_home_deliverables=allow_home_deliverables,
        )
    )


_FEISHU_IMAGE_KEY_RE = re.compile(r"^img_(?:v\d+_)?[A-Za-z0-9_-]{8,}$")
_FENCED_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")
_CODE_LINE_FRAGMENT_RE = re.compile(r"^L\d+(?:-L?\d+)?$", re.IGNORECASE)

_DELIVERY_EMOJI_CHARS = "📄📝📁📎📑📦📊📈📤🗂💾🎵🎬"
_MARKDOWN_LINK_WITH_PREFIX_RE = re.compile(
    rf"(?P<prefix>(?:[{_DELIVERY_EMOJI_CHARS}]\s*|(?:附件|发送文件|推送文件|下载文件|导出文件|attachment|deliver)\s*[:：]\s*)?)"
    rf"`?(?<!!)\[(?P<label>[^\]\n]*?)\]\((?P<target>[^)\n]+)\)`?"
)
_EXPLICIT_LABEL_DELIVERY_RE = re.compile(
    r"(?:下载|导出|附件|发送文件|推送|点击查收|\b(?:download|export|attachment|deliver)\b)",
    re.IGNORECASE,
)

# 仅在未加显式附件标记时自动推送的「纯交付产物」扩展名（文档/压缩包/安装包/固件/音视频）
DELIVERABLE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".apk", ".ipa", ".dmg", ".pkg", ".deb", ".rpm", ".exe", ".msi",
    ".bin", ".hex", ".elf", ".uf2",
    ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
}
_TMP_EXTRA_DELIVERABLE_EXTENSIONS = {".csv", ".tsv"}

_DELIVERABLE_EXT_PATTERN = "|".join(
    re.escape(ext.lstrip(".")) for ext in sorted(DELIVERABLE_EXTENSIONS, key=len, reverse=True)
)
_EXPLICIT_BARE_PATH_RE = re.compile(
    rf"(?:[{_DELIVERY_EMOJI_CHARS}]\s*(?:(?:文件|附件|路径|文件路径)\s*[:：]\s*)?|(?:附件|发送文件|推送文件|下载文件|导出文件|attachment|deliver)\s*[:：]\s*)"
    rf"`?(?P<path>(?:file://)?(?:~/|/)[^\s`\"'<>()\[\]{{}}]+)`?"
)
_DELIVERABLE_BARE_PATH_RE = re.compile(
    rf"(?<![A-Za-z0-9_:/.-])`?(?P<path>(?:file://)?(?:~/|/)[^\s`\"'<>()\[\]{{}}]+?\.(?:{_DELIVERABLE_EXT_PATTERN}))`?(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)

_SENSITIVE_FILENAMES = {
    ".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "authorized_keys", "known_hosts", "shadow", "passwd",
    "antigravity_bot.db", "user_profiles.json", "chat_sessions.json",
    ".npmrc", ".pypirc", ".netrc", ".git-credentials",
}
_SENSITIVE_PATH_SEGMENTS = (
    "/.ssh/", "/.aws/", "/.gnupg/", "/.kube/", "/.docker/",
    "/etc/", "/proc/", "/sys/", "/root/",
)
_SENSITIVE_EXTENSIONS = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore",
    ".sqlite", ".sqlite3", ".db", ".kdbx",
)
_INTERNAL_NOISE_DIRS = ("/node_modules/", "/.git/", "/__pycache__/", "/.venv/", "/venv/")


def _strip_fenced_code(text):
    """Remove fenced code blocks before scanning links/paths."""
    return _FENCED_CODE_BLOCK_RE.sub("", text or "")


def _strip_markdown_code(text):
    """Remove fenced code blocks and inline code spans."""
    return _INLINE_CODE_SPAN_RE.sub("", _strip_fenced_code(text))


def _is_plausible_local_target(target):
    """Exclude remote URLs, Feishu image keys, and bare placeholder words like `url`."""
    if not target:
        return False
    t = target.strip().strip("<>")
    if not t or t.startswith(("http://", "https://", "mailto:", "conversation://")) or _FEISHU_IMAGE_KEY_RE.match(t):
        return False
    return ("/" in t) or ("\\" in t) or ("." in t)


def _normalize_local_target(raw_target, base_dirs=None):
    """Normalize a Markdown link target or local path into (abs_path, is_line_ref)."""
    t = (raw_target or "").strip().strip("<>")
    if t.startswith("file://localhost/"):
        t = "/" + t[len("file://localhost/"):]
    elif t.startswith("file://"):
        t = t[len("file://"):]

    path_part, _, fragment = t.partition("#")
    is_line_ref = bool(fragment and _CODE_LINE_FRAGMENT_RE.match(fragment.strip()))
    path_part = unquote(path_part).strip()
    if not path_part:
        return "", is_line_ref

    expanded = os.path.expanduser(path_part)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded), is_line_ref

    if base_dirs:
        for base in base_dirs:
            if not base or base in ("默认", "Default"):
                continue
            cand = os.path.abspath(os.path.join(os.path.expanduser(base), expanded))
            if os.path.isfile(cand):
                return cand, is_line_ref

    return os.path.abspath(expanded), is_line_ref


def is_sensitive_upload_path(path):
    """Return True if path points to credentials, keys, system configs, or bot databases."""
    try:
        expanded = os.path.expanduser(path)
        candidates = (os.path.abspath(expanded), os.path.realpath(expanded))
        for p in candidates:
            base_name = os.path.basename(p).lower()
            if base_name in _SENSITIVE_FILENAMES or base_name.startswith(".env."):
                return True
            if any(seg in p for seg in _SENSITIVE_PATH_SEGMENTS):
                return True
            if base_name.endswith(_SENSITIVE_EXTENSIONS):
                return True
    except Exception:
        return True
    return False


def _extract_local_image_paths(text, base_dirs=None):
    """Extract plausible local image paths outside Markdown code blocks/spans."""
    cleaned = _strip_markdown_code(text)
    results = []
    for img in re.findall(r'!\[.*?\]\(([^)]+)\)', cleaned):
        if not _is_plausible_local_target(img):
            continue
        abs_img, is_line_ref = _normalize_local_target(img, base_dirs=base_dirs)
        if abs_img and not is_line_ref:
            results.append(abs_img)
    return results


def _collect_file_candidates(text, base_dirs=None):
    """Collect candidate local file deliveries from links and explicit/deliverable bare paths."""
    fenced_stripped = _strip_fenced_code(text)
    candidates = []
    seen_spans = []

    def _overlaps(start, end):
        return any(not (end <= s or start >= e) for s, e in seen_spans)

    for m in _MARKDOWN_LINK_WITH_PREFIX_RE.finditer(fenced_stripped):
        raw_full = m.group(0)
        is_in_backticks = raw_full.startswith("`") and raw_full.endswith("`")
        prefix = (m.group("prefix") or "").strip()
        label = (m.group("label") or "").strip()
        raw_target = (m.group("target") or "").strip()
        if not _is_plausible_local_target(raw_target):
            continue
        abs_file, is_line_ref = _normalize_local_target(raw_target, base_dirs=base_dirs)
        if not abs_file:
            continue
        has_explicit_marker = bool(prefix) or bool(_EXPLICIT_LABEL_DELIVERY_RE.search(label))
        # 行内反引号包裹的普通链接仅在存在显式交付标记或文件真实存在且为可交付类型时保留，防止文档占位符触发误报
        if is_in_backticks and not has_explicit_marker:
            _, ext = os.path.splitext(abs_file)
            if ext.lower() not in DELIVERABLE_EXTENSIONS or not os.path.isfile(abs_file):
                continue
        seen_spans.append((m.start(), m.end()))
        candidates.append({
            "raw_target": raw_target,
            "abs_file": abs_file,
            "label": label,
            "has_explicit_marker": has_explicit_marker,
            "is_line_ref": is_line_ref,
            "is_bare_path": False,
            "from_backticks": is_in_backticks,
        })

    for m in _EXPLICIT_BARE_PATH_RE.finditer(fenced_stripped):
        if _overlaps(m.start(), m.end()):
            continue
        raw_target = (m.group("path") or "").strip().rstrip(".,;:")
        if not _is_plausible_local_target(raw_target):
            continue
        abs_file, is_line_ref = _normalize_local_target(raw_target, base_dirs=base_dirs)
        if not abs_file:
            continue
        seen_spans.append((m.start(), m.end()))
        candidates.append({
            "raw_target": raw_target,
            "abs_file": abs_file,
            "label": os.path.basename(abs_file),
            "has_explicit_marker": True,
            "is_line_ref": is_line_ref,
            "is_bare_path": True,
            "from_backticks": "`" in m.group(0),
        })

    for m in _DELIVERABLE_BARE_PATH_RE.finditer(fenced_stripped):
        if _overlaps(m.start(), m.end()):
            continue
        raw_target = (m.group("path") or "").strip().rstrip(".,;:")
        if not _is_plausible_local_target(raw_target):
            continue
        abs_file, is_line_ref = _normalize_local_target(raw_target, base_dirs=base_dirs)
        if not abs_file or is_line_ref or not os.path.isfile(abs_file):
            continue
        if any(seg in abs_file for seg in _INTERNAL_NOISE_DIRS):
            continue
        seen_spans.append((m.start(), m.end()))
        candidates.append({
            "raw_target": raw_target,
            "abs_file": abs_file,
            "label": os.path.basename(abs_file),
            "has_explicit_marker": False,
            "is_line_ref": False,
            "is_bare_path": True,
            "from_backticks": "`" in m.group(0),
        })

    return candidates


def extract_and_upload_resources(
    text, message_id, api_client, additional_safe_dirs=None, chat_id=None, allow_home_deliverables=False
):
    """
    Scans `text` for image and explicit/deliverable file references and uploads
    them into Feishu chat using official Lark OpenAPI.
    Returns a summary dict: {"uploaded": [...], "skipped": [...], "failed": [...]}.
    """
    summary = {"uploaded": [], "skipped": [], "failed": []}
    if not text:
        return summary

    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_dir = tempfile.gettempdir()
    home_dir = os.path.abspath(os.path.expanduser("~"))
    brain_dir = os.path.abspath(get_brain_dir())

    downloads_dir = os.path.abspath(os.path.join(workspace_dir, "downloads"))
    scratch_dir = os.path.abspath(os.path.join(workspace_dir, "scratch"))

    safe_prefixes = [
        downloads_dir,
        scratch_dir,
        tmp_dir,
        "/tmp",
        brain_dir,
    ]
    for user_sub in ("Downloads", "Desktop", "Documents", "Pictures", "Videos", "Music"):
        sub_path = os.path.join(home_dir, user_sub)
        if os.path.isdir(sub_path):
            safe_prefixes.append(sub_path)

    # 用户主目录本身绝不允许作为无限制回传白名单（防止默认 WORKSPACE_ROOT=~ 时
    # 非授权会话把任意 home 文件上传到飞书）。具体子目录（如 ~/my-project）不受影响。
    project_safe_dirs = []
    if additional_safe_dirs:
        for d in additional_safe_dirs:
            if d and d not in ["默认", "Default"]:
                abs_d = os.path.abspath(os.path.expanduser(d))
                if abs_d != home_dir:
                    safe_prefixes.append(abs_d)
                    project_safe_dirs.append(abs_d)

    def _is_under_dir(target_path, directory):
        d = os.path.realpath(os.path.abspath(directory))
        tp = os.path.realpath(os.path.abspath(target_path))
        return tp == d or tp.startswith(d + os.sep)

    real_home_dir = os.path.realpath(home_dir)

    def _is_hidden_home_subdir(target_path):
        tp = os.path.realpath(os.path.abspath(target_path))
        if not _is_under_dir(tp, real_home_dir) or tp == real_home_dir:
            return False
        if _is_under_dir(tp, brain_dir):
            return False
        rel = os.path.relpath(tp, real_home_dir)
        parts = rel.split(os.sep)
        return any(part.startswith(".") for part in parts[:-1])

    def is_safe_path(path):
        try:
            expanded = os.path.expanduser(path)
            abs_path = os.path.abspath(expanded)
            real_path = os.path.realpath(abs_path)
            if abs_path == home_dir or real_path == real_home_dir:
                return False
            if is_sensitive_upload_path(abs_path) or is_sensitive_upload_path(real_path):
                return False
            for prefix in safe_prefixes:
                if _is_under_dir(real_path, prefix):
                    return True
            if allow_home_deliverables and _is_under_dir(real_path, real_home_dir) and not _is_hidden_home_subdir(real_path):
                return True
        except Exception as e:
            log.debug(f"[Multimodal] is_safe_path check error for {path}: {e}")
        return False

    cleaned_text = _strip_markdown_code(text)
    images = _extract_local_image_paths(cleaned_text, base_dirs=additional_safe_dirs)
    candidates = _collect_file_candidates(text, base_dirs=additional_safe_dirs)

    # Also scan inside artifact/explicitly-delivered .md files for images (only if safe & bounded size)
    for cand in candidates:
        abs_file = cand["abs_file"]
        is_brain_md = _is_under_dir(abs_file, brain_dir)
        if (
            abs_file.lower().endswith(".md")
            and (is_brain_md or cand["has_explicit_marker"])
            and os.path.isfile(abs_file)
            and is_safe_path(abs_file)
        ):
            try:
                if os.path.getsize(abs_file) <= 2 * 1024 * 1024:
                    with open(abs_file, "r", encoding="utf-8", errors="replace") as f:
                        md_content = f.read()
                        images.extend(_extract_local_image_paths(md_content, base_dirs=[os.path.dirname(abs_file)]))
            except Exception as e:
                log.error(f"[Multimodal] Error scanning md file: {e}")

    # 1. Upload Images (concurrently upload keys when multiple images exist, then send in deterministic order)
    from concurrent.futures import ThreadPoolExecutor

    valid_images = []
    seen_images = set()
    for img_path in images:
        if img_path in seen_images:
            continue
        seen_images.add(img_path)
        if not os.path.isfile(img_path):
            continue
        if not is_safe_path(img_path):
            log.warning(f"[Multimodal] Blocked unsafe image upload path: {img_path}")
            continue
        try:
            if os.path.getsize(img_path) > MAX_IMAGE_UPLOAD_BYTES:
                log.warning(f"[Multimodal] Skipping oversized image (>10MB): {img_path}")
                summary["failed"].append((img_path, "图片超过 10MB 限制"))
                continue
        except Exception as e:
            summary["failed"].append((img_path, f"读取图片大小异常: {e}"))
            continue
        valid_images.append(img_path)

    def _upload_single_image_key(img_path: str):
        try:
            with open(img_path, "rb") as f:
                req = lark.api.im.v1.CreateImageRequest.builder().request_body(
                    lark.api.im.v1.CreateImageRequestBody.builder().image_type("message").image(f).build()
                ).build()
                resp = api_client.im.v1.image.create(req)
            if resp.code == 0:
                img_key = json.loads(resp.raw.content).get('data', {}).get('image_key')
                if img_key:
                    return (img_path, img_key, None)
                return (img_path, None, "飞书图片上传未返回 image_key")
            log.error(f"[Multimodal] Failed to upload image: {resp.msg}")
            return (img_path, None, f"飞书图片上传失败: {resp.msg}")
        except Exception as e:
            log.error(f"[Multimodal] Error uploading image: {e}")
            return (img_path, None, f"飞书图片上传异常: {e}")

    if len(valid_images) == 1:
        image_upload_results = [_upload_single_image_key(valid_images[0])]
    elif len(valid_images) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(valid_images))) as pool:
            image_upload_results = list(pool.map(_upload_single_image_key, valid_images))
    else:
        image_upload_results = []

    for img_path, img_key, err_reason in image_upload_results:
        if err_reason or not img_key:
            summary["failed"].append((img_path, err_reason or "飞书图片上传失败"))
            continue
        try:
            log.info(f"[Multimodal] Image uploaded successfully, image_key: {img_key}")
            if message_id and str(message_id).startswith("om_"):
                msg_req = lark.api.im.v1.ReplyMessageRequest.builder().message_id(message_id).request_body(
                    lark.api.im.v1.ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": img_key})).build()
                ).build()
                api_client.im.v1.message.reply(msg_req)
            elif chat_id:
                msg_req = lark.api.im.v1.CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
                    lark.api.im.v1.CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("image").content(json.dumps({"image_key": img_key})).build()
                ).build()
                api_client.im.v1.message.create(msg_req)
            summary["uploaded"].append(img_path)
        except Exception as e:
            log.error(f"[Multimodal] Error sending uploaded image {img_path}: {e}")
            summary["failed"].append((img_path, f"飞书图片发送异常: {e}"))

    # 2. Upload Files / Audio (validate upfront, concurrently upload keys if multiple, then send in order)
    uploaded_files = set()
    valid_file_tasks = []
    for cand in candidates:
        abs_file = cand["abs_file"]
        if abs_file in uploaded_files:
            continue

        # 代码行号引用（如 file.py#L10-L20）永远不作为附件上传
        if cand["is_line_ref"]:
            summary["skipped"].append(abs_file)
            continue

        _, ext = os.path.splitext(abs_file)
        ext_lower = ext.lower()

        is_staging_dir = (
            _is_under_dir(abs_file, downloads_dir)
            or _is_under_dir(abs_file, scratch_dir)
        )
        is_under_project = any(_is_under_dir(abs_file, p) for p in project_safe_dirs)
        is_tmp_export_dir = (_is_under_dir(abs_file, tmp_dir) or _is_under_dir(abs_file, "/tmp")) and not is_under_project

        is_intended_delivery = (
            cand["has_explicit_marker"]
            or (is_staging_dir and ext_lower not in {".pyc", ".pyo"})
            or (is_tmp_export_dir and ext_lower in (DELIVERABLE_EXTENSIONS | _TMP_EXTRA_DELIVERABLE_EXTENSIONS))
            or (ext_lower in DELIVERABLE_EXTENSIONS and not any(seg in abs_file for seg in _INTERNAL_NOISE_DIRS))
        )

        if not is_intended_delivery:
            log.info(f"[Multimodal] Skipping auto-upload for regular code reference: {cand['raw_target']}")
            summary["skipped"].append(abs_file)
            continue

        # 仅当目标确实存在且为普通文件时上传（目录或文本示例占位符直接跳过）
        if not os.path.isfile(abs_file):
            if cand["has_explicit_marker"] and not cand["from_backticks"]:
                log.info(f"[Multimodal] Skipping missing or non-file delivery target: {abs_file}")
            summary["skipped"].append(abs_file)
            continue

        if not is_safe_path(abs_file):
            log.warning(f"[Multimodal] Blocked unsafe file upload path: {abs_file}")
            summary["failed"].append((abs_file, "路径不在允许的安全传输目录内"))
            continue

        try:
            file_size = os.path.getsize(abs_file)
            if file_size <= 0:
                log.warning(f"[Multimodal] Skipping empty file (0 bytes): {abs_file}")
                summary["failed"].append((abs_file, "文件为空 (0 字节)，飞书不支持上传空文件"))
                continue
            if file_size > MAX_FILE_UPLOAD_BYTES:
                log.warning(f"[Multimodal] Skipping oversized file (>30MB): {abs_file}")
                summary["failed"].append((abs_file, "文件超过飞书 30MB 单文件上限"))
                continue
            uploaded_files.add(abs_file)
            valid_file_tasks.append((abs_file, ext_lower == ".opus"))
        except Exception as e:
            log.error(f"[Multimodal] Error inspecting file {abs_file}: {e}")
            summary["failed"].append((abs_file, f"上传异常: {e}"))

    def _upload_single_file_key(task_item):
        abs_file, is_opus_voice = task_item
        try:
            duration_ms = None
            if is_opus_voice:
                try:
                    probe_cmd = [
                        "ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", abs_file
                    ]
                    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5)
                    duration_ms = max(1000, int(float(probe_res.stdout.strip()) * 1000))
                except Exception:
                    duration_ms = 1000
            file_key = upload_file_sdk(abs_file, duration_ms=duration_ms)
            if file_key:
                return (abs_file, is_opus_voice, file_key, None)
            log.error(f"[Multimodal] Failed to upload file via Lark API: {abs_file}")
            return (abs_file, is_opus_voice, None, "飞书文件上传接口返回失败")
        except Exception as e:
            log.error(f"[Multimodal] Error uploading file {abs_file}: {e}")
            return (abs_file, is_opus_voice, None, f"上传异常: {e}")

    if len(valid_file_tasks) == 1:
        file_upload_results = [_upload_single_file_key(valid_file_tasks[0])]
    elif len(valid_file_tasks) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(valid_file_tasks))) as pool:
            file_upload_results = list(pool.map(_upload_single_file_key, valid_file_tasks))
    else:
        file_upload_results = []

    for abs_file, is_opus_voice, file_key, err_reason in file_upload_results:
        if err_reason or not file_key:
            summary["failed"].append((abs_file, err_reason or "飞书文件上传接口返回失败"))
            continue
        try:
            log.info(f"[Multimodal] File uploaded to Lark API (key={file_key}, path={abs_file})")
            if is_opus_voice:
                ok = reply_voice_sdk(message_id, file_key) if (message_id and str(message_id).startswith("om_")) else False
                if not ok and chat_id:
                    ok = send_voice_to_chat_sdk(chat_id, file_key)
            else:
                ok = reply_file_sdk(message_id, file_key) if (message_id and str(message_id).startswith("om_")) else False
                if not ok and chat_id:
                    ok = send_file_to_chat_sdk(chat_id, file_key)
            if ok:
                summary["uploaded"].append(abs_file)
            else:
                summary["failed"].append((abs_file, "飞书文件消息发送失败"))
        except Exception as e:
            log.error(f"[Multimodal] Error sending uploaded file {abs_file}: {e}")
            summary["failed"].append((abs_file, f"上传异常: {e}"))

    return summary
