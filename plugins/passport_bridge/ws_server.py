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
            log.info("[PassportBridge] 硬件录音结束，开始语音识别处理...")
            raw_pcm = bytes(self.voice_buffers.pop(ws, bytearray()))
            if raw_pcm:
                asyncio.create_task(self._process_recorded_audio(ws, raw_pcm))

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
                raw_pcm = bytes(self.voice_buffers.pop(ws, bytearray()))
                if raw_pcm and len(raw_pcm) > 3200:  # 大于 100ms
                    asyncio.create_task(self._process_recorded_audio(ws, raw_pcm))
                else:
                    await self.broadcast_ai_state("idle", "")

        elif btn == "up":
            if action == "short_press":
                # 看板主动刷新
                await self.send_dashboard_to_client(ws)
            elif action == "long_press":
                # 切换/提示当前活跃项目
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
                # 触发健康巡检卡片
                await self.send_dashboard_to_client(ws)
            elif action == "long_press":
                # 紧急熔断物理键：停止正在运行的任务
                log.warning("[PassportBridge] 硬件端触发紧急熔断 Physical Stop 信号！")
                await self._trigger_emergency_stop()
                await self.send_json(ws, {
                    "type": "alert_popup",
                    "level": "danger",
                    "title": "⚠️ 任务已紧急熔断",
                    "content": "已终止所有后台正在执行的 Agent 任务！",
                    "duration_sec": 4
                })

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

            # 3. 联动飞书卡片归档
            chat_id = self.plugin.get_config().get("bound_chat_id") if self.plugin else ""
            if not chat_id and hasattr(app_state, "chat_queues") and app_state.chat_queues:
                chat_id = list(app_state.chat_queues.keys())[0]

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

            # 4. 调用 AGY Agent 获取回答
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
        
        try:
            communicate = edge_tts.Communicate(spoken_text, "zh-CN-XiaoxiaoNeural")
            await communicate.save(mp3_path)
            
            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                with open(mp3_path, "rb") as f:
                    mp3_data = f.read()
                # 分片以二进制推给硬件
                chunk_size = 1024
                for i in range(0, len(mp3_data), chunk_size):
                    chunk = mp3_data[i:i+chunk_size]
                    await ws.send_bytes(chunk)
                    await asyncio.sleep(0.01)

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
        """下发看板数据"""
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
