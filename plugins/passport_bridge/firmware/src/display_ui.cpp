#include "display_ui.h"

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
      alertStartTime(0),
      lastAnimTime(0),
      animFrame(0) {}

void DisplayUI::init() {
    lcd.init();
    lcd.setRotation(0); // 竖屏 240x320
    lcd.setBrightness(200);
    lcd.fillScreen(0x101018); // 深空蓝黑底色
    setState(UI_STATE_CONNECTING);
}

void DisplayUI::setState(UiState state) {
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
    subtitleText = text;
    if (currentState == UI_STATE_THINKING) {
        renderThinking();
    }
}

void DisplayUI::setSpeakingText(const String& text) {
    subtitleText = text;
    if (currentState == UI_STATE_SPEAKING) {
        renderSpeaking();
    }
}

void DisplayUI::showAlert(const String& title, const String& content, const String& level) {
    alertTitle = title;
    alertContent = content;
    alertLevel = level;
    alertStartTime = millis();
    setState(UI_STATE_ALERT);
}

void DisplayUI::drawWaveform(int level) {
    if (currentState != UI_STATE_LISTENING) return;
    int centerX = 120;
    int centerY = 160;
    lcd.fillCircle(centerX, centerY, 50, 0x101018);
    // 动态波形圆环
    int r = map(constrain(level, 0, 100), 0, 100, 20, 50);
    lcd.drawCircle(centerX, centerY, r, 0x00E5FF);
    lcd.drawCircle(centerX, centerY, r + 2, 0x00B0FF);
}

void DisplayUI::renderConnecting() {
    lcd.setTextColor(0x00E5FF, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("FoloToy AI", 120, 100);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xAAAAAA, 0x101018);
    lcd.drawString("Antigravity Passport", 120, 130);
    
    lcd.fillRoundRect(30, 180, 180, 6, 3, 0x333333);
    lcd.fillRoundRect(30, 180, 90, 6, 3, 0x00E5FF);
    
    lcd.drawString("Searching Host Gateway...", 120, 210);
    lcd.drawString("UDP Discovery: 8765", 120, 230);
}

void DisplayUI::renderDashboard() {
    // 顶部状态栏
    lcd.fillRect(0, 0, 240, 28, 0x1E1E2E);
    lcd.setTextColor(0x00E676, 0x1E1E2E);
    lcd.setTextDatum(TL_DATUM);
    lcd.drawString("ONLINE", 10, 8);
    
    lcd.setTextColor(0xFFFFFF, 0x1E1E2E);
    lcd.setTextDatum(TR_DATUM);
    lcd.drawString(currentTimeStr, 230, 8);

    // 中部：当前工作项目卡片
    lcd.fillRoundRect(12, 40, 216, 85, 8, 0x1F2335);
    lcd.drawRoundRect(12, 40, 216, 85, 8, 0x3B4261);
    
    lcd.setTextColor(0x7AA2F7, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    lcd.setTextSize(1);
    lcd.drawString("ACTIVE WORKSPACE", 24, 52);
    
    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.setTextSize(2);
    lcd.drawString(currentProject, 24, 72);
    
    lcd.setTextColor(0x9ECE6A, 0x1F2335);
    lcd.setTextSize(1);
    lcd.drawString("Agent Engine: READY", 24, 102);

    // 下部：控制状态指标卡片
    lcd.fillRoundRect(12, 135, 216, 120, 8, 0x1F2335);
    lcd.drawRoundRect(12, 135, 216, 120, 8, 0x3B4261);
    
    lcd.setTextColor(0xBB9AF7, 0x1F2335);
    lcd.drawString("FEISHU DUAL-LINK", 24, 147);
    
    lcd.setTextColor(0xC0CAF5, 0x1F2335);
    lcd.drawString("• Voice ASR : Whisper Enabled", 24, 170);
    lcd.drawString("• TTS Output: Edge-TTS Native", 24, 190);
    lcd.drawString("• Mesh Sync : Connected", 24, 210);
    lcd.drawString("• Status    : " + currentStatus, 24, 230);

    // 底部快捷按键提示
    lcd.fillRect(0, 290, 240, 30, 0x181824);
    lcd.setTextColor(0x7DCFFF, 0x181824);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("[Hold OK: Talk] [Up: Proj] [Down: Stop]", 120, 305);
}

void DisplayUI::renderListening() {
    lcd.setTextColor(0x00E5FF, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("LISTENING...", 120, 60);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xAAAAAA, 0x101018);
    lcd.drawString("Recording I2S Audio Stream", 120, 90);
    
    // 中间动态声纹圆环
    drawWaveform(40);
    
    lcd.setTextColor(0xFF9800, 0x101018);
    lcd.drawString("Release [OK] to Send to Feishu", 120, 260);
}

void DisplayUI::renderThinking() {
    lcd.setTextColor(0xBB9AF7, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("THINKING...", 120, 60);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xC0CAF5, 0x101018);
    lcd.drawString("Antigravity Agent Processing", 120, 90);
    
    // 转圈动画位置
    int centerX = 120;
    int centerY = 160;
    for (int i = 0; i < 8; i++) {
        float angle = i * (PI / 4.0) + (animFrame * 0.2);
        int x = centerX + cos(angle) * 35;
        int y = centerY + sin(angle) * 35;
        uint16_t c = (i == 0) ? 0xBB9AF7 : 0x3B4261;
        lcd.fillCircle(x, y, 4, c);
    }
    
    if (subtitleText.length() > 0) {
        lcd.setTextColor(0x00E5FF, 0x101018);
        lcd.drawString(subtitleText.substring(0, 20), 120, 240);
    }
}

void DisplayUI::renderSpeaking() {
    lcd.setTextColor(0x00E676, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString("SPEAKING...", 120, 50);
    
    lcd.fillRoundRect(16, 90, 208, 170, 8, 0x1F2335);
    lcd.drawRoundRect(16, 90, 208, 170, 8, 0x00E676);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xFFFFFF, 0x1F2335);
    lcd.setTextDatum(TL_DATUM);
    
    // 多行字幕排版
    int y = 105;
    for (int i = 0; i < subtitleText.length(); i += 18) {
        if (y > 230) break;
        lcd.drawString(subtitleText.substring(i, min((int)subtitleText.length(), i + 18)), 26, y);
        y += 20;
    }
    
    lcd.setTextColor(0x7AA2F7, 0x101018);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("Synced with Feishu Card", 120, 285);
}

void DisplayUI::renderAlert() {
    uint16_t headerColor = (alertLevel == "danger") ? 0xFF5252 : 0xFFB300;
    
    lcd.fillRoundRect(10, 40, 220, 240, 10, 0x221515);
    lcd.drawRoundRect(10, 40, 220, 240, 10, headerColor);
    
    lcd.setTextColor(headerColor, 0x221515);
    lcd.setTextDatum(MC_DATUM);
    lcd.setTextSize(2);
    lcd.drawString(alertTitle, 120, 70);
    
    lcd.setTextSize(1);
    lcd.setTextColor(0xFFFFFF, 0x221515);
    lcd.setTextDatum(TL_DATUM);
    
    int y = 110;
    for (int i = 0; i < alertContent.length(); i += 16) {
        if (y > 230) break;
        lcd.drawString(alertContent.substring(i, min((int)alertContent.length(), i + 16)), 24, y);
        y += 20;
    }
    
    lcd.setTextColor(0xAAAAAA, 0x221515);
    lcd.setTextDatum(MC_DATUM);
    lcd.drawString("Auto-dismissing in 5s...", 120, 260);
}

void DisplayUI::loop() {
    unsigned long now = millis();
    if (now - lastAnimTime > 80) {
        lastAnimTime = now;
        animFrame++;
        if (currentState == UI_STATE_THINKING) {
            renderThinking();
        }
    }
    
    if (currentState == UI_STATE_ALERT && (now - alertStartTime > 5000)) {
        setState(UI_STATE_DASHBOARD);
    }
}
