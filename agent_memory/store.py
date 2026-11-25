"""Core storage, FTS5 retrieval, transcript backfill, compaction guard, and session relay."""

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import config
except Exception:
    class _FallbackConfig:
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        @staticmethod
        def get_transcript_path(conv_id: str) -> str:
            agy_home = os.environ.get("ANTIGRAVITY_HOME") or os.path.expanduser("~/.gemini/antigravity-cli")
            return os.path.join(
                agy_home, "brain", conv_id, ".system_generated", "logs", "transcript.jsonl"
            )

    config = _FallbackConfig()  # type: ignore

try:
    from logger import log
except Exception:
    import logging
    log = logging.getLogger("agent_memory")


# Maximum characters for PROJECT_STATE.md L1 Memory Capsule (~1.5k tokens)
MAX_CAPSULE_CHARS = 6000

# Proactive session relay threshold (number of CHECKPOINT compactions in a single conversation)
DEFAULT_RELAY_CHECKPOINT_THRESHOLD = 3

# Model capacity / server overload (HTTP 503) detection for seamless model-switch session relay
_CAPACITY_ERROR_RE = re.compile(
    r"(?:no capacity available for model|unavailable\s*\(code\s*503\)|experiencing high traffic right now|overloaded_error|model is overloaded)",
    re.IGNORECASE,
)
_CAPACITY_ERROR_MODEL_RE = re.compile(
    r"no capacity available for model\s+([A-Za-z0-9_.\-]+)",
    re.IGNORECASE,
)


def get_memory_root(custom_root: Optional[str] = None) -> str:
    """Return `<BOT_ROOT>/data/agent_memory` (or `AGENT_MEMORY_DIR` env override)."""
    root = custom_root or os.environ.get("AGENT_MEMORY_DIR")
    if not root:
        root = os.path.join(config.BASE_DIR, "data", "agent_memory")
    os.makedirs(root, exist_ok=True)
    return os.path.abspath(root)


def get_db_path(custom_root: Optional[str] = None) -> str:
    return os.path.join(get_memory_root(custom_root), "memory.db")


def get_project_slug(project_dir: Optional[str]) -> str:
    """Resolve a deterministic, filesystem-safe slug for a project directory."""
    if not project_dir or str(project_dir).strip() in ("", "默认", "Default", "default"):
        return "default_workspace"
    norm = os.path.abspath(os.path.expanduser(str(project_dir).strip()))
    base = os.path.basename(norm) or "root"
    safe_base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("_")[:40] or "project"
    short_hash = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return f"{safe_base}_{short_hash}"


def get_project_memory_dir(project_dir: Optional[str], custom_root: Optional[str] = None) -> str:
    slug = get_project_slug(project_dir)
    proj_mem_dir = os.path.join(get_memory_root(custom_root), "projects", slug)
    os.makedirs(proj_mem_dir, exist_ok=True)
    return proj_mem_dir


def get_capsule_path(project_dir: Optional[str], custom_root: Optional[str] = None) -> str:
    return os.path.join(get_project_memory_dir(project_dir, custom_root), "PROJECT_STATE.md")


def get_journal_path(project_dir: Optional[str], custom_root: Optional[str] = None) -> str:
    return os.path.join(get_project_memory_dir(project_dir, custom_root), "journal.jsonl")


def get_contracts_dir(custom_root: Optional[str] = None) -> str:
    d = os.path.join(get_memory_root(custom_root), "contracts")
    os.makedirs(d, exist_ok=True)
    return d


def _sanitize_text(text: str) -> str:
    """Mask sensitive tokens, passwords, and private keys before persisting to disk."""
    if not text:
        return ""
    try:
        from system_one_gate import mask_sensitive_credentials
        masked, _ = mask_sensitive_credentials(str(text))
        return masked
    except Exception:
        # Fallback regex masking if system_one_gate is unavailable
        s = str(text)
        s = re.sub(r"\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b", "[REDACTED_TOKEN]", s)
        s = re.sub(r"(密码|password|passwd)\s*[:：=]?\s*([^\s,，。;；]{4,})", r"\1: [REDACTED]", s, flags=re.IGNORECASE)
        return s


_SCHEMA_INIT_LOCK = __import__("threading").Lock()
_INITIALIZED_DB_PATHS = set()


def _get_conn(custom_root: Optional[str] = None) -> sqlite3.Connection:
    db_path = get_db_path(custom_root)
    real_db_path = os.path.abspath(db_path)
    existed_before = os.path.exists(real_db_path) and os.path.getsize(real_db_path) > 0
    conn = sqlite3.connect(real_db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    with _SCHEMA_INIT_LOCK:
        if not existed_before or real_db_path not in _INITIALIZED_DB_PATHS:
            conn.execute("PRAGMA journal_mode=WAL")
            _init_schema(conn)
            _INITIALIZED_DB_PATHS.add(real_db_path)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS episodic_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn_hash TEXT UNIQUE,
            chat_id TEXT DEFAULT '',
            project_slug TEXT NOT NULL,
            project_dir TEXT DEFAULT '',
            conv_id TEXT DEFAULT '',
            step_index INTEGER DEFAULT 0,
            user_request TEXT NOT NULL,
            scope TEXT DEFAULT 'modify',
            tier TEXT DEFAULT 'Medium',
            outcome TEXT DEFAULT 'success',
            reply_digest TEXT DEFAULT '',
            files_changed TEXT DEFAULT '',
            dead_ends TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_episodic_project_slug ON episodic_turns(project_slug, id DESC);
        CREATE INDEX IF NOT EXISTS idx_episodic_conv_id ON episodic_turns(conv_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
            user_request,
            reply_digest,
            files_changed,
            dead_ends,
            project_slug UNINDEXED,
            conv_id UNINDEXED,
            turn_id UNINDEXED
        );

        CREATE TABLE IF NOT EXISTS project_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_slug TEXT NOT NULL,
            fact_type TEXT NOT NULL,
            content TEXT NOT NULL,
            source_conv_id TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(project_slug, fact_type, content)
        );

        CREATE INDEX IF NOT EXISTS idx_facts_project ON project_facts(project_slug, fact_type, active);

        CREATE TABLE IF NOT EXISTS compaction_state (
            conv_id TEXT PRIMARY KEY,
            project_slug TEXT NOT NULL,
            total_steps INTEGER DEFAULT 0,
            checkpoint_count INTEGER DEFAULT 0,
            last_checkpoint_step INTEGER DEFAULT 0,
            last_checkpoint_summary TEXT DEFAULT '',
            is_corrupted INTEGER DEFAULT 0,
            corruption_reason TEXT DEFAULT '',
            last_byte_offset INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


# ============================================================================
# Boilerplate Header Stripper
# ============================================================================

_BOILERPLATE_BLOCK_PATTERNS = [
    re.compile(r"^\[System Rule:.*?\n\]?\s*", re.MULTILINE),
    re.compile(r"^\[System Rule:[\s\S]*?Output ONLY your final answer directly in Simplified Chinese\.\]\s*", re.MULTILINE),
    re.compile(r"^\[Role:[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[System Feishu Resource Delivery / 飞书文件传送规范\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[Feishu File Rule:[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[Voice Interaction[^\n]*\]\n(?:.*?\n)*?\n?", re.MULTILINE),
    re.compile(r"^\[System Active Project Context\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[Active Project:[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[System Execution & Safety Guardrails\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[System One Sentry/Co-Pilot[^\n]*\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[Security & Egress Guard:[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[Active Project Specific Rules & Description\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[System Mandatory Task Planning Directive[^\n]*\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[TASK_PLAN Rule:[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[System Autonomous Execution Directive[^\n]*\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[System Interaction Directive[^\n]*\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[User Long-Term Memory[^\n]*\]\n(?:.*?\n)*?\n", re.MULTILINE),
    re.compile(r"^\[User's Permanent Notes / 备忘录\]\n(?:- [^\n]*\n)+\s*", re.MULTILINE),
    re.compile(r"^\[User's Relevant Notes\]\n(?:- [^\n]*\n)+\s*", re.MULTILINE),
    re.compile(r"^\[System One Adaptive Reasoning:\s*(?:LOW|MEDIUM|HIGH)\]\n[^\n]*\n+", re.MULTILINE),
    re.compile(r"^\[S1\s+scope=[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[【[^\n]*契约[^\n]*\]\s*", re.MULTILINE),
    re.compile(r"^\[Project Memory Capsule[\s\S]*?\[/Project Memory Capsule\]\s*", re.MULTILINE),
    re.compile(r"^\[Session Relay Handoff[\s\S]*?\[/Session Relay Handoff\]\s*", re.MULTILINE),
    re.compile(r"^\[Budget Watchdog Auto-Retry[\s\S]*?\[/Budget Watchdog Auto-Retry\]\s*", re.MULTILINE),
    re.compile(r"^\[Safety Filter Self-Healing[\s\S]*?\[/Safety Filter Self-Healing\]\s*", re.MULTILINE),
    re.compile(r"^\[Memory Guard:[\s\S]*?(?=\n\n|\Z)\s*", re.MULTILINE),
    re.compile(r"^\[Recalled Episodic Memory[\s\S]*?(?=\n\n|\Z)\s*", re.MULTILINE),
    re.compile(r"^\[System Context: User preferences:\]\n(?:- [^\n]*\n)+\s*\[User's Message:\]\s*", re.MULTILINE),
]


def strip_injected_prompt_headers(raw_user_input: str) -> str:
    """Strip repetitive per-turn system instructions/notes from USER_INPUT to recover the pure user request."""
    if not raw_user_input:
        return ""
    text = str(raw_user_input).strip()
    m_ur = re.search(r"<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>", text, re.IGNORECASE)
    if m_ur:
        text = m_ur.group(1).strip()
    else:
        text = re.sub(r"</?USER_REQUEST>", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"<ADDITIONAL_METADATA>[\s\S]*$", "", text, flags=re.IGNORECASE).strip()

    if "[User's Message:]" in text:
        text = text.split("[User's Message:]", 1)[-1].strip()
    if "[User Request / 用户本轮指令]" in text:
        text = text.split("[User Request / 用户本轮指令]", 1)[-1].strip()

    for _ in range(3):
        prev = text
        for pat in _BOILERPLATE_BLOCK_PATTERNS:
            text = pat.sub("", text).strip()
        if text == prev:
            break

    # Final fallback: if there are still bracketed system blocks at the top followed by newline
    while text.startswith("[") and "]\n" in text:
        first_line_end = text.find("\n")
        first_line = text[:first_line_end].strip()
        if any(
            first_line.startswith(prefix)
            for prefix in (
                "[System",
                "[Active Project",
                "[Security",
                "[TASK_PLAN Rule",
                "[User's",
                "[Role:",
                "[Voice",
                "[S1 ",
                "[Memory",
                "[【",
                "[当前为长任务断点续跑模式",
            )
        ):
            text = text[first_line_end + 1 :].lstrip()
            # Also strip single-line Chinese adaptive reasoning hint right after
            text = re.sub(
                r"^当前任务判定为(?:轻量日常问答或闲聊|常规工程开发|多步复杂工程或系统操作).*?\n+",
                "",
                text,
            ).lstrip()
        else:
            break

    return text.strip()


# ============================================================================
# Task Scope & Step/Time Budget Classifier
# ============================================================================

_INSPECT_PATTERNS = re.compile(
    r"(?:看看|看下|看一下|查一下|查查|检查|排查|查看|是什么原因|为什么|怎么回事|有没有|是否存在|确认一下|分析一下|状态|版本|到哪了|进展)",
    re.IGNORECASE,
)

_MODIFY_OR_EXEC_PATTERNS = re.compile(
    r"(?:修改|修复|改一下|改吧|重写|重构|实现|开发|新增|添加|编写|创建|升级|提升到|更新到|开始做|动手做|做吧|优化|部署|同步到|推送|git\s+push|安装|卸载|删除|清理|重启|启动|停止|开始逐步推进|执行到底|继续执行)",
    re.IGNORECASE,
)

_DEVICE_EXEC_PATTERNS = re.compile(
    r"(?:烧录|刷入|刷写|刷机|openocd|st-link|stlink|jlink|j-link|flash\s+write|复位芯片|连接车|蓝牙压测|发包测试)",
    re.IGNORECASE,
)

_DEPLOY_PATTERNS = re.compile(
    r"(?:推送到\s*github|git\s+push|发布上线|部署到|同步到树莓派|重启服务)",
    re.IGNORECASE,
)


def classify_task_scope_and_budget(
    user_text: str,
    tier: str = "Medium",
    is_operation: bool = False,
    is_continuation: bool = False,
) -> Dict[str, Any]:
    """Classify the turn into an explicit Task Scope with orthogonal Step & Time Budgets.

    Scopes:
      - `inspect`: Read-only check / query / diagnosis ("看看有没有...", "查一下为什么...").
      - `advise`: Pure Q&A / conceptual explanation / greeting.
      - `execute_device`: Hardware probe / flashing / OpenOCD / BLE / serial operations.
      - `deploy`: Git push / remote deployment / service release.
      - `modify`: Code creation, bug fixing, refactoring, or multi-stage engineering.
    """
    clean_q = strip_injected_prompt_headers(user_text or "")
    tier_norm = (tier or "Medium").capitalize()

    if is_continuation or "[CONTINUE_PAUSED_TASK]" in (user_text or ""):
        return {
            "scope": "modify",
            "scope_label": "接力续跑 (Continuation)",
            "max_steps": 60,
            "max_seconds": 900,
            "allow_write": True,
            "allow_device_op": True,
            "boundary_directive": (
                "当前为长任务断点续跑模式：请直接基于已完成步骤与项目记忆胶囊继续推进剩余未完成步骤，严禁从头重复已完成的探查。"
            ),
        }

    # Mask inquiry phrases like "有没有安装/有没有修改/是否修复/为什么失败" before checking imperative modify/device verbs
    inquiry_masked_q = re.sub(
        r"(?:有没有|是否|有没|是否已经|完成了没有|了没有|没|为什么|怎么会|为何)(?:安装|修改|修复|部署|烧录|启动|重启|删除|配置|升级|复用)",
        "查询状态",
        clean_q,
    )

    has_inspect = bool(_INSPECT_PATTERNS.search(clean_q))
    has_modify = bool(_MODIFY_OR_EXEC_PATTERNS.search(inquiry_masked_q))
    has_device = bool(_DEVICE_EXEC_PATTERNS.search(inquiry_masked_q))
    has_deploy = bool(_DEPLOY_PATTERNS.search(inquiry_masked_q))

    # 1. Pure inspection / root-cause check without modification/flashing authorization
    if has_inspect and not has_modify and not has_device and not has_deploy and len(clean_q) <= 180:
        return {
            "scope": "inspect",
            "scope_label": "只读探查 (Inspect Only)",
            "max_steps": 15,
            "max_seconds": 240,
            "allow_write": False,
            "allow_device_op": False,
            "boundary_directive": (
                "【只读探查契约 (scope=inspect)】：用户本轮仅要求查看、检查环境或分析原因。"
                "查明事实后必须立即停止工具调用并输出结论！严禁擅自扩写任务去执行未授权的下载、代码修改、全盘搜索、逆向工程或硬件烧录（预算上限：15步 / 4分钟）。"
            ),
        }

    # 2. Pure Q&A / advise (both Low and Medium non-operational questions without imperative modify/device/deploy verbs)
    if not is_operation and not has_modify and not has_device and not has_deploy and not has_inspect:
        return {
            "scope": "advise",
            "scope_label": "轻量解答 (Advise)",
            "max_steps": 8 if tier_norm == "Low" else 15,
            "max_seconds": 150 if tier_norm == "Low" else 300,
            "allow_write": tier_norm != "Low",
            "allow_device_op": False,
            "boundary_directive": "直接回答用户问题，无需调用无关工具。",
        }

    # 3. Hardware / device operations
    if has_device:
        return {
            "scope": "execute_device",
            "scope_label": "硬件与设备操作 (Device Op)",
            "max_steps": 45,
            "max_seconds": 600,
            "allow_write": True,
            "allow_device_op": True,
            "boundary_directive": (
                "【硬件操作契约 (scope=execute_device)】：执行前先核对项目记忆胶囊中的『已证伪路径』，避免重复已被证明无效的引脚或烧录命令（预算上限：45步 / 10分钟）。"
            ),
        }

    # 4. Deployment / Git push
    if has_deploy and not has_modify:
        return {
            "scope": "deploy",
            "scope_label": "构建与部署 (Deploy)",
            "max_steps": 30,
            "max_seconds": 480,
            "allow_write": True,
            "allow_device_op": False,
            "boundary_directive": "聚焦代码提交、远端同步与部署验证，完成后立即汇报结果（预算上限：30步 / 8分钟）。",
        }

    # 5. General engineering modification (Medium / High)
    if tier_norm == "High":
        max_steps = 55
        max_seconds = 900
    else:
        max_steps = 35
        max_seconds = 600

    return {
        "scope": "modify",
        "scope_label": "工程开发与修改 (Modify)",
        "max_steps": max_steps,
        "max_seconds": max_seconds,
        "allow_write": True,
        "allow_device_op": True,
        "boundary_directive": (
            f"【工程执行契约 (scope=modify)】：严格围绕用户目标推进，遵守项目记忆胶囊中的事实与死胡同禁令（单轮预算上限：{max_steps}步 / {max_seconds // 60}分钟）。"
        ),
    }


# ============================================================================
# Turn Contract (Shared with agy_hooks.py & executor.py)
# ============================================================================

def write_turn_contract(
    chat_id: str,
    project_dir: Optional[str],
    user_request: str,
    scope_info: Dict[str, Any],
    conv_id: str = "",
    attempt: int = 1,
    custom_root: Optional[str] = None,
) -> str:
    """Persist the active turn contract to `<BOT_ROOT>/data/agent_memory/contracts/`."""
    contracts_dir = get_contracts_dir(custom_root)
    clean_req = _sanitize_text(strip_injected_prompt_headers(user_request))
    dead_ends = [f["content"] for f in get_project_facts(project_dir, fact_type="dead_end", custom_root=custom_root)]
    contract = {
        "chat_id": chat_id,
        "conv_id": conv_id or "",
        "project_dir": project_dir or "默认",
        "project_slug": get_project_slug(project_dir),
        "user_request": clean_req[:500],
        "scope": scope_info.get("scope", "modify"),
        "scope_label": scope_info.get("scope_label", ""),
        "max_steps": int(scope_info.get("max_steps", 35)),
        "max_seconds": int(scope_info.get("max_seconds", 600)),
        "allow_write": bool(scope_info.get("allow_write", True)),
        "allow_device_op": bool(scope_info.get("allow_device_op", True)),
        "boundary_directive": scope_info.get("boundary_directive", ""),
        "dead_ends": dead_ends[:15],
        "attempt": attempt,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
    }
    # Save keyed by chat_id, project_slug, and conv_id (if available) so hooks can locate it by any key
    keys = [f"chat_{re.sub(r'[^a-zA-Z0-9_-]', '_', str(chat_id or 'default'))}", f"proj_{contract['project_slug']}"]
    if conv_id:
        keys.append(f"conv_{re.sub(r'[^a-zA-Z0-9_-]', '_', str(conv_id))}")

    primary_path = ""
    for k in keys:
        p = os.path.join(contracts_dir, f"{k}.json")
        if not primary_path:
            primary_path = p
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(contract, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"[AgentMemory] Failed to write turn contract {p}: {e}")
    return primary_path


def read_turn_contract(
    conv_id: str = "",
    project_dir: Optional[str] = None,
    chat_id: str = "",
    custom_root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    contracts_dir = get_contracts_dir(custom_root)
    candidates = []
    if conv_id:
        candidates.append(os.path.join(contracts_dir, f"conv_{re.sub(r'[^a-zA-Z0-9_-]', '_', str(conv_id))}.json"))
    if project_dir:
        candidates.append(os.path.join(contracts_dir, f"proj_{get_project_slug(project_dir)}.json"))
    if chat_id:
        candidates.append(os.path.join(contracts_dir, f"chat_{re.sub(r'[^a-zA-Z0-9_-]', '_', str(chat_id))}.json"))

    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


# ============================================================================
# Project Facts & L1 Memory Capsule (PROJECT_STATE.md)
# ============================================================================

def add_project_fact(
    project_dir: Optional[str],
    fact_type: str,
    content: str,
    source_conv_id: str = "",
    custom_root: Optional[str] = None,
) -> bool:
    """Add a structured fact (`fact`, `decision`, `dead_end`, `artifact`, `todo`, `goal`) to SQLite and sync PROJECT_STATE.md."""
    clean_content = _sanitize_text(str(content or "").strip())
    if not clean_content or len(clean_content) < 3:
        return False
    clean_content = re.sub(r"\s+", " ", clean_content)[:350]
    slug = get_project_slug(project_dir)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_conn(custom_root)
    try:
        if fact_type == "goal":
            # Replace previous active goal with the latest active goal
            conn.execute(
                "UPDATE project_facts SET active = 0 WHERE project_slug = ? AND fact_type = 'goal'",
                (slug,),
            )
        conn.execute(
            """
            INSERT INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(project_slug, fact_type, content)
            DO UPDATE SET active = 1, created_at = excluded.created_at
            """,
            (slug, fact_type, clean_content, source_conv_id or "", now_str),
        )
        conn.commit()
    finally:
        conn.close()

    _rebuild_capsule_from_db(project_dir, custom_root=custom_root)
    return True


def get_project_facts(
    project_dir: Optional[str],
    fact_type: Optional[str] = None,
    limit: int = 20,
    custom_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    slug = get_project_slug(project_dir)
    conn = _get_conn(custom_root)
    try:
        if fact_type:
            rows = conn.execute(
                """
                SELECT id, fact_type, content, source_conv_id, created_at
                FROM project_facts
                WHERE project_slug = ? AND fact_type = ? AND active = 1
                ORDER BY id DESC LIMIT ?
                """,
                (slug, fact_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, fact_type, content, source_conv_id, created_at
                FROM project_facts
                WHERE project_slug = ? AND active = 1
                ORDER BY id DESC LIMIT ?
                """,
                (slug, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def read_project_capsule(project_dir: Optional[str], custom_root: Optional[str] = None) -> str:
    """Read `PROJECT_STATE.md` for the given project, building it from DB if missing."""
    path = get_capsule_path(project_dir, custom_root)
    if not os.path.exists(path):
        _rebuild_capsule_from_db(project_dir, custom_root=custom_root)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def write_project_capsule(
    project_dir: Optional[str],
    markdown_content: str,
    custom_root: Optional[str] = None,
) -> str:
    import tempfile
    path = get_capsule_path(project_dir, custom_root)
    sanitized = _sanitize_text(markdown_content)
    if len(sanitized) > MAX_CAPSULE_CHARS:
        sanitized = sanitized[: MAX_CAPSULE_CHARS - 60] + "\n\n_(胶囊已自动精简至 6KB 安全上限内)_\n"
    parent_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".capsule_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sanitized)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise
    return path


def _rebuild_capsule_from_db(project_dir: Optional[str], custom_root: Optional[str] = None) -> str:
    """Synthesize a crisp `<= 6KB` `PROJECT_STATE.md` capsule from SQLite facts and recent episodic turns."""
    slug = get_project_slug(project_dir)
    conn = _get_conn(custom_root)
    try:
        facts_rows = conn.execute(
            """
            SELECT fact_type, content, created_at
            FROM project_facts
            WHERE project_slug = ? AND active = 1
            ORDER BY id DESC
            """,
            (slug,),
        ).fetchall()

        recent_turns = conn.execute(
            """
            SELECT user_request, scope, outcome, reply_digest, files_changed, created_at
            FROM episodic_turns
            WHERE project_slug = ?
            ORDER BY id DESC LIMIT 6
            """,
            (slug,),
        ).fetchall()

        comp_stats = conn.execute(
            """
            SELECT COUNT(*) as conv_count, COALESCE(SUM(checkpoint_count), 0) as total_checkpoints
            FROM compaction_state
            WHERE project_slug = ?
            """,
            (slug,),
        ).fetchone()
    finally:
        conn.close()

    by_type: Dict[str, List[str]] = {
        "goal": [],
        "fact": [],
        "decision": [],
        "dead_end": [],
        "artifact": [],
        "todo": [],
    }
    for r in facts_rows:
        ft = r["fact_type"]
        if ft in by_type and r["content"] not in by_type[ft]:
            by_type[ft].append(r["content"])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    disp_proj = project_dir if (project_dir and project_dir not in ("默认", "Default")) else "默认工作区"
    conv_cnt = comp_stats["conv_count"] if comp_stats else 0
    cp_cnt = comp_stats["total_checkpoints"] if comp_stats else 0

    lines = [
        "# 🧠 项目长效记忆胶囊 (Project Memory Capsule)",
        f"- **项目路径**: `{disp_proj}` (`{slug}`)",
        f"- **更新时间**: {now_str} | **累计关联会话**: {conv_cnt} | **历史压缩次数**: {cp_cnt}",
        "",
        "## 1. 当前核心目标 (Current Goal)",
    ]
    if by_type["goal"]:
        for g in by_type["goal"][:2]:
            lines.append(f"- {g}")
    elif recent_turns:
        lines.append(f"- {recent_turns[0]['user_request'][:220]}")
    else:
        lines.append("- 暂无活跃目标记录")

    lines.extend(["", "## 2. 已证伪路径与死胡同 (Dead Ends - 严禁重复尝试)"])
    if by_type["dead_end"]:
        for de in by_type["dead_end"][:10]:
            lines.append(f"- ⛔ {de}")
    else:
        lines.append("- 暂无已证伪死胡同记录")

    lines.extend(["", "## 3. 已确认事实与环境基线 (Confirmed Facts)"])
    if by_type["fact"]:
        for fct in by_type["fact"][:10]:
            lines.append(f"- ✅ {fct}")
    else:
        lines.append("- 暂无显式环境事实记录")

    lines.extend(["", "## 4. 关键技术决策与架构约束 (Key Decisions)"])
    if by_type["decision"]:
        for dec in by_type["decision"][:8]:
            lines.append(f"- 📌 {dec}")
    else:
        lines.append("- 暂无关键决策记录")

    lines.extend(["", "## 5. 关键产物与文件变更 (Key Artifacts)"])
    if by_type["artifact"]:
        for art in by_type["artifact"][:8]:
            lines.append(f"- 📄 {art}")
    else:
        lines.append("- 暂无关键产物记录")

    lines.extend(["", "## 6. 最近执行轨迹摘要 (Recent Episodic Turns)"])
    if recent_turns:
        for t in reversed(list(recent_turns[:5])):
            req_short = re.sub(r"\s+", " ", (t["user_request"] or "")).strip()[:90]
            dig_short = re.sub(r"\s+", " ", (t["reply_digest"] or "")).strip()[:140]
            lines.append(f"- **[{t['created_at']} | {t['scope']}]** `{req_short}` → {dig_short}")
    else:
        lines.append("- 暂无历史轮次记录")

    if by_type["todo"]:
        lines.extend(["", "## 7. 待办事项 (Pending Todos)"])
        for td in by_type["todo"][:6]:
            lines.append(f"- [ ] {td}")

    md = "\n".join(lines) + "\n"
    return write_project_capsule(project_dir, md, custom_root=custom_root)


# ============================================================================
# Heuristic Fact & Dead-End Extractor
# ============================================================================

_DEAD_END_REGEX = re.compile(
    r"(?:已证伪|死胡同|不可行|行不通|失败原因|切勿再|禁止重复|不能使用|无法通过|报错原因|不工作|无响应|超时失败|blocked by Gemini's filters)[^。\n]{4,140}",
    re.IGNORECASE,
)

_FACT_REGEX = re.compile(
    r"(?:已确认|实测发现|根因是|根本原因|环境版本|硬件连接|引脚定义|串口路径|芯片型号|部署路径|编译通过|测试通过)[^。\n]{4,140}",
    re.IGNORECASE,
)

_DECISION_REGEX = re.compile(
    r"(?:决定采用|技术方案|最终方案|架构设计|修复策略|改为使用|统一采用)[^。\n]{4,140}",
    re.IGNORECASE,
)

_FILE_PATH_REGEX = re.compile(
    r"(?:/[a-zA-Z0-9._-]+){2,}\.(?:py|c|h|cpp|rs|js|ts|json|md|yaml|yml|sh|bin|hex|elf|cfg)\b"
)


def _extract_structured_items_from_turn(
    user_request: str,
    reply_text: str,
    tool_steps: Optional[List[str]] = None,
    dead_end_notes: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    extracted: Dict[str, List[str]] = {
        "facts": [],
        "decisions": [],
        "dead_ends": list(dead_end_notes or []),
        "artifacts": [],
    }
    combined = f"{reply_text or ''}"

    for m in _DEAD_END_REGEX.finditer(combined):
        s = m.group(0).strip(" ，。:：-*")
        if len(s) >= 8 and s not in extracted["dead_ends"]:
            extracted["dead_ends"].append(s[:220])

    for m in _FACT_REGEX.finditer(combined):
        s = m.group(0).strip(" ，。:：-*")
        if len(s) >= 8 and s not in extracted["facts"]:
            extracted["facts"].append(s[:220])

    for m in _DECISION_REGEX.finditer(combined):
        s = m.group(0).strip(" ，。:：-*")
        if len(s) >= 8 and s not in extracted["decisions"]:
            extracted["decisions"].append(s[:220])

    for m in _FILE_PATH_REGEX.finditer(combined):
        fp = m.group(0).strip()
        if "/.gemini/" not in fp and fp not in extracted["artifacts"]:
            extracted["artifacts"].append(fp)

    for step in (tool_steps or []):
        if "编辑文件:" in step or "写入文件:" in step:
            fname = step.split(":", 1)[-1].strip()
            if fname and fname not in extracted["artifacts"]:
                extracted["artifacts"].append(fname)

    return extracted


# ============================================================================
# L2 Episodic Recording & FTS5 Search
# ============================================================================

def _normalize_turn_timestamp(raw_ts: Optional[str]) -> str:
    """Normalize ISO-8601 (including UTC 'Z' / '+00:00') or local timestamps to local '%Y-%m-%d %H:%M:%S'."""
    if not raw_ts or not str(raw_ts).strip():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    s = str(raw_ts).strip()
    try:
        if "T" in s or s.endswith("Z") or "+" in s[10:]:
            iso_s = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso_s)
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return s[:19].replace("T", " ")


def record_episodic_turn(
    project_dir: Optional[str],
    user_request: str,
    reply_text: str,
    chat_id: str = "",
    conv_id: str = "",
    step_index: int = 0,
    scope: str = "modify",
    tier: str = "Medium",
    outcome: str = "success",
    tool_steps: Optional[List[str]] = None,
    dead_ends: Optional[List[str]] = None,
    created_at: Optional[str] = None,
    custom_root: Optional[str] = None,
    rebuild_capsule: bool = True,
) -> bool:
    """Record a completed (or intercepted) turn into SQLite FTS5, `journal.jsonl`, and `PROJECT_STATE.md`."""
    clean_req = _sanitize_text(strip_injected_prompt_headers(user_request or ""))
    if not clean_req:
        return False

    clean_reply = _sanitize_text(reply_text or "")
    is_filter_blocked = bool(_CORRUPTED_CHECKPOINT_PATTERNS.search(clean_reply))
    if is_filter_blocked:
        digest_text = "（云端安全过滤器误报拦截，已自动触发无感会话接力与合规术语重述）"
        outcome = "filter_blocked"
    else:
        # Condense reply into a crisp digest (strip code blocks to save space while keeping conclusions)
        digest_text = re.sub(r"```.*?```", "[代码块]", clean_reply, flags=re.DOTALL)
        digest_text = re.sub(r"\s+", " ", digest_text).strip()[:600]

    extracted = _extract_structured_items_from_turn(
        clean_req, "" if is_filter_blocked else clean_reply, tool_steps=tool_steps, dead_end_notes=dead_ends
    )
    files_str = ", ".join(extracted["artifacts"][:10])
    dead_ends_str = " | ".join(extracted["dead_ends"][:6])

    slug = get_project_slug(project_dir)
    now_str = _normalize_turn_timestamp(created_at)
    # Content-based turn fingerprint independent of step_index so post-turn write (step_index=0)
    # and subsequent transcript backfill (step_index=N) never create duplicate episodic rows!
    turn_hash = hashlib.sha1(
        f"{slug}:{conv_id}:{clean_req[:200]}:{digest_text[:160]}".encode("utf-8")
    ).hexdigest()

    conn = _get_conn(custom_root)
    inserted = False
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO episodic_turns
            (turn_hash, chat_id, project_slug, project_dir, conv_id, step_index,
             user_request, scope, tier, outcome, reply_digest, files_changed, dead_ends, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_hash,
                chat_id or "",
                slug,
                str(project_dir or "默认"),
                conv_id or "",
                int(step_index or 0),
                clean_req[:1000],
                scope or "modify",
                tier or "Medium",
                outcome or "success",
                digest_text,
                files_str,
                dead_ends_str,
                now_str,
            ),
        )
        if cur.rowcount and cur.rowcount > 0:
            turn_id = cur.lastrowid
            inserted = True
            conn.execute(
                """
                INSERT INTO episodic_fts (user_request, reply_digest, files_changed, dead_ends, project_slug, conv_id, turn_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_req[:1000],
                    digest_text,
                    files_str,
                    dead_ends_str,
                    slug,
                    conv_id or "",
                    str(turn_id),
                ),
            )
        elif step_index and int(step_index) > 0:
            conn.execute(
                "UPDATE episodic_turns SET step_index = ? WHERE turn_hash = ? AND step_index = 0",
                (int(step_index), turn_hash),
            )

        # Also persist extracted facts/decisions/dead_ends/artifacts
        goal_candidate = clean_req
        if "[CONTINUE_PAUSED_TASK]" in goal_candidate:
            m_orig = re.search(r"-\s*原始指令[:：]\s*([^\n]+)", goal_candidate)
            goal_candidate = m_orig.group(1).strip() if m_orig else ""
        if (
            scope in ("modify", "execute_device", "deploy")
            and len(goal_candidate) >= 6
            and not is_filter_blocked
            and not goal_candidate.startswith("## ")
        ):
            conn.execute(
                "UPDATE project_facts SET active = 0 WHERE project_slug = ? AND fact_type = 'goal'",
                (slug,),
            )
            conn.execute(
                """
                INSERT INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
                VALUES (?, 'goal', ?, ?, 1, ?)
                ON CONFLICT(project_slug, fact_type, content)
                DO UPDATE SET active = 1, created_at = excluded.created_at
                """,
                (slug, goal_candidate[:240], conv_id or "", now_str),
            )

        for de in extracted["dead_ends"][:5]:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
                VALUES (?, 'dead_end', ?, ?, 1, ?)
                """,
                (slug, de[:300], conv_id or "", now_str),
            )
        for fct in extracted["facts"][:5]:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
                VALUES (?, 'fact', ?, ?, 1, ?)
                """,
                (slug, fct[:300], conv_id or "", now_str),
            )
        for dec in extracted["decisions"][:5]:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
                VALUES (?, 'decision', ?, ?, 1, ?)
                """,
                (slug, dec[:300], conv_id or "", now_str),
            )
        for art in extracted["artifacts"][:6]:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_facts (project_slug, fact_type, content, source_conv_id, active, created_at)
                VALUES (?, 'artifact', ?, ?, 1, ?)
                """,
                (slug, art[:240], conv_id or "", now_str),
            )

        conn.commit()
    finally:
        conn.close()

    if inserted:
        journal_path = get_journal_path(project_dir, custom_root)
        entry = {
            "created_at": now_str,
            "conv_id": conv_id or "",
            "step_index": step_index,
            "scope": scope,
            "tier": tier,
            "outcome": outcome,
            "user_request": clean_req[:500],
            "reply_digest": digest_text[:400],
            "files_changed": extracted["artifacts"][:8],
            "dead_ends": extracted["dead_ends"][:5],
        }
        try:
            with open(journal_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"[AgentMemory] Failed to append journal {journal_path}: {e}")

    if rebuild_capsule:
        _rebuild_capsule_from_db(project_dir, custom_root=custom_root)
    return inserted


def update_project_capsule_incremental(
    project_dir: Optional[str],
    user_request: str,
    reply_text: str,
    chat_id: str = "",
    conv_id: str = "",
    scope: str = "modify",
    tier: str = "Medium",
    outcome: str = "success",
    tool_steps: Optional[List[str]] = None,
    dead_ends: Optional[List[str]] = None,
    custom_root: Optional[str] = None,
) -> str:
    """Record turn and refresh `PROJECT_STATE.md` capsule, returning the capsule path."""
    record_episodic_turn(
        project_dir=project_dir,
        user_request=user_request,
        reply_text=reply_text,
        chat_id=chat_id,
        conv_id=conv_id,
        scope=scope,
        tier=tier,
        outcome=outcome,
        tool_steps=tool_steps,
        dead_ends=dead_ends,
        custom_root=custom_root,
    )
    return get_capsule_path(project_dir, custom_root=custom_root)


def _tokenize_query_for_fts(query: str) -> List[str]:
    """Extract meaningful Chinese bi-grams/phrases and English/numeric tokens for FTS5 / LIKE matching."""
    clean = strip_injected_prompt_headers(query or "")
    # Extract alphanumeric identifiers (filenames, functions, chips, error codes)
    ascii_tokens = re.findall(r"[a-zA-Z0-9_.-]{2,30}", clean)
    # Extract Chinese sequences and split into 2-4 char overlapping terms
    zh_seqs = re.findall(r"[\u4e00-\u9fa5]{2,12}", clean)
    stop_words = {
        "一下", "看看", "什么", "怎么", "为什么", "帮我", "现在", "当前", "这个", "那个",
        "可以", "没有", "是否", "如何", "一个", "我们", "你们", "进行", "执行", "查看",
    }
    zh_tokens = []
    for seq in zh_seqs:
        if seq not in stop_words and len(seq) <= 6:
            zh_tokens.append(seq)
        for i in range(0, max(1, len(seq) - 1)):
            bg = seq[i : i + 2]
            if len(bg) == 2 and bg not in stop_words and bg not in zh_tokens:
                zh_tokens.append(bg)
    tokens = []
    for t in ascii_tokens + zh_tokens:
        t_clean = re.sub(r'["\'()*^:?\\]', "", t).strip()
        if len(t_clean) >= 2 and t_clean not in tokens:
            tokens.append(t_clean)
    return tokens[:10]


def search_episodic_memory(
    project_dir: Optional[str],
    query: str,
    limit: int = 5,
    cross_project: bool = False,
    custom_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search episodic memory using SQLite FTS5 with automatic fallback to multi-term keyword scoring."""
    slug = get_project_slug(project_dir)
    tokens = _tokenize_query_for_fts(query)
    if not tokens:
        return []

    conn = _get_conn(custom_root)
    results: List[Dict[str, Any]] = []
    seen_ids = set()
    try:
        # 1. Try FTS5 MATCH with OR query
        fts_expr = " OR ".join(f'"{t}"' for t in tokens[:8])
        try:
            if cross_project:
                rows = conn.execute(
                    """
                    SELECT e.id, e.project_slug, e.conv_id, e.step_index, e.user_request,
                           e.scope, e.outcome, e.reply_digest, e.files_changed, e.dead_ends, e.created_at
                    FROM episodic_fts f
                    JOIN episodic_turns e ON CAST(f.turn_id AS INTEGER) = e.id
                    WHERE episodic_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (fts_expr, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.id, e.project_slug, e.conv_id, e.step_index, e.user_request,
                           e.scope, e.outcome, e.reply_digest, e.files_changed, e.dead_ends, e.created_at
                    FROM episodic_fts f
                    JOIN episodic_turns e ON CAST(f.turn_id AS INTEGER) = e.id
                    WHERE episodic_fts MATCH ? AND e.project_slug = ?
                    ORDER BY rank LIMIT ?
                    """,
                    (fts_expr, slug, limit),
                ).fetchall()
            for r in rows:
                d = dict(r)
                seen_ids.add(d["id"])
                results.append(d)
        except Exception:
            pass

        # 2. Complement with substring scoring over recent episodic turns (handles CJK substrings inside words)
        if len(results) < limit:
            where_clause = "" if cross_project else "WHERE project_slug = ?"
            params: Tuple[Any, ...] = () if cross_project else (slug,)
            cand_rows = conn.execute(
                f"""
                SELECT id, project_slug, conv_id, step_index, user_request,
                       scope, outcome, reply_digest, files_changed, dead_ends, created_at
                FROM episodic_turns
                {where_clause}
                ORDER BY id DESC LIMIT 200
                """,
                params,
            ).fetchall()
            scored = []
            for r in cand_rows:
                if r["id"] in seen_ids:
                    continue
                haystack = f"{r['user_request']} {r['reply_digest']} {r['files_changed']} {r['dead_ends']}".lower()
                score = sum(2 if len(t) >= 3 else 1 for t in tokens if t.lower() in haystack)
                if score > 0:
                    scored.append((score, r["id"], dict(r)))
            scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
            for _, _, item in scored[: limit - len(results)]:
                results.append(item)
    finally:
        conn.close()

    # Deduplicate recalled entries with identical Q&A content (protects recall slots against historical duplicates)
    deduped: List[Dict[str, Any]] = []
    seen_qa = set()
    for item in results:
        qa_sig = (
            str(item.get("user_request") or "").strip()[:120],
            str(item.get("reply_digest") or "").strip()[:120],
        )
        if qa_sig in seen_qa:
            continue
        seen_qa.add(qa_sig)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    return deduped


# ============================================================================
# Transcript Compaction Inspector & Backfill Engine
# ============================================================================

_CORRUPTED_CHECKPOINT_PATTERNS = re.compile(
    r"(?:blocked by Gemini's filters|blocked by content safety|Sorry,\s*I couldn't generate|request was blocked)",
    re.IGNORECASE,
)


def inspect_and_backfill_transcript(
    transcript_path: str,
    project_dir: Optional[str] = None,
    chat_id: str = "",
    conv_id: str = "",
    force_full: bool = False,
    custom_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Incrementally scan an `agy` `transcript.jsonl` file:
    1. Detects all `CHECKPOINT` compactions and checks whether any summary was truncated/corrupted by safety filters.
    2. Extracts and backfills every completed turn (`USER_INPUT` -> `PLANNER_RESPONSE`) into SQLite FTS5 + `journal.jsonl`.
    3. Extracts failed commands / tool errors into `project_facts` (`dead_end`).
    4. Updates `PROJECT_STATE.md`.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return {
            "conv_id": conv_id,
            "total_steps": 0,
            "checkpoint_count": 0,
            "is_corrupted": False,
            "corruption_reason": "",
            "backfilled_turns": 0,
        }

    if not conv_id:
        # Infer conv_id from `/brain/<conv_id>/.system_generated/logs/transcript.jsonl`
        parts = os.path.abspath(transcript_path).split(os.sep)
        for p in parts:
            if len(p) == 36 and p.count("-") == 4:
                conv_id = p
                break
        if not conv_id:
            conv_id = hashlib.sha1(transcript_path.encode("utf-8")).hexdigest()[:16]

    slug = get_project_slug(project_dir)
    file_size = os.path.getsize(transcript_path)

    conn = _get_conn(custom_root)
    try:
        row = conn.execute(
            "SELECT * FROM compaction_state WHERE conv_id = ?", (conv_id,)
        ).fetchone()
    finally:
        conn.close()

    start_offset = 0
    total_steps = 0
    checkpoint_count = 0
    last_checkpoint_step = 0
    last_checkpoint_summary = ""
    is_corrupted = False
    corruption_reason = ""

    if row and not force_full and row["last_byte_offset"] <= file_size:
        start_offset = int(row["last_byte_offset"])
        total_steps = int(row["total_steps"])
        checkpoint_count = int(row["checkpoint_count"])
        last_checkpoint_step = int(row["last_checkpoint_step"])
        last_checkpoint_summary = row["last_checkpoint_summary"] or ""
        is_corrupted = bool(row["is_corrupted"])
        corruption_reason = row["corruption_reason"] or ""

    if start_offset >= file_size and row and not force_full:
        return {
            "conv_id": conv_id,
            "total_steps": total_steps,
            "checkpoint_count": checkpoint_count,
            "last_checkpoint_step": last_checkpoint_step,
            "is_corrupted": is_corrupted,
            "corruption_reason": corruption_reason,
            "backfilled_turns": 0,
        }

    new_lines = []
    new_offset = file_size
    try:
        with open(transcript_path, "rb") as bf:
            if start_offset > 0:
                bf.seek(start_offset)
            raw_bytes = bf.read()
            new_offset = start_offset + len(raw_bytes)
        new_lines = raw_bytes.decode("utf-8", errors="ignore").splitlines()
    except Exception as e:
        log.warning(f"[AgentMemory] Failed reading transcript {transcript_path}: {e}")
        return {
            "conv_id": conv_id,
            "total_steps": total_steps,
            "checkpoint_count": checkpoint_count,
            "is_corrupted": is_corrupted,
            "corruption_reason": corruption_reason,
            "backfilled_turns": 0,
        }

    current_user_req = ""
    current_user_step = 0
    current_user_time = ""
    current_planner_reply = ""
    current_files: List[str] = []
    current_dead_ends: List[str] = []
    backfilled_turns = 0

    def _flush_turn():
        nonlocal backfilled_turns, current_user_req, current_planner_reply, current_files, current_dead_ends
        if current_user_req and (current_planner_reply or current_dead_ends):
            scope_guess = classify_task_scope_and_budget(current_user_req).get("scope", "modify")
            ok = record_episodic_turn(
                project_dir=project_dir,
                user_request=current_user_req,
                reply_text=current_planner_reply or "（本轮未生成最终总结或被中途截断）",
                chat_id=chat_id,
                conv_id=conv_id,
                step_index=current_user_step,
                scope=scope_guess,
                outcome="success" if current_planner_reply else "interrupted",
                tool_steps=[f"编辑文件: {f}" for f in current_files],
                dead_ends=current_dead_ends,
                created_at=current_user_time or None,
                custom_root=custom_root,
                rebuild_capsule=False,
            )
            if ok:
                backfilled_turns += 1
        current_user_req = ""
        current_planner_reply = ""
        current_files = []
        current_dead_ends = []

    for line in new_lines:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            obj = json.loads(line_str)
        except Exception:
            continue

        total_steps += 1
        step_idx = obj.get("step_index", total_steps)
        step_type = obj.get("type", "")
        content = str(obj.get("content") or "")
        created_at_raw = str(obj.get("created_at") or "")
        created_at_clean = _normalize_turn_timestamp(created_at_raw)

        if step_type == "USER_INPUT":
            _flush_turn()
            cleaned = strip_injected_prompt_headers(content)
            # Ignore internal system-only wakeup prompts if empty
            if cleaned:
                current_user_req = cleaned
                current_user_step = step_idx
                current_user_time = created_at_clean

        elif step_type == "PLANNER_RESPONSE":
            t_calls = obj.get("tool_calls") or []
            for tc in t_calls:
                if isinstance(tc, dict):
                    t_name = tc.get("name") or tc.get("tool_name") or ""
                    t_args = tc.get("args") or tc.get("parameters") or {}
                    if isinstance(t_args, dict) and t_name in ("write_file", "edit_file", "write_to_file", "replace_file_content"):
                        fp = t_args.get("Path") or t_args.get("TargetFile") or t_args.get("path") or ""
                        if fp and fp not in current_files:
                            current_files.append(os.path.basename(str(fp)))
            if content.strip():
                # Strip [TASK_PLAN] tag from planner response
                clean_resp = re.sub(r"\[TASK_PLAN\].*?\[/TASK_PLAN\]\s*", "", content, flags=re.DOTALL).strip()
                if _CORRUPTED_CHECKPOINT_PATTERNS.search(clean_resp):
                    is_corrupted = True
                    corruption_reason = f"Step {step_idx} 触发 Gemini 云端安全过滤器拦截 (blocked by Gemini's filters)"
                if clean_resp and not clean_resp.startswith("Task \"") and "completed with exit code" not in clean_resp[:80]:
                    current_planner_reply = clean_resp

        elif step_type == "GENERIC":
            # Detect command failures or explicit error outputs (skip Created At / Completed At headers)
            if "exited with code" in content and "exited with code 0" not in content:
                body = content.split("Command output:", 1)[-1] if "Command output:" in content else content
                err_lines = [
                    ln.strip()
                    for ln in body.splitlines()
                    if ln.strip()
                    and not ln.strip().startswith(("Created At:", "Completed At:", "Command exited with code", "Command output:"))
                ]
                first_err = err_lines[0][:180] if err_lines else ""
                if len(first_err) >= 6 and f"命令非零退出: {first_err}" not in current_dead_ends:
                    current_dead_ends.append(f"命令非零退出: {first_err}")

        elif step_type == "CHECKPOINT":
            checkpoint_count += 1
            last_checkpoint_step = step_idx
            last_checkpoint_summary = _sanitize_text(content[:1500])
            if _CORRUPTED_CHECKPOINT_PATTERNS.search(content):
                is_corrupted = True
                corruption_reason = f"Step {step_idx} CHECKPOINT 摘要被安全过滤器截断 (blocked by Gemini's filters)"
                current_dead_ends.append(corruption_reason)
            elif len(content.strip()) < 260:
                is_corrupted = True
                corruption_reason = f"Step {step_idx} CHECKPOINT 摘要异常过短 ({len(content.strip())} chars)"

    _flush_turn()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn(custom_root)
    try:
        conn.execute(
            """
            INSERT INTO compaction_state
            (conv_id, project_slug, total_steps, checkpoint_count, last_checkpoint_step,
             last_checkpoint_summary, is_corrupted, corruption_reason, last_byte_offset, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conv_id) DO UPDATE SET
                project_slug = excluded.project_slug,
                total_steps = excluded.total_steps,
                checkpoint_count = excluded.checkpoint_count,
                last_checkpoint_step = excluded.last_checkpoint_step,
                last_checkpoint_summary = excluded.last_checkpoint_summary,
                is_corrupted = excluded.is_corrupted,
                corruption_reason = excluded.corruption_reason,
                last_byte_offset = excluded.last_byte_offset,
                updated_at = excluded.updated_at
            """,
            (
                conv_id,
                slug,
                total_steps,
                checkpoint_count,
                last_checkpoint_step,
                last_checkpoint_summary,
                1 if is_corrupted else 0,
                corruption_reason,
                new_offset,
                now_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _rebuild_capsule_from_db(project_dir, custom_root=custom_root)
    return {
        "conv_id": conv_id,
        "total_steps": total_steps,
        "checkpoint_count": checkpoint_count,
        "last_checkpoint_step": last_checkpoint_step,
        "is_corrupted": is_corrupted,
        "corruption_reason": corruption_reason,
        "backfilled_turns": backfilled_turns,
    }


def get_compaction_state(conv_id: str, custom_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not conv_id:
        return None
    conn = _get_conn(custom_root)
    try:
        row = conn.execute("SELECT * FROM compaction_state WHERE conv_id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["is_corrupted"] = bool(d.get("is_corrupted"))
        return d
    finally:
        conn.close()


# ============================================================================
# Capacity-Error Health Check & Recent Turn Extraction (503 异常轨迹检测与上下文提取)
# ============================================================================

def inspect_conversation_capacity_health(
    transcript_path: Optional[str],
    min_byte_offset: int = 0,
) -> Dict[str, Any]:
    """Inspect a transcript.jsonl file for trailing HTTP 503 / model capacity errors.

    Counts consecutive capacity `ERROR_MESSAGE` steps that occurred AFTER the last
    non-empty `PLANNER_RESPONSE` (or since `min_byte_offset` when polling live).
    A subsequent successful `PLANNER_RESPONSE` resets `trailing_capacity_errors` to 0.
    """
    result: Dict[str, Any] = {
        "exists": False,
        "trailing_capacity_errors": 0,
        "total_capacity_errors": 0,
        "has_any_planner_reply": False,
        "failed_models": [],
        "last_error_text": "",
        "is_capacity_tainted": False,
    }
    if not transcript_path or not os.path.exists(transcript_path):
        return result

    result["exists"] = True
    try:
        file_size = os.path.getsize(transcript_path)
        start_pos = max(0, int(min_byte_offset or 0))
        if start_pos == 0 and file_size > 262144:
            # For very large transcripts, scanning the last 256KB is sufficient for trailing health
            start_pos = file_size - 262144
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            if start_pos > 0:
                f.seek(start_pos)
                if min_byte_offset == 0:
                    f.readline()  # discard partial first line after seek
            lines = f.readlines()
    except Exception as e:
        log.warning(f"[AgentMemory] Failed to inspect capacity health of {transcript_path}: {e}")
        return result

    trailing_errors = 0
    total_errors = 0
    has_reply = False
    failed_models: List[str] = []
    last_err = ""

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except Exception:
            continue
        step_type = obj.get("type", "")
        if step_type == "PLANNER_RESPONSE":
            content = str(obj.get("content") or "").strip()
            t_calls = obj.get("tool_calls") or []
            if content or t_calls:
                has_reply = True
                trailing_errors = 0
                failed_models.clear()
        elif step_type == "ERROR_MESSAGE":
            err_text = str(obj.get("error") or obj.get("content") or "").strip()
            if err_text and _CAPACITY_ERROR_RE.search(err_text):
                total_errors += 1
                trailing_errors += 1
                last_err = err_text
                m = _CAPACITY_ERROR_MODEL_RE.search(err_text)
                if m:
                    model_id = m.group(1).strip()
                    if model_id and model_id not in failed_models:
                        failed_models.append(model_id)

    result["trailing_capacity_errors"] = trailing_errors
    result["total_capacity_errors"] = total_errors
    result["has_any_planner_reply"] = has_reply
    result["failed_models"] = failed_models
    result["last_error_text"] = last_err
    result["is_capacity_tainted"] = trailing_errors >= 1
    return result


def extract_recent_conversation_turns(
    transcript_path: Optional[str],
    max_turns: int = 6,
    max_chars: int = 2400,
) -> str:
    """Extract the most recent clean Q&A turns from a conversation transcript.jsonl.

    Preserves actual dialogue continuity (user requests + assistant replies) across a
    seamless model-switch session relay without carrying over poisoned 503 error steps.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""

    turns: List[Tuple[str, str]] = []
    current_req = ""
    current_reply = ""

    def _flush():
        nonlocal current_req, current_reply
        if current_req and current_reply:
            turns.append((current_req, current_reply))
        current_req = ""
        current_reply = ""

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except Exception:
                    continue
                step_type = obj.get("type", "")
                content = str(obj.get("content") or "")
                if step_type == "USER_INPUT":
                    _flush()
                    cleaned = strip_injected_prompt_headers(content)
                    if cleaned:
                        current_req = cleaned
                elif step_type == "PLANNER_RESPONSE" and content.strip():
                    clean_resp = re.sub(
                        r"\[TASK_PLAN\].*?\[/TASK_PLAN\]\s*", "", content, flags=re.DOTALL
                    ).strip()
                    if (
                        clean_resp
                        and not clean_resp.startswith('Task "')
                        and "completed with exit code" not in clean_resp[:80]
                        and not _CORRUPTED_CHECKPOINT_PATTERNS.search(clean_resp)
                    ):
                        current_reply = clean_resp
        _flush()
    except Exception as e:
        log.warning(f"[AgentMemory] Failed to extract recent turns from {transcript_path}: {e}")
        return ""

    if not turns:
        return ""

    recent = turns[-max_turns:]
    formatted: List[str] = []
    used_chars = 0
    for q_text, a_text in reversed(recent):
        q_clean = re.sub(r"\s+", " ", q_text).strip()[:220]
        a_clean = re.sub(r"\s+", " ", a_text).strip()[:360]
        entry = f"- 用户：{q_clean}\n  助手：{a_clean}"
        if used_chars + len(entry) > max_chars and formatted:
            break
        formatted.append(entry)
        used_chars += len(entry) + 1

    formatted.reverse()
    return "\n".join(formatted)


def _build_session_relay_handoff(
    conv_id: str,
    proj: str,
    relay_reason: str,
    transcript_path: Optional[str] = None,
    custom_root: Optional[str] = None,
    include_recent_turns: bool = False,
) -> Tuple[str, int]:
    """Build a compact Relay Handoff Package combining recent Q&A turns + PROJECT_STATE.md."""
    recent_turns_md = ""
    recent_turns_count = 0
    if include_recent_turns and transcript_path:
        recent_turns_md = extract_recent_conversation_turns(transcript_path)
        if recent_turns_md:
            recent_turns_count = recent_turns_md.count("\n- 用户：") + (1 if recent_turns_md.startswith("- 用户：") else 0)

    capsule_md = read_project_capsule(proj, custom_root=custom_root).strip()
    parts = [
        "[Session Relay Handoff / 跨会话无感接力记忆包]",
        f"- 前序会话 ID: `{conv_id}` ({relay_reason})",
        f"- 历史完整对话已归档至本地 FTS5 记忆库，可通过 `python3 {os.path.join(config.BASE_DIR, 'tools', 'memory_cli.py')} search \"<关键词>\"` 随时检索。",
    ]
    if recent_turns_md:
        parts.append("")
        parts.append("[近期对话上下文摘要 / Recent Conversation Turns]")
        parts.append(recent_turns_md)
    if capsule_md and "暂无活跃目标记录" not in capsule_md:
        parts.append("")
        parts.append(capsule_md[:2800 if recent_turns_md else 3600])
    parts.append("[/Session Relay Handoff]")
    return "\n".join(parts) + "\n", recent_turns_count


def relay_conversation_for_model_switch(
    chat_id: str,
    session_data: Dict[str, Any],
    new_model: str,
    old_model: str = "",
    project_dir: Optional[str] = None,
    transcript_path_override: Optional[str] = None,
    custom_root: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Seamlessly relay a 503/capacity-tainted conversation when the user switches models.

    If the current conversation has trailing 503 capacity errors (or was marked
    `_capacity_tainted_conv` by executor), this archives its clean dialogue history into
    SQLite FTS5 + a Relay Handoff Package (including recent Q&A turns + PROJECT_STATE.md)
    and resets `session_data["conversation"] = ""` so the new model starts on a clean
    trajectory WITHOUT requiring the user to run `/clear`.

    Healthy conversations with no capacity errors are left untouched (`relayed=False`).
    """
    conv_id = (session_data or {}).get("conversation", "")
    if not conv_id:
        # Clear any stale taint markers even if conversation is already empty
        session_data.pop("_capacity_tainted_conv", None)
        session_data.pop("_capacity_error_model", None)
        session_data.pop("_capacity_error_at", None)
        return {"relayed": False, "reason": "no_active_conversation"}

    proj = project_dir or session_data.get("project") or "默认"
    tx_path = transcript_path_override or config.get_transcript_path(conv_id)
    cap_health = inspect_conversation_capacity_health(tx_path)
    marked_tainted = bool(session_data.get("_capacity_tainted_conv") == conv_id)

    if not force and not marked_tainted and not cap_health.get("is_capacity_tainted"):
        return {
            "relayed": False,
            "conv_id": conv_id,
            "reason": "conversation_healthy",
        }

    comp_info = inspect_and_backfill_transcript(
        tx_path, project_dir=proj, chat_id=chat_id, conv_id=conv_id, custom_root=custom_root
    )
    failed_models = cap_health.get("failed_models") or []
    err_model = (
        session_data.get("_capacity_error_model")
        or (failed_models[-1] if failed_models else "")
        or old_model
        or "原模型"
    )
    trailing_cnt = int(cap_health.get("trailing_capacity_errors", 0))
    relay_reason = (
        f"模型 {err_model} 触发 503 容量不足（尾部异常 {trailing_cnt} 次），"
        f"切换至 {new_model} 自动无感接力"
    )

    lineage = list(session_data.get("conversation_lineage") or [])
    lineage.append(
        {
            "conv_id": conv_id,
            "project": proj,
            "checkpoint_count": int(comp_info.get("checkpoint_count", 0)),
            "total_steps": int(comp_info.get("total_steps", 0)),
            "is_corrupted": False,
            "reason": relay_reason,
            "relayed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    session_data["conversation_lineage"] = lineage[-20:]

    handoff_pkg, preserved_turns = _build_session_relay_handoff(
        conv_id=conv_id,
        proj=proj,
        relay_reason=relay_reason,
        transcript_path=tx_path,
        custom_root=custom_root,
        include_recent_turns=True,
    )
    session_data["_relay_handoff_context"] = handoff_pkg
    session_data["conversation"] = ""
    session_data.pop("_sys_prompt_injected_conv", None)
    session_data.pop("_capacity_tainted_conv", None)
    session_data.pop("_capacity_error_model", None)
    session_data.pop("_capacity_error_at", None)

    log.info(
        f"[AgentMemory] Capacity-switch auto-relay triggered for chat {chat_id}: "
        f"old_conv={conv_id}, {err_model} -> {new_model}, trailing_503={trailing_cnt}, "
        f"preserved_turns={preserved_turns}"
    )
    return {
        "relayed": True,
        "old_conv_id": conv_id,
        "reason": relay_reason,
        "preserved_turns": preserved_turns,
        "handoff_context": handoff_pkg,
    }


# ============================================================================
# Proactive Session Relay (无感会话接力)
# ============================================================================

def check_and_prepare_session_relay(
    chat_id: str,
    session_data: Dict[str, Any],
    project_dir: Optional[str] = None,
    checkpoint_threshold: int = DEFAULT_RELAY_CHECKPOINT_THRESHOLD,
    transcript_path_override: Optional[str] = None,
    custom_root: Optional[str] = None,
    current_model: str = "",
) -> Dict[str, Any]:
    """Check if the active conversation in `session_data` has reached the compaction threshold
    (`checkpoint_count >= 3`), suffered a corrupted `CHECKPOINT` summary, or accumulated
    trailing 503 model-capacity errors (especially after switching to another model).

    When triggered:
      1. Backfills all turns from the old conversation into SQLite FTS5 and `PROJECT_STATE.md`.
      2. Appends the old `conv_id` to `session_data["conversation_lineage"]`.
      3. Builds a compact Relay Handoff Package (`session_data["_relay_handoff_context"]`).
      4. Clears `session_data["conversation"] = ""` so the next turn seamlessly starts a fresh `agy` conversation.
    """
    conv_id = (session_data or {}).get("conversation", "")
    if not conv_id:
        return {"relayed": False, "reason": "no_active_conversation"}

    proj = project_dir or session_data.get("project") or "默认"
    tx_path = transcript_path_override or config.get_transcript_path(conv_id)
    comp_info = inspect_and_backfill_transcript(
        tx_path, project_dir=proj, chat_id=chat_id, conv_id=conv_id, custom_root=custom_root
    )

    cp_count = int(comp_info.get("checkpoint_count", 0))
    is_corrupted = bool(comp_info.get("is_corrupted", False))
    corruption_reason = comp_info.get("corruption_reason", "")

    # Also check for 503 capacity-tainted conversation trajectories (safety net when
    # model was switched or a prior conversation accumulated repeated 503 errors)
    cap_health = inspect_conversation_capacity_health(tx_path)
    trailing_503 = int(cap_health.get("trailing_capacity_errors", 0))
    failed_models = cap_health.get("failed_models") or []
    active_model = (current_model or session_data.get("model") or "").strip()
    marked_tainted = bool(session_data.get("_capacity_tainted_conv") == conv_id)
    recorded_err_model = (session_data.get("_capacity_error_model") or "").strip()
    model_changed_after_503 = bool(
        active_model
        and (
            (recorded_err_model and active_model != recorded_err_model)
            or (failed_models and active_model not in failed_models)
        )
    )
    capacity_relay_needed = bool(
        (marked_tainted and model_changed_after_503)
        or (trailing_503 >= 1 and model_changed_after_503)
        or (trailing_503 >= 8)
    )

    if cp_count < checkpoint_threshold and not is_corrupted and not capacity_relay_needed:
        return {
            "relayed": False,
            "conv_id": conv_id,
            "checkpoint_count": cp_count,
            "is_corrupted": False,
        }

    if is_corrupted:
        relay_reason = corruption_reason
    elif capacity_relay_needed:
        err_m = recorded_err_model or (failed_models[-1] if failed_models else "原模型")
        relay_reason = f"会话尾部存在 {trailing_503} 次模型容量不足(503)异常 ({err_m})，自动无感接力至干净会话"
    else:
        relay_reason = f"会话累计发生 {cp_count} 次上下文压缩 (达到 >= {checkpoint_threshold} 次主动接力阈值)"

    lineage = list(session_data.get("conversation_lineage") or [])
    lineage.append(
        {
            "conv_id": conv_id,
            "project": proj,
            "checkpoint_count": cp_count,
            "total_steps": comp_info.get("total_steps", 0),
            "is_corrupted": is_corrupted,
            "reason": relay_reason,
            "relayed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    session_data["conversation_lineage"] = lineage[-20:]

    # Build the Relay Handoff Context (include recent Q&A turns for seamless continuity)
    handoff_pkg, _ = _build_session_relay_handoff(
        conv_id=conv_id,
        proj=proj,
        relay_reason=relay_reason,
        transcript_path=tx_path,
        custom_root=custom_root,
        include_recent_turns=capacity_relay_needed,
    )
    session_data["_relay_handoff_context"] = handoff_pkg
    session_data["conversation"] = ""
    session_data.pop("_sys_prompt_injected_conv", None)
    session_data.pop("_capacity_tainted_conv", None)
    session_data.pop("_capacity_error_model", None)
    session_data.pop("_capacity_error_at", None)

    log.info(
        f"[AgentMemory] Proactive Session Relay triggered for chat {chat_id}: "
        f"old_conv={conv_id}, checkpoints={cp_count}, corrupted={is_corrupted}, "
        f"capacity_relay={capacity_relay_needed}, reason={relay_reason}"
    )
    return {
        "relayed": True,
        "old_conv_id": conv_id,
        "checkpoint_count": cp_count,
        "is_corrupted": is_corrupted,
        "capacity_relayed": capacity_relay_needed,
        "reason": relay_reason,
        "handoff_context": handoff_pkg,
    }


# ============================================================================
# On-Demand Recall for Prompt Injection
# ============================================================================

def recall_for_turn(
    project_dir: Optional[str],
    user_query: str,
    is_new_or_relayed: bool = False,
    recently_compacted: bool = False,
    relay_handoff_context: str = "",
    custom_root: Optional[str] = None,
) -> str:
    """Build the memory context block to inject into the turn prompt.
    - If `relay_handoff_context` is present: returns the full relay handoff package + relevant FTS5 hits.
    - If `is_new_or_relayed` or `recently_compacted`: returns the `PROJECT_STATE.md` capsule + relevant FTS5 hits.
    - Otherwise (normal subsequent turn in the same conversation): returns only a compact `< 800B` reminder
      containing active `dead_end` guardrails and top-2 FTS5 matches (if any).
    """
    blocks: List[str] = []

    if relay_handoff_context:
        blocks.append(relay_handoff_context.strip())
    elif is_new_or_relayed or recently_compacted:
        capsule = read_project_capsule(project_dir, custom_root=custom_root).strip()
        if capsule and "暂无活跃目标记录" not in capsule:
            blocks.append(
                f"[Project Memory Capsule / 项目长效记忆胶囊]\n{capsule[:3200]}\n[/Project Memory Capsule]"
            )
    else:
        # Compact reminder for ongoing conversation turns: only dead_ends + top FTS hits
        dead_ends = get_project_facts(project_dir, fact_type="dead_end", limit=5, custom_root=custom_root)
        if dead_ends:
            de_lines = "\n".join(f"  - ⛔ {d['content']}" for d in dead_ends)
            blocks.append(f"[Memory Guard: 已证伪路径(禁止重复尝试)]\n{de_lines}")

    hits = search_episodic_memory(project_dir, user_query, limit=3, custom_root=custom_root)
    if hits:
        hit_lines = []
        for h in hits[:3]:
            req_s = re.sub(r"\s+", " ", h.get("user_request", "")).strip()[:70]
            dig_s = re.sub(r"\s+", " ", h.get("reply_digest", "")).strip()[:130]
            hit_lines.append(f"  - [{h.get('created_at', '')[:16]}] Q: {req_s} -> A: {dig_s}")
        blocks.append("[Recalled Episodic Memory / 检索命中的历史记忆]\n" + "\n".join(hit_lines))

    return "\n\n".join(blocks)


def get_project_memory_summary(
    project_dir: Optional[str],
    session_data: Optional[Dict[str, Any]] = None,
    custom_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Return structured statistics and capsule preview for the `/memory` card UI."""
    slug = get_project_slug(project_dir)
    capsule_md = read_project_capsule(project_dir, custom_root=custom_root)
    conn = _get_conn(custom_root)
    try:
        turns_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM episodic_turns WHERE project_slug = ?", (slug,)
        ).fetchone()["c"]
        facts_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM project_facts WHERE project_slug = ? AND active = 1", (slug,)
        ).fetchone()["c"]
        dead_ends_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM project_facts WHERE project_slug = ? AND fact_type = 'dead_end' AND active = 1",
            (slug,),
        ).fetchone()["c"]
    finally:
        conn.close()

    conv_id = (session_data or {}).get("conversation", "")
    comp_state = get_compaction_state(conv_id, custom_root=custom_root) if conv_id else None
    lineage = (session_data or {}).get("conversation_lineage") or []

    return {
        "project_dir": project_dir or "默认",
        "project_slug": slug,
        "capsule_path": get_capsule_path(project_dir, custom_root=custom_root),
        "capsule_markdown": capsule_md,
        "episodic_turns_count": turns_cnt,
        "active_facts_count": facts_cnt,
        "dead_ends_count": dead_ends_cnt,
        "current_conv_id": conv_id,
        "current_checkpoints": comp_state["checkpoint_count"] if comp_state else 0,
        "is_corrupted": comp_state["is_corrupted"] if comp_state else False,
        "relay_lineage_count": len(lineage),
    }
