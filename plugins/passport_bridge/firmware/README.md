# FoloToy AI Passport (ESP32-C3) 嵌入式固件工程

本工程为 **FoloToy AI Passport** 专用定制固件，严格对齐官方开发规范与硬件抽象层事实来源（[`components/bsp/include/bsp_pins.h`](https://github.com/FoloToy/ai-passport/blob/main/components/bsp/include/bsp_pins.h)），用于与局域网内的 **Antigravity 飞书机器人 (`antigravity-feishu-bot`)** 实现软硬件全双工协同联动。

---

## 硬件外设与引脚分配（对齐官方 BSP 单一事实来源）

| 外设模块 | 核心芯片 / 方案 | ESP32-C3 引脚 | 电气特性与工作规范 |
| :--- | :--- | :--- | :--- |
| **彩色显示屏** | ST7789P3 (240×320) | MOSI:`9`, SCLK:`8`, CS:`1`, DC:`20`, RST:`-1`, BL:`21` | SPI2 40MHz 高速驱动，RST 硬接 3.3V 走 SWRESET 软复位，强制反色 (INVON) |
| **实体交互按键** | 3 键共用单个 ADC | **GPIO 0** (ADC1_CH0) | 板载 10k 外部上拉，电阻分压识别：<br>• **上键** (0Ω): `0 ~ 150 mV`<br>• **下键** (1kΩ): `150 ~ 447 mV`<br>• **确定键** (2.2kΩ): `447 ~ 1900 mV`<br>• **松开态**: `3300 mV` |
| **I2C 控制总线** | ES8311 + CW2017 | SDA:`10`, SCL:`7` | I2C0 总线共享，ES8311 (`0x18`) 负责音频，CW2017 (`0x63`) 负责高精度电池计量 |
| **数字音频总线** | ES8311 (I2S 全双工) | MCLK:`6`, BCLK:`5`, WS:`3`, DOUT:`2`, DIN:`4` | 16kHz 16bit 单声道标准 I2S，MCU DOUT 播放，DIN 麦克风录音 |
| **背光调光控制** | LEDC PWM 智能调光 | **GPIO 21** | 硬件 PWM 平滑渐变调光，支持超时节能息屏 |

---

## 固件核心功能与高级特性

1. **语音随时打断机制 (Barge-in)**：
   - 当 AI 正在播报长篇答复时，按下任意键（短按 OK 或 Down）即可发送中断信号，服务端立即终止 TTS 推流，扬声器瞬间静音，支持像真人一样随时插话。

2. **SoftAP + NVS 网页热点配网 (Captive Portal)**：
   - 首次开机无 Wi-Fi 或长按 **上键 (Up)** 时，自动启动热点 `FoloToy-Passport-AP`（密码 `12345678`）；
   - 手机连入后打开 `192.168.4.1` 即可图形化扫描周围 Wi-Fi 并输入密码保存，配置永久保存在 ESP32 NVS 闪存中，支持断电记忆与出差随身漫游。

3. **物理 2FA 高危二次鉴权 (Physical 2FA)**：
   - 当飞书机器人收到高危指令（如发布生产、删除环境等）时，硬件屏幕弹出红色警报卡片并鸣响蜂鸣器：
     - 短按 **上键 (Up)**：物理批准放行；
     - 短按 **下键 (Down)**：物理拒绝拦截。

4. **随身独立番茄钟 (Pomodoro Focus)**：
   - 仪表盘第 4 页提供 25 分钟专注倒计时与环形进度表盘：
     - 短按 **确定键 (OK)**：启动 / 暂停倒计时；
     - 长按 **下键 (Down)**：重置番茄钟；
     - 计时结束时播放三和弦庆祝提示音并弹窗提醒休息。

5. **CW2017 电量计与动态状态栏**：
   - 实时采集端电压（mV）、电量百分比（SOC）与充电（CHG）标志，结合 Wi-Fi 4 阶梯信号格呈现在顶部常驻状态栏；
   - 支持 5% 临界低电警报音与弹窗保护。

6. **灵动拟人化表情与操作音效**：
   - 涵盖待机眨眼（Blink）、专注聆听、8 频段跳动频谱律动（Equalizer）、双环粒子思考、嘴巴音频开合说话等丰富表情；
   - 配备 PTT 启动升调、结束降调、连线和弦与警报双重蜂鸣音。

7. **OTA 空中升级与 GitHub Actions CI/CD**：
   - 支持通过局域网下发 `ota_update` 远程无线拉取固件刷写，屏幕显示下载百分比；
   - 代码库已配置 `.github/workflows/firmware_ci.yml`，每次推送到 GitHub 远程自动云端编译并输出 `0x0` 镜像。

---

## 快速上手与刷机指南

### 1. 编译与 0x0 合并固件生成
将 AI Passport 用 Type-C 数据线插入电脑或开发机，运行：
```bash
bash flash_firmware.sh
```
脚本将全自动完成：
1. 编译 PlatformIO 工程源码；
2. 按照官方规范自动合成为从 `0x0` 刷写的单一镜像：`merged_firmware_0x0.bin`；
3. 检测串口并在获得确认后完成高速烧录。

> 💡 **官方 Web 刷机工具支持**：生成的 `merged_firmware_0x0.bin` 支持直接打开 [FoloToy 官方 Web Flasher](https://ai-passport.folotoy.cn/tools/web-flasher/)，在浏览器中免驱动一键安装。
