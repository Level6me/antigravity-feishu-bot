# FoloToy AI Passport (ESP32-C3) 嵌入式固件工程

本工程为 **FoloToy AI Passport** 专用定制开源固件，用于与局域网内的 **Antigravity 飞书机器人 (`antigravity-feishu-bot`)** 实现软硬全双工协同联动。

---

## 硬件外设与引脚分配

| 外设模块 | 核心器件 | ESP32-C3 引脚 | 说明 |
| :--- | :--- | :--- | :--- |
| **显示屏幕** | ST7789 240×320 LCD | MOSI: `GPIO 7`, SCLK: `GPIO 6`, CS: `GPIO 10`, DC: `GPIO 2`, RST: `GPIO 3`, BL: `GPIO 1` | 硬件 SPI 40MHz 高速驱动 |
| **音频编解码** | ES8311 I2C 总线 | SDA: `GPIO 8`, SCL: `GPIO 0` (I2C 地址: `0x18`) | 负责模拟增益与音量调节 |
| **数字音频流** | I2S 总线 (16kHz 16bit) | BCLK: `GPIO 18`, WS: `GPIO 19`, DOUT: `GPIO 21`, DIN: `GPIO 10` | 麦克风采集与扬声器放音 |
| **物理按键** | 3 颗实体轻触按键 | 上键: `GPIO 4`, 下键: `GPIO 5`, 中键(OK): `GPIO 9` | 内部弱上拉，按下为低电平 |

---

## 快速上手与树莓派刷机

### 1. 配置 Wi-Fi
打开 `include/config.h`，修改你的 Wi-Fi 名称和密码：
```c
#define DEFAULT_WIFI_SSID       "你的_WiFi_名称"
#define DEFAULT_WIFI_PASSWORD   "你的_WiFi_密码"
```

### 2. 树莓派一键刷机
将 AI Passport 用 Type-C 数据线插入树莓派 USB 端口，在终端直接运行：
```bash
./flash_firmware.sh
```
脚本将自动检测端口、下载依赖、编译源码并完成固件烧录。

---

## 核心操作与交互说明

* **常态看板**：开机自动通过 UDP 8765 端口发现飞书机器人主机，并建立 WebSocket 长连接。屏幕实时显示时间、网络状态、当前活跃工程空间；
* **按住对讲 (PTT)**：长按 **OK 键** 进入录音模式，麦克风将音频流以 16kHz PCM 实时推向服务端；松开按键后自动转为思考中并由扬声器播报，手机飞书同步推送完整卡片；
* **看板与项目切换**：短按 **Up 键** 刷新看板，长按 **Up 键** 提示当前活跃工程；
* **紧急熔断**：长按 **Down 键** 触发 Physical Stop 物理安全熔断，立即终止服务端所有正在运行的后台 Agent 进程。
