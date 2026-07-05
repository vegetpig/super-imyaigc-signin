#!/usr/bin/env python3
"""Run the optional IMYAI-Agent all-in-one model once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from imyai_config import default_config_path
from imyai_proxy import ImyaiClient


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


AGENT_MODEL_TYPE_ID = 228


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run IMYAI-Agent all-in-one model")
    parser.add_argument("--config", default=str(default_config_path(script_dir)), help="Path to signin skill config.json")
    parser.add_argument("--phone", default=None, help="Phone number whose saved cookies should be used")
    parser.add_argument("--task", default="", help="Task description for IMYAI-Agent")
    parser.add_argument("--group-id", type=int, default=None, help="Reuse an existing official IMYAI group id")
    parser.add_argument("--timeout", type=int, default=180, help="Upstream request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def is_credit_error(message: str) -> bool:
    text = message.lower()
    return "insufficient_credits" in text or "积分" in message


def main() -> None:
    args = parse_args()
    task = args.task.strip()
    if not task:
        print("ERROR: --task is required", file=sys.stderr)
        raise SystemExit(1)

    try:
        client = ImyaiClient(Path(args.config), phone=args.phone, timeout=args.timeout)
        result = client.official_chat(
            task,
            AGENT_MODEL_TYPE_ID,
            group_id=args.group_id,
            group_name="IMYAI-Agent",
        )
        history_entries = result.get("history_entries")
        payload: dict[str, Any] = {
            "phone": client.phone,
            "group_id": result.get("group_id"),
            "text": result.get("text") or "",
            "history_entries_count": len(history_entries) if isinstance(history_entries, list) else 0,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(payload["text"])
        print(f"groupId={payload['group_id']}")
    except Exception as exc:
        message = str(exc)
        if is_credit_error(message):
            print(f"ERROR: IMYAI-Agent 积分不足或不可用: {message}", file=sys.stderr)
        else:
            print(f"ERROR: {message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
