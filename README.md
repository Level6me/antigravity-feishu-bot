# 🚀 Antigravity Feishu Bot

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-brightgreen?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/Feishu-WebSocket%20Lark%20OAPI-orange?style=flat-square" alt="Feishu">
  <img src="https://img.shields.io/badge/Engine-Google%20Antigravity%20(agy)-purple?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/Gateway-TypeSafe%20AI%20(Jev)-teal?style=flat-square" alt="TypeSafe AI">
  <img src="https://img.shields.io/badge/Process-PM2%20%7C%20Docker-blueviolet?style=flat-square" alt="PM2">
</p>

基于飞书原生 WebSocket 长连接与宿主机 `antigravity`（`agy`）核心引擎，深度融合 **TypeSafe AI (System One Jev)** 毫秒级决策网关的企业级智能研发助手。

无需公网 IP 或 Webhook 回调地址，在飞书内即可直接远程驱动服务器完成全栈代码读写、智能终端命令执行、多模态音视频深度解析、实时交互卡片流转与任务规划自动化执行。

---

## 🌟 核心特性与技术亮点

### 🧠 1. TypeSafe AI (System One Jev) 毫秒级智能安全路由网关
- **前置智能安全防御（Safety Gate）**：基于 Jev 结构化判断模型，在任何指令到达宿主机大模型执行引擎前进行安全与合规检测，实时拦截 Prompt 注入、角色越狱、提权攻击以及恶意的系统级破坏指令（如越权删除、格式化磁盘等）。
- **结构化意图与复杂度度量（Intent & Complexity Routing）**：毫秒级并行输出分类意图、置信度与连续复杂度数值（`complexity_score` 与 `needs_terminal` 标记），动态隔离轻量对话与重型工程任务。
- **高频场景极速 Fast-Path（< 200ms 秒回）**：
  - 针对服务器健康监控（`server_health`）、备忘笔记（`notes`）、定时任务计划（`cron`）、网关配置（`typesafe_status`）等高频意图，网关直接命中 Fast-Path 并在本地秒级回传交互卡片，彻底免除等待大模型生成的数秒延迟与 Token 开销。
- **动态约束与任务规划流转（Task Planning Directive）**：
  - 对高置信度工程任务自动注入 `[TASK_PLAN]` 步骤流转并在飞书卡片中实时打勾 ✅。
  - 注入**自主执行防选择题准则**：遇到多技术分支时由 Agent 自动评估并选取最优方案推进，严禁向用户输出无意义的选择题。
  - 对轻量纯文本咨询模式禁用一切终端与文件写入工具，保障安全与极速响应。
- **无感静默降级（Zero-Overhead Fallback）**：未配置 Key 或网络不可达时，自动短路无缝降级为纯本地内存级启发式规则引擎（耗时仅 **~0.05ms**，零性能损耗）。

### 🎛️ 2. 全新飞书原生交互式配置控制台 (`/typesafe`)
- 飞书聊天框内直接发送 `/typesafe` 或 `/ts`，唤起原生配置卡片。
- **在线 Ping 连通性测试**：实时探测 TypeSafe API 连接状态、往返延迟与模型名称。
- **一键切换模型与开关**：支持在卡片中即时切换 `jev-latest` / `jev-preview`，一键开启或暂停网关。
- **交互式 Key 录入与清除**：支持在飞书端安全交互式输入或清除 `TYPESAFE_API_KEY`，并自动热同步持久化至服务器 `.env` 文件。

### 🖥️ 3. 本地全栈开发与执行引擎
- **宿主机直驱**：依托本机 `agy` 引擎，支持在宿主机目录读写源码、安装依赖、调试构建与执行 Shell 脚本。
- **项目工作区隔离**：`/project` 呼出可视化项目管理器，支持多目录快速切换、新建工程空间、或直接输入 Git 仓库地址自动 Clone 并在飞书里即刻开发。
- **生成物智能捕获回传**：自动嗅探模型在执行过程中生成的图片、数据图表、Word、Excel、PDF 及压缩包，自动通过飞书富媒体通道安全回传。

### 💬 4. 原生卡片流转与异步会话管理
- **动态流转卡片**：从任务排队、资源加载、思考推理、步骤规划打勾、工具执行耗时追踪到最终交付，全生命周期原地刷新（In-place Patch）。
- **会话独立排队**：按 `chat_id` 维护独立异步任务队列，避免并发冲突；支持 `/stop` 随时紧急熔断中断任务。
- **上下文预热池（Prewarm Pool）**：内置会话预热守护机制，大幅削减大模型 CLI 初始化冷启动延迟。

### 🎙️ 5. 多模态与语音双向交互
- **全格式下行多模态**：支持直接向机器人发送图片、PDF、Word 文档、代码文件、短视频或音频条，自动抽取并提供上下文分析。
- **原生双向语音**：用户发送飞书语音条时，系统自动识别转写、执行推理，并使用 Edge-TTS 实时回传生动自然的口语化语音答复。

---

## 📸 界面预览

| 首次部署欢迎与快捷引导 | 交互式工作区项目管理器 | 实时动作与工具耗时指示 |
| :---: | :---: | :---: |
| ![欢迎卡片](docs/images/screenshot_1.jpg) | ![项目管理器](docs/images/screenshot_3.jpg) | ![耗时指示](docs/images/screenshot_2.jpg) |

| 视频多模态深度解析 | 生成物自动捕获与回传 | 系统 OTA 自我热升级 |
| :---: | :---: | :---: |
| ![视频解析](docs/images/screenshot_4.jpg) | ![生成物回传](docs/images/screenshot_5.jpg) | ![系统升级](docs/images/screenshot_0.jpg) |

---

## ⌨️ 完整 Slash 指令列表

| 指令 | 权限级别 | 功能与交互说明 |
| :--- | :---: | :--- |
| `/help` | 全部 | 呼出交互式帮助卡片与全功能快捷入口 |
| `/project` | 全部 | 呼出可视化项目管理器（切换工作区、新建项目、设置根目录、克隆仓库） |
| `/model` / `/card` / `/menu` | 全部 | 呼出大模型切换面板（支持 Gemini 3.7/3.8、Claude Sonnet、GPT-OSS 等） |
| `/typesafe` / `/ts` | 全部 | **TypeSafe AI 网关控制台**（Ping 探测、模型切换、Key 设置与启用开关） |
| `/health` / `/sysinfo` | 全部 | **服务器健康看板**（CPU 负载、内存占用、磁盘空间，Fast-Path 毫秒级秒回） |
| `/note` / `/notes` | 全部 | 随身记事本卡片（`/note add` 新增、`/note del` 删除、`/notes` 展开列表） |
| `/cron` / `/schedule` | 全部 | 定时提醒与 Cron 任务计划面板（支持自然语言设置倒计时与周期闹钟） |
| `/memory` | 全部 | 用户个人偏好记忆管理（交互式添加与删除当前用户的个性化偏好） |
| `/brain` | 全部 | Antigravity 全局跨会话记忆与知识图谱透视看板 |
| `/context` | 全部 | 查看当前会话 Token 容量、占用水位与上下文滑动窗口状态 |
| `/quota` | 授权 | 实时探测 Google AI Pro / Antigravity 当前账户剩余额度与配额 |
| `/clear` | 全部 | 清空当前会话上下文并热重置 Prewarm 进程池，开启全新对话 |
| `/stop` | 全部 | 紧急叫停当前正在运行的后台任务并清空本会话排队任务 |
| `/ping` | 全部 | 探测核心服务与网络健康存活状态 |
| `/status` | 管理员 | 查看 Bot 进程 Uptime、CPU/内存指标、重启计数与近期错误日志摘要 |
| `/plugins` / `/plugin` | 管理员 | 打开插件中心管理器（查看已挂载插件、切换启用状态、热重载插件） |
| `/user` | 管理员 | 用户与群聊权限管理控制台（授权、降级、拉黑、分配权限等） |
| `/auth` | 未授权 | 向系统管理员发送授权申请卡片（显示申请人、群名、申请理由） |
| `/update` | 管理员 | 检查云端最新版本；输入 `/update confirm` 触发 OTA 无损平滑热升级 |

---

## 🔐 权限与安全风控机制

1. **第一私聊自动提权（Auto-Admin）**：
   - 首次部署启动后，**首个向 Bot 发送私聊消息的用户将自动绑定为系统最高管理员**（群聊不可被自动绑定）。
2. **三档细粒度授权体系**：
   - 未授权会话默认保持静默，发送 `/auth` 后，管理员会收到包含申请者信息的审批卡片，支持一键审批：
     - **基础版（Basic）**：日常文本问答、记事本、简单查询。
     - **开发版（Dev）**：可使用项目切换、代码查看与受限工具。
     - **完全版（Full）**：具备终端 Shell 执行与宿主机全权限。
3. **双层安全防护网**：
   - **TypeSafe System One**：语义层深度扫描注入与恶意意图，阻断危险行为；
   - **系统级命令守卫**：对底层 `rm -rf /`、磁盘覆写、系统重启等高危指令进行物理级阻断；`--dangerously-skip-permissions` 仅对受信任的最高管理员生效。
4. **防刷限流（Rate Limiting）**：
   - 普通授权用户每分钟最多发送 5 条消息，每日上限 100 次工具执行（管理员不限）。

---

## 🚀 安装部署指南

### 环境要求
- **Linux** (Ubuntu 20.04+ / Debian 11+ / CentOS / Arch 等) 或 macOS
- **Python 3.10+**
- **Node.js & PM2**（用于生产环境高可用守护：`npm install -g pm2`）
- 本机已安装并完成登录认证的 **Antigravity CLI**（`agy` 或 `antigravity`）

---

### 方法 1：一键交互式脚本安装（推荐）

在服务器终端直接执行：

```bash
bash <(curl -sL https://raw.githubusercontent.com/Level6me/antigravity-feishu-bot/main/install.sh)
```

脚本将自动引导您输入飞书 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，并自动构建虚拟环境、安装依赖与启动 PM2 守护进程。

**本地已 Clone 代码时的一键运维：**
```bash
chmod +x install.sh
./install.sh           # 安装与初始化
./install.sh update    # 极速拉取并平滑重启
./install.sh uninstall # 彻底清理后台服务与环境
```

---

### 方法 2：手动源码部署

```bash
# 1. 克隆代码仓库
git clone https://github.com/Level6me/antigravity-feishu-bot.git
cd antigravity-feishu-bot

# 2. 创建并激活 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖包
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
nano .env  # 填入飞书凭证，按需配置 TYPESAFE_API_KEY

# 5. 使用 PM2 启动服务（推荐）
pm2 start venv/bin/python3 --name "feishu-bot" -- main.py
pm2 save
pm2 startup
```

---

### 方法 3：Docker / Docker Compose 部署

```bash
cp .env.example .env
# 编辑 .env 配置飞书凭据与挂载目录

docker compose up -d --build
```

---

## ⚙️ 完整环境变量配置指南

编辑项目根目录下的 `.env` 文件：

```env
# ==========================================
# 1. 飞书开放平台配置 (必填)
# ==========================================
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ==========================================
# 2. 安全与白名单配置 (可选)
# ==========================================
# 允许访问的 open_id 或 chat_id，多个用英文逗号分隔；留空则由 /auth 授权机制管理
ALLOWED_USERS=
ALLOWED_CHATS=
# 是否向 agy 传递跳过权限确认标记 (默认 true，仅对管理员生效)
DANGEROUSLY_SKIP_PERMISSIONS=true

# ==========================================
# 3. Antigravity 引擎与工作区配置
# ==========================================
# agy 可执行文件绝对路径；留空则系统自动探测
ANTIGRAVITY_BIN=
# antigravity-cli 数据存储目录 (默认: ~/.gemini/antigravity-cli)
ANTIGRAVITY_HOME=
# 默认公共工作区根目录 (默认: ~)
WORKSPACE_ROOT=/home/ubuntu
# 默认大模型 (如: gemini-3.7-flash-low / gemini-3.8-flash-high / claude-sonnet-4-6)
DEFAULT_MODEL=gemini-3.7-flash-low

# ==========================================
# 4. TypeSafe AI (System One Jev) 网关配置
# ==========================================
# TypeSafe 平台 API 密钥 (留空则自动无缝降级为本地规则兜底，完全不影响系统速度)
TYPESAFE_API_KEY=
# 是否启用 TypeSafe 决策网关
TYPESAFE_ENABLED=true
# 默认使用的 Jev 决策模型
TYPESAFE_MODEL=jev-latest
# TypeSafe 服务基础接口地址
TYPESAFE_BASE_URL=https://api.typesafe.ai

# ==========================================
# 5. 语音交互与 TTS 设置
# ==========================================
# 是否启用双向语音答复
ENABLE_VOICE_REPLY=true
# Edge-TTS 音色 (推荐: zh-CN-XiaoxiaoNeural / zh-CN-YunxiNeural)
TTS_VOICE=zh-CN-XiaoxiaoNeural

# ==========================================
# 6. OTA 升级网络镜像源 (可选)
# ==========================================
GITEE_MIRROR_URL=
```

---

## 📋 飞书开放平台后台配置（极简 4 步）

1. **开启 WebSocket 长连接模式**：
   - 登录 [飞书开放平台](https://open.feishu.cn/)，进入创建的企业自建应用。
   - 打开 **开发配置 → 事件与回调**（或“事件订阅”），将接收方式切换为 **WebSocket 长连接**（无需填写公网 URL）。
2. **开通必要权限（权限管理）**：
   - `im:message`（获取与发送单聊、群组消息）
   - `im:message:resource`（获取消息中的图片、富文本与音视频资源）
   - `im:image`（上传图片）
   - `im:file`（上传本地文件与生成物）
   - `im:message.reaction`（消息表情回复状态标记）
   - `im:chat:readonly`（读取群聊名称）
   - `contact:user.base:readonly`（读取用户飞书昵称）
3. **订阅核心事件**：
   - 添加事件：`im.message.receive_v1`（接收消息事件）
   - 卡片动作回调：`card.action.trigger`（自动支持，无需额外权限）
4. **发布应用版本**：
   - 确认并在 **版本管理与发布** 中创建新版本并发布，确保你的飞书账号位于应用的**可用范围**内。

---

## 🏗️ 核心系统架构

```mermaid
flowchart TD
    User["飞书客户端 (用户 / 群聊 / 语音条)"] -->|WebSocket Lark OAPI| Main["main.py (事件分发器)"]
    Main --> Pre["handlers/messages.py (消息防抖与指令路由)"]
    Pre -->|Slash 命令 (/project /typesafe 等)| Cmd["commands.py (交互式卡片渲染)"]
    
    Pre -->|自然语言请求| Gate{"TypeSafe AI 决策网关\n(System One Jev)"}
    
    Gate -->|高频意图| FastPath["Fast-Path 本地极速响应\n(服务器监控/备忘录/闹钟/TS状态)"]
    FastPath -->|< 200ms 秒回| CardUI["飞书原生交互式卡片"]

    Gate -->|拦截安全隐患| RejectCard["安全风控拦截告警卡片"]
    
    Gate -->|复杂工程任务| AgentQueue["会话异步排队队列 (chat_id)"]
    AgentQueue --> Executor["executor.py (Antigravity CLI 引擎)"]
    
    Executor -->|Task Plan 步骤流转| CardUI
    Executor -->|执行 Shell / 读写代码| HostFS["宿主机操作系统 & 工作区"]
    Executor -->|产出图像/文件回传| MultiModal["multimodal.py (飞书资源通道)"]
    MultiModal --> CardUI
    CardUI --> User
```

---

## 🛠️ 常用运维排错命令

```bash
# 查看主程序实时运行日志
pm2 logs feishu-bot

# 检查进程状态与内存占用
pm2 status

# 重启飞书机器人服务
pm2 restart feishu-bot

# 停止服务
pm2 stop feishu-bot
```

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议开源。欢迎提交 Issue 与 Pull Request！
