#include "wifi_manager.h"
#include "display_ui.h"

WifiManager wifiMgr;

WifiManager::WifiManager() 
    : server(80), 
      apMode(false), 
      currentSSID(""), 
      currentPassword(""),
      portalStartTime(0) {}

bool WifiManager::init() {
    prefs.begin("wifi_cfg", false);
    currentSSID = prefs.getString("ssid", DEFAULT_WIFI_SSID);
    currentPassword = prefs.getString("password", DEFAULT_WIFI_PASSWORD);

    log_i("WiFi Manager: Attempting connection to SSID [%s]...", currentSSID.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.begin(currentSSID.c_str(), currentPassword.c_str());

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(400);
        attempts++;
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
        log_i("\nWiFi connected! IP: %s", WiFi.localIP().toString().c_str());
        return true;
    } else {
        log_w("\nWiFi connection failed! Starting SoftAP configuration portal...");
        startConfigPortal();
        return false;
    }
}

void WifiManager::startConfigPortal() {
    apMode = true;
    portalStartTime = millis();
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP("FoloToy-Passport-AP", "12345678");
    IPAddress apIP = WiFi.softAPIP();
    log_i("SoftAP started. AP SSID: FoloToy-Passport-AP, IP: %s", apIP.toString().c_str());

    setupWebServer();
    server.begin();

    ui.showAlert("Wi-Fi 配网模式", "连接热点: FoloToy-Passport-AP\n密码: 12345678\n浏览器打开: 192.168.4.1", "warning");
}

void WifiManager::setupWebServer() {
    server.on("/", HTTP_GET, [this]() { this->handleRoot(); });
    server.on("/save", HTTP_POST, [this]() { this->handleSave(); });
}

void WifiManager::handleRoot() {
    int n = WiFi.scanNetworks();
    String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
    html += "<title>FoloToy Passport 配网</title>";
    html += "<style>body{background:#181825;color:#cad3f5;font-family:sans-serif;padding:20px;text-align:center;}";
    html += "input,select{width:90%;max-width:320px;padding:12px;margin:8px 0;background:#1e1e2e;color:#fff;border:1px solid #7aa2f7;border-radius:6px;font-size:16px;}";
    html += "button{width:90%;max-width:320px;padding:12px;background:#00e5ff;color:#000;border:none;border-radius:6px;font-weight:bold;font-size:16px;cursor:pointer;margin-top:12px;}";
    html += ".card{background:#1f2335;padding:20px;border-radius:12px;display:inline-block;width:100%;max-width:360px;box-shadow:0 4px 12px rgba(0,0,0,0.5);}";
    html += "</style></head><body><div class='card'>";
    html += "<h2>📟 FoloToy AI Passport</h2><p>请选择周围 Wi-Fi 并输入密码：</p>";
    html += "<form method='POST' action='/save'>";
    html += "<select name='ssid'>";
    for (int i = 0; i < n; ++i) {
        html += "<option value='" + WiFi.SSID(i) + "'>" + WiFi.SSID(i) + " (" + String(WiFi.RSSI(i)) + "dBm)</option>";
    }
    html += "</select><br>";
    html += "<input type='password' name='pass' placeholder='Wi-Fi 密码'><br>";
    html += "<button type='submit'>💾 保存配置并重启</button>";
    html += "</form></div></body></html>";
    server.send(200, "text/html", html);
}

void WifiManager::handleSave() {
    String newSsid = server.arg("ssid");
    String newPass = server.arg("pass");

    if (newSsid.length() > 0) {
        prefs.putString("ssid", newSsid);
        prefs.putString("password", newPass);
        log_i("New Wi-Fi credentials saved to NVS: [%s]", newSsid.c_str());

        String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
        html += "<body style='background:#181825;color:#00e676;font-family:sans-serif;padding:30px;text-align:center;'>";
        html += "<h2>✅ 配置已保存！</h2><p style='color:#fff;'>Passport 正在自动重启连接目标网络...</p></body></html>";
        server.send(200, "text/html", html);

        ui.showAlert("配置保存成功", "正在重启连接目标 Wi-Fi:\n" + newSsid, "info");
        delay(1500);
        ESP.restart();
    } else {
        server.send(400, "text/plain", "SSID cannot be empty");
    }
}

void WifiManager::loop() {
    if (apMode) {
        server.handleClient();
    }
}
