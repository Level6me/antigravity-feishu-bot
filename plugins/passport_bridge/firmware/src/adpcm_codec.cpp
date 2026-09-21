#include "adpcm_codec.h"

int16_t AdpcmCodec::predictedSample = 0;
int8_t AdpcmCodec::stepIndex = 0;

const int16_t AdpcmCodec::stepTable[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
};

const int8_t AdpcmCodec::indexTable[16] = {
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8
};

uint8_t AdpcmCodec::encodeSample(int16_t sample, int16_t* predicted, int8_t* index) {
    int32_t diff = sample - *predicted;
    uint8_t nibble = 0;
    int32_t step = stepTable[*index];
    int32_t diffq = step >> 3;

    if (diff < 0) {
        nibble = 8;
        diff = -diff;
    }

    if (diff >= step) {
        nibble |= 4;
        diff -= step;
        diffq += step;
    }
    step >>= 1;
    if (diff >= step) {
        nibble |= 2;
        diff -= step;
        diffq += step;
    }
    step >>= 1;
    if (diff >= step) {
        nibble |= 1;
        diffq += step;
    }

    if (nibble & 8) {
        *predicted -= diffq;
        if (*predicted < -32768) *predicted = -32768;
    } else {
        *predicted += diffq;
        if (*predicted > 32767) *predicted = 32767;
    }

    *index += indexTable[nibble];
    if (*index < 0) *index = 0;
    else if (*index > 88) *index = 88;

    return nibble;
}

int16_t AdpcmCodec::decodeSample(uint8_t nibble, int16_t* predicted, int8_t* index) {
    int32_t step = stepTable[*index];
    int32_t diffq = step >> 3;

    if (nibble & 4) diffq += step;
    if (nibble & 2) diffq += step >> 1;
    if (nibble & 1) diffq += step >> 2;

    if (nibble & 8) {
        *predicted -= diffq;
        if (*predicted < -32768) *predicted = -32768;
    } else {
        *predicted += diffq;
        if (*predicted > 32767) *predicted = 32767;
    }

    *index += indexTable[nibble];
    if (*index < 0) *index = 0;
    else if (*index > 88) *index = 88;

    return *predicted;
}

size_t AdpcmCodec::encode(const int16_t* pcmIn, size_t sampleCount, uint8_t* adpcmOut) {
    size_t outBytes = 0;
    for (size_t i = 0; i < sampleCount; i += 2) {
        uint8_t nibble0 = encodeSample(pcmIn[i], &predictedSample, &stepIndex);
        uint8_t nibble1 = 0;
        if (i + 1 < sampleCount) {
            nibble1 = encodeSample(pcmIn[i + 1], &predictedSample, &stepIndex);
        }
        adpcmOut[outBytes++] = (nibble1 << 4) | (nibble0 & 0x0F);
    }
    return outBytes;
}

size_t AdpcmCodec::decode(const uint8_t* adpcmIn, size_t byteCount, int16_t* pcmOut) {
    size_t outSamples = 0;
    for (size_t i = 0; i < byteCount; i++) {
        uint8_t byte = adpcmIn[i];
        pcmOut[outSamples++] = decodeSample(byte & 0x0F, &predictedSample, &stepIndex);
        pcmOut[outSamples++] = decodeSample((byte >> 4) & 0x0F, &predictedSample, &stepIndex);
    }
    return outSamples;
}
