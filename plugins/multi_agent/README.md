# 🤖 Multi-Agent Feishu Plugin (多 Agent 自动协同开发插件)

基于 **antigravity-feishu-bot 官方插件架构** 实现的多设备 Agent 双向自动化协同开发插件。

## 🌟 核心功能与特色

- **斜杠指令自动化**：在飞书群中输入 `/agent_assign` 或 `/multi_agent` 自动唤醒 Leader/Architect Agent 进行需求拆解。
- **任务 JSON 契约派单**：自动格式化交互卡片并向指定 Worker Agent 派发带有代码域保护（Scope）与 Git 目标分支的分工卡片。
- **命令行安全保护**：继承 `antigravity-cli` 超时保护控制，在后台安全异步跑代码生成、单元测试与 Git Auto Merge。
- **完全零耦合**：完全遵循 `feishu-bot-plugin` 规范，继承 `BasePlugin`，可无缝安装于 `antigravity-feishu-bot` 的插件集中仓库中。

## 📂 插件目录规范

```text
plugins/multi_agent/
├── manifest.json        # 插件元数据（已声明 /agent_assign, /multi_agent 指令）
├── plugin.py            # 插件核心逻辑（继承 BasePlugin）
├── README.md            # 插件使用与安装规范说明
└── requirements.txt     # 第三方依赖 (无额外依赖)
```

## 🚀 指令说明

| 指令 | 参数示例 | 描述 |
| :--- | :--- | :--- |
| `/agent_assign` | `/agent_assign [需求描述]` | 唤醒架构师 Agent 拆解项目需求并向开发 Agent 派发任务卡片 |
| `/multi_agent` | `/multi_agent status` | 查看当前两台设备 Agent 节点协同与任务队列状态 |

## 🛠️ 安装方法

将 `multi_agent` 文件夹复制到 `antigravity-feishu-bot` 根目录下的 `plugins/` 目录中，在机器人聊天框发送 `/update` 即可一键在线热加载该插件。
