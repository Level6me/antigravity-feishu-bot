#include "network_ws.h"

NetworkWS net;

NetworkWS::NetworkWS() 
    : serverPort(DEFAULT_SERVER_PORT),
      wsConnected(false),
      lastDiscoveryAttempt(0),
      lastHeartbeat(0) {}

void NetworkWS::init() {
    connectWiFi();
    udp.begin(UDP_DISCOVERY_PORT);
}

void NetworkWS::connectWiFi() {
    log_i("Connecting to Wi-Fi SSID: %s", DEFAULT_WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(DEFAULT_WIFI_SSID, DEFAULT_WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        attempts++;
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
        log_i("\nWi-Fi connected! IP: %s", WiFi.localIP().toString().c_str());
        discoverServer();
    } else {
        log_w("\nWi-Fi connection failed or not set. Running in AP discovery mode.");
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
            
            // 发送登记握手包
            {
                JsonDocument doc;
                doc["type"] = "handshake";
                doc["device_id"] = "passport_c3";
                doc["version"] = "1.0.0";
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
        ui.showAlert(String(title), String(content), String(level));
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

bool NetworkWS::isConnected() {
    return wsConnected;
}

void NetworkWS::loop() {
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
