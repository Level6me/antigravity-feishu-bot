#include "display_ui.h"
#include "audio_driver.h"

DisplayUI ui;

DisplayUI::DisplayUI() 
    : currentState(UI_STATE_BOOT),
      currentProject("antigravity-bot"),
      currentTimeStr("--:--:--"),
      currentStatus("CONNECTING"),
      subtitleText(""),
      alertTitle(""),
      alertContent(""),
      alertLevel("info"),
      weatherDesc("SUNNY"),
      tempStr("24C"),
      aqiStr("AQI 32"),
      twoFAActionId(""),
      twoFATitle(""),
      twoFADetails(""),
      pomodoroRunning(false),
      pomodoroRemainingSec(25 * 60),
      lastPomodoroTick(0),
      dashboardPage(0),
      alertStartTime(0),
      lastAnimTime(0),
      lastActivityTime(0),
      currentBrightness(200),
      targetBrightness(200),
      animFrame(0),
      otaPercent(0) {}

void DisplayUI::init() {
    lcd.init();
    lcd.setRotation(0); // 竖屏 240x320
    lcd.setBrightness(200);
    lcd.fillScreen(0x101018); // 深空蓝黑底色
    lastActivityTime = millis();
    setState(UI_STATE_CONNECTING);
}

void DisplayUI::notifyActivity() {
    lastActivityTime = millis();
    targetBrightness = 200;
}

void DisplayUI::sleepDisplay() {
    lcd.setBrightness(0);
    lcd.sleep();
}

void DisplayUI::nextDashboardPage() {
    notifyActivity();
    dashboardPage = (dashboardPage + 1) % 5;
    if (currentState == UI_STATE_DASHBOARD) {
        renderDashboard();
    }
}

void DisplayUI::prevDashboardPage() {
    notifyActivity();
    dashboardPage = (dashboardPage + 4) % 5;
    if (currentState == UI_STATE_DASHBOARD) {
        renderDashboard();
    }
}

void DisplayUI::togglePomodoro() {
    notifyActivity();
    pomodoroRunning = !pomodoroRunning;
    lastPomodoroTick = millis();
    if (currentState == UI_STATE_DASHBOARD && dashboardPage == 3) {
        renderDashboard();
    }
}

void DisplayUI::resetPomodoro() {
    notifyActivity();
    pomodoroRunning = false;
    pomodoroRemainingSec = 25 * 60;
    if (currentState == UI_STATE_DASHBOARD && dashboardPage == 3) {
        renderDashboard();
    }
}

void DisplayUI::updateWeather(const String& weather, const String& temp, const String& aqi) {
    weatherDesc = weather;
    tempStr = temp;
    aqiStr = aqi;
    if (currentState == UI_STATE_DASHBOARD && dashboardPage == 4) {
        renderDashboard();
    }
}

void DisplayUI::triggerFindAlert() {
    notifyActivity();
    for (int i = 0; i < 3; i++) {
        lcd.fillScreen(0xFFFFFF);
        audio.playTone(TONE_ALERT);
        delay(100);
        lcd.fillScreen(0xFFD600);
        delay(100);
    }
    showAlert("🔍 正在寻机", "收到飞书寻机指令！设备位置已标记", "danger");
}

void DisplayUI::setState(UiState state) {
    notifyActivity();
    if (currentState == state && state != UI_STATE_DASHBOARD) return;
    currentState = state;
    lcd.fillScreen(0x101018);
    
    switch (currentState) {
        case UI_STATE_CONNECTING: renderConnecting(); break;
        case UI_STATE_DASHBOARD:  renderDashboard();  break;
        case UI_STATE_LISTENING:  renderListening();  break;
        case UI_STATE_THINKING:   renderThinking();   break;
        case UI_STATE_SPEAKING:   renderSpeaking();   break;
        case UI_STATE_ALERT:      renderAlert();      break;
        case UI_STATE_CONFIRM_2FA: render2FA();       break;
        case UI_STATE_OTA:        renderOTA();        break;
        default: break;
    }
}

void DisplayUI::updateDashboard(const String& project, const String& time_str, int clients, const String& status) {
    currentProject = project;
    currentTimeStr = time_str;
    currentStatus = status;
    if (currentState == UI_STATE_DASHBOARD) {
        renderDashboard();
    }
}

void DisplayUI::setThinkingText(const String& text) {
    notifyActivity();
    subtitleText = text;
    if (currentState == UI_STATE_THINKING) {
        renderThinking();
    }
}

void DisplayUI::setSpeakingText(const String& text) {
    notifyActivity();
    subtitleText = text;
    if (currentState == UI_STATE_SPEAKING) {
        renderSpeaking();
    }
}

void DisplayUI::showAlert(const String& title, const String& content, const String& level) {
    notifyActivity();
    alertTitle = title;
    alertContent = content;
    alertLevel = level;
    alertStartTime = millis();
    setState(UI_STATE_ALERT);
}

void DisplayUI::show2FA(const String& actionId, const String& title, const String& details) {
    notifyActivity();
    twoFAActionId = actionId;
    twoFATitle = title;
    twoFADetails = details;
    setState(UI_STATE_CONFIRM_2FA);
}

void DisplayUI::showOTAProgress(int percent) {
    notifyActivity();
    otaPercent = constrain(percent, 0, 100);
    if (currentState != UI_STATE_OTA) {
        setState(UI_STATE_OTA);
    } else {
        renderOTA();
    }
}

// 顶部全功能状态栏
void DisplayUI::renderStatusBar() {
    lcd.fillRect(0, 0, 240, 26, 0x181825);
    
    // 1. 飞书在线指示灯
    lcd.fillCircle(12, 13, 4, 0x00E676);
    lcd.setTextSize(1);
    lcd.setTextColor(0x9ECE6A, 0x181825);
    lcd.setTextDatum(ML_DATUM);
    lcd.drawString("LINK", 22, 13);

    // 2. 时间
    lcd.setTextColor(0xFFFFFF, 0x181825);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString(currentTimeStr, 110, 13);

    // 3. Wi-Fi RSSI 信号格
    int rssi = WiFi.RSSI();
    bool wifiOk = (WiFi.status() == WL_CONNECTED);
    int bars = 0;
    if (wifiOk) {
        if (rssi >= -60) bars = 4;
        else if (rssi >= -72) bars = 3;
        else if (rssi >= -85) bars = 2;
        else bars = 1;
    }
    int wifiX = 160;
    for (int b = 1; b <= 4; b++) {
        int barH = b * 3;
        uint16_t c = (b <= bars) ? 0x00E5FF : 0x383C4A;
        lcd.fillRect(wifiX + (b * 4), 18 - barH, 3, barH, c);
    }

    // 4. CW2017 电池状态与百分比
    uint8_t pct = battery.getPercent();
    bool charging = battery.isCharging();
    int batX = 188;
    int batY = 6;
    lcd.drawRoundRect(batX, batY, 26, 14, 2, 0x7AA2F7);
    lcd.fillRect(batX + 26, batY + 3, 2, 8, 0x7AA2F7);
    
    int fillW = map(constrain(pct, 0, 100), 0, 100, 0, 22);
    uint16_t batColor = (pct > 40) ? 0x00E676 : ((pct > 15) ? 0xFFD600 : 0xFF5252);
    if (fillW > 0) {
        lcd.fillRect(batX + 2, batY + 2, fillW, 10, batColor);
    }
    
    lcd.setTextDatum(MR_DATUM);
    lcd.setTextSize(1);
    if (charging) {
        lcd.setTextColor(0xFFD600, 0x181825);
        lcd.drawString("CHG", batX - 2, 13);
    } else {
        lcd.setTextColor(0xCAD3F5, 0x181825);
        lcd.drawString(String(pct) + "%", batX - 2, 13);
    }
}

// 灵动拟人化表情头像
void DisplayUI::renderAvatarFace(int cx, int cy, const char* mood) {
    lcd.fillRoundRect(cx - 50, cy - 35, 100, 70, 16, 0x1F2335);
    lcd.drawRoundRect(cx - 50, cy - 35, 100, 70, 16, 0x3B4261);

    bool blink = (animFrame % 60 >= 56);

    if (strcmp(mood, "idle") == 0) {
        if (blink) {
            lcd.fillRect(cx - 28, cy - 2, 18, 4, 0x00E5FF);
            lcd.fillRect(cx + 10, cy - 2, 18, 4, 0x00E5FF);
        } else {
            lcd.fillRoundRect(cx - 28, cy - 14, 18, 28, 8, 0x00E5FF);
            lcd.fillRoundRect(cx + 10, cy - 14, 18, 28, 8, 0x00E5FF);
            lcd.fillCircle(cx - 24, cy - 8, 3, 0xFFFFFF);
            lcd.fillCircle(cx + 14, cy - 8, 3, 0xFFFFFF);
        }
    } else if (strcmp(mood, "listening") == 0) {
        lcd.fillRoundRect(cx - 30, cy - 16, 20, 32, 9, 0x00E5FF);
        lcd.fillRoundRect(cx + 10, cy - 16, 20, 32, 9, 0x00E5FF);
        lcd.fillCircle(cx - 20, cy - 2, 4, 0xFFFFFF);
        lcd.fillCircle(cx + 20, cy - 2, 4, 0xFFFFFF);
    } else if (strcmp(mood, "thinking") == 0) {
        float rot = (animFrame * 0.15f);
        int eyeOffX = (int)(cos(rot) * 4);
        int eyeOffY = (int)(sin(rot) * 4);
        lcd.fillRoundRect(cx - 28 + eyeOffX, cy - 10 + eyeOffY, 18, 20, 6, 0xBB9AF7);
        lcd.fillRoundRect(cx + 10 + eyeOffX, cy - 10 + eyeOffY, 18, 20, 6, 0xBB9AF7);
    } else if (strcmp(mood, "speaking") == 0) {
        lcd.fillRoundRect(cx - 28, cy - 14, 18, 22, 6, 0x00E676);
        lcd.fillRoundRect(cx + 10, cy - 14, 18, 22, 6, 0x00E676);
        int mouthH = 4 + (animFrame % 4) * 3;
        lcd.fillRoundRect(cx - 12, cy + 14, 24, mouthH, 3, 0x00E676);
    }
}

void DisplayUI::drawWaveform(int level) {
    if (currentState != UI_STATE_LISTENING) return;
    
    int baseX = 24;
    int baseY = 240;
    int barW = 18;
    int gap = 6;
    
    for (int i = 0; i < 8; i++) {
        int phase = (animFrame * 2 + i * 3) % 10;
        int dynamicH = map(level, 0, 100, 6, 65) + ((i % 2 == 0) ? phase * 3 : -phase * 2);
        dynamicH = constrain(dynamicH, 6, 80);
        
        int x = baseX + i * (barW + gap);
        lcd.fillRect(x, baseY - 80, barW, 80, 0x101018);
        uint16_t col = (i < 4) ? 0x00E5FF : 0x7AA2F7;
        lcd.fillRoundRect(x, baseY - dynamicH, barW, dynamicH, 4, col);
    }
}

void DisplayUI::renderConnecting() {
    renderStatusBar();
    renderAvatarFace(120, 90, "thinking");

    lcd.setTextColor(0x00E5FF, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("FoloToy AI", 120, 150);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xAAAAAA, 0x101018);
    lcd.drawString("Antigravity Passport", 120, 180);
    
    lcd.fillRoundRect(30, 215, 180, 8, 4, 0x222233);
    int barLen = (animFrame * 12) % 180;
    lcd.fillRoundRect(30, 215, barLen, 8, 4, 0x00E5FF);
    
    lcd.drawString("Auto UDP Discovery: 8765", 120, 245);
    lcd.drawString("Connecting to Gateway...", 120, 265);
}

void DisplayUI::renderDashboard() {
    renderStatusBar();

    if (dashboardPage == 0) {
        renderDashboardPage0();
    } else if (dashboardPage == 1) {
        renderDashboardPage1();
    } else if (dashboardPage == 2) {
        renderDashboardPage2();
    } else if (dashboardPage == 3) {
        renderDashboardPage3();
    } else {
        renderDashboardPage4();
    }

    lcd.fillRect(0, 292, 240, 28, 0x181824);
    lcd.setTextColor(0x7DCFFF, 0x181824);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(1);
    String pageIndicator = "[" + String(dashboardPage + 1) + "/5] ";
    if (dashboardPage == 3) {
        lcd.drawString(pageIndicator + "[OK: Start/Pause] [Down: Page]", 120, 306);
    } else if (dashboardPage == 4) {
        lcd.drawString(pageIndicator + "[Flip Clock] [Down: Next Page]", 120, 306);
    } else {
        lcd.drawString(pageIndicator + "[Hold OK: Talk] [Up/Down: Page]", 120, 306);
    }
}

// 页面 0: 飞书双链工作区看板
void DisplayUI::renderDashboardPage0() {
    renderAvatarFace(120, 72, "idle");

    lcd.fillRoundRect(12, 115, 216, 75, 8, 0x1F2335);
    lcd.drawRoundRect(12, 115, 216, 75, 8, 0x3B4261);
    
    lcd.setTextColor(0x7AA2F7, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    lcd.setTextSize(1);
    lcd.drawString("ACTIVE WORKSPACE", 24, 125);
    
    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.setTextSize(2);
    lcd.drawString(currentProject, 24, 142);
    
    lcd.setTextColor(0x9ECE6A, 0x1F2335);
    lcd.setTextSize(1);
    lcd.drawString("Dual-Link Engine: READY", 24, 168);

    lcd.fillRoundRect(12, 198, 216, 85, 8, 0x1F2335);
    lcd.drawRoundRect(12, 198, 216, 85, 8, 0x3B4261);
    
    lcd.setTextColor(0xBB9AF7, 0x1F2335);
    lcd.drawString("FEISHU SMART DUAL-LINK", 24, 208);
    
    lcd.setTextColor(0xC0CAF5, 0x1F2335);
    lcd.drawString("• Voice ASR : ADPCM 4:1 Stream", 24, 226);
    lcd.drawString("• TTS Output: Edge-TTS Native", 24, 244);
    lcd.drawString("• Bot Status: " + currentStatus, 24, 262);
}

// 页面 1: 硬件诊断与电量监控 (CW2017 + ESP32-C3)
void DisplayUI::renderDashboardPage1() {
    lcd.fillRoundRect(12, 36, 216, 248, 8, 0x1F2335);
    lcd.drawRoundRect(12, 36, 216, 248, 8, 0x3B4261);

    lcd.setTextColor(0x00E5FF, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    lcd.setTextSize(1);
    lcd.drawString("HARDWARE DIAGNOSTICS", 24, 48);

    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    int y = 72;
    int dy = 22;

    lcd.drawString("Battery Gauge: CW2017 (0x63)", 24, y); y += dy;
    lcd.drawString("Battery SOC  : " + String(battery.getPercent()) + "%", 24, y); y += dy;
    lcd.drawString("Cell Voltage : " + String(battery.getVoltageMv()) + " mV", 24, y); y += dy;
    lcd.drawString("Power State  : " + String(battery.isCharging() ? "Charging" : "Discharging"), 24, y); y += dy;

    uint32_t freeHeap = esp_get_free_heap_size() / 1024;
    lcd.drawString("Free Heap    : " + String(freeHeap) + " KB", 24, y); y += dy;
    lcd.drawString("CPU Freq     : 160 MHz (RISC-V)", 24, y); y += dy;
    lcd.drawString("Wi-Fi IP     : " + WiFi.localIP().toString(), 24, y); y += dy;
    lcd.drawString("Wi-Fi RSSI   : " + String(WiFi.RSSI()) + " dBm", 24, y); y += dy;
    lcd.drawString("Uptime       : " + String(millis() / 1000) + "s", 24, y);
}

// 页面 2: 飞书待办与通知详情流
void DisplayUI::renderDashboardPage2() {
    lcd.fillRoundRect(12, 36, 216, 248, 8, 0x1F2335);
    lcd.drawRoundRect(12, 36, 216, 248, 8, 0x3B4261);

    lcd.setTextColor(0xBB9AF7, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    lcd.setTextSize(1);
    lcd.drawString("FEISHU NOTIFICATIONS", 24, 48);

    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.drawString("Recent Card Feed:", 24, 72);

    lcd.setTextColor(0xCAD3F5, 0x1F2335);
    lcd.drawString("• Bitable Quick Capture: Ready", 24, 98);
    lcd.drawString("• Meeting Alert: Auto Pushed", 24, 120);
    lcd.drawString("• Action Macro: Double-Click", 24, 142);
    lcd.drawString("• Barge-in Interrupt Supported", 24, 164);

    lcd.fillRoundRect(24, 195, 192, 70, 6, 0x181825);
    lcd.setTextColor(0x7DCFFF, 0x181825);
    lcd.drawString("Voice Whisper Ready:", 32, 205);
    lcd.setTextColor(0x9ECE6A, 0x181825);
    lcd.drawString("Press & Hold [OK] to talk", 32, 225);
    lcd.drawString("Release to submit to Agent", 32, 243);
}

// 页面 3: 随身独立番茄钟 (Pomodoro Focus)
void DisplayUI::renderDashboardPage3() {
    lcd.fillRoundRect(12, 36, 216, 248, 8, 0x1F2335);
    lcd.drawRoundRect(12, 36, 216, 248, 8, 0x3B4261);

    lcd.setTextColor(0xFF7043, 0x1F2335);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("POMODORO", 120, 60);

    int cx = 120;
    int cy = 145;
    int radius = 55;
    lcd.drawCircle(cx, cy, radius, 0x3B4261);
    
    float progress = (float)(1500 - pomodoroRemainingSec) / 1500.0f;
    int endAngle = (int)(progress * 360.0f);
    for (int a = 0; a < endAngle; a += 4) {
        float rad = (a - 90) * (PI / 180.0f);
        int px = cx + cos(rad) * radius;
        int py = cy + sin(rad) * radius;
        lcd.fillCircle(px, py, 2, 0xFF7043);
    }

    int minutes = pomodoroRemainingSec / 60;
    int seconds = pomodoroRemainingSec % 60;
    char timeBuffer[10];
    snprintf(timeBuffer, sizeof(timeBuffer), "%02d:%02d", minutes, seconds);

    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.setTextSize(3);
    lcd.drawString(timeBuffer, cx, cy);

    lcd.setTextSize(1);
    if (pomodoroRunning) {
        lcd.setTextColor(0x00E676, 0x1F2335);
        lcd.drawString("FOCUSING...", cx, cy + 80);
    } else {
        lcd.setTextColor(0xFFD600, 0x1F2335);
        lcd.drawString("PAUSED (Short OK: Start)", cx, cy + 80);
    }
}

// 页面 4: 复古全屏翻页天气时钟 (Flip Clock & Weather)
void DisplayUI::renderDashboardPage4() {
    // 顶部天气徽标卡片
    lcd.fillRoundRect(12, 34, 216, 60, 8, 0x1F2335);
    lcd.drawRoundRect(12, 34, 216, 60, 8, 0x3B4261);

    lcd.setTextColor(0xFFD600, 0x1F2335);
    lcd.setTextDatum(ML_DATUM);
    lcd.setTextSize(2);
    lcd.drawString(weatherDesc, 26, 64);

    lcd.setTextColor(0x00E5FF, 0x1F2335);
    lcd.setTextDatum(MR_DATUM);
    lcd.drawString(tempStr + " | " + aqiStr, 214, 64);

    // 中间复古翻页时钟大卡片 (小时和分钟卡片)
    String hh = currentTimeStr.length() >= 2 ? currentTimeStr.substring(0, 2) : "12";
    String mm = currentTimeStr.length() >= 5 ? currentTimeStr.substring(3, 5) : "00";

    int cardW = 96;
    int cardH = 100;
    int yCard = 105;

    // 小时卡片
    lcd.fillRoundRect(18, yCard, cardW, cardH, 8, 0x24283B);
    lcd.drawRoundRect(18, yCard, cardW, cardH, 8, 0x414868);
    lcd.drawFastHLine(18, yCard + (cardH / 2), cardW, 0x181825); // 翻页折痕

    lcd.setTextColor(0xFFFFFF, 0x24283B);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(5);
    lcd.drawString(hh, 18 + (cardW / 2), yCard + (cardH / 2));

    // 分钟卡片
    lcd.fillRoundRect(126, yCard, cardW, cardH, 8, 0x24283B);
    lcd.drawRoundRect(126, yCard, cardW, cardH, 8, 0x414868);
    lcd.drawFastHLine(126, yCard + (cardH / 2), cardW, 0x181825); // 翻页折痕

    lcd.drawString(mm, 126 + (cardW / 2), yCard + (cardH / 2));

    // 底部日期状态卡片
    lcd.fillRoundRect(12, 218, 216, 66, 8, 0x1F2335);
    lcd.drawRoundRect(12, 218, 216, 66, 8, 0x3B4261);

    lcd.setTextColor(0x9ECE6A, 0x1F2335);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(1);
    lcd.drawString("DESKTOP COMPANION MODE", 120, 236);
    lcd.setTextColor(0xCAD3F5, 0x1F2335);
    lcd.drawString("Charging Dock Synced", 120, 258);
}

void DisplayUI::renderListening() {
    renderStatusBar();
    renderAvatarFace(120, 80, "listening");

    lcd.setTextColor(0x00E5FF, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("LISTENING...", 120, 135);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xAAAAAA, 0x101018);
    lcd.drawString("Recording ADPCM Audio Stream", 120, 160);
    
    lcd.setTextColor(0xFF9800, 0x101018);
    lcd.drawString("Release [OK] to Send to Feishu", 120, 275);
}

void DisplayUI::renderThinking() {
    renderStatusBar();
    renderAvatarFace(120, 80, "thinking");

    lcd.setTextColor(0xBB9AF7, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("THINKING...", 120, 135);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xC0CAF5, 0x101018);
    lcd.drawString("Antigravity Agent Processing", 120, 160);
    
    int centerX = 120;
    int centerY = 205;
    for (int i = 0; i < 8; i++) {
        float angle = i * (PI / 4.0f) + (animFrame * 0.25f);
        int x = centerX + cos(angle) * 32;
        int y = centerY + sin(angle) * 32;
        uint16_t c = (i == 0) ? 0x00E5FF : ((i < 3) ? 0xBB9AF7 : 0x3B4261);
        lcd.fillCircle(x, y, 4, c);
    }
    
    if (subtitleText.length() > 0) {
        lcd.setTextColor(0x7DCFFF, 0x101018);
        lcd.drawString(subtitleText.substring(0, 20), 120, 260);
    }
}

void DisplayUI::renderSpeaking() {
    renderStatusBar();
    renderAvatarFace(120, 75, "speaking");

    lcd.setTextColor(0x00E676, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("SPEAKING...", 120, 125);
    
    lcd.fillRoundRect(16, 145, 208, 120, 8, 0x1F2335);
    lcd.drawRoundRect(16, 145, 208, 120, 8, 0x00E676);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    
    int y = 156;
    for (int i = 0; i < subtitleText.length(); i += 18) {
        if (y > 245) break;
        lcd.drawString(subtitleText.substring(i, min((int)subtitleText.length(), i + 18)), 26, y);
        y += 18;
    }
    
    lcd.setTextColor(0xFF9800, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("Press OK or Down to Interrupt", 120, 280);
}

void DisplayUI::renderAlert() {
    uint16_t headerColor = (alertLevel == "danger") ? 0xFF5252 : 0xFFB300;
    
    lcd.fillRoundRect(10, 35, 220, 250, 10, 0x221515);
    lcd.drawRoundRect(10, 35, 220, 250, 10, headerColor);
    
    lcd.setTextColor(headerColor, 0x221515);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString(alertTitle, 120, 65);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xFFFFFF, 0x221515);
    lcd.setTextDatum(TL_DATUM);
    
    int y = 100;
    for (int i = 0; i < alertContent.length(); i += 16) {
        if (y > 235) break;
        lcd.drawString(alertContent.substring(i, min((int)alertContent.length(), i + 16)), 24, y);
        y += 20;
    }
    
    lcd.setTextColor(0xAAAAAA, 0x221515);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("Auto-dismissing in 5s...", 120, 265);
}

// 物理 2FA 高危确认界面
void DisplayUI::render2FA() {
    lcd.fillRoundRect(10, 30, 220, 255, 10, 0x2B1515);
    lcd.drawRoundRect(10, 30, 220, 255, 10, 0xFF5252);

    lcd.setTextColor(0xFF5252, 0x2B1515);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("PHYSICAL 2FA", 120, 55);

    lcd.setTextSize(1);
    lcd.setTextColor(0xFFD600, 0x2B1515);
    lcd.drawString("High-Risk Action Request", 120, 80);

    lcd.fillRoundRect(20, 100, 200, 95, 6, 0x1F1212);
    lcd.setTextColor(0xFFFFFF, 0x1F1212);
    lcd.setTextDatum(TL_DATUM);
    lcd.drawString("Task: " + twoFATitle, 30, 112);
    
    int y = 135;
    for (int i = 0; i < twoFADetails.length(); i += 18) {
        if (y > 185) break;
        lcd.drawString(twoFADetails.substring(i, min((int)twoFADetails.length(), i + 18)), 30, y);
        y += 18;
    }

    lcd.fillRoundRect(22, 210, 90, 36, 6, 0x00E676);
    lcd.setTextColor(0x000000, 0x00E676);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("[Up] Approve", 67, 228);

    lcd.fillRoundRect(128, 210, 90, 36, 6, 0xFF5252);
    lcd.setTextColor(0xFFFFFF, 0xFF5252);
    lcd.drawString("[Down] Reject", 173, 228);

    lcd.setTextColor(0xAAAAAA, 0x2B1515);
    lcd.drawString("Waiting for hardware button...", 120, 268);
}

void DisplayUI::renderOTA() {
    lcd.fillRect(0, 0, 240, 320, 0x101018);
    
    lcd.setTextColor(0x00E5FF, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("OTA UPDATING", 120, 100);

    lcd.setTextSize(1);
    lcd.setTextColor(0xAAAAAA, 0x101018);
    lcd.drawString("Wireless Firmware Upgrade", 120, 130);

    lcd.fillRoundRect(20, 170, 200, 16, 8, 0x222233);
    int barW = map(otaPercent, 0, 100, 0, 196);
    if (barW > 0) {
        lcd.fillRoundRect(22, 172, barW, 12, 6, 0x00E5FF);
    }

    lcd.setTextColor(0xFFFFFF, 0x101018);
    lcd.setTextSize(2);
    lcd.drawString(String(otaPercent) + "%", 120, 215);

    lcd.setTextSize(1);
    lcd.setTextColor(0xFF9800, 0x101018);
    lcd.drawString("Do NOT power off device!", 120, 255);
}

void DisplayUI::updateBacklight() {
    unsigned long idle = millis() - lastActivityTime;
    if (idle > 120000) {
        targetBrightness = 0;
    } else if (idle > 45000) {
        targetBrightness = 40;
    } else {
        targetBrightness = 200;
    }

    if (currentBrightness != targetBrightness) {
        if (currentBrightness < targetBrightness) {
            currentBrightness = min((int)targetBrightness, (int)currentBrightness + 8);
        } else {
            currentBrightness = max((int)targetBrightness, (int)currentBrightness - 8);
        }
        lcd.setBrightness(currentBrightness);
    }
}

void DisplayUI::loop() {
    unsigned long now = millis();
    updateBacklight();

    // 智能桌面模式：如果在充电中且超过 45 秒无按键操作，自动切换到翻页天气时钟
    if (battery.isCharging() && (now - lastActivityTime > 45000) && currentState == UI_STATE_DASHBOARD && dashboardPage != 4) {
        dashboardPage = 4;
        renderDashboard();
    }

    // 独立番茄钟倒计时步进
    if (pomodoroRunning && (now - lastPomodoroTick >= 1000)) {
        lastPomodoroTick = now;
        if (pomodoroRemainingSec > 0) {
            pomodoroRemainingSec--;
            if (currentState == UI_STATE_DASHBOARD && dashboardPage == 3) {
                renderDashboardPage3();
            }
        } else {
            pomodoroRunning = false;
            pomodoroRemainingSec = 25 * 60;
            showAlert("🎉 专注达成！", "恭喜完成一个 25 分钟番茄时段，建议休息 5 分钟！", "info");
        }
    }

    if (now - lastAnimTime > 80) {
        lastAnimTime = now;
        animFrame++;
        
        if (currentState == UI_STATE_THINKING) {
            renderThinking();
        } else if (currentState == UI_STATE_DASHBOARD && dashboardPage == 0 && (animFrame % 60 >= 56 || animFrame % 60 == 0)) {
            renderAvatarFace(120, 72, "idle");
        }
    }
    
    if (currentState == UI_STATE_ALERT && (now - alertStartTime > 5000)) {
        setState(UI_STATE_DASHBOARD);
    }
}
