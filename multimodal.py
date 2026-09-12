import re
import os
import json
import time
import tempfile
import subprocess
import lark_oapi as lark
from logger import log
from config import get_brain_dir
from lark_client import upload_file_sdk, reply_file_sdk, reply_voice_sdk, send_file_to_chat_sdk, send_voice_to_chat_sdk


def extract_and_upload_resources(text, message_id, api_client, additional_safe_dirs=None, chat_id=None):
    """
    Scans `text` for image and file references and automatically uploads
    and delivers them into Feishu chat using official Lark OpenAPI.
    """
    if not text:
        return

    images = [
        img for img in re.findall(r'!\[.*?\]\((?:file://)?([^)]+)\)', text)
        if not img.startswith(('http://', 'https://'))
    ]
    raw_files = [
        (label.strip(), f.strip())
        for label, f in re.findall(r'(?<!!)\[(.*?)\]\((?:file://)?([^)]+)\)', text)
        if not f.startswith(('http://', 'https://'))
    ]

    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_dir = tempfile.gettempdir()
    safe_prefixes = [
        os.path.join(workspace_dir, "downloads"),
        os.path.join(workspace_dir, "scratch"),
        tmp_dir,
        "/tmp",
        get_brain_dir()
    ]

    # 用户主目录本身绝不允许作为回传白名单（防止默认 WORKSPACE_ROOT=~ 时
    # 模型把任意 home 文件上传到飞书）。具体子目录（如 ~/my-project）不受影响。
    home_dir = os.path.abspath(os.path.expanduser("~"))

    if additional_safe_dirs:
        for d in additional_safe_dirs:
            if d and d not in ["默认", "Default"] and os.path.abspath(d) != home_dir:
                safe_prefixes.append(d)

    def is_safe_path(path):
        try:
            abs_path = os.path.abspath(path)
            if abs_path == home_dir:
                return False
            for prefix in safe_prefixes:
                p = os.path.abspath(prefix)
                # 目录边界判断：/safe 不能通过 /safe_evil 的校验
                if abs_path == p or abs_path.startswith(p + os.sep):
                    return True
        except Exception:
            pass
        return False

    IGNORED_EXTENSIONS = {
        '.py', '.swift', '.js', '.ts', '.html', '.css', '.json', '.md',
        '.java', '.cpp', '.c', '.h', '.m', '.txt', '.log', '.sh', '.rb',
        '.go', '.rs', '.pbxproj', '.xcworkspacedata', '.plist'
    }

    # Also scan inside artifact .md files for images
    for _, file_path in raw_files:
        if file_path.endswith(".md") and os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    md_content = f.read()
                    imgs = [
                        img for img in re.findall(r'!\[.*?\]\((?:file://)?([^)]+)\)', md_content)
                        if not img.startswith(('http://', 'https://'))
                    ]
                    images.extend(imgs)
            except Exception as e:
                log.error(f"[Multimodal] Error scanning md file: {e}")

    # 1. Upload Images
    for img_path in set(images):
        if not is_safe_path(img_path):
            log.warning(f"[Multimodal] Blocked unsafe image upload path: {img_path}")
            continue
        if os.path.exists(img_path):
            try:
                with open(img_path, "rb") as f:
                    req = lark.api.im.v1.CreateImageRequest.builder().request_body(
                        lark.api.im.v1.CreateImageRequestBody.builder().image_type("message").image(f).build()
                    ).build()
                    resp = api_client.im.v1.image.create(req)
                if resp.code == 0:
                    img_key = json.loads(resp.raw.content).get('data', {}).get('image_key')
                    if img_key:
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
                else:
                    log.error(f"[Multimodal] Failed to upload image: {resp.msg}")
            except Exception as e:
                log.error(f"[Multimodal] Error uploading image: {e}")

    # 2. Upload Files / Audio
    uploaded_files = set()
    for label, file_path in raw_files:
        if file_path in uploaded_files:
            continue
        if not is_safe_path(file_path):
            log.warning(f"[Multimodal] Blocked unsafe file upload path: {file_path}")
            continue

        abs_file = os.path.abspath(file_path)
        _, ext = os.path.splitext(abs_file)
        ext_lower = ext.lower()

        # 判断是否属于明确要求发送/导出的文件：
        # 1. 最近 15 分钟内新创建或修改的文件（大模型本次任务现场生成的交付物）
        # 2. 存放在 downloads/、scratch/ 或 tmp 交付目录下的文件
        # 3. 链接文本或整体回复内容中含有明确的交付/下载/查收意图
        # 4. 链接带有文档类 Emoji 标记（如 📄 [文件名](...)）
        # 5. 后缀名本身不是常见的被忽略源码类型
        is_delivery_dir = (
            abs_file.startswith(os.path.abspath(os.path.join(workspace_dir, "downloads"))) or
            abs_file.startswith(os.path.abspath(os.path.join(workspace_dir, "scratch"))) or
            abs_file.startswith(os.path.abspath(tmp_dir)) or
            abs_file.startswith("/tmp")
        )

        is_recent_file = False
        try:
            if os.path.exists(abs_file):
                # 15 分钟内修改过的文件视作当前任务产物
                is_recent_file = (time.time() - os.path.getmtime(abs_file) < 900)
        except Exception:
            pass

        has_delivery_intent = any(
            kw in label.lower() for kw in 
            ["发送", "下载", "导出", "附件", "文档", "笑话", "报告", "数据", "表格", "send", "file", "download", "export", "doc"]
        )

        context_has_delivery_intent = any(
            kw in text for kw in 
            ["请查收", "查收", "发给", "生成了文档", "整理好", "保存在", "生成了", "点击查收", "为您生成", "附件", "下载", "文档"]
        )

        has_emoji_attachment = bool(re.search(r'[📄📝📁📎📑📦📊📈]\s*\[' + re.escape(label) + r'\]', text))

        is_intended_delivery = (
            is_delivery_dir or 
            is_recent_file or 
            has_delivery_intent or 
            context_has_delivery_intent or 
            has_emoji_attachment or 
            ext_lower not in IGNORED_EXTENSIONS
        )

        if not is_intended_delivery:
            log.info(f"[Multimodal] Skipping auto-upload for regular code reference: {file_path}")
            continue


        if os.path.exists(abs_file):
            try:
                uploaded_files.add(file_path)
                duration_ms = None
                is_opus_voice = (ext_lower == ".opus")
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
                    log.info(f"[Multimodal] File uploaded to Lark API (key={file_key}, path={abs_file})")
                    if is_opus_voice:
                        ok = reply_voice_sdk(message_id, file_key) if (message_id and str(message_id).startswith("om_")) else False
                        if not ok and chat_id:
                            send_voice_to_chat_sdk(chat_id, file_key)
                    else:
                        ok = reply_file_sdk(message_id, file_key) if (message_id and str(message_id).startswith("om_")) else False
                        if not ok and chat_id:
                            send_file_to_chat_sdk(chat_id, file_key)
                else:
                    log.error(f"[Multimodal] Failed to upload file via Lark API: {abs_file}")
            except Exception as e:
                log.error(f"[Multimodal] Error uploading file {abs_file}: {e}")
