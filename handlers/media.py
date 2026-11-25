"""Inbound media/message content processors."""
import os
import re
import asyncio

from config import BASE_DIR
from card_builder import CardBuilder
from lark_client import send_interactive_card_sdk, download_message_resource_sdk
from logger import log

DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")


async def prestage_media_item_async(loop, message_id: str, message_type: str, content_json: dict, content_raw: str) -> None:
    """Pre-download an inbound image/file during the multi-media debounce window so it is ready in 0ms."""
    try:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        if message_type == "image":
            image_key = (content_json or {}).get("image_key", "")
            if not image_key and isinstance(content_raw, str):
                m = re.search(r"img_[a-zA-Z0-9_\-]+", content_raw)
                if m:
                    image_key = m.group(0)
            if image_key:
                out_p = os.path.abspath(os.path.join(DOWNLOADS_DIR, f"img_{image_key}.jpg"))
                if not (os.path.isfile(out_p) and os.path.getsize(out_p) > 0):
                    await loop.run_in_executor(
                        None, lambda: download_message_resource_sdk(message_id, image_key, "image", out_p)
                    )
        elif message_type in ("file", "media"):
            file_key = (content_json or {}).get("file_key", "") or (content_json or {}).get("media_key", "")
            file_name = os.path.basename((content_json or {}).get("file_name", "") or "")
            if file_key:
                if not file_name:
                    file_name = f"video_{file_key}.mp4" if message_type == "media" else f"file_{file_key}"
                if message_type == "media" and not any(
                    file_name.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v"]
                ):
                    file_name = f"{file_name}.mp4"
                out_p = os.path.abspath(os.path.join(DOWNLOADS_DIR, file_name))
                if not (os.path.isfile(out_p) and os.path.getsize(out_p) > 0):
                    await loop.run_in_executor(
                        None, lambda: download_message_resource_sdk(message_id, file_key, "file", out_p)
                    )
    except Exception as e:
        log.debug(f"[Media] Prestage download skipped: {e}")


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
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        output_filename = os.path.join(DOWNLOADS_DIR, f"img_{image_key}.jpg")
        output_path = os.path.abspath(output_filename)
        already_cached = os.path.isfile(output_path) and os.path.getsize(output_path) > 0

        if not already_cached:
            dl_card = CardBuilder.build_download_indicator(os.path.basename(output_filename), "图片")
            bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))
            success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, image_key, "image", output_path))
        else:
            success = True

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
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        total_imgs = len(image_keys)
        indicator_label = "图片内容" if total_imgs == 1 else f"图文中的 {total_imgs} 张图片"
        dl_card = CardBuilder.build_download_indicator(indicator_label)
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, dl_card))

        downloaded_paths = []
        failed_count = 0
        for idx, image_key in enumerate(image_keys):
            if not image_key:
                continue
            suffix = f"_{idx}" if total_imgs > 1 else ""
            output_filename = os.path.join(DOWNLOADS_DIR, f"img_{image_key}{suffix}.jpg")
            output_path = os.path.abspath(output_filename)
            ok = await loop.run_in_executor(
                None,
                lambda ik=image_key, op=output_path: download_message_resource_sdk(message_id, ik, "image", op),
            )
            if ok:
                downloaded_paths.append(output_path)
            else:
                failed_count += 1

        download_success = (failed_count == 0)
        if downloaded_paths:
            if len(downloaded_paths) == 1:
                downloaded_file_name = os.path.basename(downloaded_paths[0])
                user_text += f"\n[附加图片路径: {downloaded_paths[0]}]"
            else:
                downloaded_file_name = f"{len(downloaded_paths)} 张图片"
                lines = [f"[附加图片路径 #{i + 1}: {p}]" for i, p in enumerate(downloaded_paths)]
                user_text += "\n" + "\n".join(lines)
        elif total_imgs > 0:
            downloaded_file_name = f"{total_imgs} 张图片"
        
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
        
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        output_filename = os.path.join(DOWNLOADS_DIR, file_name)
        if message_type == "audio":
            init_card = CardBuilder.build_typing_indicator(is_voice=True)
        else:
            init_card = CardBuilder.build_download_indicator(file_name, message_type)
        bot_reply_msg_id = await loop.run_in_executor(None, lambda: send_interactive_card_sdk(message_id, init_card))

        output_path = os.path.abspath(output_filename)
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            download_success = True
        else:
            download_success = await loop.run_in_executor(None, lambda: download_message_resource_sdk(message_id, file_key, "file", output_path))
        downloaded_file_name = file_name
        
        if message_type == "file":
            user_text = f"请详细阅读这份文件（{file_name}），并做出响应。文件路径: {output_path}"
        elif message_type == "audio":
            from voice_service import transcribe_audio_file
            try:
                recognized_text = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: transcribe_audio_file(output_path)),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                log.warning("[Audio] Voice recognition timed out (10s)")
                recognized_text = ""
            except Exception as e:
                log.error(f"[Audio] Voice recognition exception: {e}")
                recognized_text = ""

            if recognized_text:
                log.info(f"[Audio] Voice transcribed successfully: {recognized_text}")
                user_text = recognized_text
            else:
                log.warning("[Audio] Voice recognition returned empty text")
                user_text = "（收到一条语音消息，但未能清晰识别出具体内容）"
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
    
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    
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
            output_path = os.path.abspath(os.path.join(DOWNLOADS_DIR, file_name))
            pre_cached_img = os.path.abspath(os.path.join(DOWNLOADS_DIR, f"img_{file_key}.jpg")) if m_type == "image" else ""
            if m_type == "image" and os.path.isfile(pre_cached_img) and os.path.getsize(pre_cached_img) > 0:
                output_path = pre_cached_img
                success = True
            elif os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                success = True
            else:
                success = await loop.run_in_executor(
                    None,
                    lambda mid=item["message_id"], fk=file_key, mt=m_type, op=output_path: download_message_resource_sdk(
                        mid, fk, "image" if mt == "image" else "file", op
                    ),
                )
            if success:
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件路径: `{output_path}`")
            else:
                download_success = False
                media_hints.append(f"{idx+1}. 多模态 {m_type.upper()} 文件 `{file_name}` (下载失败)")
                
    user_text = f"请查看以下 {len(items)} 个关联多模态文件并做出综合关联回应：\n\n" + "\n".join(media_hints)
    downloaded_file_name = f"合并批处理 ({len(items)} 个文件)"
    return user_text, downloaded_file_name, download_success, bot_reply_msg_id

