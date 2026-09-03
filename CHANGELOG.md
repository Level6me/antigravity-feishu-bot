# 开发记录与进度追踪 (Development Log)

## [v3.0.0] - 2026-09-03

### 🚀 机器人核心能力重大升级 (v3.0 Major Release)
- **定时调度引擎升级 (Cron Engine v3.0)**：重构底层定时任务调度机制，全面支持高级 Cron 表达式、超时熔断保护与秒级状态流转追踪。
- **系统状态自动化巡检与汇报**：内置系统健康巡检、内存与硬件指标探测，支持周期性定时组装并自动下发结构化交互卡片。
- **微内核插件生态深化**：优化微内核热加载机制，增强多模态交互与事件帧分发容错能力。
- **全指令安全防御加固**：全面落地超时保护与受限目录规避机制，提升长时间运行的稳定性。

## [v2.0.0] - 2026-08-08

### 🚀 架构重大升级 (v2.0 Microkernel Architecture)
- **微内核插件系统**：全面重构系统架构，将 `/cron` (计划任务)、`/memory` (AI记忆)、`/notes` (备忘录)、`/update` (云端更新) 彻底从内核中剥离，转为独立解耦的 Plugin 扩展。
- **全动态指令与双向 AI Hook**：引入 `on_before_ai`、`on_after_ai`、动态指令注册表，打造高可扩展、支持 GitHub 在线增删改查的插件生态。

## [unreleased] - 2026-08-04

### 🐞 修复缺陷 (Bug Fixes)
- **修复卡片按钮回调报错 code:200671**：`lark-oapi` 1.6.8 的 WebSocket 客户端对 `CARD` 类型帧直接丢弃，导致授权卡片等所有交互按钮点击后无回调响应。已在 `main.py` 增加运行时补丁，将 CARD 帧接入事件分发器；`requirements.txt` 同步放开 `lark-oapi>=1.6.8`。

## [v1.2.0] - 2026-07-26

### 工程成熟度
- **Docker 完整交付**：新增 `Dockerfile`、`.dockerignore`，重写 `docker-compose.yml`（含可选 `agy-daemon` profile）。
- **路径可移植**：`ANTIGRAVITY_HOME` / `ANTIGRAVITY_BIN` / `WORKSPACE_ROOT` 可配置；`transcript`、OAuth、global memory 统一走 `config.get_*`。
- **模块拆分**：`main.py` 拆为 `handlers/*` + `app_state.py`；`card_builder` 拆为 `cards/*` 包（保留 `card_builder.CardBuilder` 兼容导入）。
- **agy_daemon 工程化**：使用 config 解析二进制路径，指数退避重启、信号处理、结构化日志。
- **仓库卫生**：移除 `archive_debug/`、`parse_log.py` 等调试残留；更新 `.gitignore`。
- **文档同步**：README / `.env.example` / 帮助卡片与当前 Slash 指令对齐（移除已废弃的 `/role` `/remember`，补充 `/brain` 等）。

## [v1.1.0] - 2026-06-21

### 🚀 新增功能 (Features)
- **纯异步重构**：将飞书事件接收器与大模型处理逻辑完全解耦，引入了 Python 原生的 `asyncio` 协程架构。
- **高并发支持**：实现了多任务后台并发派发，支持在群聊或多人群发场景下的大并发访问而不会发生阻塞。
- **动态 Emoji 跑马灯**：引入了状态流转动画。在等待大模型响应的期间，机器人会在消息上动态轮播 `THINKING` (🤔)、`Typing` (⌨️)、`Mac` (💻)、`Communicate` (💬) 等表情，实时给用户正向的响应反馈。
- **状态清理机制**：在表情状态轮换以及最终生成回复后，自动销毁过期的表情包（`delete_emoji`），保证视觉上的整洁。

### 🐞 修复缺陷 (Bug Fixes)
- **修复了大模型无限挂起（卡死）的问题**：在此前版本中，使用 `PM2` 挂载非交互式守护进程时，大模型 `antigravity` 因为标准输入流（`stdin`）未关闭而陷入无限等待。现通过强制传入 `stdin=subprocess.DEVNULL` 参数彻底解决了在后台环境的死锁现象。

### ⚙️ 工程化构建 (Chore)
- **后台常驻服务化**：使用 `PM2` 工具成功将 `bot.py` 注册为了可靠的持久化后台进程（名称：`feishu-bot`），可免疫终端关闭，同时具备崩溃级秒级自动重启的能力。
- **代码版本化**：自动通过调用 Gitee OpenAPI 新建了远程仓库，并将项目推送到 `singkong/antigravity-feishu-bot` 中。
- **文档完善**：构建了一份详尽的 `README.md`，规范化了一键部署教程及依赖清单。

---

## [v1.0.0] - 早前版本

### 🎯 初始设计
- 基于 `lark-cli` 事件消费（`im.message.receive_v1`）。
- 同步模式下调用本地大模型。
- 最初测试仅具备有限的表情响应（如 `StatusReading`）。
