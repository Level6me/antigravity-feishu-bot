# 🍓 树莓派 GPIO 状态灯与蜂鸣器指示插件 (API 网关联动版 v2.2.0)

基于 [pi_led_api](https://github.com/Level6me/pi_led_api) (LED 控制网关) 与 [buzzer-api](http://127.0.0.1:8001) (无源蜂鸣器控制网关) 集中式 HTTP 服务，控制树莓派红绿黄三色 LED 与蜂鸣器提示音，实现与飞书 Bot 任务生命周期的深度全景联动。

---

## 🎯 核心联动机制与视听表现

| 机器人运行阶段 | 视觉灯效 (LED) | 听觉提示音 (Buzzer) | 说明与音效细节 |
| :--- | :--- | :--- | :--- |
| **🚀 OTA 更新成功** | 绿灯常亮 (`success`) | **角色升级** (`level_up`) | `/update confirm` 重启上线后自动触发 |
| **🟡 思考推理阶段** | 黄灯常亮 (`thinking`) | **单哔确认** (`beep`) | 每 10 秒循环提示 AI 正在持续思考 |
| **✨ 工具调用阶段** | 黄灯正弦呼吸 (`breathing`) | **双哔确认** (`two_beeps`) | 每 10 秒循环提示正在调用外部能力 |
| **🟢 任务成功完成** | 绿灯常亮 300s 渐灭 (`success`) | **操作成功** (`success`) | 任务正常交付时单次播放愉悦音效 |
| **🔴 任务异常报错** | 红灯常亮 (`error`) | **操作失败** (`error`) | 执行异常或 `/stop` 中断时触发警告音 |
| **⏹️ 手动全关** | 全部熄灭 (`off`) | 立即停止鸣叫 (`stop`) | 一键静音并关灯 |

---

## 💬 飞书交互指令

| 指令 | 说明 |
| :--- | :--- |
| `/led` 或 `/light` | 弹出飞书 LED 与蜂鸣器统一控制面板卡片（支持预设切换、旋律试听与网关连通性测速） |
| `/stat` | 查看树莓派系统综合健康全景卡片（CPU 温度、内存、系统负载、LED 状态及蜂鸣器状态） |
| `/led test` | 快速测试 LED 与蜂鸣器双网关连通性与网络 RTT 延迟 |
| `/led config` | 进入交互式配置中心 |
| `/led thinking` | 切换为【思考中】(黄灯常亮) |
| `/led breathing` | 切换为【任务执行中】(黄灯正弦呼吸) |
| `/led success` | 切换为【任务完成】(绿灯常亮 + 成功提示音) |
| `/led error` | 切换为【系统异常】(红灯常亮 + 失败提示音) |
| `/led startup` | 触发【开机自检】(绿灯连闪 5 次 + 升级音) |
| `/led off` | 熄灭所有灯光通道并停止蜂鸣器 |

---

## ⚙️ 配置文件说明 (`config.json`)

```json
{
  "enabled": true,
  "api_url": "http://127.0.0.1:8080",
  "api_token": "YOUR_LED_API_TOKEN",
  "buzzer_api_url": "http://127.0.0.1:8001",
  "buzzer_api_token": "YOUR_BUZZER_API_TOKEN",
  "buzzer_enabled": true,
  "buzzer_volume": 25,
  "auto_indicator_enabled": true,
  "success_duration_sec": 300
}
```

