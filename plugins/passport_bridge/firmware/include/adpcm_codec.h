#pragma once
#include <stdint.h>
#include <stddef.h>

// IMA-ADPCM 4:1 轻量级音频压缩编解码器
// 专为 ESP32-C3 (无 PSRAM) 优化，超低内存开销，纯整数查表运算

class AdpcmCodec {
public:
    static size_t encode(const int16_t* pcmIn, size_t sampleCount, uint8_t* adpcmOut);
    static size_t decode(const uint8_t* adpcmIn, size_t byteCount, int16_t* pcmOut);

private:
    static int16_t predictedSample;
    static int8_t stepIndex;

    static const int16_t stepTable[89];
    static const int8_t indexTable[16];

    static uint8_t encodeSample(int16_t sample, int16_t* predicted, int8_t* index);
    static int16_t decodeSample(uint8_t nibble, int16_t* predicted, int8_t* index);
};
