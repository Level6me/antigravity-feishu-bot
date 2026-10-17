#!/usr/bin/env bash
# ==============================================================================
# Passport Bridge 插件生命周期构建与环境初始化脚本 (setup.sh)
# 供 feishu-bot-plugin 面板一键安装 / 更新钩子及独立初始化调用
# ==============================================================================

set -e

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PLUGIN_DIR"

echo "[PassportBridge:Setup] 正在执行插件初始化配置..."

# 1. 寻找 Python 环境 (优先探测宿主虚拟环境，其次当前虚拟环境，最后系统 Python)
HOST_VENV="../../venv/bin/python3"
LOCAL_VENV="./venv/bin/python3"

if [ -f "$HOST_VENV" ]; then
    PYTHON_BIN="$HOST_VENV"
    echo "[PassportBridge:Setup] 使用飞书机器人宿主虚拟环境: $PYTHON_BIN"
elif [ -f "$LOCAL_VENV" ]; then
    PYTHON_BIN="$LOCAL_VENV"
    echo "[PassportBridge:Setup] 使用本地虚拟环境: $PYTHON_BIN"
else
    PYTHON_BIN="$(command -v python3 || echo "python3")"
    echo "[PassportBridge:Setup] 使用系统 Python: $PYTHON_BIN"
fi

# 2. 检查系统级音视频编解码依赖
MISSING_SYS=""
if ! command -v ffmpeg >/dev/null 2>&1; then
    MISSING_SYS="$MISSING_SYS ffmpeg"
fi
if [ ! -f "/usr/lib/aarch64-linux-gnu/libopus.so.0" ] && \
   [ ! -f "/usr/lib/x86_64-linux-gnu/libopus.so.0" ] && \
   [ ! -f "/usr/lib/libopus.so.0" ] && \
   ! ldconfig -p 2>/dev/null | grep -q "libopus\.so"; then
    MISSING_SYS="$MISSING_SYS libopus0 libopus-dev"
fi

if [ -n "$MISSING_SYS" ]; then
    echo "[PassportBridge:Setup] 检测到缺失系统底层库:$MISSING_SYS，尝试自动安装..."
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$EUID" -eq 0 ]; then
            apt-get update -y && apt-get install -y $MISSING_SYS || true
        elif command -v sudo >/dev/null 2>&1; then
            sudo apt-get update -y && sudo apt-get install -y $MISSING_SYS || true
        fi
    fi
fi

# 3. 安装/更新 Python 依赖
if [ -f "requirements.txt" ]; then
    echo "[PassportBridge:Setup] 正在安装 Python 依赖项 (requirements.txt)..."
    "$PYTHON_BIN" -m pip install -r requirements.txt -q || {
        echo "[PassportBridge:Setup] 批量安装出现警告，尝试逐项容错安装..."
        "$PYTHON_BIN" -m pip install aiohttp edge-tts SpeechRecognition lark-oapi sherpa-onnx numpy requests -q || true
    }
fi

# 4. 初始化数据与固件存储目录
mkdir -p data/models/sense-voice
mkdir -p data/devices
mkdir -p plugin_data
mkdir -p firmware

# 5. 检查 SenseVoice-Small 离线模型
MODEL_DIR="data/models/sense-voice"
if [ ! -f "$MODEL_DIR/model.int8.onnx" ]; then
    echo "[PassportBridge:Setup] 未找到 SenseVoice 离线模型，尝试拉取模型包..."
    TAR_NAME="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
    DOWNLOAD_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$TAR_NAME"
    if curl -L --retry 2 --connect-timeout 8 -o "$TAR_NAME" "$DOWNLOAD_URL" 2>/dev/null; then
        tar -xjf "$TAR_NAME" 2>/dev/null || true
        SRC_DIR="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
        if [ -d "$SRC_DIR" ]; then
            cp -f "$SRC_DIR/model.int8.onnx" "$MODEL_DIR/" 2>/dev/null || true
            cp -f "$SRC_DIR/tokens.txt" "$MODEL_DIR/" 2>/dev/null || true
            rm -rf "$SRC_DIR" "$TAR_NAME"
            echo "[PassportBridge:Setup] SenseVoice 离线模型加载就绪！"
        fi
    fi
fi

# 6. 初始化配置文件 (若不存在)
if [ ! -f "config.json" ] && [ -f "config.example.json" ]; then
    cp config.example.json config.json
    echo "[PassportBridge:Setup] 已从模板初始化 config.json"
fi

echo "[PassportBridge:Setup] 插件初始化构建完成！已就绪。"
exit 0
