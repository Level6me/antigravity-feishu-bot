#pragma once
#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>

typedef void (*EspNowRecvCallback)(const uint8_t *mac, const uint8_t *data, int len);

class EspNowMesh {
public:
    EspNowMesh();
    bool init();
    bool sendBroadcast(const uint8_t* data, size_t len);
    void setOnReceive(EspNowRecvCallback cb);
    bool isReady() const { return initialized; }

private:
    bool initialized;
    uint8_t broadcastPeer[6];
};

extern EspNowMesh espMesh;
