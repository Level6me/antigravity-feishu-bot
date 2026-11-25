"""Antigravity CLI (`agy 1.5.x`) native plugin manager wrapper.

Provides structured plugin inventory (combining `agy plugin list --json` with local
workspace/global/builtin plugin directory discovery), validation (`agy plugin validate --json`),
import (`agy plugin import`), installation, uninstallation, and enable/disable toggling.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import config
from logger import log

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SAFE_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._@-]{0,127}$")
_JSONC_COMMENT_RE = re.compile(r"(^|\s)//.*$", re.MULTILINE)
_JSONC_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _get_agy_bin() -> Optional[str]:
    return config.ANTIGRAVITY_BIN or config.find_antigravity_bin()


def is_valid_agy_plugin_name(name: str) -> bool:
    s = (name or "").strip()
    return bool(s and not s.startswith("-") and _SAFE_PLUGIN_NAME_RE.match(s))


def _parse_jsonc(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON or JSONC (allowing // comments and trailing commas)."""
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    try:
        cleaned = _JSONC_COMMENT_RE.sub("", text)
        cleaned = _JSONC_TRAILING_COMMA_RE.sub(r"\1", cleaned)
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _load_global_plugin_enabled_overrides(config_paths: Optional[List[str]] = None) -> Dict[str, bool]:
    """Read `plugins.<dir_name>.enabled` from `~/.gemini/config/config.json`."""
    home = os.path.expanduser("~")
    paths = config_paths if config_paths is not None else [
        os.path.join(home, ".gemini", "config", "config.json"),
        os.path.join(config.get_antigravity_home(), "config.json"),
    ]
    overrides: Dict[str, bool] = {}
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = _parse_jsonc(f.read())
        except Exception:
            data = None
        if not isinstance(data, dict):
            continue
        plugins_map = data.get("plugins")
        if isinstance(plugins_map, dict):
            for dir_key, entry in plugins_map.items():
                if isinstance(dir_key, str) and isinstance(entry, dict) and "enabled" in entry:
                    overrides[dir_key] = bool(entry.get("enabled"))
    return overrides


def _inspect_plugin_directory(plugin_dir: str, scope: str, enabled_overrides: Dict[str, bool]) -> Optional[Dict[str, Any]]:
    """Inspect a single plugin directory and extract manifest + component statistics."""
    if not plugin_dir or not os.path.isdir(plugin_dir):
        return None

    manifest_rel_candidates = [
        "plugin.json",
        os.path.join(".antigravity-plugin", "plugin.json"),
        os.path.join(".gemini-plugin", "plugin.json"),
        os.path.join(".claude-plugin", "plugin.json"),
        "gemini-extension.json",
    ]
    manifest_path = ""
    manifest_data: Optional[Dict[str, Any]] = None
    for rel in manifest_rel_candidates:
        cand = os.path.join(plugin_dir, rel)
        if os.path.isfile(cand):
            manifest_path = cand
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    manifest_data = _parse_jsonc(f.read())
            except Exception:
                manifest_data = {}
            break

    if not manifest_path:
        return None
    if not isinstance(manifest_data, dict):
        manifest_data = {}

    dir_name = os.path.basename(os.path.normpath(plugin_dir))
    name = str(manifest_data.get("name") or dir_name).strip() or dir_name
    display_name = str(manifest_data.get("displayName") or manifest_data.get("name") or dir_name).strip()
    version = str(manifest_data.get("version") or "-").strip()
    description = str(manifest_data.get("description") or "").strip()

    author_raw = manifest_data.get("author")
    if isinstance(author_raw, dict):
        author = str(author_raw.get("name") or "").strip()
    elif isinstance(author_raw, str):
        author = author_raw.strip()
    else:
        author = ""

    default_enabled = not bool(manifest_data.get("disabled", False))
    if dir_name in enabled_overrides:
        enabled = enabled_overrides[dir_name]
    elif name in enabled_overrides:
        enabled = enabled_overrides[name]
    else:
        enabled = default_enabled

    # Count bundled components
    skill_names: List[str] = []
    skills_dir = os.path.join(plugin_dir, "skills")
    if os.path.isdir(skills_dir):
        try:
            for entry in sorted(os.listdir(skills_dir)):
                s_dir = os.path.join(skills_dir, entry)
                if os.path.isdir(s_dir) and (
                    os.path.isfile(os.path.join(s_dir, "SKILL.md"))
                    or os.path.isfile(os.path.join(s_dir, "SKILL.md.disabled"))
                ):
                    skill_names.append(entry)
        except Exception:
            pass

    rule_files: List[str] = []
    rules_dir = os.path.join(plugin_dir, "rules")
    if os.path.isdir(rules_dir):
        try:
            for entry in sorted(os.listdir(rules_dir)):
                if entry.lower().endswith(".md"):
                    rule_files.append(entry)
        except Exception:
            pass

    agent_files: List[str] = []
    agents_dir = os.path.join(plugin_dir, "agents")
    if os.path.isdir(agents_dir):
        try:
            for entry in sorted(os.listdir(agents_dir)):
                if entry.lower().endswith(".md"):
                    agent_files.append(entry[:-3])
        except Exception:
            pass

    has_hooks = os.path.isfile(os.path.join(plugin_dir, "hooks.json")) or os.path.isfile(
        os.path.join(plugin_dir, "hooks", "hooks.json")
    )

    mcp_names: List[str] = []
    mcp_cfg_path = os.path.join(plugin_dir, "mcp_config.json")
    if os.path.isfile(mcp_cfg_path):
        try:
            with open(mcp_cfg_path, "r", encoding="utf-8") as f:
                mcp_obj = _parse_jsonc(f.read()) or {}
            srv_map = mcp_obj.get("mcpServers") or mcp_obj.get("mcp_servers") or {}
            if isinstance(srv_map, dict):
                mcp_names = sorted(str(k) for k in srv_map.keys())
        except Exception:
            pass

    sidecars_cnt = 0
    if os.path.isdir(os.path.join(plugin_dir, "sidecars")) or os.path.isfile(os.path.join(plugin_dir, "sidecar.json")):
        sidecars_cnt = 1

    prompts_raw = manifest_data.get("suggestedPrompts")
    suggested_prompts = [str(p).strip() for p in prompts_raw if str(p).strip()][:3] if isinstance(prompts_raw, list) else []

    return {
        "name": name,
        "display_name": display_name,
        "dir_name": dir_name,
        "version": version,
        "description": description,
        "author": author,
        "scope": scope,  # workspace | global | builtin | cli
        "enabled": enabled,
        "path": plugin_dir,
        "skill_names": skill_names,
        "rule_files": rule_files,
        "agent_names": agent_files,
        "mcp_names": mcp_names,
        "has_hooks": has_hooks,
        "components": {
            "skills": len(skill_names),
            "rules": len(rule_files),
            "agents": len(agent_files),
            "mcpServers": len(mcp_names),
            "hooks": 1 if has_hooks else 0,
            "sidecars": sidecars_cnt,
        },
        "suggested_prompts": suggested_prompts,
    }


def _discover_local_agy_plugins(
    workspace_dir: Optional[str] = None,
    plugin_roots: Optional[List[Tuple[str, str]]] = None,
    config_paths: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Scan workspace, global, and builtin plugin roots for `agy` native plugins."""
    overrides = _load_global_plugin_enabled_overrides(config_paths=config_paths)
    if plugin_roots is not None:
        roots = list(plugin_roots)
    else:
        home = os.path.expanduser("~")
        agy_home = config.get_antigravity_home()
        roots = []
        if workspace_dir and os.path.isdir(workspace_dir):
            for dot in (".agents", ".agent", "_agents", "_agent"):
                roots.append(("workspace", os.path.join(workspace_dir, dot, "plugins")))
        roots.append(("global", os.path.join(home, ".gemini", "config", "plugins")))
        roots.append(("global", os.path.join(agy_home, "plugins")))
        roots.append(("builtin", os.path.join(agy_home, "builtin", "plugins")))

    discovered: Dict[str, Dict[str, Any]] = {}
    for scope, root_dir in roots:
        if not root_dir or not os.path.isdir(root_dir):
            continue
        try:
            entries = sorted(os.listdir(root_dir))
        except Exception:
            continue
        for entry in entries:
            p_dir = os.path.join(root_dir, entry)
            info = _inspect_plugin_directory(p_dir, scope=scope, enabled_overrides=overrides)
            if not info:
                continue
            key = info["name"].lower()
            # Higher-precedence root wins (workspace > global > builtin)
            if key not in discovered:
                discovered[key] = info
    return discovered


async def _exec_agy_plugin(argv_tail: List[str], timeout_seconds: float = 25.0) -> Tuple[bool, str, str]:
    """Execute `agy plugin <argv_tail...>` safely without shell expansion."""
    bin_path = _get_agy_bin()
    if not bin_path:
        return False, "", "未找到 `agy` 可执行文件。"

    cmd = [bin_path, "plugin"] + list(argv_tail)
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
        return False, "", f"执行 `agy plugin {' '.join(argv_tail[:2])}` 超时（{int(timeout_seconds)}s）。"
    except Exception as e:
        return False, "", f"执行 `agy plugin` 异常：{e}"


_AGY_PLUGIN_LIST_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]], Optional[str]]] = {}
_AGY_PLUGIN_CACHE_TTL = 15.0


def invalidate_agy_plugin_cache() -> None:
    _AGY_PLUGIN_LIST_CACHE.clear()


async def list_agy_plugins(
    workspace_dir: Optional[str] = None,
    plugin_roots: Optional[List[Tuple[str, str]]] = None,
    config_paths: Optional[List[str]] = None,
    force_refresh: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return `(plugins, error_msg)` combining `agy plugin list --json` and filesystem discovery (15s cache)."""
    import time as _time
    use_cache = (plugin_roots is None and config_paths is None)
    cache_key = os.path.abspath(workspace_dir) if (workspace_dir and os.path.isdir(workspace_dir)) else "__default__"
    if use_cache and not force_refresh and cache_key in _AGY_PLUGIN_LIST_CACHE:
        ts, cached_plugins, cached_err = _AGY_PLUGIN_LIST_CACHE[cache_key]
        if (_time.time() - ts) < _AGY_PLUGIN_CACHE_TTL:
            return [dict(p) for p in cached_plugins], cached_err

    local_map = _discover_local_agy_plugins(
        workspace_dir=workspace_dir,
        plugin_roots=plugin_roots,
        config_paths=config_paths,
    )
    ok, stdout_s, stderr_s = await _exec_agy_plugin(["list", "--json"], timeout_seconds=15.0)
    if ok and stdout_s.startswith("["):
        try:
            cli_items = json.loads(stdout_s)
            if isinstance(cli_items, list):
                for item in cli_items:
                    if not isinstance(item, dict):
                        continue
                    raw_name = str(item.get("name") or item.get("id") or "").strip()
                    if not raw_name:
                        continue
                    key = raw_name.lower()
                    raw_path = str(item.get("path") or item.get("dir") or "").strip()
                    dir_name = os.path.basename(os.path.normpath(raw_path)) if raw_path else raw_name
                    scope = "builtin" if "/builtin/plugins/" in raw_path else (
                        "workspace" if (workspace_dir and raw_path.startswith(workspace_dir)) else "global"
                    )
                    if key in local_map:
                        merged = dict(local_map[key])
                        if "enabled" in item:
                            merged["enabled"] = bool(item.get("enabled"))
                        elif "disabled" in item:
                            merged["enabled"] = not bool(item.get("disabled"))
                        if item.get("version") and merged.get("version") in ("", "-"):
                            merged["version"] = str(item["version"])
                        if item.get("description") and not merged.get("description"):
                            merged["description"] = str(item["description"])
                        local_map[key] = merged
                    else:
                        enabled = (
                            not bool(item.get("disabled"))
                            if "disabled" in item
                            else bool(item.get("enabled", True))
                        )
                        local_map[key] = {
                            "name": raw_name,
                            "display_name": str(item.get("displayName") or raw_name),
                            "dir_name": dir_name,
                            "version": str(item.get("version") or "-"),
                            "description": str(item.get("description") or ""),
                            "author": str(item.get("author") or ""),
                            "scope": scope,
                            "enabled": enabled,
                            "path": raw_path,
                            "skill_names": [],
                            "rule_files": [],
                            "agent_names": [],
                            "mcp_names": [],
                            "has_hooks": False,
                            "components": item.get("components") if isinstance(item.get("components"), dict) else {},
                            "suggested_prompts": [],
                        }
        except Exception as e:
            log.debug(f"[AgyPluginManager] Failed to parse agy plugin list json: {e}")

    scope_order = {"workspace": 0, "global": 1, "cli": 2, "builtin": 3}
    plugins = sorted(
        local_map.values(),
        key=lambda p: (scope_order.get(str(p.get("scope", "global")), 9), str(p.get("name", "")).lower()),
    )
    if not ok and not plugins:
        return [], (stderr_s or stdout_s or "查询 Antigravity 原生插件列表失败")
    if use_cache:
        if len(_AGY_PLUGIN_LIST_CACHE) > 32:
            _AGY_PLUGIN_LIST_CACHE.clear()
        _AGY_PLUGIN_LIST_CACHE[cache_key] = (_time.time(), [dict(p) for p in plugins], None)
    return plugins, None


def _update_local_plugin_config_enabled(target_name: str, enabled: bool, config_path: Optional[str] = None) -> bool:
    """Fallback persistence in `~/.gemini/config/config.json` when CLI toggle targets a local/builtin directory plugin."""
    cfg_file = config_path or os.path.join(os.path.expanduser("~"), ".gemini", "config", "config.json")
    try:
        os.makedirs(os.path.dirname(cfg_file), exist_ok=True)
        data: Dict[str, Any] = {}
        if os.path.isfile(cfg_file):
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = _parse_jsonc(f.read()) or {}
        plugins_map = data.get("plugins")
        if not isinstance(plugins_map, dict):
            plugins_map = {}
        entry = plugins_map.get(target_name)
        if not isinstance(entry, dict):
            entry = {}
        entry["enabled"] = bool(enabled)
        plugins_map[target_name] = entry
        data["plugins"] = plugins_map
        fd, tmp_path = tempfile.mkstemp(prefix=".cfg_", suffix=".json", dir=os.path.dirname(cfg_file))
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, cfg_file)
        return True
    except Exception as e:
        log.warning(f"[AgyPluginManager] Fallback config.json update failed: {e}")
        return False


async def set_agy_plugin_enabled(name: str, enabled: bool, dir_name: str = "") -> Tuple[bool, str]:
    clean_name = (dir_name or name or "").strip()
    if not is_valid_agy_plugin_name(clean_name):
        return False, f"非法插件名称：`{clean_name}`"
    sub = "enable" if enabled else "disable"
    ok, out_s, err_s = await _exec_agy_plugin([sub, clean_name], timeout_seconds=20.0)
    verb = "已启用" if enabled else "已禁用"
    if ok:
        invalidate_agy_plugin_cache()
        return True, f"{verb} Antigravity 原生插件 `{clean_name}`"
    # Fallback to config.json plugins map if CLI reports unknown/not installed in registry
    if _update_local_plugin_config_enabled(clean_name, enabled):
        invalidate_agy_plugin_cache()
        return True, f"{verb}插件 `{clean_name}`（已写入全局插件配置）"
    return False, (err_s or out_s or f"{sub} 失败")


async def uninstall_agy_plugin(name: str, plugin_path: str = "", scope: str = "") -> Tuple[bool, str]:
    clean_name = (name or "").strip()
    if not is_valid_agy_plugin_name(clean_name):
        return False, f"非法插件名称：`{clean_name}`"
    if scope == "builtin" or (plugin_path and "/builtin/plugins/" in plugin_path.replace("\\", "/")):
        return False, f"内置插件 `{clean_name}` 随 CLI 发行，不支持物理卸载；您可以点击「⏸️ 禁用」将其停用。"

    ok, out_s, err_s = await _exec_agy_plugin(["uninstall", clean_name], timeout_seconds=25.0)
    if ok:
        invalidate_agy_plugin_cache()
        return True, f"已卸载 Antigravity 原生插件 `{clean_name}`"

    # If it is a manually installed directory inside ~/.gemini/config/plugins/<clean_name>, remove it safely
    global_plugins_root = os.path.realpath(os.path.join(os.path.expanduser("~"), ".gemini", "config", "plugins"))
    candidate_dir = os.path.realpath(plugin_path) if plugin_path else os.path.realpath(os.path.join(global_plugins_root, clean_name))
    if candidate_dir.startswith(global_plugins_root + os.sep) and os.path.isdir(candidate_dir):
        try:
            shutil.rmtree(candidate_dir)
            invalidate_agy_plugin_cache()
            return True, f"已移除全局插件目录 `{clean_name}`"
        except Exception as e:
            return False, f"移除插件目录失败：{e}"

    return False, (err_s or out_s or "卸载失败")


async def install_agy_plugin(target: str) -> Tuple[bool, str]:
    clean_target = (target or "").strip()
    if not clean_target or clean_target.startswith("-"):
        return False, "请提供有效的插件目录路径、GitHub 仓库 URL 或插件标识。"

    expanded = os.path.expanduser(clean_target)
    if os.path.isdir(expanded):
        clean_target = os.path.abspath(expanded)

    ok, out_s, err_s = await _exec_agy_plugin(["install", clean_target], timeout_seconds=45.0)
    if ok:
        invalidate_agy_plugin_cache()
        msg = out_s or err_s or f"已成功安装插件 `{clean_target}`"
        return True, msg[:360]

    # If target is a GitHub URL and `agy plugin install` requires a local directory, clone and install
    if re.match(r"^https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", clean_target, re.IGNORECASE):
        repo_name = clean_target.rstrip("/").split("/")[-1].replace(".git", "") or "custom-plugin"
        repo_name = re.sub(r"[^A-Za-z0-9._-]", "-", repo_name)
        global_plugins_root = os.path.join(os.path.expanduser("~"), ".gemini", "config", "plugins")
        os.makedirs(global_plugins_root, exist_ok=True)
        dest_dir = os.path.join(global_plugins_root, repo_name)
        try:
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", clean_target, dest_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, g_err = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode != 0:
                return False, f"Git 克隆插件仓库失败：{(g_err or b'').decode('utf-8', errors='replace').strip()[:240]}"
            ok2, out2, err2 = await _exec_agy_plugin(["install", dest_dir], timeout_seconds=30.0)
            if ok2 or os.path.isfile(os.path.join(dest_dir, "plugin.json")):
                invalidate_agy_plugin_cache()
                return True, f"已从 GitHub 克隆并安装原生插件 `{repo_name}`"
            return False, (err2 or out2 or "安装失败")[:360]
        except Exception as e:
            return False, f"从 GitHub 安装插件异常：{e}"

    return False, (err_s or out_s or "安装插件失败")[:360]


async def import_agy_plugins(source: str = "claude") -> Tuple[bool, str]:
    clean_src = (source or "claude").strip()
    if not clean_src or clean_src.startswith("-"):
        return False, "无效的导入来源（支持 `claude`、`gemini` 或目录路径）。"
    ok, out_s, err_s = await _exec_agy_plugin(["import", clean_src], timeout_seconds=30.0)
    combined = "\n".join(x for x in (out_s, err_s) if x).strip()
    if ok:
        invalidate_agy_plugin_cache()
        return True, (combined or f"已完成从 `{clean_src}` 导入插件")[:360]
    return False, (combined or f"从 `{clean_src}` 导入插件失败")[:360]


async def validate_agy_plugin(target: str, strict: bool = False) -> Tuple[bool, str]:
    clean_target = (target or "").strip()
    if not clean_target or clean_target.startswith("-"):
        return False, "请提供待校验的插件目录、`.zip` 或 GitHub URL。"
    expanded = os.path.expanduser(clean_target)
    if os.path.exists(expanded):
        clean_target = os.path.abspath(expanded)

    argv = ["validate", clean_target, "--json"]
    if strict:
        argv.append("--strict")
    ok, out_s, err_s = await _exec_agy_plugin(argv, timeout_seconds=30.0)
    if out_s.startswith("{"):
        try:
            rep = json.loads(out_s)
            p_name = rep.get("name") or os.path.basename(clean_target)
            comps = rep.get("components") or {}
            comp_parts = [f"{k}: {v}" for k, v in comps.items() if v]
            comp_str = " · ".join(comp_parts) if comp_parts else "无组件"
            findings = rep.get("findings") or []
            errs = [f for f in findings if isinstance(f, dict) and f.get("severity") == "error"]
            warns = [f for f in findings if isinstance(f, dict) and f.get("severity") == "warning"]
            summary = f"插件 `{p_name}` 校验{'通过' if ok else '未通过'}（组件：{comp_str}；错误 {len(errs)} 项，警告 {len(warns)} 项）"
            details = []
            for f_item in (errs + warns)[:3]:
                sev = "❌" if f_item.get("severity") == "error" else "⚠️"
                details.append(f"{sev} `{f_item.get('file', '')}`: {f_item.get('message', '')}")
            if details:
                summary += "\n" + "\n".join(details)
            return ok, summary[:480]
        except Exception:
            pass
    combined = "\n".join(x for x in (out_s, err_s) if x).strip()
    return ok, (combined or ("校验通过" if ok else "校验未通过"))[:400]


async def run_raw_agy_plugin_cli(parsed_args: List[str]) -> Tuple[bool, str]:
    ok, out_s, err_s = await _exec_agy_plugin(parsed_args, timeout_seconds=30.0)
    combined = "\n".join(x for x in (out_s, err_s) if x).strip()
    return ok, (combined or ("执行成功" if ok else "执行失败"))
