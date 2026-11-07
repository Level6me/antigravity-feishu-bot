#!/usr/bin/env python3
"""CLI tool for querying and updating Long-Horizon Project Memory (`<BOT_ROOT>/data/agent_memory`).

Usage examples:
  python3 tools/memory_cli.py capsule --project /path/to/repo
  python3 tools/memory_cli.py search "PA2 外部供电 电压" --project /path/to/repo
  python3 tools/memory_cli.py add-dead-end "PA2 在不接电瓶时悬空为 0V，不可作为唤醒触发源" --project /path/to/repo
  python3 tools/memory_cli.py add-fact "MCU 型号为 GD32E230C8T6，SWD 接口正常" --project /path/to/repo
  python3 tools/memory_cli.py backfill --transcript /path/to/transcript.jsonl --project /path/to/repo
"""

import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from agent_memory.store import (
    add_project_fact,
    get_project_facts,
    get_project_memory_summary,
    inspect_and_backfill_transcript,
    read_project_capsule,
    search_episodic_memory,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Long-Horizon Memory CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capsule", help="View the current PROJECT_STATE.md capsule")
    p_cap.add_argument("--project", default=os.getcwd(), help="Project directory")

    p_search = sub.add_parser("search", help="Search episodic memory via FTS5")
    p_search.add_argument("query", help="Keywords or question to search")
    p_search.add_argument("--project", default=os.getcwd(), help="Project directory")
    p_search.add_argument("--limit", type=int, default=5, help="Max results")
    p_search.add_argument("--all-projects", action="store_true", help="Search across all projects")

    p_facts = sub.add_parser("facts", help="List structured facts/decisions/dead_ends")
    p_facts.add_argument("--project", default=os.getcwd(), help="Project directory")
    p_facts.add_argument("--type", default=None, choices=["fact", "decision", "dead_end", "artifact", "todo", "goal"])

    p_add_fact = sub.add_parser("add-fact", help="Record a confirmed fact or decision")
    p_add_fact.add_argument("content", help="Fact description")
    p_add_fact.add_argument("--type", default="fact", choices=["fact", "decision", "dead_end", "artifact", "todo", "goal"])
    p_add_fact.add_argument("--project", default=os.getcwd(), help="Project directory")

    p_add_de = sub.add_parser("add-dead-end", help="Record a disproven path / dead end")
    p_add_de.add_argument("content", help="Dead-end description")
    p_add_de.add_argument("--project", default=os.getcwd(), help="Project directory")

    p_bf = sub.add_parser("backfill", help="Inspect and backfill an agy transcript.jsonl")
    p_bf.add_argument("--transcript", required=True, help="Path to transcript.jsonl")
    p_bf.add_argument("--project", default=os.getcwd(), help="Project directory")
    p_bf.add_argument("--conv-id", default="", help="Optional conversation ID")
    p_bf.add_argument("--force", action="store_true", help="Force full rescan from byte 0")

    p_stat = sub.add_parser("status", help="Show memory summary JSON for a project")
    p_stat.add_argument("--project", default=os.getcwd(), help="Project directory")

    args = parser.parse_args()

    if args.cmd == "capsule":
        print(read_project_capsule(args.project))
        return 0

    if args.cmd == "search":
        hits = search_episodic_memory(
            args.project, args.query, limit=args.limit, cross_project=args.all_projects
        )
        if not hits:
            print("未检索到匹配的历史记忆条目。")
            return 0
        for idx, h in enumerate(hits, 1):
            print(f"[{idx}] ({h['created_at']} | conv={h['conv_id'][:8]} | scope={h['scope']})")
            print(f"  Q: {h['user_request']}")
            print(f"  A: {h['reply_digest']}")
            if h.get("dead_ends"):
                print(f"  ⛔ Dead Ends: {h['dead_ends']}")
            if h.get("files_changed"):
                print(f"  📄 Files: {h['files_changed']}")
            print()
        return 0

    if args.cmd == "facts":
        facts = get_project_facts(args.project, fact_type=args.type, limit=30)
        for f in facts:
            print(f"- [{f['fact_type']}] ({f['created_at']}): {f['content']}")
        return 0

    if args.cmd == "add-fact":
        ok = add_project_fact(args.project, args.type, args.content)
        print("已写入项目记忆胶囊。" if ok else "写入失败（内容为空或过短）。")
        return 0 if ok else 1

    if args.cmd == "add-dead-end":
        ok = add_project_fact(args.project, "dead_end", args.content)
        print("已记录证伪死胡同并同步至 PROJECT_STATE.md。" if ok else "记录失败。")
        return 0 if ok else 1

    if args.cmd == "backfill":
        res = inspect_and_backfill_transcript(
            args.transcript,
            project_dir=args.project,
            conv_id=args.conv_id,
            force_full=args.force,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "status":
        summary = get_project_memory_summary(args.project)
        summary.pop("capsule_markdown", None)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
