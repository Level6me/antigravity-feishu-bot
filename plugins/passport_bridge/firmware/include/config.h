#pragma once
#include <Arduino.h>

// ==============================================================================
// 1. Wi-Fi 网络配置 (支持在此预设，也可通过开机热点动态配网)
// ==============================================================================
#define DEFAULT_WIFI_SSID       "Your_WiFi_SSID"
#define DEFAULT_WIFI_PASSWORD   "Your_WiFi_Password"

// 局域网服务发现与端口
#define DEFAULT_SERVER_PORT     8765
#define UDP_DISCOVERY_PORT      8765

// ==============================================================================
// 2. FoloToy AI Passport (ESP32-C3) 硬件引脚映射表
// ==============================================================================
// 2.1 彩色显示屏 (ST7789 240x320 SPI)
#define PIN_LCD_MOSI            7
#define PIN_LCD_SCLK            6
#define PIN_LCD_CS              10
#define PIN_LCD_DC              2
#define PIN_LCD_RST             3
#define PIN_LCD_BL              1

// 2.2 实体控制按键 (默认内部上拉，按下为低电平 LOW)
#define PIN_BTN_UP              4    // 上翻 / 项目切换
#define PIN_BTN_DOWN            5    // 下翻 / 巡检与紧急停止
#define PIN_BTN_OK              9    // 确认 / 语音长按对讲 (ESP32-C3 Boot 键)

// 2.3 ES8311 音频编解码芯片 (I2C 控制总线)
#define PIN_I2C_SDA             8
#define PIN_I2C_SCL             0
#define ES8311_I2C_ADDR         0x18

// 2.4 I2S 数字音频流接口 (麦克风录音 + 扬声器播放)
#define PIN_I2S_BCLK            18
#define PIN_I2S_WS              19
#define PIN_I2S_DOUT            21   // DAC 扬声器数据输出
#define PIN_I2S_DIN             10   // MIC 麦克风录音输入

// 音频采样规格
#define AUDIO_SAMPLE_RATE       16000
#define AUDIO_SAMPLE_BITS       16
#define AUDIO_CHANNELS          1
#define AUDIO_DMA_BUF_LEN       512
#define AUDIO_DMA_BUF_COUNT     4

// 按键防抖与长按判定阈值 (毫秒)
#define DEBOUNCE_MS             50
#define LONG_PRESS_MS           600
