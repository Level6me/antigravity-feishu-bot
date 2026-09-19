#include "audio_driver.h"

AudioDriver audio;

AudioDriver::AudioDriver() {}

bool AudioDriver::init() {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);
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
    Wire.beginTransmission(ES8311_I2C_ADDR);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t AudioDriver::readReg(uint8_t reg) {
    Wire.beginTransmission(ES8311_I2C_ADDR);
    Wire.write(reg);
    Wire.endTransmission();
    Wire.requestFrom(ES8311_I2C_ADDR, 1);
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
    writeReg(0x0D, 0x01); // Master / Slave
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
        .bck_io_num = PIN_I2S_BCLK,
        .ws_io_num = PIN_I2S_WS,
        .data_out_num = PIN_I2S_DOUT,
        .data_in_num = PIN_I2S_DIN
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
