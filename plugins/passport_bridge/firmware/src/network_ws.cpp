#include "network_ws.h"

NetworkWS net;

NetworkWS::NetworkWS() 
    : serverPort(DEFAULT_SERVER_PORT),
      wsConnected(false),
      lastDiscoveryAttempt(0),
      lastHeartbeat(0) {}

void NetworkWS::init() {
    // 采用 NVS 凭证优先与 SoftAP Web 配网管理器
    bool connected = wifiMgr.init();
    udp.begin(UDP_DISCOVERY_PORT);
    if (connected) {
        discoverServer();
    }
}

bool NetworkWS::discoverServer() {
    log_i("Broadcasting UDP discovery on port %d...", UDP_DISCOVERY_PORT);
    IPAddress broadcastIP(255, 255, 255, 255);
    udp.beginPacket(broadcastIP, UDP_DISCOVERY_PORT);
    udp.write((const uint8_t*)"DISCOVER_FEISHU_PASSPORT", 24);
    udp.endPacket();

    unsigned long startWait = millis();
    while (millis() - startWait < 1200) {
        int packetSize = udp.parsePacket();
        if (packetSize) {
            char packetBuffer[256];
            int len = udp.read(packetBuffer, 255);
            if (len > 0) packetBuffer[len] = 0;

            JsonDocument doc;
            DeserializationError err = deserializeJson(doc, packetBuffer);
            if (!err && doc["service"] == "feishu_passport") {
                serverIP = udp.remoteIP();
                serverPort = doc["ws_port"] | DEFAULT_SERVER_PORT;
                log_i("Discovered Feishu Bot Gateway at: %s:%d", serverIP.toString().c_str(), serverPort);

                // 配置并启动 WebSocket 客户端
                wsClient.begin(serverIP.toString().c_str(), serverPort, "/ws/passport");
                wsClient.onEvent([this](WStype_t type, uint8_t * payload, size_t length) {
                    this->onWsEvent(type, payload, length);
                });
                wsClient.setReconnectInterval(2000);
                return true;
            }
        }
        delay(50);
    }
    return false;
}

void NetworkWS::onWsEvent(WStype_t type, uint8_t * payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            wsConnected = false;
            log_w("[WS] Disconnected from server!");
            ui.setState(UI_STATE_CONNECTING);
            break;

        case WStype_CONNECTED:
            wsConnected = true;
            log_i("[WS] Connected to %s", (char*)payload);
            ui.setState(UI_STATE_DASHBOARD);
            audio.playTone(TONE_CONNECTED);
            
            // 发送登记握手包
            {
                JsonDocument doc;
                doc["type"] = "handshake";
                doc["device_id"] = "passport_c3";
                doc["version"] = "1.2.0";
                String out;
                serializeJson(doc, out);
                wsClient.sendTXT(out);
            }
            break;

        case WStype_TEXT:
            handleTextMessage((char*)payload);
            break;

        case WStype_BIN:
            // 接收服务端回传的 TTS 播放音频流并写入 I2S 扬声器
            audio.writePlayData(payload, length);
            break;

        case WStype_PONG:
        case WStype_ERROR:
            break;
    }
}

void NetworkWS::handleTextMessage(const char* jsonText) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, jsonText);
    if (err) return;

    const char* type = doc["type"];
    if (!type) return;

    if (strcmp(type, "dashboard_sync") == 0) {
        const char* proj = doc["project"] | "feishu-bot";
        const char* t_str = doc["time"] | "00:00:00";
        const char* st = doc["status"] | "ready";
        int clients = doc["clients_count"] | 1;
        ui.updateDashboard(String(proj), String(t_str), clients, String(st));
    }
    else if (strcmp(type, "ai_state") == 0) {
        const char* state = doc["state"] | "idle";
        const char* detail = doc["detail"] | "";
        if (strcmp(state, "listening") == 0) {
            ui.setState(UI_STATE_LISTENING);
        } else if (strcmp(state, "thinking") == 0) {
            ui.setState(UI_STATE_THINKING);
            ui.setThinkingText(String(detail));
        } else if (strcmp(state, "speaking") == 0) {
            ui.setState(UI_STATE_SPEAKING);
        } else {
            ui.setState(UI_STATE_DASHBOARD);
        }
    }
    else if (strcmp(type, "ai_speech_start") == 0) {
        const char* subtitle = doc["text"] | "";
        ui.setState(UI_STATE_SPEAKING);
        ui.setSpeakingText(String(subtitle));
    }
    else if (strcmp(type, "ai_speech_end") == 0) {
        ui.setState(UI_STATE_DASHBOARD);
    }
    else if (strcmp(type, "alert_popup") == 0) {
        const char* title = doc["title"] | "通知";
        const char* content = doc["content"] | "";
        const char* level = doc["level"] | "warning";
        audio.playTone(TONE_ALERT);
        ui.showAlert(String(title), String(content), String(level));
    }
    else if (strcmp(type, "confirm_request") == 0) {
        // 物理 2FA 高危二次确认弹窗
        const char* actionId = doc["action_id"] | "unknown";
        const char* title = doc["title"] | "高危指令确认";
        const char* details = doc["details"] | "需物理按键确认批准";
        audio.playTone(TONE_ALERT);
        ui.show2FA(String(actionId), String(title), String(details));
    }
    else if (strcmp(type, "ota_update") == 0) {
        const char* url = doc["url"] | "";
        if (url && strlen(url) > 0) {
            startOTA(String(url));
        }
    }
    else if (strcmp(type, "find_device") == 0) {
        // 飞书寻机指令触发
        ui.triggerFindAlert();
    }
    else if (strcmp(type, "weather_sync") == 0) {
        // 局域网天气与温湿度同步
        const char* w = doc["weather"] | "SUNNY";
        const char* temp = doc["temp"] | "24C";
        const char* aqi = doc["aqi"] | "AQI 32";
        ui.updateWeather(String(w), String(temp), String(aqi));
    }
    else if (strcmp(type, "meeting_alert") == 0) {
        // 飞书日历会议到期提前提醒
        const char* title = doc["title"] | "飞书会议";
        const char* timeStr = doc["time"] | "5分钟后开始";
        audio.playTone(TONE_ALERT);
        ui.showAlert("📅 会议提醒", String(title) + "\n" + String(timeStr), "warning");
    }
}

void NetworkWS::sendButtonEvent(const char* button, const char* action) {
    if (!wsConnected) return;
    JsonDocument doc;
    doc["type"] = "button_event";
    doc["button"] = button;
    doc["action"] = action;
    String out;
    serializeJson(doc, out);
    wsClient.sendTXT(out);
}

void NetworkWS::sendVoiceStart() {
    if (!wsConnected) return;
    wsClient.sendTXT("{\"type\":\"voice_start\"}");
}

void NetworkWS::sendVoiceEnd() {
    if (!wsConnected) return;
    wsClient.sendTXT("{\"type\":\"voice_end\"}");
}

void NetworkWS::sendAudioChunk(const uint8_t* data, size_t len) {
    if (!wsConnected || len == 0) return;
    wsClient.sendBIN(data, len);
}

// 语音打断通知 (Barge-in)
void NetworkWS::sendInterrupt() {
    if (!wsConnected) return;
    wsClient.sendTXT("{\"type\":\"interrupt\"}");
    log_i("[WS] Sent voice interrupt (barge-in) signal to server");
}

// 物理 2FA 确认或拒绝上报
void NetworkWS::send2FAResponse(const String& actionId, const String& result) {
    if (!wsConnected) return;
    JsonDocument doc;
    doc["type"] = "confirm_response";
    doc["action_id"] = actionId;
    doc["result"] = result;
    String out;
    serializeJson(doc, out);
    wsClient.sendTXT(out);
    log_i("[WS] Sent 2FA response: id=%s, result=%s", actionId.c_str(), result.c_str());
}

// 局域网 OTA 无线升级
void NetworkWS::startOTA(const String& url) {
    log_i("[OTA] Starting OTA wireless upgrade from: %s", url.c_str());
    wsClient.disconnect();
    ui.showOTAProgress(0);

    WiFiClient client;
    httpUpdate.setLedPin(-1);
    
    Update.onProgress([](size_t current, size_t total) {
        if (total > 0) {
            int pct = (current * 100) / total;
            ui.showOTAProgress(pct);
        }
    });

    t_httpUpdate_return ret = httpUpdate.update(client, url);
    switch (ret) {
        case HTTP_UPDATE_FAILED:
            log_e("[OTA] Update failed! Error (%d): %s", httpUpdate.getLastError(), httpUpdate.getLastErrorString().c_str());
            ui.showAlert("OTA 升级失败", httpUpdate.getLastErrorString(), "danger");
            break;
        case HTTP_UPDATE_NO_UPDATES:
            log_w("[OTA] No updates available");
            break;
        case HTTP_UPDATE_OK:
            log_i("[OTA] Update successfully finished! Rebooting...");
            ui.showAlert("OTA 升级成功", "固件校验通过，正在重启生效...", "info");
            delay(1000);
            ESP.restart();
            break;
    }
}

bool NetworkWS::isConnected() {
    return wsConnected;
}

void NetworkWS::loop() {
    wifiMgr.loop();

    if (WiFi.status() == WL_CONNECTED) {
        if (!wsConnected) {
            unsigned long now = millis();
            if (now - lastDiscoveryAttempt > 4000) {
                lastDiscoveryAttempt = now;
                discoverServer();
            }
        } else {
            wsClient.loop();
        }
    }
}
