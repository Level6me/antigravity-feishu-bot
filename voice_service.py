"""Voice synthesis and native Feishu voice message reply service."""
import os
import re
import uuid
import tempfile
import asyncio
import subprocess
from typing import Optional, Tuple

import edge_tts
from config import TTS_VOICE, ENABLE_VOICE_REPLY
from logger import log
from lark_client import upload_file_sdk, reply_voice_sdk, send_voice_to_chat_sdk


def clean_text_for_speech(text: str, max_chars: int = 180) -> str:
    """Prepares markdown/raw LLM text for natural Chinese TTS speech."""
    if not text:
        return ""

    s = text

    # Remove think blocks / system blocks if any
    s = re.sub(r'<think>.*?</think>', '', s, flags=re.DOTALL)

    # Replace fenced code blocks with spoken placeholder
    s = re.sub(r'```[\w]*\n[\s\S]*?```', '，相关代码内容已在消息卡片中列出。', s)

    # Remove inline code `...`
    s = re.sub(r'`([^`]+)`', r'\1', s)

    # Remove markdown images ![alt](url) and links [text](url) -> text
    s = re.sub(r'!\[.*?\]\(.*?\)', '', s)
    s = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', s)

    # Remove URLs
    s = re.sub(r'https?://\S+', '', s)

    # Remove headers (#, ##), bold (**), italic (*), strike (~~), blockquotes (>)
    s = re.sub(r'^[ \t]*#{1,6}\s*', '', s, flags=re.MULTILINE)
    s = re.sub(r'\*\*(.*?)\*\*', r'\1', s)
    s = re.sub(r'\*(.*?)\*', r'\1', s)
    s = re.sub(r'~~(.*?)~~', r'\1', s)
    s = re.sub(r'^[ \t]*>\s*', '', s, flags=re.MULTILINE)

    # Remove bullet markers (- , * , 1. )
    s = re.sub(r'^[ \t]*[-*+]\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'^[ \t]*\d+\.\s+', '', s, flags=re.MULTILINE)

    # Remove emojis and common non-speech symbols
    emoji_pattern = re.compile(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\U00002600-\U000027BF]+'
    )
    s = emoji_pattern.sub('', s)


    # Normalize whitespace
    s = re.sub(r'\n+', '，', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'[，,]{2,}', '，', s)
    s = s.strip('，, ')

    if not s:
        return ""

    # Truncate if speech is overly long
    if len(s) > max_chars:
        # Try cutting at nearest punctuation mark
        cut_idx = max_chars
        for p in ['。', '！', '？', '；', '，', ' ']:
            idx = s.rfind(p, int(max_chars * 0.7), max_chars)
            if idx != -1:
                cut_idx = idx + 1
                break
        s = s[:cut_idx].rstrip('，, ') + "。更多详细内容请在卡片中查看。"

    return s


async def text_to_opus(text: str, voice: Optional[str] = None) -> Tuple[Optional[str], int]:
    """Generates an OPUS voice file using edge-tts and ffmpeg.
    Returns (opus_file_path, duration_ms).
    """
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return None, 0

    voice = voice or TTS_VOICE or "zh-CN-XiaoxiaoNeural"
    temp_dir = tempfile.gettempdir()
    file_id = uuid.uuid4().hex[:10]
    mp3_path = os.path.join(temp_dir, f"tts_{file_id}.mp3")
    opus_path = os.path.join(temp_dir, f"tts_{file_id}.opus")

    try:
        # Step 1: Synthesize to MP3 via edge-tts
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(mp3_path)

        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            log.error("[VoiceService] edge-tts output file is empty")
            return None, 0

        # Step 2: Convert to OPUS using ffmpeg (Lark IM audio format requirement)
        loop = asyncio.get_running_loop()
        cmd_convert = [
            "ffmpeg", "-y", "-i", mp3_path,
            "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
            opus_path
        ]
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd_convert, capture_output=True, text=True)
        )
        if proc.returncode != 0 or not os.path.exists(opus_path):
            log.error(f"[VoiceService] ffmpeg conversion failed: {proc.stderr}")
            return None, 0

        # Step 3: Extract duration directly from ffmpeg output (saves ~400ms vs separate ffprobe)
        duration_ms = 0
        matches = re.findall(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', proc.stderr or "")
        if matches:
            h, m, s = matches[-1]
            sec = float(h) * 3600.0 + float(m) * 60.0 + float(s)
            duration_ms = max(1000, int(sec * 1000))

        if duration_ms <= 0:
            cmd_probe = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", opus_path
            ]
            probe_res = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd_probe, capture_output=True, text=True)
            )
            duration_sec = 0.0
            try:
                duration_sec = float(probe_res.stdout.strip())
            except Exception:
                pass
            duration_ms = max(1000, int(duration_sec * 1000))

        return opus_path, duration_ms

    except Exception as e:
        log.error(f"[VoiceService] text_to_opus failed: {e}")
        if os.path.exists(opus_path):
            try:
                os.remove(opus_path)
            except Exception:
                pass
        return None, 0
    finally:
        if os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except Exception:
                pass


async def send_native_voice_reply(message_id: str, chat_id: str, text: str, api_client=None) -> bool:
    """Generates voice for `text` and delivers it to Feishu as a native audio voice message."""
    if not ENABLE_VOICE_REPLY:
        log.info("[VoiceService] Native voice reply is disabled in config")
        return False

    opus_path, duration_ms = await text_to_opus(text)
    if not opus_path or duration_ms <= 0:
        return False

    loop = asyncio.get_running_loop()
    try:
        # Step 1: Upload OPUS file to Lark via Lark OpenAPI
        file_key = await loop.run_in_executor(
            None,
            lambda: upload_file_sdk(opus_path, file_type="opus", duration_ms=duration_ms)
        )
        if not file_key:
            log.error("[VoiceService] Failed to upload opus file to Feishu")
            return False

        log.info(f"[VoiceService] Voice uploaded successfully (key={file_key}, duration={duration_ms}ms)")

        # Step 2: Send native voice reply (msg_type='audio')
        ok = False
        if message_id and str(message_id).startswith("om_"):
            ok = await loop.run_in_executor(
                None,
                lambda: reply_voice_sdk(message_id, file_key)
            )

        if not ok and chat_id:
            ok = await loop.run_in_executor(
                None,
                lambda: send_voice_to_chat_sdk(chat_id, file_key)
            )

        if ok:
            log.info(f"[VoiceService] Native voice message sent to Feishu (chat={chat_id}, reply_to={message_id})")
        return ok

    except Exception as e:
        log.error(f"[VoiceService] send_native_voice_reply error: {e}")
        return False
    finally:
        if os.path.exists(opus_path):
            try:
                os.remove(opus_path)
            except Exception:
                pass


def transcribe_audio_file(audio_path: str) -> str:
    """Transcribes an audio file (ogg, opus, mp3, wav) into Chinese text using speech_recognition."""
    if not audio_path or not os.path.exists(audio_path):
        return ""

    temp_dir = tempfile.gettempdir()
    file_id = uuid.uuid4().hex[:10]
    wav_path = os.path.join(temp_dir, f"asr_{file_id}.wav")

    try:
        # Convert to 16kHz mono WAV
        res = subprocess.run(
            ['ffmpeg', '-y', '-i', audio_path, '-ar', '16000', '-ac', '1', wav_path],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode != 0 or not os.path.exists(wav_path):
            log.error(f"[VoiceService] ASR ffmpeg conversion failed: {res.stderr}")
            return ""

        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = r.record(source)

        text = r.recognize_google(audio_data, language='zh-CN')
        return text.strip() if text else ""
    except Exception as e:
        log.error(f"[VoiceService] ASR transcription error: {e}")
        return ""
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
