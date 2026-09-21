#pragma once
#include <Arduino.h>
#include <Wire.h>
#include "driver/i2s.h"
#include "config.h"

enum ToneType {
    TONE_PTT_START,
    TONE_PTT_END,
    TONE_CONNECTED,
    TONE_ALERT
};

class AudioDriver {
public:
    AudioDriver();
    bool init();
    void setVolume(uint8_t volume); // 0 - 100
    
    // 录音接口 (读取 16kHz 16bit 单声道 PCM 数据)
    size_t readRecordData(int16_t* buffer, size_t samples);
    
    // 播放接口 (写入 16kHz 16bit 单声道 PCM 数据)
    size_t writePlayData(const uint8_t* buffer, size_t bytes);

    // 硬件音效与操作听觉反馈接口
    void playBeep(uint16_t freqHz, uint16_t durationMs, uint8_t volumePercent = 50);
    void playTone(ToneType type);

private:
    bool initES8311();
    bool initI2S();
    void writeReg(uint8_t reg, uint8_t val);
    uint8_t readReg(uint8_t reg);
};

extern AudioDriver audio;
