# 权限管理机制与安全加固方案

> 状态：设计稿（待实施）
> 目标：在不影响现有管理员使用体验的前提下，引入"首个会话绑定管理员 + 申请制授权 + 能力分级"的权限体系，并同步修复审计报告中 P0/P1 级安全与功能问题。

---

## 一、用户权限管理机制

### 1.1 角色模型

| 角色 | 说明 | 默认能力 |
|---|---|---|
| `admin` | 最高管理员，首次部署第一个会话自动绑定 | 全部能力 |
| `user` | 已授权会话（可细分权限档） | 按授权档位 |
| `pending` | 已申请、待管理员审批 | 仅 `/auth` |
| `guest`（未授权） | 未申请/未通过 | 仅首次消息提示 + `/auth` |
| `banned` | 黑名单 | 完全静默 |

### 1.2 能力集（scopes）

每个授权会话绑定一组最小能力：

| Scope | 含义 | 风险 |
|---|---|---|
| `chat` | 基础对话（调 agy 处理文本） | 中 |
| `media` | 图片/文件/音视频上传解析 | 中 |
| `files` | 产物回传（下载本地文件到飞书） | 高 |
| `project` | 项目管理/切换工作区 | 高 |
| `shell` | 让 agy 执行 Shell 命令（需配合权限模式） | 极高 |
| `quota` | 查看 Google AI Pro 额度 | 中（涉 token） |
| `notes_memory` | 笔记与偏好记忆 | 低 |
| `update` | OTA 升级 | 极高，仅 `admin` |

**三档快速授权**（管理员在授权卡片上一键选择）：

| 档位 | scopes |
|---|---|
| 基础 | `chat` + `media` + `notes_memory` |
| 开发 | 基础 + `project` + `files` |
| 完全 | 开发 + `shell` + `quota` |

### 1.3 数据模型（SQLite 新增表）

```sql
CREATE TABLE IF NOT EXISTS bot_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- key: admin_chat_id / bootstrap_done / hint_...

CREATE TABLE IF NOT EXISTS auth_sessions (
    chat_id        TEXT PRIMARY KEY,
    chat_type      TEXT NOT NULL,          -- p2p / group
    display_name   TEXT DEFAULT '',        -- 群名或用户昵称
    sender_open_id TEXT DEFAULT '',        -- 私聊申请者
    role           TEXT NOT NULL DEFAULT 'guest',  -- admin/user/pending/banned/guest
    scopes         TEXT NOT NULL DEFAULT '[]',     -- JSON 数组
    created_at     INTEGER,
    updated_at     INTEGER,
    granted_by     TEXT DEFAULT '',
    request_count  INTEGER DEFAULT 0,
    last_request_at INTEGER DEFAULT 0,
    last_hint_at   INTEGER DEFAULT 0       -- 未授权提示时间戳，用于防骚扰
);

CREATE TABLE IF NOT EXISTS pending_tasks (  -- 队列持久化（可选阶段）
    chat_id TEXT,
    task    TEXT NOT NULL,
    created_at INTEGER,
    PRIMARY KEY (chat_id, created_at)
);
```

实现建议：在 `database.py` 增加同步版 `get_bot_meta / set_bot_meta / get_auth_session / upsert_auth_session`（复用现有 sqlite3 连接模式），因为事件回调是同步上下文，需要同步访问。

### 1.4 首次部署绑定管理员

流程（在 `handlers/events.py` 消息入口最前面执行）：

1. `bot_meta.admin_chat_id` 不存在 → 进入 bootstrap。
2. 绑定条件建议：**仅 `chat_type == "p2p"` 的私聊会话可被绑定为 admin**（防止 bot 刚上线被拉入群聊、被陌生人抢绑）。私聊发送第一条消息即绑定。
3. 绑定后向该会话推送"管理员绑定成功"卡片，并正常处理这条消息（保证"第一条消息不丢"）。
4. 若需要重新绑定（例如部署者手滑/换人）：管理员通过 `/user reset-admin`（需二次确认卡片）或运维直接改 DB。
5. 兼容旧配置：现有 `ALLOWED_USERS` / `ALLOWED_CHATS` 仍生效，命中者自动视为已授权 `user`（默认"开发"档），老部署无感升级。

### 1.5 未授权会话静默机制

规则（只对 `guest` / `banned` 生效，`admin` 与 `user` 完全不受影响）：

1. `banned`：任何消息直接丢弃，不回复、不提示。
2. `guest` 收到普通消息：
   - 若 `now - last_hint_at > 24h`（从未提示过则必然触发）→ 回复一条提示卡片："当前会话未授权，发送 `/auth` 申请使用权限"；更新 `last_hint_at`。
   - 24h 内再次发消息 → 完全静默，不调用 agy、不进队列。
3. `guest` 发送 `/auth` → 走申请流程（见 1.6）。
4. `/auth` 防刷：`pending` 有效期 24h；每会话两次申请间隔 ≥ 10 分钟，超限回复"申请过于频繁"。

### 1.6 申请与授权卡片

**申请端**：`guest` 发送 `/auth`
- 写入/更新 `auth_sessions`（role=`pending`，记录 `sender_open_id`、`chat_type`、`request_count`、`last_request_at`）。
- 向管理员会话推送授权卡片（通过 `chat_id` 定向发送，用 `im.v1.message` 发送新消息而非 reply）。

**授权卡片内容**（`cards/auth.py` 新增）：

- 会话类型：私聊 / 群聊
- 会话名称：群聊用 `im.v1.chat.get` 查 `name`；私聊用 `contact.v3.user.get` 按 `open_id` 查 `name`（SDK 均已确认可用）
- 会话 ID：`chat_id`
- 申请者：`open_id`（私聊时即用户名）
- 申请时间、申请次数
- 最近一条消息摘要（截断 50 字，防隐私泄露）

**操作按钮**（值内携带 `chat_id`）：

| 按钮 | 动作 |
|---|---|
| ✅ 通过（基础） | role=`user`, scopes=`[chat,media,notes_memory]` |
| ✅ 通过（开发） | 基础 + `project,files` |
| ✅ 通过（完全） | 开发 + `shell,quota` |
| ❌ 拒绝 | role=`guest`, 记 `denied`（下次申请间隔 24h） |
| 🚫 拉黑 | role=`banned` |

**结果通知**：管理员点击后，向申请人会话推送结果卡片（"已授权 / 已拒绝"）。

### 1.7 `/user` 管理命令（仅 admin）

| 命令 | 作用 |
|---|---|
| `/user` | 查看管理面板：已授权 / 待审批 / 黑名单三组列表（卡片+按钮） |
| `/user grant <chat_id> [basic\|dev\|full]` | 手动授权指定会话 |
| `/user revoke <chat_id>` | 撤销授权（回到 guest） |
| `/user ban <chat_id>` / `/user unban <chat_id>` | 拉黑 / 解除 |
| `/user promote <chat_id>` | 提升为管理员（全能力） |
| `/user demote <chat_id>` | 降为 user（保留 scopes） |
| `/user reset-admin` | 重新绑定管理员（二次确认） |

面板卡片同样复用 `cards/auth.py`，按钮 value 携带动作 + chat_id。

### 1.8 非管理员权限分配建议（延伸设计）

**默认最小权限原则**：

1. 新授权会话默认只给"基础"档，`shell` / `project` / `files` / `quota` 一律默认关闭，管理员按需升档。
2. 群聊策略：群聊授权默认**不开放** `shell`、`project`、`quota`（群成员共享权限，人多面大）；群聊里 `/user`、`/update` 等管理命令无效。
3. 执行模式联动（关键）：
   - `admin` 会话：沿用现有行为（是否传 `--dangerously-skip-permissions` 由配置决定），体验零变化。
   - `user` 会话：**一律不传** `--dangerously-skip-permissions`，并且只有拥有 `project` scope 时才允许设置非默认工作区；`shell` scope 只影响 agy 受限模式下的命令自由度。
4. 限流（新加，防止刷爆配额/CPU）：
   - 每会话每分钟最多 5 条消息（超限回复"操作太频繁"）。
   - 每会话每日 agy 执行次数上限（建议 100 次，可在 meta 配置），超出当日拒绝并提示。
5. 权限校验落点：
   - 消息入口（`handlers/events.py`）：未授权 → 静默/引导。
   - 卡片按钮（`handlers/card_actions.py`）：未授权 → toast 拒绝。
   - Slash 命令（`commands.py`）：`/update`、`/quota`、`/status` 等按 scope 校验。
   - 执行器（`executor.py`）：非 admin 强制受限模式 + 工作区约束。

---

## 二、quota / 平台兼容性方案

### 现状问题

- `/quota` 与卡片 `refresh_quota` 两份重复实现（commands.py + card_actions.py）。
- 端口发现依赖 Linux `/proc` + `ss`，macOS 不可用。
- `ssl._create_unverified_context()` 关闭证书校验。
- 读取本机 OAuth token 外发（功能所需，但要收紧时机与日志）。

### 方案

1. **抽公共模块** `services/quota.py`（或 `utils/quota.py`），提供 `fetch_quota() -> dict`，commands 与 card_actions 都调用它，删除重复代码。
2. **端口发现优先级**（`discover_lsp_port()`）：
   1. 环境变量 `ANTIGRAVITY_LSP_PORT`（显式指定，最高优先级）；
   2. `agy_daemon` 心跳文件 `~/.gemini/antigravity-cli/lsp_port`（agy_daemon 每次探测到端口后写入，跨平台）；
   3. Linux：现有 `/proc` 扫描（保留）；
   4. macOS：`lsof -nP -iTCP -sTCP:LISTEN` 过滤 agy 进程（新增）；
   5. 都失败 → 返回 `None`，走 token + Google API 兜底。
3. **TLS 校验**：
   - Google API 请求：移除 `_create_unverified_context()`，强制校验证书。
   - 本地 LSP（127.0.0.1）：默认校验；若部署环境是自签证书，提供显式开关 `ANTIGRAVITY_QUOTA_INSECURE=true` 才允许跳过。
4. **token 使用收紧**：
   - 仅拥有 `quota` scope 的会话可触发；
   - 读取 token 的代码路径集中到 quota 模块，日志只记 token 前 6 位 + 脱敏；
   - 卡片上不展示 token 相关内容（现状已不展示，保持）。
5. **错误降级**：本地 LSP 与远程 API 都失败时，卡片显示"无法获取额度"+ 原因分类（网络/未登录/无权限），不再把异常堆栈发给用户。

---

## 三、功能改进（推荐方案）

### 3.1 executor 增量读取（性能）

`fetch_current_action` 与 transcript 提取改为增量读取：记录 `(offset, size)`，仅当文件 `size` 变大时从上次 `offset` 读新增部分；`mtime` 回退（文件重建）时重置 offset。避免每 0.5s 全量 `readlines`。

### 3.2 `/model` 子进程超时

`agy models` 的 `communicate()` 包 `asyncio.wait_for(..., timeout=10)`，超时返回错误卡片并 kill 进程。

### 3.3 队列持久化（可选阶段）

`pending_tasks` 表记录排队任务（chat_id + JSON 载荷 + 时间戳）；进程启动时恢复未处理任务；`/stop` 时清空对应记录。实现成本低、收益是重启不丢请求。

### 3.4 清理调试残留

- 删除 `/test_ss` 命令（ss 输出直发飞书属信息泄露）。
- `messages.py` 里重复的 `import re` 清理。
- `/quota`、`refresh_quota` 重复代码抽公共模块（见二）。

### 3.5 统计持久化（可选）

`stats.py` 增加 SQLite 落盘（`bot_stats` 表），/status 展示累计而非仅本次进程数据。

---

## 四、安全修复清单（不影响现有功能）

### P0

| # | 问题 | 处理方式 | 影响面 |
|---|---|---|---|
| 1 | 默认零访问控制 | 引入本方案权限系统后：未授权会话一律静默；`--dangerously-skip-permissions` 仅对 `admin` 生效，`user` 强制受限模式。`.env.example` 与 `install.sh` 增加 `ALLOWED_USERS` / 管理员引导说明 | admin 无感，新增默认保护 |
| 2 | 关闭 TLS 校验 | 移除 `ssl._create_unverified_context()`；本地 LSP 用 `ANTIGRAVITY_QUOTA_INSECURE` 显式开关 | 正常环境无感；自签环境需加开关 |
| 3 | `/test_ss` 泄露系统信息 | 删除该命令 | 无 |
| 4 | `/status` 泄露错误日志原文 | 错误日志摘要只显示"有 N 条错误/最近错误类型"，过滤路径、token、消息内容；或改为仅 admin 可见 | 展示样式小改 |
| 5 | uninstall 删除整个 `~/.pm2` | 改为 `pm2 delete feishu-bot / agy-daemon` + `pm2 save`，提示用户可手动清理 `~/.pm2`，脚本不再递归删除 | 卸载行为收敛 |

### P1

| # | 问题 | 处理方式 | 影响面 |
|---|---|---|---|
| 6 | 产物回传白名单过宽/前缀匹配 | 前缀校验加 `os.sep` 边界；默认白名单去掉 `~`，仅包含 `downloads`、`scratch`、brain 目录、显式配置的 `WORKSPACE_ROOT` 与当前 `project` | 现有功能不受影响（当前 project 仍在白名单） |
| 7 | `/update` stash 冲突丢本地改动 | `stash pop` 失败时：保留冲突文件，写 `.update_conflict.txt` 说明，**不再** `git checkout -- .`；升级前后记录 commit hash，失败时提示回滚命令 | 升级失败时更安全 |
| 8 | 去重仅内存 | 新增 `recent_messages` 表（message_id + chat_id + create_time，保留 12h），事件去重先查内存再查 DB | 重启后重放事件不再重复执行 |
| 9 | 发送重试非幂等 | 发送类（reply / send card）降为不重试或仅 1 次重试；patch 类保留重试 | 减少重复消息 |
| 10 | 无速率限制 | per-chat 令牌桶：每分钟 5 条 + 每日执行上限（见 1.8） | 新增保护，正常用户无感 |
| 11 | `/proc` 扫描低效 | 收敛为仅当端口未知时扫描一次并缓存 5 分钟；所有读 `/proc` 的异常已 try/except，继续降级 | 无 |
| 12 | 日志明文消息内容 | `[RAW RECEIVE EVENT]` 只记 message_id / chat_id / type，不记 content 全文；ws client 日志级别降到 INFO | 排查定位仍可用 |

### 低危/工程（✅ 已实施 2026-08-04）

- ✅ SQLite 定期备份：`garbage_collection.py` 每日 `VACUUM INTO` 备份到 `backups/`（保留 7 份）。
- ✅ `PendingCommand` 枚举统一使用，消除字符串混用。
- ✅ `commands.py` / `card_actions.py` 拆小：quota 逻辑抽到 `utils/quota.py`、授权管理卡片动作抽到 `handlers/auth_actions.py`。
- ✅ 依赖文件精简：`requirements.txt` 改为手工维护最小依赖（lark-oapi、pydantic、pydantic-settings、aiosqlite、pexpect）。

---

## 五、实施路线图

### 阶段 A：权限系统（核心，改动面最大）

1. `database.py`：新增表 + 同步访问函数。
2. `cards/auth.py`：授权卡片 / 管理面板 / 提示卡片。
3. `handlers/events.py`：bootstrap 绑定 + 静默/引导逻辑。
4. `handlers/card_actions.py`：授权按钮、/user 面板按钮。
5. `commands.py`：`/auth`、`/user` 系列命令 + 现有命令 scope 校验。
6. `executor.py`：非 admin 强制受限模式 + 工作区约束。

### 阶段 B：安全修复（P0 → P1）

按第四节清单逐项实施，先做 1/2/3/4/5，再做 6-12。

### 阶段 C：功能改进

quota 重构（第二节）、增量读取（3.1）、/model 超时（3.2）、队列持久化（3.3）、清理残留（3.4）。

---

## 六、涉及文件清单

新增：`database.py`（表）、`cards/auth.py`、`services/quota.py`、`handlers/auth.py`（权限判断工具，可选）
修改：`config.py`、`handlers/events.py`、`handlers/messages.py`、`handlers/pipeline.py`、`handlers/card_actions.py`、`commands.py`、`executor.py`、`multimodal.py`、`lark_client.py`、`stats.py`、`garbage_collection.py`、`main.py`、`.env.example`、`install.sh`、`requirements.txt`、`logger.py`
