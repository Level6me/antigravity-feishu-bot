#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

#include "config.h"
#include "display_ui.h"
#include "audio_driver.h"

class NetworkWS {
public:
    NetworkWS();
    void init();
    void loop();
    
    bool isConnected();
    void sendButtonEvent(const char* button, const char* action);
    void sendVoiceStart();
    void sendVoiceEnd();
    void sendAudioChunk(const uint8_t* data, size_t len);

private:
    WebSocketsClient wsClient;
    WiFiUDP udp;
    IPAddress serverIP;
    uint16_t serverPort;
    bool wsConnected;
    unsigned long lastDiscoveryAttempt;
    unsigned long lastHeartbeat;

    void connectWiFi();
    bool discoverServer();
    void onWsEvent(WStype_t type, uint8_t * payload, size_t length);
    void handleTextMessage(const char* jsonText);
};

extern NetworkWS net;
