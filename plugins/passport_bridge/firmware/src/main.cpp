#include <Arduino.h>
#include "config.h"
#include "display_ui.h"
#include "audio_driver.h"
#include "network_ws.h"

// 按键状态跟踪结构体
struct ButtonState {
    int pin;
    const char* name;
    bool isPressed;
    unsigned long pressStartTime;
    bool longPressedTriggered;
};

ButtonState btnUp   = { PIN_BTN_UP,   "up",   false, 0, false };
ButtonState btnDown = { PIN_BTN_DOWN, "down", false, 0, false };
ButtonState btnOk   = { PIN_BTN_OK,   "ok",   false, 0, false };

bool isRecording = false;
int16_t micPcmBuffer[AUDIO_DMA_BUF_LEN];

void handleButton(ButtonState& btn) {
    bool rawState = (digitalRead(btn.pin) == LOW); // 低电平代表按下
    unsigned long now = millis();

    if (rawState && !btn.isPressed) {
        // 按下瞬间
        btn.isPressed = true;
        btn.pressStartTime = now;
        btn.longPressedTriggered = false;
    } 
    else if (rawState && btn.isPressed) {
        // 持续按下中，检查是否达到长按判定
        if (!btn.longPressedTriggered && (now - btn.pressStartTime >= LONG_PRESS_MS)) {
            btn.longPressedTriggered = true;
            if (strcmp(btn.name, "ok") == 0) {
                // OK 键长按：启动语音对讲录音
                isRecording = true;
                net.sendVoiceStart();
                ui.setState(UI_STATE_LISTENING);
            } else {
                net.sendButtonEvent(btn.name, "long_press");
            }
        }
    } 
    else if (!rawState && btn.isPressed) {
        // 松开瞬间
        btn.isPressed = false;
        if (strcmp(btn.name, "ok") == 0 && isRecording) {
            // OK 键松开：结束录音
            isRecording = false;
            net.sendVoiceEnd();
            ui.setState(UI_STATE_THINKING);
            ui.setThinkingText("发送给飞书 Agent...");
        } else if (!btn.longPressedTriggered) {
            // 短按触发
            net.sendButtonEvent(btn.name, "short_press");
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(200);
    log_i("==================================================");
    log_i("  FoloToy AI Passport - Antigravity Feishu Client ");
    log_i("==================================================");

    // 1. 初始化按键引脚
    pinMode(PIN_BTN_UP, INPUT_PULLUP);
    pinMode(PIN_BTN_DOWN, INPUT_PULLUP);
    pinMode(PIN_BTN_OK, INPUT_PULLUP);

    // 2. 初始化 240x320 LCD 显示屏
    ui.init();

    // 3. 初始化 ES8311 音频芯片与 I2S 总线
    if (!audio.init()) {
        log_e("Audio hardware initialization failed!");
    }

    // 4. 初始化局域网通信 (Wi-Fi + UDP 发现)
    net.init();
}

void loop() {
    // 处理 3 颗实体按键事件
    handleButton(btnUp);
    handleButton(btnDown);
    handleButton(btnOk);

    // 录音循环：当用户按住 OK 键时，不断从 I2S 读取 PCM 并发往 WebSocket
    if (isRecording) {
        size_t samples = audio.readRecordData(micPcmBuffer, 256);
        if (samples > 0) {
            // 计算音频能量级别绘制声纹
            int32_t sum = 0;
            for (size_t i = 0; i < samples; i++) {
                sum += abs(micPcmBuffer[i]);
            }
            int energy = sum / samples;
            int waveLevel = map(constrain(energy, 100, 3000), 100, 3000, 10, 100);
            ui.drawWaveform(waveLevel);

            // 发送 PCM 二进制帧 (16kHz 16bit mono)
            net.sendAudioChunk((const uint8_t*)micPcmBuffer, samples * sizeof(int16_t));
        }
    }

    // 刷新 UI 动效与后台网络事件
    ui.loop();
    net.loop();
}
