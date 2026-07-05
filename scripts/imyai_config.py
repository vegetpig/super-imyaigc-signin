#!/usr/bin/env python3
"""Shared config bootstrap and path resolution for local-only IMYAI state."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_LOCAL_PATHS = {
    "screenshot_dir": ".local/screenshots",
    "cookie_dir": ".local/cookies",
    "history_file": ".local/history.log",
    "log_file": ".local/signin.log",
}


def default_config_path(script_dir: Path) -> Path:
    return script_dir / "config.json"


def template_config_path(config_path: Path) -> Path:
    return config_path.with_name("config.template.json")


def skill_root(config_path: Path) -> Path:
    return config_path.resolve().parent.parent


def _expand_path(text: str) -> str:
    return os.path.expanduser(os.path.expandvars(text))


def resolve_local_path(value: Any, base_dir: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    expanded = Path(_expand_path(text))
    if expanded.is_absolute():
        return str(expanded)
    return str((base_dir / expanded).resolve())


def ensure_config_file(config_path: Path) -> Path:
    config_path = config_path.resolve()
    if config_path.exists():
        return config_path
    template_path = template_config_path(config_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Config missing and template not found: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, config_path)
    return config_path


def _normalize_paths(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    base_dir = skill_root(config_path)
    paths = config.setdefault("paths", {})
    for key, fallback in DEFAULT_LOCAL_PATHS.items():
        paths[key] = resolve_local_path(paths.get(key) or fallback, base_dir)
    return config


def load_config(config_path: str | Path, *, resolve_paths: bool = True) -> dict[str, Any]:
    path = ensure_config_file(Path(config_path))
    with path.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Config file must contain a JSON object: {path}")
    if not resolve_paths:
        return data
    return _normalize_paths(deepcopy(data), path)


def save_config(config: dict[str, Any], config_path: str | Path) -> Path:
    path = ensure_config_file(Path(config_path))
    with path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=4, ensure_ascii=False)
    return path
