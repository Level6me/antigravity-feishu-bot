#pragma once
#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include "config.h"
#include "battery_gauge.h"

enum UiState {
    UI_STATE_BOOT,
    UI_STATE_CONNECTING,
    UI_STATE_DASHBOARD,
    UI_STATE_LISTENING,
    UI_STATE_THINKING,
    UI_STATE_SPEAKING,
    UI_STATE_ALERT,
    UI_STATE_CONFIRM_2FA,
    UI_STATE_OTA
};

class LGFX_ST7789 : public lgfx::LGFX_Device {
    lgfx::Panel_ST7789      _panel_instance;
    lgfx::Bus_SPI           _bus_instance;
    lgfx::Light_PWM         _light_instance;

public:
    LGFX_ST7789() {
        {
            auto cfg = _bus_instance.config();
            cfg.spi_host = SPI2_HOST;
            cfg.spi_mode = 0;
            cfg.freq_write = 40000000;
            cfg.freq_read  = 16000000;
            cfg.spi_3wire = true;
            cfg.use_lock = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk = BSP_LCD_SCLK;
            cfg.pin_mosi = BSP_LCD_MOSI;
            cfg.pin_miso = -1;
            cfg.pin_dc   = BSP_LCD_DC;
            _bus_instance.config(cfg);
            _panel_instance.setBus(&_bus_instance);
        }
        {
            auto cfg = _panel_instance.config();
            cfg.pin_cs           = BSP_LCD_CS;
            cfg.pin_rst          = BSP_LCD_RST;
            cfg.pin_busy         = -1;
            cfg.panel_width      = BSP_LCD_W;
            cfg.panel_height     = BSP_LCD_H;
            cfg.offset_x         = 0;
            cfg.offset_y         = 0;
            cfg.offset_rotation  = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits  = 1;
            cfg.readable         = false;
            cfg.invert           = (BSP_LCD_INVERT_COLOR == 1);
            cfg.rgb_order        = false;
            cfg.dlen_16bit       = false;
            cfg.bus_shared       = false;
            _panel_instance.config(cfg);
        }
        {
            auto cfg = _light_instance.config();
            cfg.pin_bl = BSP_LCD_BL;
            cfg.invert = false;
            cfg.freq   = 44100;
            cfg.pwm_channel = 7;
            _light_instance.config(cfg);
            _panel_instance.setLight(&_light_instance);
        }
        setPanel(&_panel_instance);
    }
};

class DisplayUI {
public:
    DisplayUI();
    void init();
    void setState(UiState state);
    UiState getState() const { return currentState; }
    void updateDashboard(const String& project, const String& time_str, int clients, const String& status);
    void updateWeather(const String& weather, const String& temp, const String& aqi);
    void setThinkingText(const String& text);
    void setSpeakingText(const String& text);
    void showAlert(const String& title, const String& content, const String& level = "warning");
    void show2FA(const String& actionId, const String& title, const String& details);
    String get2FAActionId() const { return twoFAActionId; }
    void showOTAProgress(int percent);
    void drawWaveform(int level);
    void triggerFindAlert();
    void sleepDisplay();
    
    void nextDashboardPage();
    void prevDashboardPage();
    int getDashboardPage() const { return dashboardPage; }
    
    // 番茄钟控制
    void togglePomodoro();
    void resetPomodoro();
    bool isPomodoroRunning() const { return pomodoroRunning; }

    void notifyActivity();
    unsigned long getLastActivityTime() const { return lastActivityTime; }
    void loop();

private:
    LGFX_ST7789 lcd;
    UiState currentState;
    String currentProject;
    String currentTimeStr;
    String currentStatus;
    String subtitleText;
    String alertTitle;
    String alertContent;
    String alertLevel;
    
    // 天气看板字段
    String weatherDesc;
    String tempStr;
    String aqiStr;

    // 物理 2FA 鉴权字段
    String twoFAActionId;
    String twoFATitle;
    String twoFADetails;

    // 独立番茄钟状态
    bool pomodoroRunning;
    int pomodoroRemainingSec;
    unsigned long lastPomodoroTick;

    int dashboardPage;       // 0: 飞书协同, 1: 硬件诊断, 2: 飞书通知, 3: 独立番茄钟, 4: 翻页天气时钟
    unsigned long alertStartTime;
    unsigned long lastAnimTime;
    unsigned long lastActivityTime;
    uint8_t currentBrightness;
    uint8_t targetBrightness;
    int animFrame;
    int otaPercent;

    void renderStatusBar();
    void renderConnecting();
    void renderDashboard();
    void renderDashboardPage0(); // 飞书协同看板
    void renderDashboardPage1(); // 硬件诊断与电量
    void renderDashboardPage2(); // 飞书通知流
    void renderDashboardPage3(); // 随身番茄钟
    void renderDashboardPage4(); // 复古全屏翻页天气时钟
    void renderListening();
    void renderThinking();
    void renderSpeaking();
    void renderAlert();
    void render2FA();
    void renderOTA();
    void renderAvatarFace(int cx, int cy, const char* mood);
    void updateBacklight();
};

extern DisplayUI ui;
