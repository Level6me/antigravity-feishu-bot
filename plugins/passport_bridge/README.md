# 📟 FoloToy AI Passport 硬件随身对讲与看板网关插件 (`passport_bridge`)

基于 **FoloToy AI Passport (ESP32-C3)** 硬件卡片的飞书软硬件全双工协同联动扩展插件。

提供局域网内低延迟全双工 WebSocket 通信、UDP 服务免配置自动发现、本地 Whisper 语音转文字、edge-tts 语音合成推流、240×320 彩屏多状态看板同步与飞书端卡片双向镜像。

---

## 🌟 核心特性

1. **即按即说（PTT 对讲）**：
   - 长按硬件 **OK 键** 实时录音，16kHz PCM 音频流秒级上传；
   - 本地 Whisper 极速转写为文字，唤醒 Antigravity Agent 执行并回复；
   - 硬件扬声器实时播放语音答复，飞书会话同步推送完整富文本卡片，支持长文追溯与后续文字跟进。
2. **多状态拟态看板**：
   - 平时作为桌面/随身电子时钟与工作状态看板，展示网络状态、当前时间、激活项目空间与 Agent 运行负载；
   - 当 Agent 在后台执行工具或大模型思考时，硬件屏幕与指示灯实时同步动画。
3. **物理安全熔断键**：
   - 长按 **Down 键** 发送 Physical Stop 紧急熔断指令，一键终止服务端所有正在运行的后台 Agent 任务。
4. **即插即用 UDP 自发现**：
   - 设备开机连入同一 Wi-Fi 后，自动广播握手寻找局域网内的飞书机器人主机（默认端口 `8765`），无需在硬件端硬编码服务器 IP。

---

## 🕹️ 飞书卡片式交互控制台

发送 `/passport` 或 `/hardware`，即可呼出**全交互式卡片控制中心**，无需输入复杂参数，所有功能一键点击即时响应（In-place Patch 原地刷新）：

| 交互功能按钮 | 按钮类型 | 交互行为说明 |
| :--- | :--- | :--- |
| **📌 绑定 / 🔓 解绑本会话** | 主按钮 / 危险 | 一键将当前飞书私聊或群聊设置为 AI Passport 语音消息接收的主通道 |
| **🔄 刷新状态** | 默认按钮 | 实时拉取已连接的硬件设备数、网络延时与运行指标 |
| **📡 同步看板** | 默认按钮 | 强制向局域网内所有硬件下发最新的工作项目与系统健康心跳数据 |
| **🔔 提示 / ⚠️ 警告 / 🚨 紧急** | 操作按钮 | 向在线硬件屏幕推送不同等级的即时弹窗告警与蜂鸣提示 |
| **🔊 轮换音色** | 默认按钮 | 一键循环切换 TTS 语音播报音色（晓晓 / 云希 / 云健 / 晓伊） |
| **🛑 紧急熔断** | 危险按钮 | 发送 Physical Stop 信号，一键中止服务端所有后台运行的 Agent 任务 |
| **📖 固件与刷机指引** | 导航按钮 | 原地切换至硬件引脚分布、树莓派一键烧录与 Wi-Fi 配网手册卡片 |


---

## 🛠️ 硬件固件与树莓派刷机指南

本插件内置了完整的 ESP32-C3 PlatformIO 嵌入式源码工程，位于 `firmware/` 目录中：

```text
plugins/passport_bridge/
├── manifest.json
├── config.json
├── plugin.py
├── ws_server.py
├── requirements.txt
└── firmware/                     # ESP32-C3 固件工程
    ├── platformio.ini
    ├── flash_firmware.sh         # 树莓派/Linux 一键编译与烧录工具
    ├── include/                  # 引脚定义与外设头文件 (ST7789, ES8311, I2S)
    └── src/                      # 固件主控与 UI 渲染源码
```

### 1. 配置 Wi-Fi
打开 `firmware/include/config.h`，填入你的局域网 Wi-Fi 名称与密码：
```c
#define DEFAULT_WIFI_SSID       "你的_WiFi_名称"
#define DEFAULT_WIFI_PASSWORD   "你的_WiFi_密码"
```

### 2. 树莓派一键烧录
用 Type-C 数据线将 AI Passport 插入树莓派 USB 端口，在终端直接运行：
```bash
cd firmware
./flash_firmware.sh
```
脚本将自动安装 PlatformIO 工具链、识别串口并高速烧录固件。
