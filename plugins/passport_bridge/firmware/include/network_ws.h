#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <HTTPUpdate.h>

#include "config.h"
#include "display_ui.h"
#include "audio_driver.h"
#include "wifi_manager.h"

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
    void sendInterrupt();
    void send2FAResponse(const String& actionId, const String& result);
    void startOTA(const String& url);

private:
    WebSocketsClient wsClient;
    WiFiUDP udp;
    IPAddress serverIP;
    uint16_t serverPort;
    bool wsConnected;
    unsigned long lastDiscoveryAttempt;
    unsigned long lastHeartbeat;

    bool discoverServer();
    void onWsEvent(WStype_t type, uint8_t * payload, size_t length);
    void handleTextMessage(const char* jsonText);
};

extern NetworkWS net;
