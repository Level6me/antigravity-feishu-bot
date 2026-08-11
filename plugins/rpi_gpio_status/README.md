# 💡 树莓派 LED 状态指示灯插件 (RPi Status Light Plugin)

本插件为 `antigravity-feishu-bot` 提供了针对树莓派 (Raspberry Pi) 的硬件级运行状态可视化解决方案。通过物理 GPIO 引脚驱动**红、黄、绿**三色 LED 灯，实时呈现 AI 引擎的工作状态。

---

## 🚦 状态灯对应逻辑

| 指示灯颜色 | 代表状态 | 触发时机 | 硬件行为 |
| :--- | :--- | :--- | :--- |
| 🟢 **绿灯 (Green)** | **系统就绪 (Idle / Standby)** | AI 空闲、任务执行完毕 | 常亮 |
| 🟡 **黄灯 (Yellow)** | **任务执行中 (Running / Thinking)** | 大模型推理、命令行/工具调用中 | 常亮 |
| 🔴 **红灯 (Red)** | **异常/错误 (Error / Failed)** | 任务抛出异常、超时或报错 | 常亮 |

---

## 🔌 硬件接线示意 (Hardware Wiring)

使用标准 330Ω 限流电阻与 3 颗 LED 灯，引脚默认使用 **BCM 编码**：

| LED 颜色 | GPIO 引脚 (BCM) | 树莓派物理 Pin | 限流电阻配置 |
| :--- | :--- | :--- | :--- |
| 🔴 **红灯** | `GPIO 17` | Pin 11 | 330Ω 电阻 -> GND |
| 🟡 **黄灯** | `GPIO 27` | Pin 13 | 330Ω 电阻 -> GND |
| 🟢 **绿灯** | `GPIO 22` | Pin 15 | 330Ω 电阻 -> GND |
| ⚪ **GND (接地)** | `GND` | Pin 6 / Pin 9 / Pin 14 | -- |

---

## ⚙️ 配置文件说明 (`config.json`)

```json
{
  "enabled": true,
  "hardware_mode": "auto",
  "gpio_pins": {
    "red": 17,
    "yellow": 27,
    "green": 22
  },
  "blink_interval_sec": 0.3
}
```

---

## 💬 飞书指令支持

- `/light` - 查看 LED 当前工作模式与引脚映射表
- `/light green` - 手动点亮绿灯 (测试系统就绪)
- `/light yellow` - 手动点亮黄灯 (测试运行状态)
- `/light red` - 手动点亮红灯 (测试错误状态)
- `/light off` - 手动关闭所有指示灯
