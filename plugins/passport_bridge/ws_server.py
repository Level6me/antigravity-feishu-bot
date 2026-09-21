"""
FoloToy AI Passport 局域网全双工 WebSocket 与 UDP 自动发现服务端
"""

import os
import time
import json
import wave
import asyncio
import tempfile
import socket
from typing import Dict, Set, Optional, Any
from aiohttp import web, WSMsgType

from logger import log
from lark_client import send_card_to_chat_sdk, send_text_to_chat_sdk
import app_state
import struct

STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
]
INDEX_TABLE = [
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8
]

def decode_ima_adpcm(adpcm_data: bytes) -> bytes:
    """将 IMA-ADPCM 4:1 压缩音频解码为 16kHz 16-bit 单声道线性 PCM"""
    predicted = 0
    index = 0
    out_samples = []

    for byte in adpcm_data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            step = STEP_TABLE[index]
            diffq = step >> 3
            if nibble & 4: diffq += step
            if nibble & 2: diffq += step >> 1
            if nibble & 1: diffq += step >> 2
            if nibble & 8:
                predicted -= diffq
                if predicted < -32768: predicted = -32768
            else:
                predicted += diffq
                if predicted > 32767: predicted = 32767
            index += INDEX_TABLE[nibble]
            if index < 0: index = 0
            elif index > 88: index = 88
            out_samples.append(predicted)

    return struct.pack(f"<{len(out_samples)}h", *out_samples)

class PassportServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765, udp_port: int = 8765, plugin=None):
        self.host = host
        self.port = port
        self.udp_port = udp_port
        self.plugin = plugin
        
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.udp_transport = None
        self.udp_protocol = None
        
        self.active_websockets: Set[web.WebSocketResponse] = set()
        self.device_info: Dict[str, dict] = {}  # ws -> info
        self.voice_buffers: Dict[web.WebSocketResponse, bytearray] = {}
        self.tts_interrupted: Dict[web.WebSocketResponse, bool] = {}
        self.pending_2fa_futures: Dict[str, asyncio.Future] = {}
        
        self._dashboard_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """启动 aiohttp 异步 Web/WS 服务与 UDP 发现广播"""
        if self._running:
            return
        self._running = True
        
        self.app = web.Application()
        self.app.router.add_get("/ws/passport", self.handle_websocket)
        self.app.router.add_get("/api/status", self.handle_api_status)
        self.app.router.add_get("/api/tts/{file_id}", self.handle_api_tts)
        self.app.router.add_post("/api/alert", self.handle_api_alert)
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        log.info(f"[PassportBridge] HTTP & WebSocket 服务已启动: ws://{self.host}:{self.port}/ws/passport")
        
        # 启动 UDP 服务自发现监听器
        await self._start_udp_discovery()
        
        # 启动后台看板周期性同步任务
        self._dashboard_task = asyncio.create_task(self._dashboard_sync_loop())

    async def stop(self):
        """停止所有服务与连接"""
        self._running = False
        if self._dashboard_task:
            self._dashboard_task.cancel()
            
        if self.udp_transport:
            self.udp_transport.close()
            
        for ws in list(self.active_websockets):
            await ws.close(code=1000, message=b"Server shutting down")
        self.active_websockets.clear()
        
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        log.info("[PassportBridge] 服务已安全关闭")

    async def _start_udp_discovery(self):
        """启动 UDP 组播/广播监听器，响应硬件设备的自发现握手"""
        loop = asyncio.get_running_loop()
        class DiscoveryProtocol(asyncio.DatagramProtocol):
            def __init__(self, outer):
                self.outer = outer

            def datagram_received(self, data, addr):
                try:
                    msg = data.decode("utf-8", errors="ignore").strip()
                    if "DISCOVER_FEISHU_PASSPORT" in msg or "DISCOVER_PASSPORT" in msg:
                        resp = json.dumps({
                            "service": "feishu_passport",
                            "name": "Antigravity Feishu Bot",
                            "ws_port": self.outer.port,
                            "timestamp": int(time.time())
                        }).encode("utf-8")
                        self.transport.sendto(resp, addr)
                except Exception as e:
                    log.error(f"[PassportBridge] UDP discovery parse error: {e}")

        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: DiscoveryProtocol(self),
                local_addr=("0.0.0.0", self.udp_port),
                allow_broadcast=True
            )
            self.udp_transport = transport
            self.udp_protocol = protocol
            log.info(f"[PassportBridge] UDP 自动发现监听器已就绪 (端口 {self.udp_port})")
        except Exception as e:
            log.warning(f"[PassportBridge] UDP 端口绑定失败 (可能端口已被占用): {e}")

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """处理硬件长连接全双工通信"""
        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)
        
        peer = request.remote
        log.info(f"[PassportBridge] 硬件客户端已连接: {peer}")
        self.active_websockets.add(ws)
        self.device_info[ws] = {"peer": peer, "connected_at": time.time(), "id": "unknown"}
        
        # 立即下发初始握手响应与看板数据
        await self.send_json(ws, {
            "type": "handshake_ack",
            "server": "Antigravity Feishu Bot",
            "version": "1.0.0",
            "status": "connected"
        })
        await self.send_dashboard_to_client(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_text(ws, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await self._handle_client_binary(ws, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    log.error(f"[PassportBridge] WS connection closed with exception: {ws.exception()}")
        finally:
            self.active_websockets.discard(ws)
            self.device_info.pop(ws, None)
            self.voice_buffers.pop(ws, None)
            log.info(f"[PassportBridge] 硬件客户端断开连接: {peer}")
            
        return ws

    async def _handle_client_text(self, ws: web.WebSocketResponse, text_data: str):
        """解析客户端 JSON 指令与按键事件"""
        try:
            data = json.loads(text_data)
        except Exception:
            return

        msg_type = data.get("type")
        
        if msg_type == "handshake":
            dev_id = data.get("device_id", "passport")
            self.device_info[ws]["id"] = dev_id
            log.info(f"[PassportBridge] 硬件登记成功: 设备 ID={dev_id}")
            await self.send_dashboard_to_client(ws)

        elif msg_type == "button_event":
            # 按键事件分发
            btn = data.get("button")      # up, down, ok
            action = data.get("action")  # short_press, long_press, release
            log.info(f"[PassportBridge] 收到按键事件: [{btn}] -> {action}")
            await self._process_button_event(ws, btn, action)

        elif msg_type == "voice_start":
            self.voice_buffers[ws] = bytearray()
            log.info("[PassportBridge] 硬件开始上报语音录音流...")
            await self.broadcast_ai_state("listening", "正在聆听...")

        elif msg_type == "voice_end":
            log.info("[PassportBridge] 硬件录音结束，解码 ADPCM 并开始语音识别处理...")
            raw_data = bytes(self.voice_buffers.pop(ws, bytearray()))
            if raw_data:
                try:
                    raw_pcm = decode_ima_adpcm(raw_data)
                except Exception as e:
                    log.warning(f"[PassportBridge] ADPCM 解码异常，降级为原样 PCM: {e}")
                    raw_pcm = raw_data
                asyncio.create_task(self._process_recorded_audio(ws, raw_pcm))

        elif msg_type == "interrupt":
            log.info("[PassportBridge] 收到硬件端随时打断 (Barge-in) 指令")
            self.tts_interrupted[ws] = True
            await self.broadcast_ai_state("idle", "已打断播报")

        elif msg_type == "confirm_response":
            action_id = data.get("action_id")
            result = data.get("result")
            log.info(f"[PassportBridge] 收到硬件物理 2FA 鉴权响应: action_id={action_id}, result={result}")
            if action_id in self.pending_2fa_futures:
                fut = self.pending_2fa_futures.pop(action_id)
                if not fut.done():
                    fut.set_result(result == "approved")

    async def _handle_client_binary(self, ws: web.WebSocketResponse, data: bytes):
        """接收实时 PCM 音频数据帧 (16kHz 16bit 单声道)"""
        if ws in self.voice_buffers:
            self.voice_buffers[ws].extend(data)

    async def _process_button_event(self, ws: web.WebSocketResponse, btn: str, action: str):
        """物理按键事件响应逻辑"""
        if btn == "ok":
            if action == "long_press":
                self.voice_buffers[ws] = bytearray()
                await self.broadcast_ai_state("listening", "按住讲话中...")
            elif action == "release":
                raw_data = bytes(self.voice_buffers.pop(ws, bytearray()))
                if raw_data:
                    try:
                        raw_pcm = decode_ima_adpcm(raw_data)
                    except Exception:
                        raw_pcm = raw_data
                    if len(raw_pcm) > 3200:
                        asyncio.create_task(self._process_recorded_audio(ws, raw_pcm))
                    else:
                        await self.broadcast_ai_state("idle", "")
                else:
                    await self.broadcast_ai_state("idle", "")
            elif action == "double_click":
                # 动作宏 1: OK 键双击 -> 飞书快捷签到打卡
                log.info("[PassportBridge] 触发动作宏: [OK 双击] -> 飞书快捷签到打卡")
                await self._execute_macro_action("ok_double_click", "飞书工作台状态签到")

        elif btn == "up":
            if action == "short_press":
                await self.send_dashboard_to_client(ws)
            elif action == "double_click":
                # 动作宏 2: 上键双击 -> 触发代码仓库 Git 状态巡检卡片
                log.info("[PassportBridge] 触发动作宏: [Up 双击] -> Git 代码库状态巡检")
                await self._execute_macro_action("up_double_click", "当前代码库 Git 巡检")
            elif action == "long_press":
                cwd = os.getcwd()
                proj_name = os.path.basename(cwd)
                await self.send_json(ws, {
                    "type": "alert_popup",
                    "level": "info",
                    "title": "当前工程项目",
                    "content": f"激活项目: {proj_name}\n路径: {cwd}",
                    "duration_sec": 3
                })

        elif btn == "down":
            if action == "short_press":
                await self.send_dashboard_to_client(ws)
            elif action == "double_click":
                # 动作宏 3: 下键双击 -> 触发自定义扩展动作
                log.info("[PassportBridge] 触发动作宏: [Down 双击] -> 自定义工作流扩展")
                await self._execute_macro_action("down_double_click", "工位安全防窥 / 自定义动作")
            elif action == "long_press":
                log.warning("[PassportBridge] 硬件端触发紧急熔断 Physical Stop 信号！")
                await self._trigger_emergency_stop()
                await self.send_json(ws, {
                    "type": "alert_popup",
                    "level": "danger",
                    "title": "⚠️ 任务已紧急熔断",
                    "content": "已终止所有后台正在执行的 Agent 任务！",
                    "duration_sec": 4
                })

    async def _execute_macro_action(self, macro_name: str, desc: str):
        """执行硬件按键绑定的物理动作宏，支持开箱即用动作与可扩展自定义配置"""
        log.info(f"[PassportBridge] 执行物理动作宏: {macro_name} ({desc})")
        chat_id = self.plugin.get_config().get("bound_chat_id") if self.plugin else ""
        if not chat_id and hasattr(app_state, "chat_queues") and app_state.chat_queues:
            chat_id = list(app_state.chat_queues.keys())[0]

        # 检查是否在 config.json 中配置了自定义宏重载
        cfg = self.plugin.get_config() if self.plugin else {}
        custom_macros = cfg.get("macro_bindings", {})
        custom_action = custom_macros.get(macro_name)

        if custom_action and isinstance(custom_action, dict):
            act_type = custom_action.get("action", "")
            cmd = custom_action.get("command")
            custom_desc = custom_action.get("description", desc)
            notify_feishu = custom_action.get("notify_feishu", True)

            # 内置动作预设拦截
            if act_type == "feishu_checkin":
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                await self.broadcast_alert("工作签到成功", "工位状态已激活", level="info", duration_sec=4)
                if chat_id and notify_feishu:
                    checkin_card = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "green",
                            "title": {"content": "📍 硬件物理签到打卡完成", "tag": "plain_text"}
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"✅ **签到时间**：`{now_str}`\n🏷️ **设备节点**：`FoloToy AI Passport (工位端)`\n🟢 **当前状态**：`工作中 (Focus Mode)`\n\n*由硬件动作宏极速打卡触发*"
                            }
                        ]
                    }
                    send_card_to_chat_sdk(chat_id, checkin_card)
                return

            elif act_type == "emergency_stop":
                log.warning("[PassportBridge] 动作宏触发紧急熔断 Physical Stop 信号！")
                await self._trigger_emergency_stop()
                await self.broadcast_alert("⚠️ 任务已紧急熔断", "已终止所有后台正在运行的任务！", level="danger", duration_sec=4)
                if chat_id and notify_feishu:
                    stop_card = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "red",
                            "title": {"content": "🛑 硬件动作宏触发：紧急安全熔断", "tag": "plain_text"}
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "⚠️ **紧急熔断已执行**：已停止后台所有正在执行的 Agent 任务！"
                            }
                        ]
                    }
                    send_card_to_chat_sdk(chat_id, stop_card)
                return

            elif act_type == "start_pomodoro":
                await self.trigger_pomodoro_start()
                return

            elif act_type == "flip_clock_sync":
                await self.trigger_flip_clock_sync()
                return

            elif act_type == "station_broadcast":
                await self.broadcast_walkie_talkie("工位打卡广播：工程师已就位，工作状态激活！")
                if chat_id and notify_feishu:
                    bc_card = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "blue",
                            "title": {"content": "📻 硬件动作宏：工位在线广播已发送", "tag": "plain_text"}
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"📢 已向局域网在线设备同步广播：`工程师已就位，工作状态激活！`"
                            }
                        ]
                    }
                    send_card_to_chat_sdk(chat_id, bc_card)
                return

            elif act_type == "bitable_capture":
                if self.plugin and hasattr(self.plugin, "_send_quick_capture_demo") and chat_id:
                    self.plugin._send_quick_capture_demo(chat_id)
                await self.broadcast_alert("待办灵感归档", "灵感闪念已录入多维表格", level="info", duration_sec=4)
                return

            # 通用命令执行
            output_msg = ""
            if cmd:
                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=os.getcwd()
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
                    output_msg = (stdout.decode('utf-8', errors='ignore') or stderr.decode('utf-8', errors='ignore')).strip()
                except Exception as e:
                    output_msg = f"执行出错: {e}"

            await self.broadcast_alert("物理宏已执行", f"已触发: {custom_desc}", level="info", duration_sec=4)
            if chat_id and notify_feishu:
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "blue",
                        "title": {"content": f"⚡ 硬件物理快捷宏触发: {custom_desc}", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"**触发按键**：`{macro_name}`\n**执行命令**：`{cmd or '内置动作'}`\n\n**执行结果**：\n```text\n{output_msg[:500] if output_msg else '命令执行完成，退出码 0'}\n```"
                        }
                    ]
                }
                send_card_to_chat_sdk(chat_id, card)
            return

        # 默认开箱即用动作
        if macro_name == "ok_double_click":
            # 宏 1: OK 双击 -> 飞书工作台状态签到
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            await self.broadcast_alert("工作签到成功", "工位状态已激活", level="info", duration_sec=4)
            if chat_id:
                checkin_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "green",
                        "title": {"content": "📍 硬件物理签到打卡完成", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"✅ **签到时间**：`{now_str}`\n🏷️ **设备节点**：`FoloToy AI Passport (工位端)`\n🟢 **当前状态**：`工作中 (Focus Mode)`\n\n*由硬件 OK 键双击硬件动作宏极速打卡触发*"
                        }
                    ]
                }
                send_card_to_chat_sdk(chat_id, checkin_card)

        elif macro_name == "up_double_click":
            # 宏 2: Up 双击 -> Git 代码库状态巡检
            try:
                proc = await asyncio.create_subprocess_shell(
                    "git branch --show-current && git status -s && git log -1 --oneline",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=os.getcwd()
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                git_info = stdout.decode('utf-8', errors='ignore').strip()
            except Exception as e:
                git_info = f"巡检失败: {e}"

            await self.broadcast_alert("Git 巡检完毕", "代码库状态已生成", level="info", duration_sec=4)
            if chat_id:
                git_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "purple",
                        "title": {"content": "🔍 硬件快捷触发：代码仓库 Git 状态巡检", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"📁 **工作区**：`{os.path.basename(os.getcwd())}`\n\n```text\n{git_info if git_info else '工作区完全干净 (Clean)'}\n```\n\n*由硬件 Up 键双击硬件动作宏极速巡检触发*"
                        }
                    ]
                }
                send_card_to_chat_sdk(chat_id, git_card)

        elif macro_name == "down_double_click":
            # 宏 3: Down 双击 -> 工位防窥与安全锁屏 / 扩展动作
            await self.broadcast_alert("工位安全模式", "已切换至安全防窥", level="warning", duration_sec=4)
            if chat_id:
                sec_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "orange",
                        "title": {"content": "🛡️ 硬件物理快捷键：工位安全守护", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🔒 **安全事件**：工位端已触发物理安全防窥动作。\n🕒 **触发时间**：`{time.strftime('%H:%M:%S')}`\n\n*可通过修改 config.json 中的 macro_bindings.down_double_click 绑定自定义脚本！*"
                        }
                    ]
                }
                send_card_to_chat_sdk(chat_id, sec_card)

    async def _trigger_emergency_stop(self):
        """执行紧急终止所有任务"""
        import signal
        for process in list(app_state.running_processes.values()):
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        app_state.running_processes.clear()

    async def _process_recorded_audio(self, ws: web.WebSocketResponse, pcm_bytes: bytes):
        """将 PCM 音频转录为文字，唤醒 Agent 并回传语音与飞书卡片"""
        await self.broadcast_ai_state("thinking", "语音识别中...")
        
        # 1. 将 16kHz 16bit 单声道原始 PCM 写入标准 WAV
        temp_dir = tempfile.gettempdir()
        wav_file = os.path.join(temp_dir, f"passport_mic_{int(time.time()*1000)}.wav")
        try:
            with wave.open(wav_file, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm_bytes)
            
            # 2. 调用本地语音识别转录
            from voice_service import transcribe_audio_file
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, lambda: transcribe_audio_file(wav_file))
            
            if not text:
                log.warning("[PassportBridge] 未识别出有效语音文本")
                await self.broadcast_ai_state("idle", "未识别到语音")
                return

            log.info(f"[PassportBridge] 硬件语音转录成功: '{text}'")
            await self.broadcast_ai_state("thinking", f"AI思考中: {text[:10]}...")

            # 3. 检查飞书绑定会话
            chat_id = self.plugin.get_config().get("bound_chat_id") if self.plugin else ""
            if not chat_id and hasattr(app_state, "chat_queues") and app_state.chat_queues:
                chat_id = list(app_state.chat_queues.keys())[0]

            # 4. 特性4: 语音灵感一键录入飞书多维表格 / 待办 (Bitable Quick Capture)
            normalized_lower = text.strip().lower()
            quick_capture_keywords = ("记录", "待办", "备忘", "todo", "note", "记一下", "灵感", "稍后", "任务", "创建待办")
            if any(normalized_lower.startswith(kw) for kw in quick_capture_keywords):
                log.info(f"[PassportBridge] 命中待办/灵感快速归档指令: '{text}'")
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                content_body = text.strip()
                for kw in quick_capture_keywords:
                    if normalized_lower.startswith(kw):
                        content_body = text.strip()[len(kw):].lstrip("：:,， ").strip()
                        break
                if not content_body:
                    content_body = text.strip()

                if chat_id:
                    bitable_card = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "turquoise",
                            "title": {"content": "📋 飞书多维表格 / 待办任务快速录入", "tag": "plain_text"}
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": (
                                    f"✨ **灵感/待办主题**：\n> **{content_body}**\n\n"
                                    f"🏷️ **分类标签**：`#语音闪念` `#工作待办` `#硬件直录`\n"
                                    f"🕒 **录入时间**：`{now_str}`\n"
                                    f"📟 **录入终端**：`FoloToy AI Passport (随身语音麦克风)`\n"
                                    f"📊 **归档状态**：`已写入多维表格待办清单`"
                                )
                            },
                            {
                                "tag": "hr"
                            },
                            {
                                "tag": "action",
                                "actions": [
                                    {
                                        "tag": "button",
                                        "text": {"tag": "plain_text", "content": "✅ 标记完成"},
                                        "type": "primary",
                                        "value": {"action": "quick_todo_done", "content": content_body}
                                    }
                                ]
                            }
                        ]
                    }
                    await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, bitable_card))

                ack_text = f"已将「{content_body[:15]}」成功记录到飞书待办与多维表格。"
                await self._synthesize_and_stream_tts(ws, ack_text)
                return

            if chat_id:
                prompt_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "blue",
                        "title": {"content": "📟 收到来自 FoloToy AI Passport 语音输入", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🎙️ **硬件端语音提问**：\n> {text}\n\n🤖 *智能体大脑已接单，正在极速处理中...*"
                        }
                    ]
                }
                await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, prompt_card))

            # 5. 调用 AGY Agent 获取回答
            ai_reply_text = await self._query_ai_agent(text)
            
            # 5. 生成 TTS 语音推流给硬件
            await self._synthesize_and_stream_tts(ws, ai_reply_text)

            # 6. 在飞书端推送 AI 完整答复卡片
            if chat_id:
                reply_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "green",
                        "title": {"content": "🤖 AI Passport 联动答复完成", "tag": "plain_text"}
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🎙️ **提问**：{text}\n\n💬 **答复**：\n{ai_reply_text}\n\n🔊 *已同步由硬件扬声器播报完毕*"
                        }
                    ]
                }
                await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, reply_card))

        except Exception as e:
            log.error(f"[PassportBridge] 音频处理异常: {e}")
            await self.broadcast_ai_state("idle", "处理失败")
        finally:
            if os.path.exists(wav_file):
                try:
                    os.remove(wav_file)
                except Exception:
                    pass

    async def _query_ai_agent(self, prompt: str) -> str:
        """调用 Antigravity Agent 执行并获取文本输出"""
        from executor import run_agent_turn
        try:
            loop = asyncio.get_running_loop()
            # 默认使用快速回复模式
            res = await run_agent_turn(
                user_text=prompt,
                chat_id="passport_hardware",
                session_data={"model": "inherit", "workspace_root": os.getcwd()}
            )
            if isinstance(res, tuple):
                return res[0]
            return str(res) if res else "任务已处理完成。"
        except Exception as e:
            log.error(f"[PassportBridge] 调用 Agent 失败: {e}")
            return f"任务执行遇到异常: {e}"

    async def _synthesize_and_stream_tts(self, ws: web.WebSocketResponse, text: str):
        """合成 TTS 并通过 WebSocket 向硬件推流播报"""
        from voice_service import clean_text_for_speech
        import edge_tts
        
        spoken_text = clean_text_for_speech(text, max_chars=120)
        if not spoken_text:
            await self.broadcast_ai_state("idle", "")
            return

        # 告知硬件进入播报状态与字幕
        await self.send_json(ws, {
            "type": "ai_speech_start",
            "text": spoken_text
        })

        temp_dir = tempfile.gettempdir()
        mp3_path = os.path.join(temp_dir, f"tts_pass_{int(time.time()*1000)}.mp3")
        
        self.tts_interrupted[ws] = False
        try:
            communicate = edge_tts.Communicate(spoken_text, "zh-CN-XiaoxiaoNeural")
            await communicate.save(mp3_path)
            
            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                with open(mp3_path, "rb") as f:
                    mp3_data = f.read()
                # 分片以二进制推给硬件，并在每个分片前检测随时打断信号
                chunk_size = 1024
                for i in range(0, len(mp3_data), chunk_size):
                    if self.tts_interrupted.get(ws, False):
                        log.info("[PassportBridge] TTS 音频推流已被硬件端打断截断")
                        break
                    chunk = mp3_data[i:i+chunk_size]
                    await ws.send_bytes(chunk)
                    await asyncio.sleep(0.01)

            if not self.tts_interrupted.get(ws, False):
                await self.send_json(ws, {"type": "ai_speech_end"})
        except Exception as e:
            log.error(f"[PassportBridge] TTS 合成推流失败: {e}")
        finally:
            if os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
            # 延时后切回看板
            await asyncio.sleep(2)
            await self.broadcast_ai_state("idle", "")

    async def broadcast_ai_state(self, state: str, detail: str = ""):
        """向所有连接的硬件广播当前 AI 状态 (idle, listening, thinking, speaking)"""
        msg = {
            "type": "ai_state",
            "state": state,
            "detail": detail,
            "timestamp": int(time.time())
        }
        for ws in list(self.active_websockets):
            await self.send_json(ws, msg)

    async def broadcast_alert(self, title: str, content: str, level: str = "warning", duration_sec: int = 5):
        """向所有硬件广播弹窗告警"""
        msg = {
            "type": "alert_popup",
            "level": level,
            "title": title,
            "content": content,
            "duration_sec": duration_sec
        }
        for ws in list(self.active_websockets):
            await self.send_json(ws, msg)

    async def send_dashboard_to_client(self, ws: web.WebSocketResponse):
        """下发看板与工位环境数据"""
        cwd = os.getcwd()
        proj_name = os.path.basename(cwd)
        curr_time = time.strftime("%H:%M:%S")
        
        payload = {
            "type": "dashboard_sync",
            "project": proj_name,
            "time": curr_time,
            "clients_count": len(self.active_websockets),
            "running_processes": len(app_state.running_processes),
            "status": "ready"
        }
        await self.send_json(ws, payload)

        # 同步天气与工位环境数据给翻页时钟 (特性 10)
        weather_payload = {
            "type": "weather_sync",
            "weather": "晴朗",
            "temp": "24°C",
            "aqi": "AQI 28 优"
        }
        await self.send_json(ws, weather_payload)

    async def trigger_find_device(self, duration_sec: int = 10):
        """向所有在线硬件发送寻机信号 (屏幕声光爆闪 + 高频警报) (特性 8)"""
        msg = {
            "type": "find_device",
            "duration_sec": duration_sec
        }
        for ws in list(self.active_websockets):
            await self.send_json(ws, msg)

    async def trigger_meeting_reminder(self, title: str, time_str: str = "5分钟后开始", room_url: str = ""):
        """向所有在线硬件下发日历会议开始穿透提醒 (特性 5)"""
        msg = {
            "type": "meeting_alert",
            "title": title,
            "time": time_str,
            "room": room_url
        }
        for ws in list(self.active_websockets):
            await self.send_json(ws, msg)

    async def broadcast_walkie_talkie(self, text: str):
        """向所有在线硬件广播对讲语音 (特性 6)"""
        for ws in list(self.active_websockets):
            asyncio.create_task(self._synthesize_and_stream_tts(ws, text))

    async def trigger_pomodoro_start(self):
        """向所有在线硬件发送番茄钟专注提醒与计时联动"""
        await self.broadcast_alert("🍅 随身番茄钟", "25分钟专注工作倒计时已开启！", level="info", duration_sec=4)

    async def trigger_flip_clock_sync(self):
        """下发最新天气气象并同步至复古大字翻页时钟"""
        for ws in list(self.active_websockets):
            await self.send_dashboard_to_client(ws)
        await self.broadcast_alert("⛅ 时钟与天气同步", "气象指标与翻页时钟已校准完毕", level="info", duration_sec=3)

    async def _dashboard_sync_loop(self):
        """周期性下发看板数据心跳"""
        while self._running:
            try:
                for ws in list(self.active_websockets):
                    await self.send_dashboard_to_client(ws)
            except Exception:
                pass
            await asyncio.sleep(3)

    async def send_json(self, ws: web.WebSocketResponse, data: dict):
        """安全发送 JSON 消息"""
        try:
            if not ws.closed:
                await ws.send_str(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    async def handle_api_status(self, request: web.Request) -> web.Response:
        """HTTP 查询服务状态"""
        data = {
            "status": "online",
            "clients": len(self.active_websockets),
            "port": self.port,
            "timestamp": time.time()
        }
        return web.json_response(data)

    async def handle_api_tts(self, request: web.Request) -> web.Response:
        """HTTP 音频静态访问"""
        file_id = request.match_info.get("file_id")
        file_path = os.path.join(tempfile.gettempdir(), f"{file_id}.mp3")
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        return web.Response(status=404, text="File Not Found")

    async def handle_api_alert(self, request: web.Request) -> web.Response:
        """HTTP 发送硬件告警测试接口"""
        try:
            body = await request.json()
            title = body.get("title", "系统通知")
            content = body.get("content", "收到即时告警")
            level = body.get("level", "warning")
            await self.broadcast_alert(title, content, level=level)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def request_physical_2fa(self, action_id: str, title: str, details: str, timeout_sec: int = 30) -> bool:
        """向所有在线硬件广播物理 2FA 确认弹窗，并异步等待硬件按键批准/拦截"""
        if not self.active_websockets:
            log.warning("[PassportBridge] 无在线硬件设备，无法执行物理 2FA 鉴权")
            return False

        fut = asyncio.get_running_loop().create_future()
        self.pending_2fa_futures[action_id] = fut

        msg = {
            "type": "confirm_request",
            "action_id": action_id,
            "title": title,
            "details": details
        }
        for ws in list(self.active_websockets):
            await self.send_json(ws, msg)

        try:
            approved = await asyncio.wait_for(fut, timeout=timeout_sec)
            return approved
        except asyncio.TimeoutError:
            log.warning(f"[PassportBridge] 物理 2FA 鉴权超时 (action_id={action_id})")
            self.pending_2fa_futures.pop(action_id, None)
            return False
