# Antigravity Feishu Bot (飞书大模型机器人)

这是一个基于飞书 (Lark) 和大模型命令行工具 `antigravity` 构建的自动化交互机器人。

它能够监听飞书中的用户消息，自动调用本地大模型进行分析、思考或生成代码，并将结果实时回复到飞书中。

## 🌟 特性

*   **纯异步高并发**：使用 Python `asyncio` 和 `lark-cli`，消息接收与大模型处理完全分离，互不阻塞，支持多人同时对话。
*   **炫酷的动态表情状态展示**：在处理过程中，机器人会像跑马灯一样为您展示 `思考中` (THINKING)、`打字/写代码` (Typing/Mac)、`通讯` (Communicate) 等动态 Emoji，直观感受它的运行进度。
*   **状态自动清理**：进入下一个状态前会自动撤回旧的表情状态，保持聊天界面清爽。
*   **自动处理权限与模型配置**：静默调用大模型并传递指令，无缝集成。

## 🚀 一键部署与运行

本项目推荐使用 `pm2` 进行后台守护进程管理，确保关掉终端后服务依然 24 小时运行。

### 1. 安装环境依赖

确保您的系统中已安装：
*   Python 3.10+
*   Node.js & PM2 (`npm install -g pm2`)
*   [Lark CLI](https://github.com/larksuite/lark-cli) (并完成飞书授权登录)
*   [Antigravity CLI](https://github.com/) (本地大模型交互工具)

### 2. 启动服务

```bash
# 进入项目目录
cd antigravity-feishu-bot

# 使用 PM2 在后台启动机器人服务
pm2 start bot.py --name "feishu-bot" --interpreter python3

# 保存 PM2 进程列表（可选，用于开机自启恢复）
pm2 save
```

### 3. 日常维护命令

*   查看实时运行日志：`pm2 logs feishu-bot`
*   停止机器人服务：`pm2 stop feishu-bot`
*   重启机器人服务：`pm2 restart feishu-bot`
*   查看所有后台服务：`pm2 list`

## ⚙️ 原理说明

1. 脚本使用 `subprocess.Popen` 调用 `lark-cli event consume im.message.receive_v1` 监听飞书实时消息。
2. 收到消息后，分配一个独立的后台协程处理任务。
3. 给收到消息打上 `StatusReading` 的已读表情。
4. 启动一个 Emoji 动画轮播任务（模拟机器人正在执行代码/思考），同时开始调用 `antigravity` 执行实际的模型运算。
5. 模型运算结束（或超时/报错），取消表情轮播动画。
6. 根据成功或失败，打上对应的 ✅ (`DONE`) 或 ❌ (`CrossMark`) 表情。
7. 最后，将模型的文字回复发送回飞书聊天框中。

## 📝 许可证
MIT License
