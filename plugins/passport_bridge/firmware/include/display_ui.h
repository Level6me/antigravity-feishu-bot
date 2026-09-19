#pragma once
#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include "config.h"

enum UiState {
    UI_STATE_BOOT,
    UI_STATE_CONNECTING,
    UI_STATE_DASHBOARD,
    UI_STATE_LISTENING,
    UI_STATE_THINKING,
    UI_STATE_SPEAKING,
    UI_STATE_ALERT
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
            cfg.pin_sclk = PIN_LCD_SCLK;
            cfg.pin_mosi = PIN_LCD_MOSI;
            cfg.pin_miso = -1;
            cfg.pin_dc   = PIN_LCD_DC;
            _bus_instance.config(cfg);
            _panel_instance.setBus(&_bus_instance);
        }
        {
            auto cfg = _panel_instance.config();
            cfg.pin_cs           = PIN_LCD_CS;
            cfg.pin_rst          = PIN_LCD_RST;
            cfg.pin_busy         = -1;
            cfg.panel_width      = 240;
            cfg.panel_height     = 320;
            cfg.offset_x         = 0;
            cfg.offset_y         = 0;
            cfg.offset_rotation  = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits  = 1;
            cfg.readable         = false;
            cfg.invert           = true;
            cfg.rgb_order        = false;
            cfg.dlen_16bit       = false;
            cfg.bus_shared       = false;
            _panel_instance.config(cfg);
        }
        {
            auto cfg = _light_instance.config();
            cfg.pin_bl = PIN_LCD_BL;
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
    void updateDashboard(const String& project, const String& time_str, int clients, const String& status);
    void setThinkingText(const String& text);
    void setSpeakingText(const String& text);
    void showAlert(const String& title, const String& content, const String& level = "warning");
    void drawWaveform(int level);
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
    unsigned long alertStartTime;
    unsigned long lastAnimTime;
    int animFrame;

    void renderConnecting();
    void renderDashboard();
    void renderListening();
    void renderThinking();
    void renderSpeaking();
    void renderAlert();
};

extern DisplayUI ui;
