#include "battery_gauge.h"

BatteryGauge battery;

BatteryGauge::BatteryGauge()
    : chipFound(false),
      batteryPercent(100),
      voltageMv(4000),
      charging(false),
      lastReadTime(0),
      lastVoltageMv(4000) {}

bool BatteryGauge::readRegister(uint8_t reg, uint8_t* val) {
    return readRegisters(reg, val, 1);
}

bool BatteryGauge::readRegisters(uint8_t reg, uint8_t* buffer, size_t len) {
    Wire.beginTransmission(BSP_I2C_CW2017_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }
    size_t received = Wire.requestFrom((uint16_t)BSP_I2C_CW2017_ADDR, (uint8_t)len);
    if (received != len) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        buffer[i] = Wire.read();
    }
    return true;
}

bool BatteryGauge::writeRegister(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(BSP_I2C_CW2017_ADDR);
    Wire.write(reg);
    Wire.write(val);
    return (Wire.endTransmission() == 0);
}

bool BatteryGauge::init() {
    // 探测 I2C 总线上的 CW2017 芯片 (地址 0x63)
    Wire.beginTransmission(BSP_I2C_CW2017_ADDR);
    if (Wire.endTransmission() == 0) {
        chipFound = true;
        log_i("CW2017 Battery Fuel Gauge found at I2C 0x63");
        
        // 唤醒 CW2017 芯片：向 MODE 寄存器写 0x00
        writeRegister(CW2017_REG_MODE, 0x00);
        delay(20);
        
        readBatteryMetrics();
        return true;
    } else {
        chipFound = false;
        log_w("CW2017 not detected on I2C bus. Running on simulated/USB power state.");
        batteryPercent = 100;
        voltageMv = 4200;
        return false;
    }
}

void BatteryGauge::readBatteryMetrics() {
    if (!chipFound) return;

    // 1. 读取电池端电压 VCELL (2 字节)
    uint8_t vcellBuf[2] = {0};
    if (readRegisters(CW2017_REG_VCELL_H, vcellBuf, 2)) {
        uint16_t rawV = ((uint16_t)vcellBuf[0] << 8) | vcellBuf[1];
        // CW2017 官方每 LSB 为 305 微伏 (0.305 mV)
        uint16_t calculatedMv = (uint32_t)rawV * 305 / 1000;
        if (calculatedMv >= 2500 && calculatedMv <= 4500) {
            voltageMv = calculatedMv;
        }
    }

    // 2. 读取电量百分比 SOC (2 字节：整数 + 小数)
    uint8_t socBuf[2] = {0};
    if (readRegisters(CW2017_REG_SOC_INT, socBuf, 2)) {
        uint8_t socInt = socBuf[0];
        if (socInt <= 100) {
            batteryPercent = socInt;
        } else {
            batteryPercent = 100;
        }
    }

    // 3. 充电状态简单推断：电压高于 4250mV 或呈现上升充电态
    if (voltageMv > 4250 || (voltageMv > lastVoltageMv && voltageMv > 4100)) {
        charging = true;
    } else {
        charging = false;
    }
    lastVoltageMv = voltageMv;

    log_d("CW2017: Voltage=%u mV, SOC=%u%%, Charging=%d", voltageMv, batteryPercent, charging);
}

void BatteryGauge::loop() {
    unsigned long now = millis();
    // 每 6 秒采集刷新一次电量数据，降低 I2C 占用
    if (now - lastReadTime >= 6000) {
        lastReadTime = now;
        readBatteryMetrics();
    }
}
