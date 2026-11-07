"""Long-Horizon Hierarchical Agent Memory & Decision Guard Package.

Stores all project memory capsules (`PROJECT_STATE.md`), episodic journals (`journal.jsonl`),
FTS5 search indices (`memory.db`), and turn contracts inside `<BOT_ROOT>/data/agent_memory/`.
"""

from agent_memory.store import (
    get_memory_root,
    get_project_slug,
    get_project_memory_dir,
    get_capsule_path,
    classify_task_scope_and_budget,
    read_project_capsule,
    write_project_capsule,
    update_project_capsule_incremental,
    add_project_fact,
    get_project_facts,
    record_episodic_turn,
    search_episodic_memory,
    inspect_and_backfill_transcript,
    get_compaction_state,
    check_and_prepare_session_relay,
    recall_for_turn,
    write_turn_contract,
    read_turn_contract,
    strip_injected_prompt_headers,
    get_project_memory_summary,
)

__all__ = [
    "get_memory_root",
    "get_project_slug",
    "get_project_memory_dir",
    "get_capsule_path",
    "classify_task_scope_and_budget",
    "read_project_capsule",
    "write_project_capsule",
    "update_project_capsule_incremental",
    "add_project_fact",
    "get_project_facts",
    "record_episodic_turn",
    "search_episodic_memory",
    "inspect_and_backfill_transcript",
    "get_compaction_state",
    "check_and_prepare_session_relay",
    "recall_for_turn",
    "write_turn_contract",
    "read_turn_contract",
    "strip_injected_prompt_headers",
    "get_project_memory_summary",
]
