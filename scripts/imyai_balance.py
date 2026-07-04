#!/usr/bin/env python3
"""Query IMYAI account point balances and recent point logs."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from imyai_proxy import API_BASE_URL, ImyaiClient


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


BALANCE_QUERY_URL = f"{API_BASE_URL}/balance/query"
RECHARGE_LOG_URL = f"{API_BASE_URL}/balance/rechargeLog"

CREDIT_DELTAS = (
    ("model3", "model3Count"),
    ("model4", "model4Count"),
    ("model5", "model5Count"),
    ("drawMj", "drawMjCount"),
    ("agent", "agentCount"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query IMYAI point balances")
    parser.add_argument("--phone", default=None, help="Phone number whose saved cookies should be used")
    parser.add_argument("--log", action="store_true", help="Fetch recent recharge/usage log rows")
    parser.add_argument("--size", type=int, default=10, help="Number of log rows to fetch")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def num(value: Any, width: int = 0) -> str:
    text = f"{as_int(value):,}"
    return text.rjust(width) if width else text


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def pad_right(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def parse_extent(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return {"raw": value}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def query_balance(client: ImyaiClient) -> dict[str, Any]:
    payload = client.request_json(BALANCE_QUERY_URL)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"Balance response did not contain an object: {payload}")
    return data


def query_log(client: ImyaiClient, size: int) -> dict[str, Any]:
    query = urllib.parse.urlencode({"page": 1, "size": max(1, size)})
    payload = client.request_json(f"{RECHARGE_LOG_URL}?{query}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"Recharge log response did not contain an object: {payload}")
    rows = data.get("rows")
    if not isinstance(rows, list):
        data["rows"] = []
        return data
    parsed_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["extentParsed"] = parse_extent(item.get("extent"))
        parsed_rows.append(item)
    data["rows"] = parsed_rows
    return data


def package_summary(balance: dict[str, Any]) -> str:
    package_id = as_int(balance.get("packageId"))
    expiration = balance.get("expirationTime")
    if package_id == 0:
        return "无"
    text = f"packageId={package_id}"
    if expiration:
        text += f", 到期 {expiration}"
    return text


def member_packages_summary(balance: dict[str, Any]) -> str:
    packages = balance.get("memberPackages")
    if not isinstance(packages, list) or not packages:
        return "[]"
    names = []
    for package in packages[:3]:
        if not isinstance(package, dict):
            continue
        name = (
            package.get("packageName")
            or package.get("name")
            or package.get("title")
            or package.get("id")
        )
        if name is not None:
            names.append(str(name))
    suffix = "" if len(packages) <= 3 else f" 等 {len(packages)} 项"
    return ", ".join(names) + suffix if names else f"{len(packages)} 项"


def balance_line(
    label: str,
    balance: dict[str, Any],
    value_key: str,
    member_key: str,
    sum_key: str,
    *,
    use_count_key: str | None = None,
    use_token_key: str | None = None,
    frozen_key: str | None = None,
) -> str:
    value = num(balance.get(value_key), 8)
    member = num(balance.get(member_key))
    total = num(balance.get(sum_key))
    parts = [f"  {pad_right(label, 19)} : {value}"]
    if use_count_key and use_token_key:
        parts.append(
            f"已用 {num(balance.get(use_count_key), 6)} 次 / "
            f"{num(balance.get(use_token_key), 12)} tokens"
        )
    elif use_count_key:
        parts.append(f"已用 {num(balance.get(use_count_key), 6)} 次")
    elif use_token_key:
        parts.append(f"已用 {num(balance.get(use_token_key), 6)} tokens")
    parts.append(f"会员赠 {member}")
    if frozen_key:
        parts.append(f"冻结 {num(balance.get(frozen_key))}")
    parts.append(f"累计 {total}")
    return "  ".join(parts)


def format_balance_report(phone: str, balance: dict[str, Any]) -> str:
    lines = [f"—— {phone} 积分快照 ——"]
    lines.append(
        balance_line(
            "普通积分 (model3)",
            balance,
            "model3Count",
            "memberModel3Count",
            "sumModel3Count",
            use_count_key="useModel3Count",
            use_token_key="useModel3Token",
        )
    )
    lines.append(
        balance_line(
            "高级积分 (model4)",
            balance,
            "model4Count",
            "memberModel4Count",
            "sumModel4Count",
            use_count_key="useModel4Count",
            use_token_key="useModel4Token",
            frozen_key="frozenModel4Count",
        )
    )
    lines.append(
        balance_line(
            "超级积分 (model5)",
            balance,
            "model5Count",
            "memberModel5Count",
            "sumModel5Count",
            use_count_key="useModel5Count",
            use_token_key="useModel5Token",
            frozen_key="frozenModel5Count",
        )
    )
    lines.append(
        balance_line(
            "绘图积分 (drawMj)",
            balance,
            "drawMjCount",
            "memberDrawMjCount",
            "sumDrawMjCount",
            use_token_key="useDrawMjToken",
            frozen_key="frozenDrawCount",
        )
    )
    lines.append(
        balance_line(
            "Agent  积分",
            balance,
            "agentCount",
            "memberAgentCount",
            "sumAgentCount",
            use_count_key="useAgentCount",
            frozen_key="frozenAgentCount",
        )
    )
    lines.append(f"  {pad_right('会员套餐', 19)} : {package_summary(balance)}")
    lines.append(f"  {pad_right('会员详情', 19)} : {member_packages_summary(balance)}")
    return "\n".join(lines)


def format_delta(row: dict[str, Any]) -> str:
    parts = []
    for label, key in CREDIT_DELTAS:
        value = as_int(row.get(key))
        if value > 0:
            parts.append(f"{label}+{value:,}")
        elif value < 0:
            parts.append(f"{label}{value:,}")
    return " ".join(parts) or f"type={row.get('rechargeType')}"


def extent_detail(row: dict[str, Any]) -> str:
    parsed = row.get("extentParsed")
    if isinstance(parsed, dict):
        model_name = parsed.get("modelName")
        version_name = parsed.get("versionName")
        names = [str(value) for value in (model_name, version_name) if value]
        if names:
            detail = " / ".join(names)
            if parsed.get("cost") is not None:
                detail += f"  cost={parsed.get('cost')}"
            return detail
        if parsed.get("raw"):
            return str(parsed["raw"])
    if any(as_int(row.get(key)) > 0 for _, key in CREDIT_DELTAS):
        return "(签到或到账)"
    if any(as_int(row.get(key)) < 0 for _, key in CREDIT_DELTAS):
        return "(消费)"
    return f"(type={row.get('rechargeType')})"


def format_log_report(log_data: dict[str, Any], requested_size: int) -> str:
    rows = log_data.get("rows")
    if not isinstance(rows, list):
        rows = []
    total = (
        log_data.get("total")
        if log_data.get("total") is not None
        else log_data.get("count", log_data.get("totalCount"))
    )
    total_text = str(total) if total is not None else str(len(rows))
    lines = [f"—— 最近 {min(requested_size, len(rows))} 条流水 (共 {total_text} 条) ——"]
    if not rows:
        lines.append("  (无流水)")
        return "\n".join(lines)
    for row in rows:
        created_at = str(row.get("createdAt") or "")
        delta = pad_right(format_delta(row), 34)
        lines.append(f"  {created_at}  {delta}  {extent_detail(row)}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    try:
        client = ImyaiClient(script_dir / "config.json", phone=args.phone)
        balance = query_balance(client)
        output: dict[str, Any] = {
            "phone": client.phone,
            "balance": balance,
        }
        log_data: dict[str, Any] | None = None
        if args.log:
            log_data = query_log(client, args.size)
            output["log"] = log_data.get("rows", [])

        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return

        print(format_balance_report(client.phone, balance))
        if args.log and log_data is not None:
            print()
            print(format_log_report(log_data, args.size))
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    main()
