# Antigravity Feishu Bot (飞书大模型私人助理)

这是一个基于飞书原生 WebSocket 接口和本地 `antigravity` AI 引擎深度结合的**全能型个人助理机器人**。

经过重构升级，本机器人已具备市面上顶级商业 AI 助手的全部核心体验，并独享基于本地服务器的“外挂”级系统权限。它可以作为你专属的生产力多面手，全天候 24 小时运行。

---

## 🌟 核心杀手锏特性

1. **🚀 极速流式卡片打字机体验**
   摒弃了传统的等待式或指令调用，采用原生 Python SDK (`lark_oapi`) 与 WebSocket 结合。后台开启 0.5s 高频热更新，让大模型的每一个字都像打字机一样实时在飞书互动卡片中流式输出，彻底消除等待焦虑。
2. **🔌 全模态感官 (文档/语音/图片/视频)**
   只需在飞书里随手发一份 PDF 财报、一段手机录音、甚至是一个报错的 `.mov/.mp4` 视频，机器人会自动在后台下载并转化为大模型能理解的数据。它能“看图”、“听音”、“阅读长文”。
3. **💻 你的私人 Mac / 服务器远程终端**
   利用 `antigravity` 的底层机制，你可以直接在飞书里命令它读取你电脑本地的文件，或者直接让它执行 `ls -al`、运行 bash 脚本。你的手机飞书变成了远程服务器的智能 SSH 管家。
4. **🔄 实体产物全自动反传拦截器**
   如果大模型为你生成了一张图片、写了一个 `.py` 脚本或绘制了架构图，内置的“雷达拦截器”会自动捕获这些本地实体文件，并将其自动转换为飞书附件直接发送给你。手机端直接保存，所见即所得。
5. **🕹️ 丰富的内置快捷指令集**
   * `/model`：弹出交互式控制面板，一键切换不同的大模型引擎。
   * `/role <设定>`：给它套上任何专家的身份（如：资深 Python 架构师）。
   * `/stop`：紧急刹车，瞬间强制结束大模型后台的高耗时推理任务，节省额度，并保留已生成的半成品文本。
   * `/clear`：一键清空上下文记忆。

---

## 🚀 安装部署指南

本机器人主要使用 Python 编写，并通过 PM2 守护进程保证 24 小时在线。

### 第一步：安装前置系统依赖
确保你的 Mac/Linux 系统中已安装以下工具：
1. **Python 3.10+** (推荐)
2. **Node.js 和 npm** (用于安装 PM2)
   ```bash
   npm install -g pm2
   ```
3. **安装 Lark CLI (飞书命令行工具)**
   `lark-cli` 用于处理多模态文件的下载和极个别系统交互。
   * 下载并安装: `npm install -g @larksuiteoapi/lark-cli`
   * （或者按飞书官方教程下载二进制版）
   * **重要**：安装后在终端运行 `lark-cli auth login` 完成飞书开发者授权登录。
4. **安装 Antigravity CLI**
   这是底层驱动大模型的引擎。你需要确保在终端中可以直接执行 `antigravity` 命令。

### 第二步：配置 Python 虚拟环境与依赖
为了不污染全局环境，建议在项目目录内使用虚拟环境 (venv)。

```bash
# 1. 克隆/进入项目目录
cd antigravity-feishu-bot

# 2. 创建 Python 虚拟环境
python3 -m venv venv

# 3. 激活虚拟环境
# Mac/Linux:
source venv/bin/activate

# 4. 安装核心依赖包
pip install -r requirements.txt
# (如果没有 requirements.txt，可以直接执行：pip install lark-oapi aiohttp)
```

### 第三步：配置飞书应用凭证
在项目根目录创建一个 `.env` 文件，填入你自己在“飞书开发者后台”创建的机器人的 `APP_ID` 和 `APP_SECRET`：

```env
APP_ID=cli_xxxxxxxxxxxx
APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
> **注意**：请确保你的飞书应用在后台开启了“WebSocket 建立长连接”的权限，并为机器人开通了收发消息的全部所需权限（包括 `im:message` 等）。代码底层已接入 `python-dotenv` 自动加载环境变量，避免了秘钥泄露至代码仓库的风险。

### 第四步：一键启动后台服务

完成配置后，使用 PM2 启动机器人即可让其在后台静默运行，无需保持终端开启。

```bash
# 启动机器人进程（请务必使用虚拟环境里的 python3 执行）
pm2 start venv/bin/python3 --name "feishu-bot" -- main.py

# 保存 PM2 进程列表，保证电脑重启后自动恢复
pm2 save
```

### 常用运维指令
* **查看实时日志**：`pm2 logs feishu-bot`
* **停止机器人**：`pm2 stop feishu-bot`
* **更新代码后重启**：`pm2 restart feishu-bot`

---

## 👨‍💻 架构原理解析

本代机器人的核心重构架构：
1. **网络层**：摒弃轮询，使用 `lark_oapi.ws.Client` 与飞书网关建立稳定的全双工长连接，事件响应延迟达到毫秒级。
2. **高度模块化解耦**：项目划分为 `main.py`（WebSocket 主轴）、`commands.py`（指令路由器）、`lark_client.py`（飞书 SDK 发送端）、`database.py`（存储层）以及 `multimodal.py`（多模态解析层），极致清爽。
3. **SQLite 持久化数据热迁移**：彻底告别脆弱的 JSON 文本文件读写，所有长时偏好记忆和对话上下文均无缝对接至原生的 SQLite 数据库，保证高并发数据安全。
4. **防僵尸与优雅退出机制**：引入 `signal` 信号流，接管中断（SIGINT/SIGTERM）。当使用 PM2 重启或者停止服务时，会自动执行 Graceful Shutdown，彻底清理后台任何遗留未处理的僵尸进程。

现在，尽情享受你的终极私人助理吧！遇到问题可以随时翻阅 `~/.pm2/logs/feishu-bot-error.log` 进行排查。

---

## 📝 更新记录 (Changelog)

### v2.0 - 核心架构重构与安全加固 (2026.06)
- **[Refactor]** 将庞大的入口文件 `ws_bot.py` 拆解更名为 `main.py`，剥离全部业务逻辑至独立模块。
- **[Refactor]** 新增 `commands.py` 统管所有 Slash Commands（如 `/help`, `/clear`, `/memory`）。
- **[Refactor]** 新增 `lark_client.py` 统一封装飞书互动卡片、流式文本的回复方法。
- **[Feature]** 引入 SQLite 取代 JSON 本地存储方案，增加旧数据自适应无损迁移脚本，彻底避免 I/O 数据损坏。
- **[Feature]** 加入系统层级的 Graceful Shutdown，在停止程序前精准点杀后台僵尸子进程，杜绝内存泄漏。
- **[Security]** 引入 `.env` 环境隔离机制与严格的 `.gitignore` 保护网，通过 Git `filter-branch` 实现了整个代码历史提交记录的秘钥清除操作。
- **[UX]** 优化了选项卡片（交互按钮）的体验，增加按钮消抖和操作确认反馈提示 `✅ 您已选择...`。

### v2.1 - 视觉交互与工业级监控升维 (2026.06)
- **[Feature]** 引入全新的多模态长时任务反馈机制：在上传巨型文件/视频时，新增“资源下载中...”过渡预告卡片，并在完成后无缝切换为状态结果，彻底消除等待焦虑。
- **[Feature]** 完全弃用 `print`，引入独立的 `logger.py` 工业级日志追踪模块，支持按级别带色彩的高亮输出与日志文件 `feishu_bot.log` 满额自动切割（Log Rotation）。
- **[UX/Refactor]** 将混乱的 JSON 卡片构建代码全部抽离为 `CardBuilder` 静态工厂类，统一飞书卡片UI风格，增加状态卡片的精致页脚与时间戳。
- **[UX]** 引入了底层的卡片刷新“防抖队列（Debounce Queue）”，将流式更新锁频为最低 `1.0s` 间隔，让打字机效果如德芙般丝滑，同时彻底杜绝了触发飞书限流阈值（Rate Limit）的风险。
