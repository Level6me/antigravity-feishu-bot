#pragma once
#include <Arduino.h>

// ==============================================================================
// 1. Wi-Fi 网络配置
// ==============================================================================
#define DEFAULT_WIFI_SSID       "Your_WiFi_SSID"
#define DEFAULT_WIFI_PASSWORD   "Your_WiFi_Password"

#define DEFAULT_SERVER_PORT     8765
#define UDP_DISCOVERY_PORT      8765

// ==============================================================================
// 2. FoloToy AI Passport 官方硬件引脚与参数 (严格对齐 components/bsp/include/bsp_pins.h)
// ==============================================================================

// 2.1 彩色显示屏: ST7789P3 240x320, 4-line SPI (无 PSRAM)
#define BSP_LCD_W               240
#define BSP_LCD_H               320
#define BSP_LCD_MOSI            9
#define BSP_LCD_SCLK            8
#define BSP_LCD_CS              1
#define BSP_LCD_DC              20
#define BSP_LCD_RST             (-1)   // 复位脚未接 MCU (硬接 3.3V)，通过 SWRESET 软复位
#define BSP_LCD_BL              21     // 背光引脚 (LEDC PWM 调光)
#define BSP_LCD_PCLK_HZ         (40 * 1000 * 1000)
#define BSP_LCD_INVERT_COLOR    1      // 屏幕反色 (0x21 INVON)

// 2.2 实体按键: 三键共用单个 ADC 引脚 (GPIO0 / ADC1_CH0)，靠电阻分压区分
// 电路: 3.3V ── 外部上拉 10k ──┬── ADC 节点(GPIO0)
//                              └── 按键 ── 分压电阻 ── GND
//   上键   : 0Ω    → 0 mV    (有效窗口: 0 ~ 150 mV)
//   下键   : 1kΩ   → 300 mV  (有效窗口: 150 ~ 447 mV)
//   确定键 : 2.2kΩ → 595 mV  (有效窗口: 447 ~ 1900 mV)
//   松开态 : 无通路 → 3300 mV
#define BSP_BTN_ADC_PIN         0      // GPIO0
#define BSP_BTN_UP_MIN_MV       0
#define BSP_BTN_UP_MAX_MV       150
#define BSP_BTN_DOWN_MIN_MV     150
#define BSP_BTN_DOWN_MAX_MV     447
#define BSP_BTN_OK_MIN_MV       447
#define BSP_BTN_OK_MAX_MV       1900

// 2.3 I2C 总线: ES8311 (音频 Codec) 与 CW2017 (电量计) 共用 I2C0
#define BSP_I2C_SDA             10
#define BSP_I2C_SCL             7
#define BSP_I2C_ES8311_ADDR     0x18
#define BSP_I2C_CW2017_ADDR     0x63

// 2.4 I2S 数字音频: ES8311 全双工 (共用 MCLK/BCLK/WS)
#define BSP_I2S_MCLK            6
#define BSP_I2S_BCLK            5
#define BSP_I2S_WS              3
#define BSP_I2S_DOUT            2      // 播放: MCU → Codec
#define BSP_I2S_DIN             4      // 录音: Codec → MCU
#define BSP_I2S_PA_CTRL         (-1)

// 音频采样规格
#define AUDIO_SAMPLE_RATE       16000
#define AUDIO_SAMPLE_BITS       16
#define AUDIO_CHANNELS          1
#define AUDIO_DMA_BUF_LEN       512
#define AUDIO_DMA_BUF_COUNT     4

// 按键防抖与长按判定阈值 (毫秒)
#define DEBOUNCE_MS             50
#define LONG_PRESS_MS           600
