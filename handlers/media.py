"""Inbound media/message content processors."""
import os
import re

from card_builder import CardBuilder
from lark_client import send_interactive_card_sdk, download_message_resource_sdk


async def _process_image_message(loop, message_id, content_json, content_raw):
    image_key = content_json.get("image_key", "")
    if not image_key:
        match = re.search(r'img_[a-zA-Z0-9_\-]+', content_raw)
        if match:
            image_key = match.group(0)

    if not image_key and content_raw.startswith("[Image: ") and content_raw.endswith("]"):
        image_key = content_raw[8:-1]
    
    bot_reply_msg_id = None
    if image_key:
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator(os.path.basename(output_filename), "图片")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        return f"请查看这张图片并做出回应。图片路径: {output_path}", os.path.basename(output_filename), success, bot_reply_msg_id
    else:
        return "[未获取到图片]", None, True, None


async def _process_post_message(loop, message_id, content_json):
    texts = []
    image_keys = []
    for line in content_json.get("content", []):
        for elem in line:
            if elem.get("tag") == "text":
                texts.append(elem.get("text", ""))
            elif elem.get("tag") == "img":
                image_keys.append(elem.get("image_key", ""))
    
    user_text = " ".join(texts)
    bot_reply_msg_id = None
    downloaded_file_name = None
    download_success = True
    
    if image_keys:
        image_key = image_keys[0]
        os.makedirs("downloads", exist_ok=True)
        output_filename = f"downloads/img_{image_key}.jpg"
        
        dl_card = CardBuilder.build_download_indicator("图片内容")
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
        
        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        
        downloaded_file_name = os.path.basename(output_filename)
        user_text += f"\n[附加图片路径: {output_path}]"
        
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id


async def _process_link_message(content_json):
    if isinstance(content_json, dict):
        user_text = content_json.get("url", content_json.get("href", ""))
    else:
        user_text = str(content_json)
    return user_text, None, True, None


async def _process_file_audio_media_message(loop, message_id, message_type, content_json):
    file_key = content_json.get("file_key", "") or content_json.get("media_key", "") or content_json.get("audio_key", "")
    file_name = content_json.get("file_name", "")
    bot_reply_msg_id = None
    download_success = True
    downloaded_file_name = None
    user_text = ""
    
    if file_key:
        if not file_name:
            if message_type == "audio":
                file_name = f"audio_{file_key}.ogg"
            elif message_type == "media":
                file_name = f"video_{file_key}.mp4"
            else:
                file_name = f"file_{file_key}"
        
        if message_type == "media" and not any(file_name.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v"]):
            file_name = f"{file_name}.mp4"
        if message_type == "audio" and "." not in file_name:
            file_name = f"{file_name}.ogg"
        
        # Purify file_name to prevent directory traversal
        file_name = os.path.basename(file_name)
        
        os.makedirs("downloads", exist_ok=True)
        output_filename = os.path.join("downloads", file_name)
        dl_card = CardBuilder.build_download_indicator(file_name, message_type)
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

        output_path = os.path.abspath(output_filename)
        download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, file_key, "file", output_path))
        downloaded_file_name = file_name
        
        if message_type == "file":
            user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
        elif message_type == "audio":
            user_text = f"请仔细听这段语音内容（语音文件路径: {output_path}），并做出响应。"
        elif message_type == "media":
            user_text = f"请仔细观看这段视频内容（视频文件路径: {output_path}），并做出响应。"
            
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id


async def _process_batch_media_message(loop, message_id, content_json):
    items = content_json.get("items", [])
    media_hints = []
    download_success = True
    
    # 批量下发资源加载指示器
    dl_card = CardBuilder.build_download_indicator(f"合并批处理 ({len(items)} 个文件)", "多媒体组")
    bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
    
    os.makedirs("downloads", exist_ok=True)
    
    for idx, item in enumerate(items):
        m_type = item["message_type"]
        c_json = item["content_json"]
        c_raw = item["content_raw"]
        
        file_key = ""
        file_name = ""
        if m_type == "image":
            file_key = c_json.get("image_key", "")
            if not file_key:
                match = re.search(r'img_[a-zA-Z0-9_\-]+', c_raw)
                if match:
                    file_key = match.group(0)
            file_name = f"batch_img_{idx}_{file_key}.jpg"
        else:
            file_key = c_json.get("file_key", "") or c_json.get("media_key", "") or c_json.get("audio_key", "")
            file_name = c_json.get("file_name", f"batch_file_{idx}_{file_key}")
            file_name = os.path.basename(file_name)
            
        if file_key:
            output_path = os.path.abspath(os.path.join("downloads", file_name))
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(item["message_id"], file_key, "image" if m_type == "image" else "file", output_path))
            if success:
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件路径: `{output_path}`")
            else:
                download_success = False
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件 `{file_name}` (下载失败)")
                
    user_text = f"请查看以下 {len(items)} 个关联多模态文件并做出综合关联回应：\n\n" + "\n".join(media_hints)
    downloaded_file_name = f"合并批处理 ({len(items)} 个文件)"
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

