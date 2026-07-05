#!/usr/bin/env python3
"""Ask an official IMYAI web model once and print the model reply."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from imyai_config import default_config_path
from imyai_proxy import DEFAULT_MODEL_ID, ImyaiClient


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PREFERRED_ALIASES = {
    "claude": [
        "claude-opus-4.7",
        "claude-opus-4.6",
        "claude-sonnet-4.6",
        "claude-haiku",
    ],
    "opus": ["claude-opus-4.7", "claude-opus-4.6", "claude-opus-4.8", "claude-opus-4.8-max"],
    "sonnet": ["claude-sonnet-4.6"],
    "haiku": ["claude-haiku"],
    "ava": ["imyai-ava"],
}


def session_key_from_cwd(phone: str | None) -> str:
    cwd = str(Path.cwd().resolve())
    digest = hashlib.sha1(f"{phone or ''}:{cwd}".encode("utf-8")).hexdigest()[:12]
    return f"codex-{digest}"


def normalize_session_key(value: str, phone: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.lower() == "auto":
        return session_key_from_cwd(phone)
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "-", value).strip("-")
    return cleaned or session_key_from_cwd(phone)


def session_state_path(config_path: Path, session_key: str) -> Path:
    return config_path.resolve().parent / "sessions" / f"{session_key}.json"


def load_session_state(config_path: Path, session_key: str) -> dict[str, Any]:
    if not session_key:
        return {}
    path = session_state_path(config_path, session_key)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def save_session_state(config_path: Path, session_key: str, state: dict[str, Any]) -> Path:
    path = session_state_path(config_path, session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["session"] = session_key
    state["updatedAt"] = int(time.time())
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return path


def clear_session_state(config_path: Path, session_key: str) -> bool:
    path = session_state_path(config_path, session_key)
    if path.exists():
        path.unlink()
        return True
    return False


def compact_session_messages(messages: list[dict[str, str]], max_turns: int) -> list[dict[str, str]]:
    max_messages = max(0, max_turns) * 2
    if not max_messages:
        return []
    return messages[-max_messages:]


def build_session_prompt(prompt: str, state: dict[str, Any], max_turns: int) -> tuple[str, bool]:
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        return prompt, False

    history_lines = []
    for message in compact_session_messages(messages, max_turns):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        label = "用户" if role == "user" else "IMYAI"
        history_lines.append(f"{label}: {content}")

    if not history_lines:
        return prompt, False

    wrapped = (
        "你正在继续一个 IMYAI 会话。下面是本地保存的最近对话历史；"
        "请基于这些历史直接回答最后一条用户消息，不要解释这段包装。\n\n"
        "最近对话历史:\n"
        + "\n\n".join(history_lines)
        + "\n\n当前用户消息:\n"
        + prompt
    )
    return wrapped, True


def update_session_after_reply(
    state: dict[str, Any],
    prompt: str,
    text: str,
    model_arg: str,
    model_type_id: int,
    chosen_model: dict[str, Any] | None,
    group_id: int | None,
    max_turns: int,
) -> dict[str, Any]:
    messages = state.get("messages")
    if not isinstance(messages, list):
        messages = []
    messages.extend(
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text},
        ]
    )
    state["messages"] = compact_session_messages(messages, max_turns)
    state["model"] = model_arg
    state["modelTypeId"] = model_type_id
    state["resolvedModel"] = chosen_model
    if group_id is not None:
        state["groupId"] = group_id
    return state


def session_summary(config_path: Path, session_key: str, state: dict[str, Any]) -> dict[str, Any]:
    messages = state.get("messages")
    return {
        "key": session_key or None,
        "path": str(session_state_path(config_path, session_key)) if session_key else None,
        "model": state.get("model"),
        "modelTypeId": state.get("modelTypeId"),
        "resolvedModel": state.get("resolvedModel"),
        "groupId": state.get("groupId"),
        "messages": len(messages) if isinstance(messages, list) else 0,
        "updatedAt": state.get("updatedAt"),
    }


def read_text_argument(value: str, prompt_file: str | None) -> str:
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8-sig")
    if value == "-":
        return sys.stdin.read()
    return value


def normalize_model_lookup(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def model_text_variants(model: dict[str, Any]) -> list[str]:
    return [
        str(model.get(key) or "")
        for key in ("id", "name", "display_name", "raw_model", "modelTypeId")
    ]


def model_match_score(model: dict[str, Any], query: str) -> int:
    query_lower = query.strip().lower()
    query_norm = normalize_model_lookup(query)
    if not query_lower:
        return 0

    variants = [value.strip() for value in model_text_variants(model) if value.strip()]
    lower_variants = [value.lower() for value in variants]
    normalized_variants = [normalize_model_lookup(value) for value in variants]
    normalized_haystack = " ".join(normalized_variants)

    if query_lower in lower_variants:
        return 100
    if query_norm and query_norm in normalized_variants:
        return 96
    if any(value.startswith(query_lower) for value in lower_variants):
        return 90
    if query_norm and any(value.startswith(query_norm) for value in normalized_variants):
        return 86
    if any(query_lower in value for value in lower_variants):
        return 78
    if query_norm and any(query_norm in value for value in normalized_variants):
        return 74

    tokens = query_norm.split()
    if tokens and all(token in normalized_haystack for token in tokens):
        return 60 + min(len(tokens), 10)
    return 0


def find_matching_models(client: ImyaiClient, query: str) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for model in client.codex_models():
        score = model_match_score(model, query)
        if score:
            scored.append((score, model))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [model for _, model in scored]


def ascii_safe(value: Any) -> str:
    return str(value or "").encode("ascii", "backslashreplace").decode("ascii")


def resolve_model_argument(client: ImyaiClient, model_arg: str) -> tuple[int, dict[str, Any] | None]:
    try:
        model_type_id = client.resolve_model_type_id(model_arg)
        chosen = next(
            (model for model in client.codex_models() if int(model.get("modelTypeId") or 0) == model_type_id),
            None,
        )
        return model_type_id, chosen
    except Exception:
        pass

    normalized = model_arg.strip().lower()
    models = client.codex_models()
    for pattern in PREFERRED_ALIASES.get(normalized, []):
        for model in models:
            name = str(model.get("name") or "").lower()
            model_id = str(model.get("id") or "").lower()
            raw_model = str(model.get("raw_model") or "").lower()
            haystack = f"{model_id} {name} {raw_model}"
            if pattern in haystack:
                return int(model["modelTypeId"]), model

    matches = find_matching_models(client, model_arg)
    if matches:
        return int(matches[0]["modelTypeId"]), matches[0]
    raise RuntimeError(f"Unknown model '{model_arg}'. Run --search-model {model_arg!r} or --list-models-compact.")


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Ask an IMYAI official web model and return its text reply")
    parser.add_argument("--config", default=str(default_config_path(script_dir)), help="Path to signin skill config.json")
    parser.add_argument("--phone", default=None, help="Phone number whose saved cookies should be used")
    parser.add_argument(
        "--model",
        default=None,
        help="Human model name/family/alias, e.g. 'Claude Sonnet 4.6', 'Qwen 3.6 flash', 'Ava'",
    )
    parser.add_argument("--prompt", default="", help="Prompt text. Use '-' to read from stdin")
    parser.add_argument("--prompt-file", default=None, help="Read prompt text from a UTF-8 file")
    parser.add_argument("--timeout", type=int, default=180, help="Upstream request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Print a JSON object instead of plain text")
    parser.add_argument("--list-models", action="store_true", help="List available IMYAI models")
    parser.add_argument("--list-models-compact", action="store_true", help="List model names in a compact table")
    parser.add_argument("--search-model", default="", help="Search available models by id, name, or raw model")
    parser.add_argument("--group-id", type=int, default=None, help="Reuse an existing official IMYAI group id")
    parser.add_argument("--no-official-history", action="store_true", help="Skip official group creation and chatlog lookup")
    parser.add_argument("--no-group-update", action="store_true", help="Skip updating official group metadata before chatting")
    parser.add_argument("--include-official-history", action="store_true", help="Include raw official chat history in JSON output")
    parser.add_argument("--history-retries", type=int, default=3, help="How many times to recheck official chat history")
    parser.add_argument("--history-delay", type=float, default=0.8, help="Seconds to wait between official chat history checks")
    parser.add_argument("--session", default="", help="Persist model, group id, and local history. Use 'auto' for this Codex workspace")
    parser.add_argument("--set-session-model", default="", help="Resolve and save the default model for a session, then exit")
    parser.add_argument("--session-status", action="store_true", help="Print saved session state summary and exit")
    parser.add_argument("--clear-session", action="store_true", help="Delete the saved session state and exit")
    parser.add_argument("--max-session-turns", type=int, default=12, help="How many recent user/assistant turns to inject")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        config_path = Path(args.config)
        client = ImyaiClient(config_path, phone=args.phone, timeout=args.timeout)
        session_key = normalize_session_key(args.session, client.phone)
        session_state = load_session_state(config_path, session_key)

        if args.clear_session:
            cleared = clear_session_state(config_path, session_key)
            payload = {"ok": True, "session": session_key or None, "cleared": cleared}
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"cleared={cleared}")
            return

        if args.session_status:
            payload = {"ok": True, "session": session_summary(config_path, session_key, session_state)}
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False))
            return

        if args.list_models or args.list_models_compact:
            payload = {"phone": client.phone, "count": len(client.codex_models()), "data": client.codex_models()}
            if args.list_models_compact:
                print("model\tprovider_model")
                for model in payload["data"]:
                    print(
                        f"{model.get('name') or ''}\t{model.get('raw_model') or ''}"
                    )
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if args.search_model:
            matches = find_matching_models(client, args.search_model)
            print(json.dumps({"query": args.search_model, "count": len(matches), "data": matches}, ensure_ascii=False, indent=2))
            return

        if args.set_session_model:
            if not session_key:
                raise RuntimeError("--set-session-model requires --session")
            model_type_id, chosen_model = resolve_model_argument(client, args.set_session_model)
            session_state["model"] = args.set_session_model
            session_state["modelTypeId"] = model_type_id
            session_state["resolvedModel"] = chosen_model
            save_session_state(config_path, session_key, session_state)
            payload = {
                "ok": True,
                "phone": client.phone,
                "model": args.set_session_model,
                "modelTypeId": model_type_id,
                "resolvedModel": chosen_model,
                "session": session_summary(config_path, session_key, session_state),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"session={session_key} model={chosen_model.get('name') if chosen_model else args.set_session_model}")
            return

        prompt = read_text_argument(args.prompt, args.prompt_file).strip()
        if not prompt:
            raise RuntimeError("--prompt, --prompt-file, or stdin is required")

        model_arg = args.model or str(session_state.get("model") or DEFAULT_MODEL_ID)
        model_type_id, chosen_model = resolve_model_argument(client, model_arg)
        prompt_to_send, history_injected = build_session_prompt(prompt, session_state, args.max_session_turns) if session_key else (prompt, False)
        group_id = args.group_id
        if group_id is None and session_key:
            saved_group_id = session_state.get("groupId")
            try:
                group_id = int(saved_group_id) if saved_group_id is not None else None
            except (TypeError, ValueError):
                group_id = None

        history_payload: dict[str, Any] | None = None
        if args.no_official_history:
            text = client.complete(prompt_to_send, model_type_id)
        else:
            history_payload = client.official_chat(
                prompt_to_send,
                model_type_id,
                group_id=group_id,
                update_group_metadata=not args.no_group_update,
                group_name=str(chosen_model.get("name") or model_arg) if chosen_model else str(model_arg),
                history_retries=args.history_retries,
                history_delay=args.history_delay,
            )
            text = history_payload["text"]
        if session_key:
            group_id = group_id if history_payload is None else history_payload.get("group_id")
            session_state = update_session_after_reply(
                session_state,
                prompt,
                text,
                model_arg,
                model_type_id,
                chosen_model,
                group_id,
                args.max_session_turns,
            )
            save_session_state(config_path, session_key, session_state)
        if args.json:
            official_summary = None
            if history_payload is not None:
                history_entries = history_payload.get("history_entries")
                official_summary = {
                    "groupId": history_payload.get("group_id"),
                    "groupCreated": history_payload.get("group_created"),
                    "groupUpdateOk": isinstance(history_payload.get("group_update_response"), dict)
                    and history_payload.get("group_update_response", {}).get("success") is True,
                    "historyEntryCount": len(history_entries) if isinstance(history_entries, list) else None,
                }
            payload = {
                "ok": True,
                "phone": client.phone,
                "model": model_arg,
                "modelTypeId": model_type_id,
                "resolvedModel": chosen_model,
                "historyInjected": history_injected,
                "session": session_summary(config_path, session_key, session_state),
                "text": text,
                "official": official_summary,
            }
            if args.include_official_history:
                payload.update(
                    {
                        "groupId": None if history_payload is None else history_payload.get("group_id"),
                        "groupCreated": None if history_payload is None else history_payload.get("group_created"),
                        "groupResponse": None if history_payload is None else history_payload.get("group_response"),
                        "groupUpdateResponse": None if history_payload is None else history_payload.get("group_update_response"),
                        "chatHistory": None if history_payload is None else history_payload.get("history_entries"),
                        "chatHistoryResponse": None if history_payload is None else history_payload.get("history_response"),
                    }
                )
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(text)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
