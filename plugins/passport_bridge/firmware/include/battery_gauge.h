#pragma once
#include <Arduino.h>
#include <Wire.h>
#include "config.h"

// CW2017 电池电量计 I2C 寄存器定义
#define CW2017_REG_VCELL_H      0x02  // 电池端电压高字节
#define CW2017_REG_VCELL_L      0x03  // 电池端电压低字节
#define CW2017_REG_SOC_INT      0x04  // SOC 剩余电量整数部分 (0-100%)
#define CW2017_REG_SOC_DEC      0x05  // SOC 剩余电量小数部分 (1/256%)
#define CW2017_REG_CONFIG       0x08  // 配置与重启寄存器
#define CW2017_REG_MODE         0x0A  // 工作模式寄存器 (0x00: Normal, 0xC0: Sleep)

class BatteryGauge {
public:
    BatteryGauge();
    bool init();
    void loop();
    
    uint8_t getPercent() const { return batteryPercent; }
    uint16_t getVoltageMv() const { return voltageMv; }
    bool isCharging() const { return charging; }
    bool isAvailable() const { return chipFound; }
    bool isLowBattery() const { return chipFound && (batteryPercent <= 15); }
    bool isCriticalBattery() const { return chipFound && (batteryPercent <= 5); }

private:
    bool chipFound;
    uint8_t batteryPercent;
    uint16_t voltageMv;
    bool charging;
    unsigned long lastReadTime;
    uint16_t lastVoltageMv;

    bool readRegister(uint8_t reg, uint8_t* val);
    bool readRegisters(uint8_t reg, uint8_t* buffer, size_t len);
    bool writeRegister(uint8_t reg, uint8_t val);
    void readBatteryMetrics();
};

extern BatteryGauge battery;
