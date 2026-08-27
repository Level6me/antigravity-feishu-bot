# 🧠 AI 长期记忆管理插件 (`ai_memory`)

`ai_memory` 是管理飞书机器人与用户对话偏好、长期背景记忆与个性化设定（User Persona & Profile）的 AI 管道扩展插件。

---

## 📌 功能特性

- **记忆库可视化**：一键弹出当前会话与全局共享的对话偏好与长期记忆卡片；
- **AI 管道前置自动注入 (`on_before_ai`)**：每次用户向大模型提问时，插件会自动读取该用户已保存的偏好与记忆，无感注入至 Prompt 上下文中；
- **跨会话持久化**：用户个性化偏好持久保存至 SQLite 数据库中。

---

## ⚙️ 指令与使用方法

| 斜杠指令 | 参数 | 描述 | 示例 |
| :--- | :--- | :--- | :--- |
| `/memory` | 无 | 查看并管理当前会话的 AI 长期记忆与偏好看板 | `/memory` |

---

## 📋 插件配置与清单 (`manifest.json`)

```json
{
  "id": "ai_memory",
  "name": "🧠 AI 长期记忆管理插件",
  "version": "2.0.0",
  "author": "Antigravity",
  "description": "管理个人的长期对话偏好与全局 AI 记忆库，并在大模型对话前自动注入",
  "commands": [
    "/memory"
  ],
  "enabled": true
}
```

---

## 🛠️ AI 管道 Hook 实现逻辑

插件继承并实现了 `BasePlugin` 的 `on_before_ai` 异步方法：
1. 从 SQLite 数据库检索 `chat_id` 对应的用户偏好数据；
2. 构造 `memory_context` 上下文文本；
3. 将修改后的 `session_data` 传递给 AI 核心执行引擎，实现大模型人格的个性化对齐。
