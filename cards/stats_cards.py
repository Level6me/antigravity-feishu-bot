import os
import json
import base64
import time
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta

from cards.common import create_footer

# 北京时间 (UTC+8) 时区定义
BEIJING_TZ = timezone(timedelta(hours=8))

# 进程级账号 Email 缓存，避免每次渲染卡片重复文件读取或阻塞主事件循环请求外网
_ACCOUNT_EMAIL_CACHE = {"email": "", "mtime": -1.0, "ts": 0.0}
_ACCOUNT_CACHE_TTL = 600.0


def get_antigravity_account(allow_network: bool = False) -> str:
    """获取 antigravity cli 当前登录的账号 Email（带进程级缓存，默认零外网阻塞）"""
    try:
        from config import get_oauth_token_path
        token_path = get_oauth_token_path()
        if not os.path.exists(token_path):
            return ""

        now = time.time()
        try:
            mtime = os.path.getmtime(token_path)
        except Exception:
            mtime = 0.0

        if (
            _ACCOUNT_EMAIL_CACHE["email"]
            and _ACCOUNT_EMAIL_CACHE["mtime"] == mtime
            and (now - _ACCOUNT_EMAIL_CACHE["ts"]) < _ACCOUNT_CACHE_TTL
        ):
            return _ACCOUNT_EMAIL_CACHE["email"]

        with open(token_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        resolved_email = ""
        if isinstance(data, dict):
            # 1. 尝试直接读取根或 token 下保存的 email
            if "email" in data and data["email"]:
                resolved_email = str(data["email"])
            token_obj = data.get("token", {}) if isinstance(data.get("token"), dict) else data
            if not resolved_email and isinstance(token_obj, dict) and "email" in token_obj and token_obj["email"]:
                resolved_email = str(token_obj["email"])

            # 2. 从 id_token 中解码 JWT 获取 email
            if not resolved_email:
                id_token = token_obj.get("id_token") if isinstance(token_obj, dict) else None
                if not id_token and isinstance(data, dict):
                    id_token = data.get("id_token")

                if id_token and isinstance(id_token, str) and id_token.count(".") == 2:
                    try:
                        payload_b64 = id_token.split(".")[1]
                        rem = len(payload_b64) % 4
                        if rem:
                            payload_b64 += "=" * (4 - rem)
                        payload_json = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
                        payload = json.loads(payload_json)
                        if "email" in payload and payload["email"]:
                            resolved_email = str(payload["email"])
                    except Exception:
                        pass

            # 3. 备用方式：仅在后台线程显式允许网络时请求 Google UserInfo API，严禁阻塞主循环
            if not resolved_email and allow_network:
                access_token = token_obj.get("access_token") if isinstance(token_obj, dict) else None
                if access_token:
                    try:
                        req = urllib.request.Request(
                            "https://www.googleapis.com/oauth2/v3/userinfo",
                            headers={"Authorization": f"Bearer {access_token}"}
                        )
                        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=2.5) as resp:
                            user_info = json.loads(resp.read().decode("utf-8"))
                            if "email" in user_info and user_info["email"]:
                                resolved_email = str(user_info["email"])
                    except Exception:
                        pass

        if resolved_email:
            _ACCOUNT_EMAIL_CACHE["email"] = resolved_email
            _ACCOUNT_EMAIL_CACHE["mtime"] = mtime
            _ACCOUNT_EMAIL_CACHE["ts"] = now
            return resolved_email
    except Exception:
        pass
    return _ACCOUNT_EMAIL_CACHE.get("email", "")


def get_local_tz_info():
    """获取当前系统设备的本地时区对象与 UTC 偏移量字符串 (如 UTC+8, UTC-5)"""
    try:
        local_now = datetime.now().astimezone()
        tz = local_now.tzinfo
        offset = local_now.utcoffset()
        if offset is None:
            return timezone.utc, "UTC+0"
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        abs_seconds = abs(total_seconds)
        hours = abs_seconds // 3600
        minutes = (abs_seconds % 3600) // 60
        if minutes == 0:
            tz_str = f"UTC{sign}{hours}"
        else:
            tz_str = f"UTC{sign}{hours}:{minutes:02d}"
        return tz, tz_str
    except Exception:
        return timezone.utc, "UTC+0"

LOCAL_TZ, LOCAL_TZ_STR = get_local_tz_info()


def build_quota_card(quota_data):
    account_email = get_antigravity_account()
    header_content = "⚡ **Google AI Pro 额度大盘** (实时同步自本地 LSP)"
    if account_email:
        header_content += f"\n👤 **当前登录账号**: `{account_email}`"

    elements = [
        {
            "tag": "markdown",
            "content": header_content
        },
        {
            "tag": "hr"
        }
    ]
    
    if not quota_data or "response" not in quota_data or "groups" not in quota_data["response"]:
        elements.append({
            "tag": "markdown",
            "content": "❌ **无法获取配额数据**\n请检查宿主机上的 Antigravity 登录状态与网络连接。"
        })
    else:
        groups = quota_data["response"]["groups"]
        for g_idx, group in enumerate(groups):
            group_name = group.get("displayName", "未知模型组")
            desc = group.get("description", "")
            group_content = f"📁 **{group_name}**\n"
            if desc:
                group_content += f"ℹ️ _{desc}_\n"
            
            buckets = group.get("buckets", [])
            for bucket in buckets:
                bucket_name = bucket.get("displayName", "未知配额项")
                remaining = bucket.get("remainingFraction", 0.0)
                reset_time = bucket.get("resetTime", "")
                
                percentage = round(remaining * 100, 1)
                progress_emoji = "🟢" if percentage > 50 else ("🟡" if percentage > 20 else "🔴")
                
                filled_blocks = int(percentage / 10)
                bar_str = "■" * filled_blocks + "□" * (10 - filled_blocks)
                
                percentage_str = f"{percentage:.1f}%"
                group_content += f"• **{bucket_name}**:\n`[{bar_str}] {percentage_str:>6}` {progress_emoji}\n"
                if reset_time:
                    try:
                        # 解析 ISO 格式 UTC 时间并转换为当前设备本地系统时区 (动态 UTC+x)
                        ts_str = reset_time.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(ts_str)
                        dt_local = dt.astimezone(LOCAL_TZ)
                        friendly_reset = dt_local.strftime('%m-%d %H:%M')
                        group_content += f"(🕒 重置: {friendly_reset} {LOCAL_TZ_STR})\n"
                    except Exception:
                        group_content += f"(🕒 {reset_time})\n"
                
            elements.append({
                "tag": "markdown",
                "content": group_content.strip()
            })
            
            if g_idx < len(groups) - 1:
                elements.append({
                    "tag": "hr"
                })
            
    # 智能探测高余量推荐模型按钮
    best_model_btn = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "🎛️ 切换模型 (/model)"},
        "type": "default",
        "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
    }
    if quota_data and "response" in quota_data and "groups" in quota_data["response"]:
        best_group = None
        best_pct = -1.0
        has_low_bucket = False
        for g in quota_data["response"].get("groups", []):
            if not isinstance(g, dict):
                continue
            buckets = [b for b in (g.get("buckets") or []) if isinstance(b, dict)]
            if not buckets:
                continue
            min_f = min(float(b.get("remainingFraction", 0.0)) for b in buckets)
            if min_f < 0.30:
                has_low_bucket = True
            if min_f > best_pct:
                best_pct = min_f
                best_group = g.get("displayName", "").lower()
        if has_low_bucket and best_pct >= 0.50 and best_group:
            rec_model = (
                "gemini-3.8-flash-high"
                if "gemini" in best_group and "argon" not in best_group
                else ("claude-sonnet-5-5-high" if ("claude" in best_group or "3p" in best_group) else "gemini-3.8-flash-high")
            )
            best_model_btn = {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"⚡ 一键切高余量 ({rec_model})"},
                "type": "default",
                "value": {"action": "switch_model", "model": rec_model}
            }

    elements.append({
        "tag": "hr"
    })
    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔄 刷新额度配额"},
                "type": "primary",
                "value": {"action": "refresh_quota"}
            },
            best_model_btn,
        ]
    })
    
    elements.append(create_footer())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📊 Google AI Pro 套餐额度查询", "tag": "plain_text"}
        },
        "elements": elements
    }


def build_context_card(stats):
    """
    构建飞书卡片消息：上下文使用率看板 (Context Usage Card)
    """
    model = stats.get("model", "Gemini 3.6 Flash (High)")
    total_tokens = stats.get("total_tokens", 0)
    max_tokens = stats.get("max_tokens", 1000000)
    total_pct = stats.get("total_pct", 0.0)
    
    user_tokens = stats.get("user_tokens", 0)
    user_pct = stats.get("user_pct", 0.0)
    agent_tokens = stats.get("agent_tokens", 0)
    agent_pct = stats.get("agent_pct", 0.0)
    tool_tokens = stats.get("tool_tokens", 0)
    tool_pct = stats.get("tool_pct", 0.0)
    free_tokens = stats.get("free_tokens", max_tokens)
    free_pct = stats.get("free_pct", 100.0)
    conv_id = stats.get("conv_id", "N/A")
    steps_count = stats.get("steps_count", 0)

    def format_k(num):
        if num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}K"
        return str(num)

    def make_progress_bar(pct, length=12):
        filled = int(round((pct / 100.0) * length))
        filled = max(0, min(length, filled))
        return "🟩" * filled + "⬜" * (length - filled)

    bar_str = make_progress_bar(total_pct)

    header_template = "blue"
    if total_pct > 80:
        header_template = "red"
    elif total_pct > 50:
        header_template = "orange"

    elements = [
        {
            "tag": "markdown",
            "content": f"🤖 **当前模型**：`{model}`\n💬 **会话 ID**：`{conv_id}` (`{steps_count}` 步历史步骤)"
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": f"⚡ **总体上下文用量 (Total Usage)**\n`{format_k(total_tokens)} / {format_k(max_tokens)} tokens` (**{total_pct}%**)\n{bar_str}"
        },
        {"tag": "hr"},
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"👤 **User Messages**\n`{format_k(user_tokens)}` tokens (**{user_pct}%**)"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🤖 **Agent Responses**\n`{format_k(agent_tokens)}` tokens (**{agent_pct}%**)"
                        }
                    ]
                }
            ]
        },
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🛠️ **Tool Calls**\n`{format_k(tool_tokens)}` tokens (**{tool_pct}%**)"
                        }
                    ]
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"🟩 **Free Space**\n`{format_k(free_tokens)}` tokens (**{free_pct}%**)"
                        }
                    ]
                }
            ]
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 刷新数据"},
                    "type": "primary",
                    "value": {"action": "user_choice", "choice": "/context", "label": "刷新数据"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🧹 清空上下文"},
                    "type": "danger",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认清空当前对话上下文"},
                        "text": {"tag": "plain_text", "content": "清空后将开启全新对话（项目长效记忆胶囊不受影响），确定继续吗？"}
                    },
                    "value": {"action": "user_choice", "choice": "/clear", "label": "清空上下文"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🎛️ 切换模型"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/model", "label": "切换模型"}
                }
            ]
        },
        create_footer()
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_template,
            "title": {"content": "🧠 上下文容量看板 (Context Usage)", "tag": "plain_text"}
        },
        "elements": elements
    }
