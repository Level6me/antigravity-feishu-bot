# 🔄 系统在线热更新插件 (`system_updater`)

`system_updater` 是负责检查 GitHub/Gitee 远端最新源码提交、比对版本差异并一键无缝拉取构建热重载机器人引擎的核心维护插件。

---

## 📌 功能特性

- **云端版本比对**：自动 `git fetch` 拉取远端 `main` 分支最新 Commit Hash 与 Changelog；
- **优雅增量比对**：支持识别已有最新版本（展示已是最新卡片）或有可升级版本（展示 Commit 变动明细与更新按钮）；
- **热重启集成**：触发更新后自动调度 PM2 重启引擎，保证机器人服务持续在线。

---

## ⚙️ 指令与使用方法

| 斜杠指令 | 参数 | 描述 | 示例 |
| :--- | :--- | :--- | :--- |
| `/update` | 无 | 触发检查 GitHub 云端最新代码版本 | `/update` |

---

## 📋 插件配置与清单 (`manifest.json`)

```json
{
  "id": "system_updater",
  "name": "🔄 系统在线热更新插件",
  "version": "2.0.0",
  "author": "Antigravity",
  "description": "检查并拉取 Git 云端最新代码版本，一键自动构建热重启机器人引擎",
  "commands": [
    "/update"
  ],
  "enabled": true
}
```

---

## 🛠️ 架构与安全防护

- **Git 环境变量隔离**：设置 `GIT_TERMINAL_PROMPT=0` 与 `DEBIAN_FRONTEND=noninteractive`，防止后台阻断等待输入；
- **镜像源自动回退**：若访问 GitHub 节点超时，会自动回退至国内镜像源拉取代码。
