#!/usr/bin/env bash
# ==============================================================================
# FoloToy AI Passport (ESP32-C3) 树莓派/Linux 一键编译、0x0 合并固件生成与烧录工具
# 严格遵循 FoloToy 官方 Agent 开发与交付规范
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}================================================================${NC}"
echo -e "${GREEN}  📟 FoloToy AI Passport 官方规范固件编译与交付工具              ${NC}"
echo -e "${CYAN}================================================================${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. 检查 PlatformIO 与 esptool 编译工具链
if ! command -v pio >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] 未检测到 PlatformIO 命令行工具，正在自动准备环境...${NC}"
    pip3 install -U platformio esptool || pip install -U platformio esptool
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. 运行工程编译
echo -e "\n${BLUE}[1/3] 正在编译工程源码并生成目标二进制...${NC}"
pio run

BUILD_DIR=".pio/build/esp32-c3"
if [ ! -f "$BUILD_DIR/firmware.bin" ]; then
    echo -e "${RED}[ERROR] 编译产物不存在，构建失败！${NC}"
    exit 1
fi

# 3. 按照官方规范生成可从 0x0 刷写的合并固件 (供官方 Web Flasher / esptool 使用)
echo -e "\n${BLUE}[2/3] 正在合成 0x0 完整可烧录合并镜像 (Merged Image)...${NC}"
MERGED_BIN="merged_firmware_0x0.bin"

if command -v esptool.py >/dev/null 2>&1; then
    esptool.py --chip esp32c3 merge_bin -o "$MERGED_BIN" \
        --flash_mode dio --flash_freq 80m --flash_size 8MB \
        0x0 "$BUILD_DIR/bootloader.bin" \
        0x8000 "$BUILD_DIR/partitions.bin" \
        0x10000 "$BUILD_DIR/firmware.bin" 2>/dev/null || \
    python3 -m esptool --chip esp32c3 merge_bin -o "$MERGED_BIN" \
        --flash_mode dio --flash_freq 80m --flash_size 8MB \
        0x0 "$BUILD_DIR/bootloader.bin" \
        0x8000 "$BUILD_DIR/partitions.bin" \
        0x10000 "$BUILD_DIR/firmware.bin"
else
    python3 -m esptool --chip esp32c3 merge_bin -o "$MERGED_BIN" \
        --flash_mode dio --flash_freq 80m --flash_size 8MB \
        0x0 "$BUILD_DIR/bootloader.bin" \
        0x8000 "$BUILD_DIR/partitions.bin" \
        0x10000 "$BUILD_DIR/firmware.bin"
fi

echo -e "${GREEN}[✓] 官方规范合并固件已就绪: ${CYAN}${SCRIPT_DIR}/${MERGED_BIN}${NC}"
echo -e "   > 支持直接拖拽至官方 Web 刷机工具 (https://ai-passport.folotoy.cn/tools/web-flasher/) 安装！"

# 4. 检测硬件连接并主动确认是否刷机 (严格遵循规范：未经确认不盲目烧录)
echo -e "\n${BLUE}[3/3] 检查连接的真实硬件设备...${NC}"
PORTS=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)

if [ -z "$PORTS" ]; then
    echo -e "${YELLOW}[!] 当前未检测到连接的 AI Passport 设备。${NC}"
    echo -e "请使用具备数据传输功能的 Type-C 线将 AI Passport 插入电脑/树莓派。"
    echo -e "你可以将生成的 ${CYAN}${MERGED_BIN}${NC} 上传至官方 Web 刷机工具完成安装。"
    exit 0
fi

TARGET_PORT=$(echo "$PORTS" | head -n 1)
echo -e "${GREEN}[✓] 检测到硬件设备串口: ${CYAN}${TARGET_PORT}${NC}"

read -p "是否立即向连接的设备 [${TARGET_PORT}] 刷写固件？(y/N): " CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}开始向 ${TARGET_PORT} 烧录固件...${NC}"
    esptool.py --chip esp32c3 --port "$TARGET_PORT" --baud 921600 write_flash 0x0 "$MERGED_BIN"
    echo -e "${GREEN}🎉 烧录完成！AI Passport 正在自动重启。${NC}"
    echo -e "正在启动串口监视器 (按 Ctrl+C 退出)..."
    sleep 1
    pio device monitor -b 115200 --port "$TARGET_PORT"
else
    echo -e "${YELLOW}[!] 已跳过烧录。固件文件保存在: ${MERGED_BIN}${NC}"
fi
