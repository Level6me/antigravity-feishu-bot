#!/usr/bin/env bash
# ==============================================================================
# FoloToy AI Passport (ESP32-C3) 树莓派/Linux 一键编译、烧录与调试脚本
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}================================================================${NC}"
echo -e "${GREEN}  📟 FoloToy AI Passport × Antigravity 固件一键编译烧录工具      ${NC}"
echo -e "${CYAN}================================================================${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. 检查 PlatformIO 编译环境
if ! command -v pio >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] 未检测到 PlatformIO 命令行工具，正在自动安装...${NC}"
    pip3 install -U platformio esptool || pip install -U platformio esptool
    export PATH="$HOME/.local/bin:$PATH"
fi

echo -e "${BLUE}[1/4] 检查目标硬件连接状态...${NC}"
# 查找常见 USB 串口设备节点
PORTS=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)

if [ -z "$PORTS" ]; then
    echo -e "${RED}[ERROR] 未检测到连接的 ESP32-C3 设备！${NC}"
    echo -e "请检查："
    echo -e " 1. 是否已用 Type-C 数据线将 AI Passport 插入树莓派 USB 口？"
    echo -e " 2. 数据线是否具备数据传输能力（部分纯充电线无法识别）？"
    exit 1
fi

TARGET_PORT=$(echo "$PORTS" | head -n 1)
echo -e "${GREEN}[✓] 成功检测到硬件端口: ${CYAN}${TARGET_PORT}${NC}"

# 串口权限检查与自动修复
if [ ! -r "$TARGET_PORT" ] || [ ! -w "$TARGET_PORT" ]; then
    echo -e "${YELLOW}[!] 当前用户对 ${TARGET_PORT} 权限受限，尝试授权...${NC}"
    sudo chmod 666 "$TARGET_PORT" 2>/dev/null || true
fi

# 2. 编译工程源码
echo -e "\n${BLUE}[2/4] 正在编译固件工程源码...${NC}"
pio run

# 3. 烧录固件到设备
echo -e "\n${BLUE}[3/4] 正在向 AI Passport 烧录固件 (波特率 921600)...${NC}"
pio run --target upload --upload-port "$TARGET_PORT"

echo -e "\n${CYAN}================================================================${NC}"
echo -e "${GREEN}🎉 固件烧录成功！AI Passport 正在自动重启中...${NC}"
echo -e "${CYAN}================================================================${NC}"

# 4. 可选：打开串口日志监视
echo -e "\n${BLUE}[4/4] 启动设备串口实时监视器 (按 Ctrl+C 可退出)...${NC}"
sleep 1
pio device monitor -b 115200 --port "$TARGET_PORT"
