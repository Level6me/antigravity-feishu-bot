"""MCP server management wrapper for `agy 1.5.x` CLI.

Provides structured listing (combining `agy mcp list` with local config metadata),
sensitive-value masking, curated templates, and safe argv-only subprocess wrappers
for `add`, `remove`, `enable`, and `disable`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

import config
from logger import log

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SENSITIVE_KV_RE = re.compile(
    r"(?i)((?:token|secret|key|password|passwd|auth|authorization|cookie)[A-Za-z0-9_.-]*\s*[:=]\s*)(?!Bearer\b)([^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)([^\s,;\"']+)")

# Curated quick-install MCP templates for the interactive Feishu card panel
MCP_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "filesystem",
        "name": "filesystem",
        "title": "📁 Filesystem（本地目录读写）",
        "description": "授权安全访问当前项目工作区目录（基于官方 @modelcontextprotocol/server-filesystem）",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{workspace}"],
    },
    {
        "id": "fetch",
        "name": "fetch",
        "title": "🌐 Fetch（网页内容抓取）",
        "description": "将网页 HTML 自动抓取并转换为 Markdown 供模型阅读",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"],
    },
    {
        "id": "memory",
        "name": "memory",
        "title": "🧠 Memory（知识图谱记忆）",
        "description": "基于本地实体关系图的持久化跨会话记忆服务",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
    },
    {
        "id": "context7",
        "name": "context7",
        "title": "📚 Context7（实时开发文档）",
        "description": "按需拉取主流开源框架最新官方文档与 API 示例",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
    },
]


def mask_sensitive_text(text: str) -> str:
    """Redact tokens, secrets, passwords, and Bearer credentials before UI display."""
    if not text:
        return ""
    masked = _BEARER_RE.sub(r"\1***", text)
    masked = _SENSITIVE_KV_RE.sub(r"\1***", masked)
    return masked


def is_valid_mcp_name(name: str) -> bool:
    return bool(name and _SAFE_NAME_RE.match(name.strip()))


def _get_agy_bin() -> Optional[str]:
    return config.ANTIGRAVITY_BIN or config.find_antigravity_bin()


def _candidate_mcp_config_paths() -> List[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".gemini", "antigravity-cli", "mcp_config.json"),
        os.path.join(home, ".gemini", "antigravity-cli", "settings.json"),
        os.path.join(home, ".gemini", "antigravity", "mcp_config.json"),
        os.path.join(home, ".gemini", "settings.json"),
        os.path.join(home, ".gemini", "mcp_config.json"),
    ]


def _load_local_mcp_config_metadata(
    config_paths: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Best-effort read of structured MCP server metadata from local agy config files.

    Only records env/header *key names* (never values) so secrets never leak to cards.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for path in (config_paths if config_paths is not None else _candidate_mcp_config_paths()):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        servers_obj = data.get("mcpServers") or data.get("mcp_servers")
        if not isinstance(servers_obj, dict):
            continue
        for srv_name, raw_cfg in servers_obj.items():
            if not isinstance(srv_name, str) or not isinstance(raw_cfg, dict):
                continue
            url = str(raw_cfg.get("url") or raw_cfg.get("serverUrl") or raw_cfg.get("httpUrl") or "").strip()
            command = str(raw_cfg.get("command") or "").strip()
            raw_args = raw_cfg.get("args")
            args_list = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            raw_type = str(raw_cfg.get("type") or raw_cfg.get("transport") or "").strip().lower()
            if raw_type in ("http", "sse", "streamable-http", "streamable_http"):
                transport = "http"
            elif raw_type == "stdio":
                transport = "stdio"
            else:
                transport = "http" if url else "stdio"

            if "disabled" in raw_cfg:
                enabled = not bool(raw_cfg.get("disabled"))
            elif "enabled" in raw_cfg:
                enabled = bool(raw_cfg.get("enabled"))
            else:
                enabled = True

            env_obj = raw_cfg.get("env")
            env_keys = sorted(str(k) for k in env_obj.keys()) if isinstance(env_obj, dict) else []
            headers_obj = raw_cfg.get("headers")
            header_keys = sorted(str(k) for k in headers_obj.keys()) if isinstance(headers_obj, dict) else []

            target = url if transport == "http" else " ".join([command] + args_list).strip()
            merged[srv_name] = {
                "name": srv_name,
                "transport": transport,
                "enabled": enabled,
                "command": command,
                "args": args_list,
                "url": url,
                "target": mask_sensitive_text(target),
                "env_keys": env_keys,
                "header_keys": header_keys,
                "source": "config",
            }
    return merged


def parse_agy_mcp_list_output(raw_output: str) -> List[Dict[str, Any]]:
    """Parse human-readable `agy mcp list` output into structured server dicts."""
    clean = _ANSI_RE.sub("", raw_output or "").strip()
    if not clean:
        return []
    lower_clean = clean.lower()
    if "no mcp servers configured" in lower_clean or lower_clean in ("[]", "{}"):
        return []

    # Support JSON output in case a future agy version outputs JSON directly
    if clean.startswith("[") or clean.startswith("{"):
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, list):
                out = []
                for item in parsed:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    url = str(item.get("url") or "").strip()
                    cmd = str(item.get("command") or "").strip()
                    args = [str(a) for a in (item.get("args") or [])] if isinstance(item.get("args"), list) else []
                    transport = str(item.get("type") or item.get("transport") or ("http" if url else "stdio")).lower()
                    enabled = (not bool(item.get("disabled"))) if "disabled" in item else bool(item.get("enabled", True))
                    target = url if transport == "http" else " ".join([cmd] + args).strip()
                    out.append({
                        "name": str(item["name"]),
                        "transport": "http" if "http" in transport else "stdio",
                        "enabled": enabled,
                        "command": cmd,
                        "args": args,
                        "url": url,
                        "target": mask_sensitive_text(target),
                        "env_keys": sorted(str(k) for k in (item.get("env") or {}).keys()) if isinstance(item.get("env"), dict) else [],
                        "header_keys": sorted(str(k) for k in (item.get("headers") or {}).keys()) if isinstance(item.get("headers"), dict) else [],
                        "source": "cli_json",
                    })
                return out
        except Exception:
            pass

    servers: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for raw_line in clean.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(("configured mcp servers", "mcp servers", "name ", "---", "===")):
            continue

        # Indented detail lines belonging to previous server entry
        if (line.startswith(("  ", "\t")) and current is not None) and ":" in stripped:
            k, _, v = stripped.partition(":")
            kl = k.strip().lower()
            val = v.strip()
            if kl in ("command", "cmd"):
                current["command"] = val
                if not current.get("target"):
                    current["target"] = mask_sensitive_text(val)
            elif kl == "args":
                current["target"] = mask_sensitive_text(f"{current.get('command', '')} {val}".strip())
            elif kl == "url":
                current["url"] = val
                current["transport"] = "http"
                current["target"] = mask_sensitive_text(val)
            elif kl in ("type", "transport"):
                current["transport"] = "http" if "http" in val.lower() else "stdio"
            elif kl == "status":
                current["enabled"] = "disabl" not in val.lower() and "off" not in val.lower()
            continue

        # Bullet / symbol prefix removal
        body = re.sub(r"^(?:[-*•✓✔✗✘🟢⚪🔴⏸️▶️]+\s*)+", "", stripped).strip()
        if not body:
            continue

        # Pattern A: `name (stdio) [disabled]: npx -y ...` or `name: https://... (disabled)`
        m = re.match(
            r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,63})"
            r"(?:\s*\((?P<meta1>[^)]+)\))?"
            r"(?:\s*\[(?P<meta2>[^\]]+)\])?"
            r"(?:\s*[:\-–—]\s*(?P<rest>.*))?$",
            body,
        )
        if not m:
            # Pattern B: whitespace-separated table row `name   stdio   enabled   npx ...`
            parts = re.split(r"\s{2,}", body)
            if parts and is_valid_mcp_name(parts[0]):
                name = parts[0]
                rest_joined = " ".join(parts[1:])
                low = rest_joined.lower()
                transport = "http" if ("http://" in low or "https://" in low or "http" in parts[1:3]) else "stdio"
                enabled = "disabled" not in low
                target = parts[-1] if len(parts) >= 2 else ""
                if target.lower() in ("enabled", "disabled", "stdio", "http"):
                    target = ""
                current = {
                    "name": name,
                    "transport": transport,
                    "enabled": enabled,
                    "command": "" if transport == "http" else target,
                    "args": [],
                    "url": target if transport == "http" else "",
                    "target": mask_sensitive_text(target),
                    "env_keys": [],
                    "header_keys": [],
                    "source": "cli_text",
                }
                servers.append(current)
            continue

        name = m.group("name")
        meta1 = (m.group("meta1") or "").strip()
        meta2 = (m.group("meta2") or "").strip()
        rest = (m.group("rest") or "").strip()
        combined_meta = f"{meta1} {meta2} {rest}".lower()

        enabled = not bool(re.search(r"\bdisabled\b|\binactive\b|已禁用|已停用", combined_meta))
        # Strip trailing status tags from rest target
        clean_target = re.sub(r"\s*[\(\[]\s*(?:enabled|disabled|stdio|http)\s*[\)\]]\s*$", "", rest, flags=re.IGNORECASE).strip()
        if "http://" in clean_target.lower() or "https://" in clean_target.lower() or "http" in f"{meta1} {meta2}".lower():
            transport = "http"
        else:
            transport = "stdio"

        current = {
            "name": name,
            "transport": transport,
            "enabled": enabled,
            "command": "" if transport == "http" else clean_target,
            "args": [],
            "url": clean_target if transport == "http" else "",
            "target": mask_sensitive_text(clean_target),
            "env_keys": [],
            "header_keys": [],
            "source": "cli_text",
        }
        servers.append(current)

    return servers


async def _exec_agy_mcp(argv_tail: List[str], timeout_seconds: float = 25.0) -> Tuple[bool, str, str]:
    """Execute `agy mcp <argv_tail...>` safely via execve (no shell) with timeout."""
    bin_path = _get_agy_bin()
    if not bin_path:
        return False, "", "未找到 `agy` 可执行文件。"

    cmd = [bin_path, "mcp"] + list(argv_tail)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        stdout_s = _ANSI_RE.sub("", (out_b or b"").decode("utf-8", errors="replace")).strip()
        stderr_s = _ANSI_RE.sub("", (err_b or b"").decode("utf-8", errors="replace")).strip()
        return proc.returncode == 0, stdout_s, stderr_s
    except asyncio.TimeoutError:
        return False, "", f"执行 `agy mcp {' '.join(argv_tail[:2])}` 超时（{int(timeout_seconds)}s）。"
    except Exception as e:
        return False, "", f"执行 `agy mcp` 异常：{e}"


async def list_mcp_servers(
    config_paths: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return `(servers, error_message)` combining `agy mcp list` and local config metadata."""
    cfg_meta = _load_local_mcp_config_metadata(config_paths=config_paths)
    ok, stdout_s, stderr_s = await _exec_agy_mcp(["list"], timeout_seconds=15.0)
    if not ok:
        # If CLI fails (e.g., sandbox read-only), fall back to local config metadata if present
        if cfg_meta:
            return sorted(cfg_meta.values(), key=lambda x: x["name"].lower()), None
        err = mask_sensitive_text(stderr_s or stdout_s or "查询 MCP 列表失败")
        return [], err

    cli_servers = parse_agy_mcp_list_output(stdout_s)
    if not cli_servers and "no mcp servers configured" in stdout_s.lower():
        return [], None

    merged_by_name: Dict[str, Dict[str, Any]] = {}
    for srv in cli_servers:
        name = srv["name"]
        base = dict(cfg_meta.get(name) or {})
        base.update({k: v for k, v in srv.items() if v not in ("", [], None)})
        # CLI authoritative enabled flag always wins if explicitly parsed
        base["enabled"] = srv["enabled"]
        base["name"] = name
        base.setdefault("transport", "stdio")
        base.setdefault("target", "")
        base.setdefault("env_keys", [])
        base.setdefault("header_keys", [])
        base.setdefault("args", [])
        base["target"] = mask_sensitive_text(base.get("target") or "")
        merged_by_name[name] = base

    # If `agy mcp list` returned non-empty text we couldn't parse line-by-line, surface config entries
    if not merged_by_name and cfg_meta:
        merged_by_name.update(cfg_meta)

    return sorted(merged_by_name.values(), key=lambda x: x["name"].lower()), None


async def set_mcp_server_enabled(name: str, enabled: bool) -> Tuple[bool, str]:
    clean_name = (name or "").strip()
    if not is_valid_mcp_name(clean_name):
        return False, f"非法 MCP 服务名称：`{clean_name}`"
    action = "enable" if enabled else "disable"
    ok, out_s, err_s = await _exec_agy_mcp([action, clean_name], timeout_seconds=20.0)
    if ok:
        verb = "已启用" if enabled else "已禁用"
        return True, f"{verb} MCP 服务 `{clean_name}`"
    return False, mask_sensitive_text(err_s or out_s or f"{action} 失败")


async def remove_mcp_server(name: str) -> Tuple[bool, str]:
    clean_name = (name or "").strip()
    if not is_valid_mcp_name(clean_name):
        return False, f"非法 MCP 服务名称：`{clean_name}`"
    ok, out_s, err_s = await _exec_agy_mcp(["remove", clean_name], timeout_seconds=20.0)
    if ok:
        return True, f"已移除 MCP 服务 `{clean_name}`"
    return False, mask_sensitive_text(err_s or out_s or "移除失败")


def parse_kv_lines(raw_text: str, *, mode: str = "env") -> List[str]:
    """Parse multiline KEY=VALUE (env) or Key: Value (header) input safely."""
    results: List[str] = []
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if mode == "env":
            if "=" in s:
                k, _, v = s.partition("=")
                if k.strip():
                    results.append(f"{k.strip()}={v.strip()}")
        else:
            if ":" in s:
                k, _, v = s.partition(":")
                if k.strip():
                    results.append(f"{k.strip()}: {v.strip()}")
    return results


async def add_mcp_server(
    name: str,
    target: str,
    *,
    transport: str = "auto",
    args: Optional[List[str]] = None,
    env_pairs: Optional[List[str]] = None,
    headers: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Add or update an MCP server via `agy mcp add` (flags strictly placed before <name>)."""
    clean_name = (name or "").strip()
    clean_target = (target or "").strip()
    if not is_valid_mcp_name(clean_name):
        return False, "服务名称仅支持字母、数字、点、下划线和短横线（1~64 字符）。"
    if not clean_target:
        return False, "请填写启动命令（stdio）或服务端点 URL（http）。"

    t_norm = (transport or "auto").strip().lower()
    if t_norm == "auto":
        t_norm = "http" if clean_target.lower().startswith(("http://", "https://")) else "stdio"
    if t_norm not in ("stdio", "http"):
        return False, f"不支持的传输类型：`{t_norm}`（仅支持 stdio 或 http）"

    argv = ["add", "--type", t_norm]
    for kv in (env_pairs or []):
        if "=" in kv:
            argv.extend(["--env", kv])
    for hdr in (headers or []):
        if ":" in hdr:
            argv.extend(["--header", hdr])

    argv.append(clean_name)

    extra_args = [str(a) for a in (args or []) if str(a).strip()]
    # Support user typing full command + args in `target` for stdio (e.g., `npx -y @pkg/foo`)
    if t_norm == "stdio" and not extra_args and (" " in clean_target):
        try:
            parts = shlex.split(clean_target)
            if parts:
                clean_target = parts[0]
                extra_args = parts[1:]
        except ValueError:
            pass

    if clean_target.startswith("-") or any(a.startswith("-") for a in extra_args):
        argv.append("--")
    argv.append(clean_target)
    if t_norm == "stdio" and extra_args:
        argv.extend(extra_args)

    ok, out_s, err_s = await _exec_agy_mcp(argv, timeout_seconds=25.0)
    if ok:
        return True, f"已添加/更新 MCP 服务 `{clean_name}`（`{t_norm}`）"
    return False, mask_sensitive_text(err_s or out_s or "添加失败")


async def add_mcp_from_template(template_id: str, workspace_dir: Optional[str] = None) -> Tuple[bool, str]:
    """Install one of the curated `MCP_TEMPLATES`."""
    tpl = next((t for t in MCP_TEMPLATES if t["id"] == template_id), None)
    if not tpl:
        return False, f"未找到 MCP 模板：`{template_id}`"
    ws = workspace_dir if (workspace_dir and os.path.isdir(workspace_dir)) else "/tmp"
    resolved_args = [a.replace("{workspace}", ws) for a in (tpl.get("args") or [])]
    return await add_mcp_server(
        name=tpl["name"],
        target=tpl["command"],
        transport=tpl.get("transport", "stdio"),
        args=resolved_args,
    )


async def run_raw_mcp_cli(parsed_args: List[str]) -> Tuple[bool, str]:
    """Run raw `agy mcp <parsed_args...>` for slash-command compatibility."""
    ok, out_s, err_s = await _exec_agy_mcp(parsed_args, timeout_seconds=25.0)
    combined = "\n".join(x for x in (out_s, err_s) if x).strip()
    return ok, mask_sensitive_text(combined or ("执行成功" if ok else "执行失败"))
