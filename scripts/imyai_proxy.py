#!/usr/bin/env python3
"""Local OpenAI-compatible adapter for super.imyaigc.com chat models."""

from __future__ import annotations

import argparse
import base64
import codecs
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from imyai_network import urlopen_auto
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CHAT_URL = "https://super.imyaigc.com/chat"
API_BASE_URL = "https://api.daka.today/api"
MODEL_LIST_URL = f"{API_BASE_URL}/models/list"
MODEL_BASE_CONFIG_URL = f"{API_BASE_URL}/models/baseConfig"
CREATE_GROUP_URL = f"{API_BASE_URL}/group/create"
UPDATE_GROUP_URL = f"{API_BASE_URL}/group/update"
CHATLOG_LIST_URL = f"{API_BASE_URL}/chatlog/chatList"
CHAT_PROCESS_URL = f"{API_BASE_URL}/chatgpt/chat-process"
DRAW_RUNTIME_MODELS_URL = f"{API_BASE_URL}/draw/runtime-models"
DRAW_MINE_LIST_URL = f"{API_BASE_URL}/draw/mineList"
GENERATE_IMAGES_URL = f"{API_BASE_URL}/generate/images"
GENERATE_STS_CREDENTIALS_URL = f"{API_BASE_URL}/generate/sts-credentials"

AES_KEY_B64 = "iIADhhgDKPZfqgULT1eDJCkpzGSVs8dtP2RVVpxKV5g="
HMAC_KEY_B64 = (
    "45fgZZoJMaNqJnlq1q+B999pHH3d92snBEzsMfi2FMyfrwoWqS9x7nYezRj3SnIx"
    "TrtmkBYIKfWJQSNJw6StgA=="
)

DEFAULT_MODEL_ID = "imyai-ava"
DEFAULT_MODEL_TYPE_ID = 3
DEFAULT_MODEL_DISPLAY_NAME = "IMYAI-Ava-default"
DEFAULT_MODEL_CONFIG = {
    "modelTypeId": DEFAULT_MODEL_TYPE_ID,
    "modelConfig": {
        "maxResponseTokens": 0,
        "topN": 0.8,
        "systemMessage": "",
        "rounds": 100,
    },
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def dump_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def now_seconds() -> int:
    return int(time.time())


def response_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def slugify_model_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "-", name).strip("-").lower()
    return cleaned or fallback


def make_cookie_header(cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def find_cross_domain_jwt(cookies: list[dict[str, Any]]) -> str:
    for cookie in cookies:
        if cookie.get("name") == "CROSS_DOMAIN_JWT" and cookie.get("value"):
            return str(cookie["value"])
    return ""


def encrypted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    aes_key = base64.b64decode(AES_KEY_B64)
    hmac_key = base64.b64decode(HMAC_KEY_B64)
    iv = os.urandom(16)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    padder = PKCS7(128).padder()
    padded = padder.update(body) + padder.finalize()
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    iv_b64 = base64.b64encode(iv).decode("ascii")
    payload_b64 = base64.b64encode(ciphertext).decode("ascii")
    timestamp = int(time.time() * 1000)
    signing_input = f"{iv_b64}.{payload_b64}.{timestamp}".encode("utf-8")
    signature = hmac.new(hmac_key, signing_input, hashlib.sha256).hexdigest()
    return {
        "iv": iv_b64,
        "payload": payload_b64,
        "signature": signature,
        "timestamp": timestamp,
    }


class ImyaiClient:
    def __init__(
        self,
        config_path: Path,
        phone: str | None = None,
        timeout: int = 120,
        auto_refresh_auth: bool = True,
    ):
        self.config_path = config_path
        self.config = load_json(config_path)
        self.paths = self.config.get("paths", {})
        self.cookie_dir = Path(self.paths.get("cookie_dir", ""))
        self.phone = phone or os.environ.get("IMYAI_PHONE") or self._find_cookie_phone()
        if not self.phone:
            raise RuntimeError("No phone selected; pass --phone or set IMYAI_PHONE")
        self.timeout = timeout
        self.auto_refresh_auth = auto_refresh_auth
        self._models_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._base_config_cache: tuple[float, dict[str, Any]] | None = None

    def _find_cookie_phone(self) -> str:
        for account in self.config.get("accounts", []):
            phone = str(account.get("phone") or "")
            if phone:
                return phone
        cookie_files = sorted(
            self.cookie_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for cookie_file in cookie_files:
            try:
                cookies = load_json(cookie_file)
                if isinstance(cookies, list) and find_cross_domain_jwt(cookies):
                    return cookie_file.stem
            except Exception:
                continue
        return ""

    @property
    def cookie_file(self) -> Path:
        return self.cookie_dir / f"{self.phone}.json"

    def read_session(self) -> tuple[str, str]:
        if not self.cookie_file.exists():
            raise RuntimeError(f"No saved cookie file for {self.phone}; run signin.py --login-only first")
        cookies = load_json(self.cookie_file)
        if not isinstance(cookies, list):
            raise RuntimeError(f"Cookie file is invalid: {self.cookie_file}")
        jwt = find_cross_domain_jwt(cookies)
        if not jwt:
            raise RuntimeError("CROSS_DOMAIN_JWT not found; run signin.py --login-only first")
        return make_cookie_header(cookies), jwt

    def headers(self, accept: str = "application/json, text/plain, */*") -> dict[str, str]:
        cookie_header, jwt = self.read_session()
        return {
            "Accept": accept,
            "Authorization": f"Bearer {jwt}",
            "Cookie": cookie_header,
            "Origin": "https://super.imyaigc.com",
            "Referer": CHAT_URL,
            "User-Agent": "Mozilla/5.0",
        }

    def should_refresh_auth(self, status_code: int | None, raw: str) -> bool:
        if not self.auto_refresh_auth:
            return False
        if status_code == 401:
            return True
        markers = ("UNAUTHORIZED", "登录已失效", "请重新登录")
        return any(marker in raw for marker in markers)

    def refresh_login(self) -> None:
        signin_script = Path(__file__).resolve().parent / "signin.py"
        cmd = [
            sys.executable,
            str(signin_script),
            "--config",
            str(self.config_path),
            "--phone",
            self.phone,
            "--login-only",
        ]
        if os.environ.get("IMYAI_LOGIN_VISIBLE"):
            cmd.append("--no-headless")
        completed = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(self.timeout + 90, 180),
        )
        if completed.returncode != 0:
            visible_cmd_parts = list(cmd)
            if "--no-headless" not in visible_cmd_parts:
                visible_cmd_parts.append("--no-headless")
            visible_cmd = " ".join(repr(part) for part in visible_cmd_parts)
            raise RuntimeError(
                "IMYAI login refresh failed: "
                + (completed.stderr or completed.stdout or "").strip()[:1000]
                + "\nTry visible login: "
                + visible_cmd
            )
        self._models_cache = None
        self._base_config_cache = None

    def request_json(
        self,
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        accept: str = "application/json, text/plain, */*",
        encrypted: bool = False,
    ) -> dict[str, Any]:
        last_error: urllib.error.HTTPError | None = None
        for attempt in range(2):
            headers = self.headers(accept=accept)
            body = None
            if data is not None:
                headers["Content-Type"] = "application/json"
                body = dump_json(encrypted_payload(data)) if encrypted else dump_json(data)
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urlopen_auto(req, timeout=self.timeout, config=self.config) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                last_error = exc
                if attempt == 0 and self.should_refresh_auth(exc.code, raw):
                    self.refresh_login()
                    continue
                raise RuntimeError(f"IMYAI request failed ({exc.code}): {raw[:500]}") from exc
        if last_error is not None:
            raise RuntimeError(f"IMYAI request failed ({last_error.code})") from last_error
        raise RuntimeError("IMYAI request failed")

    def enabled_models(self, force: bool = False) -> list[dict[str, Any]]:
        if self._models_cache and not force and time.time() - self._models_cache[0] < 300:
            return self._models_cache[1]
        payload = self.request_json(MODEL_LIST_URL)
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"Model list request failed: {payload.get('message') or payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Model list response did not contain an array")
        models = [model for model in data if isinstance(model, dict) and int(model.get("status") or 0) == 1]
        self._models_cache = (time.time(), models)
        return models

    def codex_models(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = [
            {
                "id": DEFAULT_MODEL_ID,
                "object": "model",
                "created": 0,
                "owned_by": "imyai",
                "name": DEFAULT_MODEL_DISPLAY_NAME,
                "display_name": DEFAULT_MODEL_DISPLAY_NAME,
                "modelTypeId": DEFAULT_MODEL_TYPE_ID,
            }
        ]
        seen_ids = {DEFAULT_MODEL_ID}
        seen_model_type_ids = {DEFAULT_MODEL_TYPE_ID}
        for model in self.enabled_models():
            raw_id = int(model.get("id") or 0)
            if not raw_id or raw_id in seen_model_type_ids:
                continue
            model_name = str(model.get("modelName") or model.get("model") or f"model-{raw_id}")
            slug = f"imyai-{raw_id}"
            if slug not in seen_ids:
                items.append(
                    {
                        "id": slug,
                        "object": "model",
                        "created": 0,
                        "owned_by": "imyai",
                        "name": model_name,
                        "display_name": model_name,
                        "modelTypeId": raw_id,
                        "raw_model": model.get("model"),
                    }
                )
                seen_ids.add(slug)
                seen_model_type_ids.add(raw_id)
        return items

    def resolve_model_type_id(self, model_id: str | None) -> int:
        model_id = (model_id or DEFAULT_MODEL_ID).strip()
        if model_id in {DEFAULT_MODEL_ID, "IMYAI-Ava-default", "IMYAI-Ava-默认模型", "IMYAI-Ava-默认模型⭐"}:
            return DEFAULT_MODEL_TYPE_ID
        if model_id.isdigit():
            return int(model_id)
        match = re.match(r"^imyai-(\d+)$", model_id)
        if match:
            return int(match.group(1))
        for model in self.codex_models():
            if model_id in {model.get("id"), model.get("name"), model.get("display_name")}:
                return int(model["modelTypeId"])
        raise RuntimeError(f"Unknown IMYAI model: {model_id}")

    def base_config(self, model_type_id: int) -> dict[str, Any]:
        if self._base_config_cache and time.time() - self._base_config_cache[0] < 300:
            base = json.loads(json.dumps(self._base_config_cache[1]))
        else:
            try:
                payload = self.request_json(MODEL_BASE_CONFIG_URL)
                base = payload.get("data") if payload.get("code") == 200 else None
            except Exception:
                base = None
            if not isinstance(base, dict) or not base:
                base = json.loads(json.dumps(DEFAULT_MODEL_CONFIG))
            self._base_config_cache = (time.time(), json.loads(json.dumps(base)))
        base["modelTypeId"] = model_type_id
        base.setdefault("modelConfig", {})
        return base

    def create_group(self, app_id: int = 0) -> dict[str, Any]:
        return self.request_json(
            CREATE_GROUP_URL,
            method="POST",
            data={"appId": app_id},
            encrypted=True,
        )

    def update_group(
        self,
        group_id: int,
        model_type_id: int,
        group_name: str | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "groupId": group_id,
            "appId": 0,
            "modelTypeId": model_type_id,
        }
        if group_name:
            payload["groupName"] = group_name
        if model_config:
            payload["modelConfig"] = model_config
        return self.request_json(UPDATE_GROUP_URL, method="POST", data=payload, encrypted=True)

    def query_chatlog_list(self, group_id: int) -> dict[str, Any]:
        query = urllib.parse.urlencode({"groupId": group_id})
        return self.request_json(f"{CHATLOG_LIST_URL}?{query}")

    def extract_group_id(self, payload: Any) -> int:
        candidates: list[Any] = [payload]
        if isinstance(payload, dict):
            for key in ("data", "result", "item", "group", "groupInfo"):
                value = payload.get(key)
                if value is not None:
                    candidates.append(value)
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("groupId", "id", "group_id"):
                    value = data.get(key)
                    if value is not None:
                        candidates.append(value)
            elif isinstance(data, list):
                candidates.extend(data)
        for candidate in candidates:
            if isinstance(candidate, dict):
                for key in ("groupId", "id", "group_id"):
                    value = candidate.get(key)
                    try:
                        if value is not None:
                            return int(value)
                    except Exception:
                        continue
            else:
                try:
                    return int(candidate)
                except Exception:
                    continue
        raise RuntimeError(f"Unable to determine group id from response: {payload}")

    def extract_chatlog_entries(self, payload: Any) -> list[dict[str, Any]]:
        data: Any = payload
        if isinstance(payload, dict):
            for key in ("data", "result", "rows", "list", "records"):
                value = payload.get(key)
                if value is not None:
                    data = value
                    break
        if isinstance(data, dict):
            for key in ("list", "rows", "records", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if all(isinstance(value, dict) for value in data.values()):
                return [value for value in data.values() if isinstance(value, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def official_chat(
        self,
        prompt: str,
        model_type_id: int,
        *,
        group_id: int | None = None,
        update_group_metadata: bool = True,
        group_name: str | None = None,
        history_retries: int = 3,
        history_delay: float = 0.8,
    ) -> dict[str, Any]:
        created_group = False
        group_response: dict[str, Any] | None = None
        update_response: dict[str, Any] | None = None

        if group_id is None:
            group_response = self.create_group()
            group_id = self.extract_group_id(group_response)
            created_group = True

        if update_group_metadata:
            try:
                update_response = self.update_group(
                    group_id,
                    model_type_id,
                    group_name=group_name,
                    model_config=self.base_config(model_type_id).get("modelConfig"),
                )
            except Exception as exc:
                update_response = {"error": str(exc)}

        text = self.complete(prompt, model_type_id, group_id=group_id)

        history_response: dict[str, Any] | None = None
        history_entries: list[dict[str, Any]] = []
        for attempt in range(max(1, history_retries)):
            history_response = self.query_chatlog_list(group_id)
            history_entries = self.extract_chatlog_entries(history_response)
            if history_entries:
                break
            if attempt < max(1, history_retries) - 1:
                time.sleep(history_delay)

        return {
            "text": text,
            "group_id": group_id,
            "group_created": created_group,
            "group_response": group_response,
            "group_update_response": update_response,
            "history_response": history_response,
            "history_entries": history_entries,
        }

    def chat_payload(self, prompt: str, model_type_id: int, group_id: int | None = None) -> dict[str, Any]:
        options = self.base_config(model_type_id)
        if group_id is not None:
            options["groupId"] = group_id
        return {
            "prompt": prompt,
            "appId": None,
            "options": options,
        }

    def chat_events(self, prompt: str, model_type_id: int, group_id: int | None = None) -> Iterable[dict[str, Any]]:
        last_error: urllib.error.HTTPError | None = None
        for attempt in range(2):
            encrypted = encrypted_payload(self.chat_payload(prompt, model_type_id, group_id=group_id))
            headers = self.headers(accept="text/event-stream, application/json, text/plain, */*")
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                CHAT_PROCESS_URL,
                data=dump_json(encrypted),
                headers=headers,
                method="POST",
            )
            try:
                response = urlopen_auto(req, timeout=self.timeout, config=self.config)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                last_error = exc
                if attempt == 0 and self.should_refresh_auth(exc.code, raw):
                    self.refresh_login()
                    continue
                raise RuntimeError(f"Chat request failed ({exc.code}): {raw[:800]}") from exc
            with response:
                yield from parse_streaming_json(response)
            return
        if last_error is not None:
            raise RuntimeError(f"Chat request failed ({last_error.code})") from last_error
        raise RuntimeError("Chat request failed")

    def complete(self, prompt: str, model_type_id: int, group_id: int | None = None) -> str:
        text = ""
        for event in self.chat_events(prompt, model_type_id, group_id=group_id):
            event_type = str(event.get("type") or "").lower()
            if event_type == "error" or event.get("error_code"):
                raise RuntimeError(str(event.get("message") or event.get("error_code") or "IMYAI model returned an error"))
            delta, replacement = extract_text_from_imyai_event(event, text)
            if replacement is not None:
                text = replacement
            elif delta:
                text += delta
        return text.strip()

    def draw_models(self) -> list[dict[str, Any]]:
        payload = self.request_json(DRAW_RUNTIME_MODELS_URL)
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"Draw model list request failed: {payload.get('message') or payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Draw model list response did not contain an array")
        return [model for model in data if isinstance(model, dict) and model.get("isEnabled") is not False]

    def draw_manifest(self, version_id: int | str) -> dict[str, Any]:
        payload = self.request_json(f"{DRAW_RUNTIME_MODELS_URL}/{version_id}/manifest")
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"Draw manifest request failed: {payload.get('message') or payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Draw manifest response did not contain an object")
        return data

    def invoke_draw(self, version_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.request_json(
            f"{DRAW_RUNTIME_MODELS_URL}/{version_id}/invoke",
            method="POST",
            data=payload,
            encrypted=True,
        )
        if response.get("code") not in {200, 201} or response.get("success") is not True:
            raise RuntimeError(f"Draw invoke failed: {response.get('message') or response.get('code')}")
        return response

    def draw_mine_list(self, page: int = 1, size: int = 20) -> dict[str, Any]:
        query = urllib.parse.urlencode({"page": page, "size": size})
        payload = self.request_json(f"{DRAW_MINE_LIST_URL}?{query}")
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"Draw mine list request failed: {payload.get('message') or payload.get('code')}")
        return payload

    def generated_images(self, page: int = 1, size: int = 20, show_in_task: bool = True) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "page": page,
                "size": size,
                "showInTask": 1 if show_in_task else 0,
            }
        )
        payload = self.request_json(f"{GENERATE_IMAGES_URL}?{query}")
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"Generated image list request failed: {payload.get('message') or payload.get('code')}")
        return payload

    def generate_sts_credentials(self) -> dict[str, Any]:
        payload = self.request_json(GENERATE_STS_CREDENTIALS_URL)
        if payload.get("code") != 200 or payload.get("success") is not True:
            raise RuntimeError(f"STS credential request failed: {payload.get('message') or payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("STS credential response did not contain an object")
        return data


def parse_streaming_json(response: Any) -> Iterable[dict[str, Any]]:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buffer += decoder.decode(chunk)
        buffer = buffer.replace("\r\n", "\n")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            yield from parse_stream_block(block)
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        yield from parse_stream_block(buffer)


def parse_stream_block(block: str) -> Iterable[dict[str, Any]]:
    data_lines: list[str] = []
    raw_json_lines: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("data:"):
            data_lines.append(stripped[5:].strip())
        elif stripped.startswith("event:") or stripped.startswith("id:"):
            continue
        elif stripped.startswith("{") or stripped.startswith("["):
            raw_json_lines.append(stripped)
    candidates = ["\n".join(data_lines)] if data_lines else raw_json_lines
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate == "[DONE]":
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    yield item


def nested_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else event


def extract_text_from_imyai_event(event: dict[str, Any], current_text: str) -> tuple[str, str | None]:
    data = nested_data(event)
    if "choices" in data:
        try:
            delta = data["choices"][0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                return content, None
        except Exception:
            return "", None

    event_type = str(data.get("type") or event.get("event") or "").lower()
    for key in ("deltaText", "delta_text", "delta"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value, None

    text = data.get("text")
    if isinstance(text, str) and text:
        if event_type in {"completed", "complete", "done"} or data.get("is_end") is True:
            return "", text
        if not current_text:
            return "", text
        if text.startswith(current_text):
            return text[len(current_text) :], None
    return "", None


def response_output_text(text: str) -> list[dict[str, Any]]:
    return [
        {
            "id": response_id("msg"),
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                }
            ],
        }
    ]


def make_response(model: str, text: str, resp_id: str | None = None) -> dict[str, Any]:
    return {
        "id": resp_id or response_id("resp"),
        "object": "response",
        "created_at": now_seconds(),
        "status": "completed",
        "model": model,
        "output": response_output_text(text),
        "parallel_tool_calls": False,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("input_text") or item.get("output_text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(part for part in parts if part)
    return ""


def extract_responses_prompt(body: dict[str, Any]) -> str:
    lines: list[str] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        lines.append(f"System:\n{instructions.strip()}")
    input_value = body.get("input")
    if isinstance(input_value, str):
        lines.append(input_value)
    elif isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, str):
                lines.append(item)
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role") or item.get("type") or "message"
            text = extract_content_text(item.get("content"))
            if text:
                lines.append(f"{role}:\n{text}")
    return "\n\n".join(line for line in lines if line).strip()


def extract_chat_prompt(body: dict[str, Any]) -> str:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return extract_responses_prompt(body)
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "message"
        text = extract_content_text(message.get("content"))
        if text:
            lines.append(f"{role}:\n{text}")
    return "\n\n".join(lines).strip()


class ProxyState:
    def __init__(self, client: ImyaiClient):
        self.client = client


class ImyaiProxyHandler(BaseHTTPRequestHandler):
    server_version = "IMYAIProxy/0.1"
    state: ProxyState

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in {"/health", "/v1/health"}:
                self.send_json({"ok": True, "phone": self.state.client.phone, "model": DEFAULT_MODEL_ID})
            elif path in {"/v1/models", "/models"}:
                self.send_json({"object": "list", "data": self.state.client.codex_models()})
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self.read_json_body()
            if path in {"/v1/responses", "/responses"}:
                self.handle_responses(body)
            elif path in {"/v1/chat/completions", "/chat/completions"}:
                self.handle_chat_completions(body)
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=None).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": {"message": message, "type": "imyai_proxy_error"}}, status=status)

    def handle_responses(self, body: dict[str, Any]) -> None:
        model = str(body.get("model") or DEFAULT_MODEL_ID)
        prompt = extract_responses_prompt(body)
        if not prompt:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "No input text found")
            return
        model_type_id = self.state.client.resolve_model_type_id(model)
        group_id = self.extract_requested_group_id(body)
        if body.get("stream") is True:
            self.stream_responses(model, prompt, model_type_id, group_id=group_id)
            return
        text = self.state.client.complete(prompt, model_type_id, group_id=group_id)
        self.send_json(make_response(model, text))

    def handle_chat_completions(self, body: dict[str, Any]) -> None:
        model = str(body.get("model") or DEFAULT_MODEL_ID)
        prompt = extract_chat_prompt(body)
        if not prompt:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "No message text found")
            return
        model_type_id = self.state.client.resolve_model_type_id(model)
        group_id = self.extract_requested_group_id(body)
        if body.get("stream") is True:
            self.stream_chat_completions(model, prompt, model_type_id, group_id=group_id)
            return
        text = self.state.client.complete(prompt, model_type_id, group_id=group_id)
        self.send_json(
            {
                "id": response_id("chatcmpl"),
                "object": "chat.completion",
                "created": now_seconds(),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )

    def extract_requested_group_id(self, body: dict[str, Any]) -> int | None:
        for key in ("groupId", "group_id", "officialGroupId"):
            value = body.get(key)
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    pass
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            for key in ("groupId", "group_id"):
                value = metadata.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except Exception:
                        pass
        return None

    def send_sse_headers(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def write_sse(self, event: str, data: dict[str, Any] | str) -> None:
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()

    def stream_responses(self, model: str, prompt: str, model_type_id: int, group_id: int | None = None) -> None:
        resp_id = response_id("resp")
        output_item_id = response_id("msg")
        content_index = 0
        output_index = 0
        full_text = ""
        created = now_seconds()
        self.send_sse_headers()
        self.write_sse(
            "response.created",
            {
                "type": "response.created",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "created_at": created,
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                },
            },
        )
        self.write_sse(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {
                    "id": output_item_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            },
        )
        self.write_sse(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "item_id": output_item_id,
                "output_index": output_index,
                "content_index": content_index,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )
        try:
            for event in self.state.client.chat_events(prompt, model_type_id, group_id=group_id):
                delta, replacement = extract_text_from_imyai_event(event, full_text)
                if replacement is not None:
                    delta = replacement[len(full_text) :] if replacement.startswith(full_text) else replacement
                    full_text = replacement
                elif delta:
                    full_text += delta
                if delta:
                    self.write_sse(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "item_id": output_item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "delta": delta,
                        },
                    )
        except Exception as exc:
            self.write_sse(
                "response.failed",
                {
                    "type": "response.failed",
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "created_at": created,
                        "status": "failed",
                        "model": model,
                        "error": {"message": str(exc), "type": "imyai_proxy_error"},
                    },
                },
            )
            self.write_sse("done", "[DONE]")
            return

        final_response = make_response(model, full_text, resp_id=resp_id)
        self.write_sse(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "item_id": output_item_id,
                "output_index": output_index,
                "content_index": content_index,
                "text": full_text,
            },
        )
        self.write_sse(
            "response.content_part.done",
            {
                "type": "response.content_part.done",
                "item_id": output_item_id,
                "output_index": output_index,
                "content_index": content_index,
                "part": {"type": "output_text", "text": full_text, "annotations": []},
            },
        )
        self.write_sse(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": final_response["output"][0],
            },
        )
        self.write_sse("response.completed", {"type": "response.completed", "response": final_response})
        self.write_sse("done", "[DONE]")

    def stream_chat_completions(self, model: str, prompt: str, model_type_id: int, group_id: int | None = None) -> None:
        completion_id = response_id("chatcmpl")
        full_text = ""
        self.send_sse_headers()
        try:
            for event in self.state.client.chat_events(prompt, model_type_id, group_id=group_id):
                delta, replacement = extract_text_from_imyai_event(event, full_text)
                if replacement is not None:
                    delta = replacement[len(full_text) :] if replacement.startswith(full_text) else replacement
                    full_text = replacement
                elif delta:
                    full_text += delta
                if delta:
                    self.write_sse(
                        "message",
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": now_seconds(),
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                        },
                    )
        except Exception as exc:
            self.write_sse("error", {"error": {"message": str(exc), "type": "imyai_proxy_error"}})
            self.write_sse("done", "[DONE]")
            return
        self.write_sse(
            "message",
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": now_seconds(),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        )
        self.write_sse("done", "[DONE]")


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run a local OpenAI-compatible proxy for IMYAI chat models")
    parser.add_argument("--config", default=str(script_dir / "config.json"), help="Path to signin skill config.json")
    parser.add_argument("--phone", default=None, help="Phone number whose saved cookies should be used")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8788, help="Bind port")
    parser.add_argument("--timeout", type=int, default=120, help="Upstream request timeout in seconds")
    parser.add_argument("--list-models", action="store_true", help="Print OpenAI-compatible model ids and exit")
    parser.add_argument("--test-prompt", default="", help="Send one prompt through IMYAI and print the response")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = ImyaiClient(Path(args.config), phone=args.phone, timeout=args.timeout)

    if args.list_models:
        models = client.codex_models()
        print(json.dumps({"phone": client.phone, "count": len(models), "data": models}, ensure_ascii=False, indent=2))
        return

    if args.test_prompt:
        model_type_id = client.resolve_model_type_id(DEFAULT_MODEL_ID)
        print(client.complete(args.test_prompt, model_type_id))
        return

    handler = ImyaiProxyHandler
    handler.state = ProxyState(client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"IMYAI proxy listening on http://{args.host}:{args.port}/v1")
    print(f"Using phone {client.phone}; default model {DEFAULT_MODEL_ID} -> modelTypeId {DEFAULT_MODEL_TYPE_ID}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
