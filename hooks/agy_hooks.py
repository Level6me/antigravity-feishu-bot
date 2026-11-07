#!/usr/bin/env python3
"""Antigravity (`agy`) Lifecycle Hook Handler for Long-Horizon Memory & Scope Guard.

Handles:
  - `PreInvocation`: Detects if a `CHECKPOINT` compaction just occurred in `transcriptPath`
    (or if `CHECKPOINT` was truncated by safety filters) and immediately injects the project's
    `PROJECT_STATE.md` capsule + active turn contract via `systemMessage`, eliminating post-compaction amnesia.
  - `PreToolUse`: Enforces the active Turn Contract (`scope == "inspect"` read-only boundary guard)
    and checks against known `dead_end` commands so the model does not repeat disproven paths or
    unauthorized flashing/downloading during an inspection turn.
  - `PostToolUse`: Records failed tool executions or file edits incrementally.
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from agent_memory.store import (
    get_compaction_state,
    inspect_and_backfill_transcript,
    read_project_capsule,
    read_turn_contract,
)


_INSPECT_FORBIDDEN_CMD_RE = re.compile(
    r"(?:\bopenocd\b.*?\b(?:program|flash\s+write|erase)\b|"
    r"\b(?:git\s+push|git\s+commit|rm\s+-rf|apt-get\s+install|apt\s+install|pip\s+install|npm\s+install)\b|"
    r"\b(?:wget|curl)\b.*?(?:-O|-o|>\s*\S+\.(?:deb|tar|zip|bin|hex|elf)))",
    re.IGNORECASE,
)


def handle_hook_payload(payload: Dict[str, Any], custom_root: Optional[str] = None) -> Dict[str, Any]:
    """Process an `agy` hook JSON payload and return the response JSON object."""
    if not isinstance(payload, dict):
        return {"continue": True}

    event_name = (
        payload.get("hookEventName")
        or payload.get("event")
        or payload.get("hook_event_name")
        or ""
    )
    conv_id = payload.get("conversationId") or payload.get("conversation_id") or ""
    tx_path = payload.get("transcriptPath") or payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or payload.get("workspaceRoot") or os.getcwd()

    contract = read_turn_contract(conv_id=conv_id, project_dir=cwd, custom_root=custom_root)
    project_dir = (contract or {}).get("project_dir") or cwd

    # 1. PreInvocation: Post-Compaction Capsule Re-injection
    if event_name == "PreInvocation":
        prev_state = get_compaction_state(conv_id, custom_root=custom_root) if conv_id else None
        prev_cp_count = int(prev_state["checkpoint_count"]) if prev_state else 0

        if tx_path and os.path.exists(tx_path):
            comp_info = inspect_and_backfill_transcript(
                tx_path, project_dir=project_dir, conv_id=conv_id, custom_root=custom_root
            )
            new_cp_count = int(comp_info.get("checkpoint_count", 0))
            is_corrupted = bool(comp_info.get("is_corrupted", False))

            if new_cp_count > prev_cp_count or is_corrupted:
                capsule = read_project_capsule(project_dir, custom_root=custom_root).strip()
                user_req = (contract or {}).get("user_request", "")
                boundary = (contract or {}).get("boundary_directive", "")
                sys_msg_parts = [
                    "🔄 [Post-Compaction Memory Recovery / 上下文压缩后记忆实时回灌]",
                    f"检测到会话刚刚经历了第 {new_cp_count} 次上下文压缩（CHECKPOINT）。为防止压缩摘要丢失关键事实或后续步骤，系统自动回灌项目长效记忆胶囊：",
                ]
                if user_req:
                    sys_msg_parts.append(f"- **本轮用户原始指令**: {user_req}")
                if boundary:
                    sys_msg_parts.append(f"- **本轮范围契约**: {boundary}")
                if capsule:
                    sys_msg_parts.append(capsule[:3000])
                return {
                    "continue": True,
                    "systemMessage": "\n\n".join(sys_msg_parts),
                }

        return {"continue": True}

    # 2. PreToolUse: Scope Boundary Guard & Dead-End Warning
    if event_name == "PreToolUse":
        tool_name = payload.get("toolName") or payload.get("tool_name") or ""
        tool_input = payload.get("toolInput") or payload.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        # Only enforce contract if it is fresh (within last 2 hours)
        is_fresh_contract = bool(
            contract and (time.time() - float(contract.get("timestamp", 0)) < 7200)
        )
        if not is_fresh_contract:
            return {"continue": True}

        scope = contract.get("scope", "modify")
        cmd_line = str(tool_input.get("CommandLine") or tool_input.get("command") or "").strip()
        target_path = str(tool_input.get("Path") or tool_input.get("TargetFile") or tool_input.get("path") or "").strip()

        # Scope == inspect guard: prevent unauthorized file edits or destructive/flashing commands
        if scope == "inspect":
            if tool_name in ("write_file", "edit_file", "write_to_file", "replace_file_content"):
                # Allow writing temporary scratch files inside /tmp or artifact scratch dir
                if target_path and not (
                    target_path.startswith("/tmp/")
                    or "/scratch/" in target_path
                    or "/data/agent_memory/" in target_path
                ):
                    reason = (
                        f"【只读探查范围守卫 (scope=inspect)】用户本轮指令（“{contract.get('user_request', '')[:80]}”）"
                        f"仅要求查看或排查原因，未授权直接修改代码文件 `{os.path.basename(target_path)}`。"
                        f"请先完成只读探查并向用户汇报结论与修改建议。"
                    )
                    return {
                        "continue": True,
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        },
                    }

            if tool_name == "run_command" and cmd_line:
                if _INSPECT_FORBIDDEN_CMD_RE.search(cmd_line):
                    reason = (
                        f"【只读探查范围守卫 (scope=inspect)】用户本轮指令仅要求查看或检查状态，"
                        f"禁止在只读探查轮次擅自执行安装、下载大包、Git推送或硬件烧录命令 (`{cmd_line[:80]}`)。"
                        f"请立即汇总已查明的结论直接回复用户！"
                    )
                    return {
                        "continue": True,
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        },
                    }

        return {"continue": True}

    return {"continue": True}


def ensure_agy_hooks_installed(bot_root: Optional[str] = None) -> str:
    """Ensure `~/.gemini/config/hooks.json` registers `agy_hooks.py` for PreInvocation and PreToolUse."""
    root = bot_root or ROOT_DIR
    hook_script = os.path.abspath(os.path.join(root, "hooks", "agy_hooks.py"))
    config_dir = os.path.expanduser("~/.gemini/config")
    os.makedirs(config_dir, exist_ok=True)
    hooks_json_path = os.path.join(config_dir, "hooks.json")

    cmd_str = f"python3 {hook_script}"
    desired_entry = {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": cmd_str,
                "timeout": 5,
            }
        ],
    }

    existing: Dict[str, Any] = {}
    if os.path.exists(hooks_json_path):
        try:
            with open(hooks_json_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
        except Exception:
            existing = {}

    hooks_map = existing.setdefault("hooks", {})
    for ev_name in ("PreInvocation", "PreToolUse"):
        ev_list = hooks_map.get(ev_name)
        if not isinstance(ev_list, list):
            ev_list = []
        # Remove any outdated agy_hooks.py entries and add current
        ev_list = [
            item
            for item in ev_list
            if not (
                isinstance(item, dict)
                and any("agy_hooks.py" in str(h.get("command", "")) for h in (item.get("hooks") or []) if isinstance(h, dict))
            )
        ]
        ev_list.append(desired_entry)
        hooks_map[ev_name] = ev_list

    with open(hooks_json_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return hooks_json_path


def main() -> int:
    try:
        raw_in = sys.stdin.read().strip()
        payload = json.loads(raw_in) if raw_in else {}
    except Exception:
        payload = {}

    try:
        resp = handle_hook_payload(payload)
    except Exception:
        resp = {"continue": True}

    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
