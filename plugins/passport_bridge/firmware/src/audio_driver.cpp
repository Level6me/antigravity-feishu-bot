#include "audio_driver.h"
#include <math.h>

AudioDriver audio;

AudioDriver::AudioDriver() {}

bool AudioDriver::init() {
    Wire.begin(BSP_I2C_SDA, BSP_I2C_SCL, 100000);
    delay(50);
    
    if (!initES8311()) {
        log_e("ES8311 Codec init failed!");
        return false;
    }
    
    if (!initI2S()) {
        log_e("I2S driver init failed!");
        return false;
    }
    
    setVolume(85);
    log_i("AudioDriver initialized successfully.");
    return true;
}

void AudioDriver::writeReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(BSP_I2C_ES8311_ADDR);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t AudioDriver::readReg(uint8_t reg) {
    Wire.beginTransmission(BSP_I2C_ES8311_ADDR);
    Wire.write(reg);
    Wire.endTransmission();
    Wire.requestFrom((uint16_t)BSP_I2C_ES8311_ADDR, (uint8_t)1);
    if (Wire.available()) {
        return Wire.read();
    }
    return 0;
}

bool AudioDriver::initES8311() {
    // 基础复位
    writeReg(0x00, 0x1F);
    delay(20);
    writeReg(0x00, 0x00);
    delay(10);
    
    // 时钟分频与使能
    writeReg(0x01, 0x30);
    writeReg(0x02, 0x10);
    writeReg(0x03, 0x10);
    writeReg(0x04, 0x00);
    writeReg(0x05, 0x00);
    writeReg(0x06, 0x00);
    writeReg(0x07, 0x00);
    writeReg(0x08, 0xFF);
    
    // 系统电源开启 (ADC + DAC 供电)
    writeReg(0x09, 0x0C);
    writeReg(0x0A, 0x0C);
    writeReg(0x0B, 0x00);
    writeReg(0x0C, 0x00);
    
    // I2S 音频接口模式 (16bit, 标准飞利浦 I2S 格式)
    writeReg(0x0D, 0x01);
    writeReg(0x0E, 0x00);
    writeReg(0x0F, 0x00);
    writeReg(0x10, 0x00);
    writeReg(0x11, 0x00);
    writeReg(0x12, 0x00);
    writeReg(0x13, 0x00);
    
    // 麦克风录音模拟前端增益 (PGA 设为 +24dB，清晰拾音)
    writeReg(0x14, 0x6A);
    writeReg(0x15, 0x40);
    writeReg(0x16, 0x00);
    writeReg(0x17, 0xBF);
    writeReg(0x18, 0x08);
    
    // DAC 扬声器音量初值
    writeReg(0x31, 0x00);
    writeReg(0x32, 0xC0); // 默认适中音量
    return true;
}

bool AudioDriver::initI2S() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX),
        .sample_rate = AUDIO_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = AUDIO_DMA_BUF_COUNT,
        .dma_buf_len = AUDIO_DMA_BUF_LEN,
        .use_apll = false,
        .tx_desc_auto_clear = true,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .mck_io_num = BSP_I2S_MCLK,
        .bck_io_num = BSP_I2S_BCLK,
        .ws_io_num = BSP_I2S_WS,
        .data_out_num = BSP_I2S_DOUT,
        .data_in_num = BSP_I2S_DIN
    };

    esp_err_t err = i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    if (err != ESP_OK) return false;

    err = i2s_set_pin(I2S_NUM_0, &pin_config);
    return (err == ESP_OK);
}

void AudioDriver::setVolume(uint8_t volume) {
    uint8_t reg_val = map(volume, 0, 100, 0, 255);
    writeReg(0x32, reg_val);
}

size_t AudioDriver::readRecordData(int16_t* buffer, size_t samples) {
    size_t bytes_read = 0;
    i2s_read(I2S_NUM_0, (void*)buffer, samples * sizeof(int16_t), &bytes_read, portMAX_DELAY);
    return bytes_read / sizeof(int16_t);
}

size_t AudioDriver::writePlayData(const uint8_t* buffer, size_t bytes) {
    size_t bytes_written = 0;
    i2s_write(I2S_NUM_0, buffer, bytes, &bytes_written, portMAX_DELAY);
    return bytes_written;
}

void AudioDriver::playBeep(uint16_t freqHz, uint16_t durationMs, uint8_t volumePercent) {
    if (freqHz == 0 || durationMs == 0) return;
    int16_t toneBuf[128];
    size_t totalSamples = (AUDIO_SAMPLE_RATE * durationMs) / 1000;
    size_t generated = 0;
    float phase = 0.0f;
    float phaseIncrement = 2.0f * (float)M_PI * (float)freqHz / (float)AUDIO_SAMPLE_RATE;
    int16_t amplitude = (int16_t)(32767.0f * (volumePercent / 100.0f) * 0.4f);

    while (generated < totalSamples) {
        size_t chunkSize = ((totalSamples - generated) < 128) ? (totalSamples - generated) : 128;
        for (size_t i = 0; i < chunkSize; i++) {
            toneBuf[i] = (int16_t)(sinf(phase) * amplitude);
            phase += phaseIncrement;
            if (phase >= 2.0f * (float)M_PI) phase -= 2.0f * (float)M_PI;
        }
        writePlayData((const uint8_t*)toneBuf, chunkSize * sizeof(int16_t));
        generated += chunkSize;
    }
}

void AudioDriver::playTone(ToneType type) {
    switch (type) {
        case TONE_PTT_START:
            // 升调灵动双音，提示开始对讲录音
            playBeep(880, 45, 50);
            playBeep(1320, 55, 50);
            break;
        case TONE_PTT_END:
            // 降调轻柔音，提示录音结束已发送
            playBeep(1100, 35, 40);
            playBeep(660, 45, 40);
            break;
        case TONE_CONNECTED:
            // 局域网网关连线成功三和弦音
            playBeep(523, 50, 40);
            playBeep(659, 50, 40);
            playBeep(784, 80, 50);
            break;
        case TONE_ALERT:
            // 警报两声
            playBeep(1500, 70, 60);
            delay(40);
            playBeep(1500, 70, 60);
            break;
    }
}
