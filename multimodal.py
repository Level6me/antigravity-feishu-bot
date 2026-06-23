import re
import os
import json
import lark_oapi as lark
from logger import log

def extract_and_upload_resources(text, message_id, api_client):
    home_dir = os.path.expanduser("~")
    home_dir_esc = re.escape(home_dir)
    images = re.findall(r'!\[.*?\]\((?:file://)?(' + home_dir_esc + r'/[^)]+)\)', text)
    files = re.findall(r'(?<!!)\[.*?\]\((?:file://)?(' + home_dir_esc + r'/[^)]+)\)', text)
    
    IGNORED_EXTENSIONS = {
        '.py', '.swift', '.js', '.ts', '.html', '.css', '.json', '.md', 
        '.java', '.cpp', '.c', '.h', '.m', '.txt', '.log', '.sh', '.rb', 
        '.go', '.rs', '.pbxproj', '.xcworkspacedata', '.plist'
    }
    
    # Also scan inside artifact .md files for images
    for file_path in files:
        if file_path.endswith(".md") and os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    md_content = f.read()
                    imgs = re.findall(r'!\[.*?\]\((?:file://)?(' + home_dir_esc + r'/[^)]+)\)', md_content)
                    images.extend(imgs)
            except Exception as e:
                log.error(f"[Multimodal] Error scanning md file: {e}")
    
    for img_path in set(images):
        if os.path.exists(img_path):
            try:
                req = lark.api.im.v1.CreateImageRequest.builder().request_body(
                    lark.api.im.v1.CreateImageRequestBody.builder().image_type("message").image(open(img_path, "rb")).build()
                ).build()
                resp = api_client.im.v1.image.create(req)
                if resp.code == 0:
                    img_key = json.loads(resp.raw.content).get('data', {}).get('image_key')
                    if img_key:
                        log.info(f"[Multimodal] Image uploaded successfully, image_key: {img_key}")
                        msg_req = lark.api.im.v1.ReplyMessageRequest.builder().message_id(message_id).request_body(
                            lark.api.im.v1.ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": img_key})).build()
                        ).build()
                        api_client.im.v1.message.reply(msg_req)
                else:
                    log.error(f"[Multimodal] Failed to upload image: {resp.msg}")
            except Exception as e:
                log.error(f"[Multimodal] Error uploading image: {e}")

    for file_path in set(files):
        _, ext = os.path.splitext(file_path)
        if ext.lower() in IGNORED_EXTENSIONS:
            log.info(f"[Multimodal] Skipping auto-upload for code/text file: {file_path}")
            continue

        if os.path.exists(file_path):
            try:
                req = lark.api.im.v1.CreateFileRequest.builder().request_body(
                    lark.api.im.v1.CreateFileRequestBody.builder().file_type("stream").file_name(os.path.basename(file_path)).file(open(file_path, "rb")).build()
                ).build()
                resp = api_client.im.v1.file.create(req)
                if resp.code == 0:
                    file_key = json.loads(resp.raw.content).get('data', {}).get('file_key')
                    if file_key:
                        msg_req = lark.api.im.v1.ReplyMessageRequest.builder().message_id(message_id).request_body(
                            lark.api.im.v1.ReplyMessageRequestBody.builder().msg_type("file").content(json.dumps({"file_key": file_key})).build()
                        ).build()
                        api_client.im.v1.message.reply(msg_req)
            except Exception as e:
                pass
