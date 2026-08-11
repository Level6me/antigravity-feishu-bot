# 🍓 树莓派 GPIO 状态灯指示插件 (rpi_gpio_status PRO)

通过树莓派 40Pin 物理 GPIO 引脚控制红绿黄三色 LED 灯，实时展现 AI 任务的就绪、运行中、成功及异常状态。

## ✨ 核心特性

- **🔴 状态实时映射**：
  - 🟢 **绿灯 (GPIO 17)**：系统就绪 / 任务成功完成
  - 🟡 **黄灯 (GPIO 27)**：AI 思考中 / 任务执行中
  - 🔴 **红灯 (GPIO 22)**：异常告警 / 执行失败
- **✨ 跑马灯自检**：提供 `/light test` 硬件自检指令与面板按钮，一键巡检三色 LED 连线。
- **⚡ 闪烁警报**：支持闪烁模式 `/light blink [red|yellow|green]`，用于长任务追踪或警报提醒。
- **🎛️ 飞书交互卡片**：在飞书群中提供全功能控制面板与按钮组。

## 💬 指令列表

- `/light` 或 `/led`：发送交互控制卡片
- `/light green`：点亮绿灯 (就绪)
- `/light yellow`：点亮黄灯 (运行)
- `/light red`：点亮红灯 (错误)
- `/light test` 或 `/light 自检`：触发跑马灯流水硬件自检
- `/light blink yellow`：开启黄灯闪烁警报
- `/light off`：关闭所有指示灯
