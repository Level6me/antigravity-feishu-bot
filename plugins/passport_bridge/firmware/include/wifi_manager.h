#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include "config.h"

class WifiManager {
public:
    WifiManager();
    bool init();
    void loop();
    void startConfigPortal();
    bool isPortalRunning() const { return apMode; }
    String getConnectedSSID() const { return currentSSID; }

private:
    Preferences prefs;
    WebServer server;
    bool apMode;
    String currentSSID;
    String currentPassword;
    unsigned long portalStartTime;

    void setupWebServer();
    void handleRoot();
    void handleSave();
};

extern WifiManager wifiMgr;
