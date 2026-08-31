# ⏱️ 计划任务与后台常驻调度中心 (Cron Scheduler v3.0)

> 基于独立 PM2 守护引擎的高精度秒级倒计时、Cron 周期调度与 AI/运维系统级巡检中心。

---

## 🌟 核心特性

- **🎯 自然语言秒级意图识别**：在飞书对话中直接发送 `一分钟后提醒我喝水`、`每天早上9点提醒站会`、`每隔10分钟巡检服务器`，毫秒级直接创建并反馈确认卡片。
- **⚡ 全功能 Slash 指令**：支持 `/cron`、`/cron add`、`/cron list`、`/cron del`、`/cron run`、`/cron on/off`。
- **🛡️ 独立 PM2 守护进程 (`daemon.py`)**：秒级精准轮询调度，与主机器人完全解耦，不卡顿主事件循环，重启自动恢复计时。
- **🖥️ 多维执行器体系**：
  - `reminder`：消息提醒（喝水、站会、会议、待办）。
  - `shell`：Linux 系统脚本执行（磁盘清理、服务备份、Docker 容器检查）。
  - `ai_agent`：AI 深度巡检报告。
  - `hardware_led`：树莓派 GPIO/LED 硬件状态联动。
- **📊 审计与日志记录**：完整记录每次任务触发时间、执行耗时、成功/失败状态与输出内容。

---

## 📂 项目结构

```text
plugins/cron_scheduler/
├── manifest.json         # 插件元数据 (v3.0.0)
├── config.json           # 插件与 Daemon 配置
├── plugin.py             # 飞书消息管道前端交互组件
├── daemon.py             # 独立常驻调度守护进程 (可由 PM2 纳管)
├── scheduler.py          # 自然语言时间解析与 Croniter 计算引擎
├── executors.py          # 多维任务执行器 (提醒/Shell/AI/硬件)
├── database.py           # SQLite 持久化存储与日志审计
└── README.md             # 使用说明文档
```

---

## 🚀 启动与 PM2 守护配置

在终端中将 `daemon.py` 注册为 PM2 常驻守护服务：

```bash
# 启动守护进程
pm2 start /home/jiang/github/feishu-bot-plugin/plugins/cron_scheduler/daemon.py --name feishu-cron-daemon --interpreter python3

# 查看运行状态与日志
pm2 status
pm2 logs feishu-cron-daemon
```

---

## 💬 常用指令与自然语言示例

### 1. 自然语言直接设定（推荐）
- `一分钟后提醒我喝水`
- `10分钟后检查系统磁盘空间`
- `半小时后提醒我出门`
- `每天早上9点提醒我站会`
- `每天23:30检查数据备份`
- `每隔10分钟巡检一次服务器`

### 2. 命令行精确管理
- `/cron` 或 `/cron list`：查看计划任务面板与状态卡片。
- `/cron add 每日日报 | 0 18 * * * | 汇总今日提交并输出报告`：标准 3 段式添加。
- `/cron del <task_id>`：删除指定计划任务。
- `/cron run <task_id>`：手动立即触发一次执行。
- `/cron on <task_id>` / `/cron off <task_id>`：启用或暂停任务。
