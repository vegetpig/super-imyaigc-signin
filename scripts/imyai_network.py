#!/usr/bin/env python3
"""Network helpers for IMYAI scripts with direct/proxy auto fallback."""

from __future__ import annotations

import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


COMMON_PROXY_PORTS = (7890, 7897, 7899, 1080, 10809, 2080, 20171, 20172)
NETWORK_ERROR_TYPES = (urllib.error.URLError, TimeoutError, OSError)


def _debug(message: str) -> None:
    if os.environ.get("IMYAI_NETWORK_DEBUG"):
        print(f"[imyai-network] {message}", file=sys.stderr)


def normalize_proxy_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith(("socks://", "socks4://", "socks5://")):
        # urllib does not support SOCKS proxies without extra dependencies.
        return ""
    return f"http://{value}"


def config_proxy_url(config: dict[str, Any] | None) -> str:
    proxy = (config or {}).get("proxy") or {}
    if not proxy.get("enabled"):
        return ""
    return normalize_proxy_url(str(proxy.get("server") or ""))


def env_proxy_urls() -> list[str]:
    values: list[str] = []
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = normalize_proxy_url(os.environ.get(name, ""))
        if value:
            values.append(value)
    return dedupe(values)


def _existing_yaml_files() -> list[Path]:
    roots = [
        Path(os.environ.get("APPDATA", "")) / "io.github.clash-verge-rev.clash-verge-rev",
        Path(os.environ.get("LOCALAPPDATA", "")) / "io.github.clash-verge-rev.clash-verge-rev",
    ]
    names = ("config.yaml", "clash-verge.yaml", "clash-verge-check.yaml")
    files: list[Path] = []
    for root in roots:
        if not str(root) or not root.exists():
            continue
        for name in names:
            path = root / name
            if path.exists():
                files.append(path)
        profile_dir = root / "profiles"
        if profile_dir.exists():
            files.extend(sorted(profile_dir.glob("*.yaml"))[:12])
    return files


def proxy_ports_from_configs() -> list[int]:
    ports: list[int] = []
    pattern = re.compile(r"^\s*(mixed-port|port)\s*:\s*['\"]?(\d{2,5})", re.I)
    for path in _existing_yaml_files():
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = pattern.match(line)
                if match:
                    port = int(match.group(2))
                    if 0 < port < 65536:
                        ports.append(port)
        except OSError:
            continue
    return dedupe_ints(ports)


def is_local_port_open(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def auto_proxy_urls() -> list[str]:
    ports = dedupe_ints([*proxy_ports_from_configs(), *COMMON_PROXY_PORTS])
    return [f"http://127.0.0.1:{port}" for port in ports if is_local_port_open(port)]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def dedupe_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def network_routes(config: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    configured = config_proxy_url(config)
    if configured:
        routes.append((f"config proxy {configured}", configured))
    routes.append(("direct", ""))
    routes.extend((f"env proxy {url}", url) for url in env_proxy_urls())
    routes.extend((f"auto proxy {url}", url) for url in auto_proxy_urls())

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for label, proxy_url in routes:
        key = proxy_url or "__direct__"
        if key not in seen:
            seen.add(key)
            unique.append((label, proxy_url))
    return unique


def clone_request(req: urllib.request.Request) -> urllib.request.Request:
    return urllib.request.Request(
        req.full_url,
        data=req.data,
        headers=dict(req.header_items()),
        method=req.get_method(),
    )


def opener_for(proxy_url: str) -> urllib.request.OpenerDirector:
    if proxy_url:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    else:
        # Empty ProxyHandler prevents urllib from silently using bad env proxies.
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def urlopen_auto(
    req: urllib.request.Request,
    *,
    timeout: int | float,
    config: dict[str, Any] | None = None,
) -> Any:
    errors: list[str] = []
    for label, proxy_url in network_routes(config):
        try:
            _debug(f"trying {label}: {req.full_url}")
            return opener_for(proxy_url).open(clone_request(req), timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except NETWORK_ERROR_TYPES as exc:
            errors.append(f"{label}: {exc}")
            _debug(f"failed {label}: {exc}")
            continue
    raise urllib.error.URLError("; ".join(errors) or "all IMYAI network routes failed")

