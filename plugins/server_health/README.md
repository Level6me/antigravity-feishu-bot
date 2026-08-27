# 🖥️ 服务器巡检与健康报告插件 (`server_health`)

`server_health` 是 Antigravity Feishu Bot 的官方系统监控插件，能够实时采集宿主机/服务器的硬件指标与健康状况，并渲染为优雅的飞书交互式可视化卡片。

---

## 📌 功能特性

- 实时获取 CPU 核心数、当前负载率百分比；
- 采集系统物理内存总容量、已用容量与占用百分比；
- 采集根分区 `/` 磁盘空间使用状况；
- 实时计算服务器已连续运行时间 (Uptime)；
- 提供卡片内置 **`🔄 实时刷新`** 交互按钮。

---

## ⚙️ 指令与使用方法

| 斜杠指令 | 参数 | 描述 | 示例 |
| :--- | :--- | :--- | :--- |
| `/sysinfo` | 无 | 弹出服务器系统硬件与健康状态交互卡片 | `/sysinfo` |
| `/health` | 无 | `/sysinfo` 指令的别名快捷方式 | `/health` |

---

## 📋 插件配置与清单 (`manifest.json`)

```json
{
  "id": "server_health",
  "name": "🖥️ 服务器巡检与健康报告",
  "version": "1.0.0",
  "author": "Antigravity",
  "description": "监控 CPU 负载、内存率、磁盘余量，发送 /sysinfo 即可查看",
  "commands": [
    "/sysinfo",
    "/health"
  ],
  "enabled": true
}
```

---

## 🛠️ 架构与底层依赖

- **底层依赖**：调用 Python 标准库及系统原生命令探查；
- **接口回调**：响应 `on_command` 生成初始卡片，通过 `on_card_action` 响应 `refresh_server_health` 交互事件。
