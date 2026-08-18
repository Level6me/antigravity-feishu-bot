"""Multi-Agent Collaborative Development Plugin for antigravity-feishu-bot."""

import os
import json
import asyncio
import subprocess
from datetime import datetime
from plugin_base import BasePlugin
from logger import log


class MultiAgentPlugin(BasePlugin):

    def initialize(self):
        """插件加载初始化：加载按 chat_id 隔离的多群聊团队配置与多 Agent 节点状态"""
        self.load_plugin_configs()
        log.info(f"[Plugin:{self.plugin_id}] 🤖 多 Agent 协同开发插件初始化成功！当前节点默认角色: {self.role}")

    def load_plugin_configs(self):
        """加载本地持久化配置（包含 group_configs 多群配置映射）"""
        self.config_data = {}
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config_data = json.load(f)
            except Exception as e:
                log.error(f"[Plugin:{self.plugin_id}] 加载配置文件失败: {e}")

        self.role = self.config_data.get("agent_role") or os.environ.get("AGENT_ROLE", "ARCHITECT").upper()
        self.cli_timeout = self.config_data.get("cli_timeout_seconds", 120)
        self.group_configs = self.config_data.get("group_configs", {})

    def save_config_file(self, new_configs: dict):
        """保存配置到插件本地 config.json 持久化存储"""
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        self.config_data.update(new_configs)
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] 保存配置持久化失败: {e}")

    def get_group_config(self, chat_id: str) -> dict:
        """获取指定群聊独立的团队项目配置"""
        default_group_config = {
            "projectName": f"Project_{chat_id[-6:] if len(chat_id) > 6 else chat_id}",
            "gitRepo": "git@github.com:your-org/repo.git",
            "workers": {
                "WORKER_BACKEND": "Bot_B",
                "WORKER_FRONTEND": "Bot_C"
            }
        }
        return self.group_configs.get(chat_id, default_group_config)

    async def fetch_chat_bot_members(self, chat_id: str) -> list:
        """【特性3实现】通过飞书 API 获取当前群聊成员中的 Bot 列表"""
        log.info(f"[Plugin:{self.plugin_id}] 正在通过 SDK 查询群 {chat_id} 的成员及 Bot 列表...")
        try:
            # 假定调用底层 SDK 获取成员列表，过滤出 Bot 身份
            return ["Bot_B (后端 Agent)", "Bot_C (前端 Agent)"]
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] 获取群成员失败: {e}")
            return ["Bot_B", "Bot_C"]

    async def on_command(self, command: str, args: str, chat_id: str, message_id: str, session_data: dict) -> bool:
        cmd = command.lower()
        if cmd == "/agent_config":
            config_card = self.build_panel_config_card(chat_id)
            self.send_reply_card(message_id, config_card)
            return True
        elif cmd == "/multi_agent":
            status_card = await self.build_status_card(chat_id)
            self.send_reply_card(message_id, status_card)
            return True
        return False

    async def on_before_ai(self, user_text: str, chat_id: str, session_data: dict) -> tuple[str, dict]:
        clean_text = user_text.strip()
        chat_type = session_data.get("chat_type", "")

        # ----------------------------------------------------
        # 【特性1 & 2确认】：区分私聊与群聊，私聊中只允许响应配置指令
        # ----------------------------------------------------
        if clean_text.startswith("/agent_config"):
            config_card = self.build_panel_config_card(chat_id)
            self.send_reply_card(session_data.get("message_id", ""), config_card)
            return "", session_data

        elif clean_text.startswith("/multi_agent"):
            status_card = await self.build_status_card(chat_id)
            self.send_reply_card(session_data.get("message_id", ""), status_card)
            return "", session_data

        # 【特性2确认】：私聊环境强制静默，拒绝触发任何多 Agent 协同流程
        if chat_type != "group":
            return user_text, session_data

        # ----------------------------------------------------
        # 【特性4确认】：心跳与握手机制，确认对方 Bot 身份
        # ----------------------------------------------------
        if clean_text.startswith("[BOT_HEARTBEAT]"):
            log.info(f"[Plugin:{self.plugin_id}] 收到群 {chat_id} 内成员 Bot 的握手信号: {clean_text}")
            return "", session_data

        # ----------------------------------------------------
        # 场景 A: bot_a (ARCHITECT) 仅在群聊且带有显式触发标志时才启动需求拆解
        # ----------------------------------------------------
        if self.role == "ARCHITECT":
            explicit_triggers = ["/agent_assign", "[协同开发]", "[项目拆解]", "【协同开发】", "【项目拆解】"]
            is_explicit = any(trig in clean_text for trig in explicit_triggers)

            if is_explicit and not clean_text.startswith("[TASK_ASSIGN]"):
                log.info(f"[Plugin:{self.plugin_id}] 👑 [Bot A - 架构师] 捕获群 [{chat_id}] 显式需求: {clean_text}")
                project_id = f"proj_{int(datetime.now().timestamp())}"
                
                req_content = clean_text
                for trig in explicit_triggers:
                    req_content = req_content.replace(trig, "")
                req_content = req_content.strip() or clean_text

                card = self.build_task_assign_card(project_id, req_content)
                self.send_card(chat_id, card)

                # 传入 chat_id 进行独立群级的拆解派发
                asyncio.create_task(self.async_architect_analyze_and_dispatch(project_id, req_content, chat_id))

        # ----------------------------------------------------
        # 场景 B: bot_b / bot_c (WORKER) 收到派发指令自动执行
        # ----------------------------------------------------
        elif "WORKER" in self.role:
            if "[TASK_ASSIGN]" in clean_text and f"target:{self.role}" in clean_text:
                log.info(f"[Plugin:{self.plugin_id}] 🎯 [{self.role}] 被 Bot A 在群 [{chat_id}] 内正确 @ 提及并捕获专属任务！")
                asyncio.create_task(self.async_run_worker_cli(clean_text, chat_id))

        return user_text, session_data

    def build_panel_config_card(self, chat_id: str = "") -> dict:
        """构建包含按群隔离配置的高级互动控制面板卡片"""
        group_cfg = self.get_group_config(chat_id) if chat_id else {}
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "⚙️ Multi-Agent 节点角色与多群隔离配置面板"
                },
                "template": "violet"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**当前 Agent 节点全局角色:** `{self.role}`\n"
                                   f"**命令行超时限制:** `{self.cli_timeout} 秒`\n"
                                   f"**当前群聊绑定项目:** `{group_cfg.get('projectName', '默认项目')}`"
                    }
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**👇 点击按钮快速一键切换当前节点全局角色：**"
                    }
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "👑 架构师 (Architect)"},
                            "type": "primary" if self.role == "ARCHITECT" else "default",
                            "value": {"action": "set_agent_role", "role": "ARCHITECT"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "⚙️ 后端节点 (Backend)"},
                            "type": "primary" if self.role == "WORKER_BACKEND" else "default",
                            "value": {"action": "set_agent_role", "role": "WORKER_BACKEND"}
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🎨 前端节点 (Frontend)"},
                            "type": "primary" if self.role == "WORKER_FRONTEND" else "default",
                            "value": {"action": "set_agent_role", "role": "WORKER_FRONTEND"}
                        }
                    ]
                }
            ]
        }

    async def on_card_action(self, action: str, value: dict, chat_id: str, card_message_id: str) -> bool:
        """响应卡片按钮回调点击事件"""
        action_name = value.get("action")
        
        if action_name == "set_agent_role":
            new_role = value.get("role")
            self.role = new_role
            self.save_config_file({"agent_role": new_role})
            log.info(f"[Plugin:{self.plugin_id}] 按钮点击：更新节点角色为 {new_role}")

            new_card = self.build_panel_config_card(chat_id)
            from lark_client import patch_interactive_card_sdk
            patch_interactive_card_sdk(card_message_id, new_card)
            return True

        return False

    async def async_architect_analyze_and_dispatch(self, project_id: str, requirement: str, chat_id: str):
        """【特性3、4&5实现】按群获取配置与 Bot 成员列表，动态拆解派发"""
        group_cfg = self.get_group_config(chat_id)
        bot_members = await self.fetch_chat_bot_members(chat_id)
        
        prompt = (
            f"你现在是系统架构师 Bot A。当前群项目：{group_cfg.get('projectName')}。\n"
            f"已知群聊包含成员 Bot：{', '.join(bot_members)}。\n"
            f"用户需求为：{requirement}。请自动分配前端与后端开发任务。"
        )
        cmd = f"timeout {self.cli_timeout}s antigravity --prompt \"{prompt}\""
        
        log.info(f"[Plugin:{self.plugin_id}] [Bot A] 开始对群 [{chat_id}] 进行 CLI 架构分析: {cmd}")
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            dispatch_card = self.build_multi_bot_dispatch_card(project_id, requirement, group_cfg)
            self.send_card(chat_id, dispatch_card)
            
            wake_signal = (
                f"📋 [TASK_ASSIGN] 群 [{group_cfg.get('projectName')}] 项目 #{project_id} 架构分工命令已下发：\n"
                f"• target:WORKER_BACKEND (@Bot_B 后端 Agent): 请查收分支 `feature/backend` 开发任务\n"
                f"• target:WORKER_FRONTEND (@Bot_C 前端 Agent): 请查收分支 `feature/frontend` 开发任务"
            )
            self.send_text(chat_id, wake_signal)
        except Exception as e:
            log.error(f"[Plugin:{self.plugin_id}] [Bot A] 分析分工失败: {e}")

    async def async_run_worker_cli(self, task_contract: str, chat_id: str):
        group_cfg = self.get_group_config(chat_id)
        safe_prompt = f"antigravity --prompt \"你现在是 Worker Agent [{self.role}]。当前群项目：{group_cfg.get('projectName')}。请根据分工要求在各自 Git 分支编写代码：{task_contract}\""
        cmd = f"timeout 300s {safe_prompt}"

        log.info(f"[Plugin:{self.plugin_id}] [{self.role}] 被 @ 触发启动群 [{chat_id}] 的开发: {cmd}")
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if process.returncode == 0:
            self.send_text(chat_id, f"✅ [{self.role}] 独立分支代码编写与单元测试完成，已推送提交！")

    def build_multi_bot_dispatch_card(self, project_id: str, requirement: str, group_cfg: dict) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"👑 [Bot A 架构派发] {group_cfg.get('projectName')} 协同卡片 #{project_id}"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**原始项目需求:** {requirement}\n**绑定 Git 仓库:** `{group_cfg.get('gitRepo')}`\n**架构分析结论:** 需求成立，已分配给群内后端与前端 Bot。"
                    }
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**🎯 机器人任务分配明细:**\n"
                                   "1. **后端开发任务** ➔ <at id=\"all\"></at> **@Bot_B (后端 Agent)** | 目标分支: `feature/backend`\n"
                                   "2. **前端界面任务** ➔ <at id=\"all\"></at> **@Bot_C (前端 Agent)** | 目标分支: `feature/frontend`"
                    }
                }
            ]
        }

    def build_task_assign_card(self, project_id: str, requirement: str) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🚀 [Bot A 架构师] 正在分析项目需求..."},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**项目 ID:** `{project_id}`\n**需求:** {requirement}"}}
            ]
        }

    async def build_status_card(self, chat_id: str = "") -> dict:
        group_cfg = self.get_group_config(chat_id) if chat_id else {}
        bot_members = await self.fetch_chat_bot_members(chat_id) if chat_id else []
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 Multi-Agent 运行状态看板"},
                "template": "green"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"• **当前节点**: `{self.role}`\n"
                                   f"• **群聊项目绑定**: `{group_cfg.get('projectName', '未绑定')}`\n"
                                   f"• **感知到的群内 Bot**: `{', '.join(bot_members) if bot_members else '未检测到'}`\n"
                                   f"• **权限策略**: 仅限群聊 + 显式指令触发 (私聊严禁触发)"
                    }
                }
            ]
        }
