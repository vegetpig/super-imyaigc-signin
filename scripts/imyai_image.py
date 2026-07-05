#!/usr/bin/env python3
"""Generate images through IMYAI drawing runtime models."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from imyai_config import default_config_path
from imyai_proxy import ImyaiClient
from imyai_network import urlopen_auto


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_DRAW_MODEL = "GPT Image 2"
AUTO_DRAW_MODEL = "auto"
PROMPT_LIMIT = 1000
REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
REFERENCE_IMAGE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
NANOID_ALPHABET = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"

TEXT_MODEL_ORDER = ["Qwen Image 2", "GPT Image 2", "Nano Banana 2", "Nano Banana Pro"]
REFERENCE_MODEL_ORDER = ["Nano Banana 2", "GPT Image 2", "Nano Banana Pro", "Qwen Image 2"]
PHOTO_MODEL_ORDER = ["GPT Image 2", "Nano Banana 2", "Seedream 5.0 Lite", "Qwen Image 2"]
ART_MODEL_ORDER = ["Midjourney V8.1", "Niji Journey V7", "Kling V3 Omni", "GPT Image 2"]
DEFAULT_MODEL_ORDER = ["GPT Image 2", "Qwen Image 2", "Nano Banana 2", "Seedream 5.0 Lite"]

TEXT_GUARD = (
    "\n\n文字渲染要求：图片中出现的所有中文、英文、代码、符号必须逐字准确、清晰可读。"
    "不要把文字画成装饰纹理；不要生成乱码、伪文字、模糊字、错误下划线或错误横线。"
    "代码必须保持精确字符，例如 __、--、()、{}、引号和大小写。"
    "如果文字较多，请减少装饰、增大字号、增加留白，优先保证可读性。"
)


def normalize_lookup(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def ascii_safe(value: Any) -> str:
    return str(value or "").encode("ascii", "backslashreplace").decode("ascii")


def read_text_argument(value: str, prompt_file: str | None) -> str:
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8-sig")
    if value == "-":
        return sys.stdin.read()
    return value


def enabled_versions(model: dict[str, Any], include_deprecated: bool = False) -> list[dict[str, Any]]:
    versions = model.get("versions")
    if not isinstance(versions, list):
        return []
    items: list[dict[str, Any]] = []
    for version in versions:
        if not isinstance(version, dict):
            continue
        if version.get("isEnabled") is False:
            continue
        if version.get("isDeprecated") is True and not include_deprecated:
            continue
        items.append(version)
    return items


def draw_model_rows(client: ImyaiClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in client.draw_models():
        for version in enabled_versions(model):
            rows.append(
                {
                    "modelId": model.get("modelId"),
                    "modelName": model.get("modelName"),
                    "categoryName": model.get("categoryName"),
                    "versionId": version.get("versionId"),
                    "versionName": version.get("versionName"),
                    "isDefault": version.get("isDefault"),
                    "shortDescription": version.get("shortDescription") or model.get("shortDescription"),
                    "agentDescription": version.get("agentDescription"),
                }
            )
    return rows


def match_score(row: dict[str, Any], query: str) -> int:
    query_lower = query.strip().lower()
    query_norm = normalize_lookup(query)
    if not query_lower:
        return 0
    variants = [
        str(row.get("versionId") or ""),
        str(row.get("versionName") or ""),
        str(row.get("modelName") or ""),
        str(row.get("categoryName") or ""),
        f"{row.get('modelName') or ''} {row.get('versionName') or ''}",
    ]
    lower_variants = [value.lower().strip() for value in variants if value.strip()]
    norm_variants = [normalize_lookup(value) for value in variants if value.strip()]
    haystack = " ".join(norm_variants)
    if query_lower in lower_variants:
        return 100
    if query_norm in norm_variants:
        return 96
    if any(value.startswith(query_lower) for value in lower_variants):
        return 90
    if any(value.startswith(query_norm) for value in norm_variants):
        return 86
    if any(query_lower in value for value in lower_variants):
        return 78
    if any(query_norm in value for value in norm_variants):
        return 74
    tokens = query_norm.split()
    if tokens and all(token in haystack for token in tokens):
        return 60 + min(len(tokens), 10)
    return 0


def find_draw_models(client: ImyaiClient, query: str) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in draw_model_rows(client):
        score = match_score(row, query)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], bool(item[1].get("isDefault"))), reverse=True)
    return [row for _, row in scored]


def model_available(client: ImyaiClient, model_name: str) -> dict[str, Any] | None:
    matches = find_draw_models(client, model_name)
    return matches[0] if matches else None


def prompt_has_any(prompt: str, keywords: list[str]) -> bool:
    lowered = prompt.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def is_text_sensitive_prompt(prompt: str) -> bool:
    text_keywords = [
        "文字",
        "中文",
        "英文",
        "代码",
        "标题",
        "说明",
        "标签",
        "标注",
        "表格",
        "流程图",
        "信息图",
        "教学",
        "课件",
        "海报",
        "class=",
        "wx.",
        "setdata",
        "bem",
        "__",
        "--",
    ]
    quote_count = prompt.count('"') + prompt.count("'") + prompt.count("`")
    return prompt_has_any(prompt, text_keywords) or quote_count >= 4


def auto_model_order(prompt: str, reference_images: list[str]) -> tuple[list[str], str]:
    if is_text_sensitive_prompt(prompt):
        return TEXT_MODEL_ORDER, "text-sensitive prompt"
    if reference_images or prompt_has_any(prompt, ["参考图", "垫图", "改图", "修复", "抠图", "换装", "保持", "上传的图片"]):
        return REFERENCE_MODEL_ORDER, "reference/edit prompt"
    if prompt_has_any(prompt, ["照片", "摄影", "真实", "写实", "产品", "商品", "人像", "室内", "建筑"]):
        return PHOTO_MODEL_ORDER, "photo/product prompt"
    if prompt_has_any(prompt, ["动漫", "插画", "奇幻", "二次元", "海报视觉", "概念艺术", "游戏", "角色"]):
        return ART_MODEL_ORDER, "art/illustration prompt"
    return DEFAULT_MODEL_ORDER, "general prompt"


def auto_select_draw_version(client: ImyaiClient, prompt: str, reference_images: list[str]) -> tuple[dict[str, Any], str]:
    order, reason = auto_model_order(prompt, reference_images)
    for model_name in order:
        row = model_available(client, model_name)
        if row:
            return row, reason
    return resolve_draw_version(client, DEFAULT_DRAW_MODEL), "fallback default"


def resolve_draw_version(client: ImyaiClient, model_arg: str | None) -> dict[str, Any]:
    query = (model_arg or DEFAULT_DRAW_MODEL).strip()
    if query.isdigit():
        version_id = int(query)
        for row in draw_model_rows(client):
            if int(row.get("versionId") or 0) == version_id:
                return row
    matches = find_draw_models(client, query)
    if matches:
        return matches[0]
    raise RuntimeError(f"Unknown IMYAI draw model '{query}'. Run --search-model {query!r} or --list-models.")


def max_parallel_count_for_model(row: dict[str, Any]) -> int:
    model_text = f"{row.get('modelName') or ''} {row.get('versionName') or ''}".lower()
    if "nano banana" in model_text or "gpt image" in model_text:
        return 2
    return 2


def guarded_prompt(prompt: str, *, enabled: bool) -> str:
    if not enabled or not is_text_sensitive_prompt(prompt):
        return prompt
    if "文字渲染要求" in prompt:
        return prompt
    max_len = PROMPT_LIMIT - len(TEXT_GUARD)
    if max_len <= 0:
        return prompt
    base = prompt[:max_len].rstrip() if len(prompt) > max_len else prompt
    return base + TEXT_GUARD


def ensure_prompt_limit(prompt: str) -> str:
    if len(prompt) <= PROMPT_LIMIT:
        return prompt
    return prompt[:PROMPT_LIMIT].rstrip()


def is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def is_file_uri(value: str) -> bool:
    return value.lower().startswith("file://")


def local_reference_path(value: str) -> Path | None:
    text = value.strip().strip('"')
    if not text or is_http_url(text):
        return None
    if is_file_uri(text):
        parsed = urllib.parse.urlparse(text)
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(text)


def nanoid(size: int = 8) -> str:
    random_bytes = os.urandom(size)
    return "".join(NANOID_ALPHABET[item & 63] for item in random_bytes)


def cos_quote(value: str, safe: str = "") -> str:
    return urllib.parse.quote(value, safe=safe + "-_.~")


def cos_upload_key(base_path: str, filename: str, prefix: str = "") -> str:
    now = time.localtime()
    date_part = f"{now.tm_year:04d}{now.tm_mon:02d}{now.tm_mday:02d}"
    suffix = Path(filename).suffix.lstrip(".") or "png"
    generated = f"{int(time.time() * 1000)}_{nanoid(8)}.{suffix}"
    base = (base_path or "").rstrip("/")
    parts = [part for part in (base, date_part, prefix.strip("/"), generated) if part]
    return "/".join(parts)


def guess_image_mime(path: Path) -> str:
    guessed = mimetypes.guess_type(str(path))[0] or ""
    if guessed == "image/jpg":
        return "image/jpeg"
    if guessed in REFERENCE_IMAGE_MIME_TYPES:
        return guessed
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    raise RuntimeError(f"Unsupported reference image type: {path.name}; use png, jpg, jpeg, or webp")


def cos_authorization(
    *,
    method: str,
    key: str,
    host: str,
    secret_id: str,
    secret_key: str,
    security_token: str,
    start_time: int,
    end_time: int,
) -> str:
    sign_time = f"{start_time};{end_time}"
    key_time = sign_time
    path = "/" + cos_quote(key, safe="/")
    signed_headers = {
        "host": host,
        "x-cos-security-token": security_token,
    }
    header_list = ";".join(sorted(signed_headers))
    http_headers = "&".join(f"{name}={cos_quote(str(signed_headers[name]))}" for name in sorted(signed_headers))
    http_string = f"{method.lower()}\n{path}\n\n{http_headers}\n"
    string_to_sign = "sha1\n%s\n%s\n" % (sign_time, hashlib.sha1(http_string.encode("utf-8")).hexdigest())
    sign_key = hmac.new(secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1).hexdigest()
    signature = hmac.new(sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).hexdigest()
    return (
        "q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={sign_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_list}"
        "&q-url-param-list="
        f"&q-signature={signature}"
    )


def upload_reference_image(client: ImyaiClient, path: Path, timeout: int) -> str:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise RuntimeError(f"Reference image does not exist: {path}")
    size = resolved.stat().st_size
    if size > REFERENCE_IMAGE_MAX_BYTES:
        raise RuntimeError(f"Reference image is too large: {resolved} ({size} bytes, max {REFERENCE_IMAGE_MAX_BYTES})")
    mime_type = guess_image_mime(resolved)
    sts = client.generate_sts_credentials()
    credentials = sts.get("credentials")
    if not isinstance(credentials, dict):
        raise RuntimeError("STS credential payload is missing credentials")
    bucket = str(sts.get("bucket") or "")
    region = str(sts.get("region") or "")
    domain = str(sts.get("domain") or "").rstrip("/")
    base_path = str(sts.get("path") or "")
    secret_id = str(credentials.get("tmpSecretId") or "")
    secret_key = str(credentials.get("tmpSecretKey") or "")
    security_token = str(credentials.get("sessionToken") or "")
    expired_time = int(sts.get("expiredTime") or (time.time() + 1800))
    if not all([bucket, region, domain, secret_id, secret_key, security_token]):
        raise RuntimeError("STS credential payload is incomplete")
    key = cos_upload_key(base_path, resolved.name)
    host = f"{bucket}.cos.{region}.myqcloud.com"
    start_time = int(time.time()) - 60
    end_time = min(expired_time, int(time.time()) + 1800)
    auth = cos_authorization(
        method="PUT",
        key=key,
        host=host,
        secret_id=secret_id,
        secret_key=secret_key,
        security_token=security_token,
        start_time=start_time,
        end_time=end_time,
    )
    url = f"https://{host}/{cos_quote(key, safe='/')}"
    data = resolved.read_bytes()
    headers = {
        "Authorization": auth,
        "Content-Type": mime_type,
        "Content-Length": str(len(data)),
        "Host": host,
        "Origin": "https://super.imyaigc.com",
        "Referer": "https://super.imyaigc.com/chat",
        "User-Agent": "Mozilla/5.0",
        "x-cos-security-token": security_token,
    }
    request = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    with urlopen_auto(request, timeout=timeout, config=client.config) as response:
        response.read()
    return f"{domain}/{key}"


def resolve_reference_images(client: ImyaiClient, values: list[str], timeout: int) -> tuple[list[str], list[dict[str, str]]]:
    resolved: list[str] = []
    uploaded: list[dict[str, str]] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        local_path = local_reference_path(value)
        if local_path is None:
            resolved.append(value)
            continue
        url = upload_reference_image(client, local_path, timeout)
        resolved.append(url)
        uploaded.append({"path": str(local_path), "url": url})
    return resolved, uploaded


def manifest_inputs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = manifest.get("inputs")
    return [item for item in inputs if isinstance(item, dict)] if isinstance(inputs, list) else []


def default_image_size(field: dict[str, Any], resolution: str, ratio: str) -> dict[str, Any]:
    presets = field.get("imageSizePresets")
    if not isinstance(presets, list) or not presets:
        return {"resolution": resolution, "ratio": ratio, "label": ratio, "value": ""}
    preset = next((item for item in presets if str(item.get("resolution") or "").lower() == resolution.lower()), None)
    preset = preset or presets[0]
    options = preset.get("options") if isinstance(preset.get("options"), list) else []
    option = next((item for item in options if str(item.get("ratio") or "") == ratio), None)
    option = option or (options[0] if options else {})
    width = int(option.get("width") or 0)
    height = int(option.get("height") or 0)
    value = f"{width}x{height}" if width and height else str(option.get("ratio") or ratio)
    return {
        "resolution": preset.get("resolution") or resolution,
        "ratio": option.get("ratio") or ratio,
        "label": option.get("label") or option.get("ratio") or ratio,
        "description": option.get("description") or "",
        "width": width,
        "height": height,
        "value": value,
    }


def default_input_value(field: dict[str, Any], *, resolution: str, ratio: str) -> Any:
    key = field.get("key")
    field_type = field.get("type")
    default_value = field.get("defaultValue")
    if key == "prompt":
        return None
    if field_type == "boolean":
        return bool(default_value) if default_value is not None and default_value != "" else False
    if field_type in {"image_list", "video_list", "audio_list"}:
        return []
    if field_type == "image_size_preset":
        return default_image_size(field, resolution, ratio)
    options = field.get("options")
    if isinstance(options, list) and options:
        value = str(default_value) if default_value is not None and default_value != "" else str(options[0])
        return value if value in [str(item) for item in options] else str(options[0])
    if field_type in {"number", "integer"}:
        if default_value is None or default_value == "":
            return None
        number = float(default_value)
        return int(number) if field_type == "integer" else number
    return "" if default_value is None else default_value


def build_draw_payload(
    manifest: dict[str, Any],
    prompt: str,
    *,
    resolution: str,
    ratio: str,
    reference_images: list[str],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"prompt": prompt, "type": "draw"}
    for field in manifest_inputs(manifest):
        key = str(field.get("key") or "")
        if not key or key == "prompt":
            continue
        value = default_input_value(field, resolution=resolution, ratio=ratio)
        if value is None:
            continue
        payload[key] = value
    if reference_images:
        image_key = next((str(field.get("key")) for field in manifest_inputs(manifest) if field.get("type") == "image_list"), "images")
        payload[image_key] = reference_images
    if overrides:
        payload.update(overrides)
    return payload


def response_task_ids(response: dict[str, Any]) -> list[str]:
    data = response.get("data")
    candidates: list[Any] = [data, response]
    if isinstance(data, list):
        candidates.extend(data)
    ids: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("taskId", "task_id", "requestId", "recordId", "id", "imageId", "request"):
                value = candidate.get(key)
                if isinstance(value, (str, int, float)) and value != "":
                    ids.append(str(value))
            for key in ("taskIds", "requestIds", "recordIds", "ids", "imageIds"):
                value = candidate.get(key)
                if isinstance(value, list):
                    ids.extend(str(item) for item in value if item is not None and item != "")
        elif candidate is not None and candidate != "":
            ids.append(str(candidate))
    return list(dict.fromkeys(ids))


def extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("list", "rows", "records", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def nested_values(value: Any) -> list[Any]:
    values: list[Any] = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(nested_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(nested_values(item))
    return values


def row_text_values(row: dict[str, Any]) -> list[str]:
    return [str(value) for value in nested_values(row) if value is not None and value != ""]


def collect_image_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        urls.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(collect_image_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(collect_image_urls(item))
    return list(dict.fromkeys(urls))


def image_urls(row: dict[str, Any]) -> list[str]:
    status = str(row.get("status") or row.get("state") or row.get("taskStatus") or row.get("generateStatus") or "").upper()
    if status in {"SUBMITTED", "PENDING", "PROCESSING", "RUNNING", "ACCEPTED"}:
        return []
    priority_groups = [
        ("finalImageUrl", "imageUrl", "url", "fileUrl", "src"),
        ("originalImageUrl", "originUrl"),
        ("thumbnailImageUrl", "thumbUrl", "thumbnailUrl", "previewUrl"),
        ("imageUrls", "assets"),
    ]
    for keys in priority_groups:
        urls: list[str] = []
        for key in keys:
            if key in row:
                urls.extend(collect_image_urls(row.get(key)))
        if urls:
            return list(dict.fromkeys(urls))

    return []


def parse_time_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 100000000000:
            return number / 1000.0
        if number > 1000000000:
            return number
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_time_value(int(text))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(text[:19], fmt))
        except Exception:
            continue
    return None


def row_created_time(row: dict[str, Any]) -> float | None:
    for key in ("createdAt", "createTime", "createdTime", "updatedAt", "updateTime", "timestamp", "created_at"):
        parsed = parse_time_value(row.get(key))
        if parsed is not None:
            return parsed
    return None


def row_matches_task(
    row: dict[str, Any],
    task_ids: list[str],
    submitted_after: float,
    prompt: str = "",
) -> bool:
    values = [
        row.get("taskId"),
        row.get("request"),
        row.get("result"),
        row.get("requestId"),
        row.get("recordId"),
        row.get("imageId"),
        row.get("id"),
    ]
    scalar_values = [value for value in values if isinstance(value, (str, int, float)) and value != ""]
    if task_ids and any(str(value) in task_ids for value in scalar_values):
        return True
    if task_ids:
        all_values = row_text_values(row)
        if any(task_id and task_id in all_values for task_id in task_ids):
            return True
        joined = "\n".join(all_values)
        if any(task_id and task_id in joined for task_id in task_ids):
            return True
    prompt = prompt.strip()
    if prompt:
        for key in ("prompt", "inputPrompt", "textPrompt", "description", "request"):
            value = row.get(key)
            if isinstance(value, str) and prompt[:120] and prompt[:120] in value:
                return True
    created_time = row_created_time(row)
    if created_time is not None and not task_ids:
        return created_time >= submitted_after - 30
    return False


def row_failed(row: dict[str, Any]) -> bool:
    status_values = [row.get("status"), row.get("state"), row.get("taskStatus"), row.get("generateStatus")]
    return any(str(value or "").upper() in {"FAILURE", "FAILED", "ERROR", "FAIL"} for value in status_values)


def poll_images(
    client: ImyaiClient,
    task_ids: list[str],
    *,
    timeout: int,
    interval: float,
    submitted_after: float,
    prompt: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    deadline = time.time() + max(0, timeout)
    last_payload: dict[str, Any] | None = None
    while True:
        try:
            last_payload = client.draw_mine_list(page=1, size=20)
        except Exception:
            last_payload = client.generated_images(page=1, size=20, show_in_task=True)
        rows = [
            row
            for row in extract_rows(last_payload)
            if row_matches_task(row, task_ids, submitted_after, prompt=prompt)
        ]
        finished = [row for row in rows if image_urls(row)]
        if finished:
            return finished, last_payload
        failed = [row for row in rows if row_failed(row)]
        if failed:
            return failed, last_payload
        if time.time() >= deadline:
            return rows, last_payload
        time.sleep(max(1.0, interval))


def safe_filename(value: str, default: str = "imyai-image") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return cleaned[:80] or default


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to choose a unique filename for {path}")


def output_dir(default_dir: str | None) -> Path:
    if default_dir:
        return Path(default_dir)
    workspace_output = Path(r"C:\Users\18511\Documents\Codex\2026-06-26\c-users-18511-codex-skills-super-2\outputs")
    return workspace_output / "imyai-images"


def download_url(
    url: str,
    out_dir: Path,
    filename_prefix: str,
    index: int,
    timeout: int = 120,
    network_config: dict[str, Any] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    suffix = Path(urllib.parse.unquote(parsed.path)).suffix
    if suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    path = unique_path(out_dir / f"{safe_filename(filename_prefix)}-{index}{suffix}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen_auto(req, timeout=timeout, config=network_config) as response:
        data = response.read()
    path.write_bytes(data)
    return path


def parse_overrides(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("--overrides-json must be a JSON object")
    return parsed


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Generate images with IMYAI draw models")
    parser.add_argument("--config", default=str(default_config_path(script_dir)), help="Path to signin skill config.json")
    parser.add_argument("--phone", default=None, help="Phone number whose saved cookies should be used")
    parser.add_argument("--model", default=AUTO_DRAW_MODEL, help="Draw model/version name, e.g. 'GPT Image 2', or 'auto'")
    parser.add_argument("--prompt", default="", help="Image prompt text. Use '-' to read from stdin")
    parser.add_argument("--prompt-file", default=None, help="Read prompt from a UTF-8 file")
    parser.add_argument("--count", type=int, default=1, help="Number of candidates to generate; capped at 2 for parallel generation")
    parser.add_argument("--no-text-guard", action="store_true", help="Do not append text-legibility guardrails for text-heavy prompts")
    parser.add_argument("--resolution", default="1K", help="Resolution preset, e.g. 1K, 2K, 4K")
    parser.add_argument("--ratio", default="1:1", help="Aspect ratio preset, e.g. 1:1, 16:9, 9:16")
    parser.add_argument("--reference-image", action="append", default=[], help="Reference image URL or local file path; may be repeated")
    parser.add_argument("--overrides-json", default="", help="JSON object merged into the runtime invoke payload")
    parser.add_argument("--timeout", type=int, default=360, help="Overall polling timeout in seconds")
    parser.add_argument("--interval", type=float, default=15.0, help="Polling interval in seconds")
    parser.add_argument("--request-timeout", type=int, default=120, help="HTTP request timeout in seconds")
    parser.add_argument("--output-dir", default="", help="Directory for downloaded images")
    parser.add_argument("--no-download", action="store_true", help="Do not download result images")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    parser.add_argument("--list-models", action="store_true", help="List IMYAI draw models")
    parser.add_argument("--list-models-compact", action="store_true", help="List draw model names in a table")
    parser.add_argument("--search-model", default="", help="Search draw models by name/version/category")
    parser.add_argument("--manifest", action="store_true", help="Print selected draw model manifest and exit")
    parser.add_argument("--submit-only", action="store_true", help="Submit the task but skip polling")
    parser.add_argument("--poll-task-id", action="append", default=[], help="Poll an existing IMYAI draw task/request/record id without submitting")
    parser.add_argument("--include-poll-response", action="store_true", help="Include the full raw poll response in JSON output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        client = ImyaiClient(Path(args.config), phone=args.phone, timeout=args.request_timeout)

        if args.list_models or args.list_models_compact:
            rows = draw_model_rows(client)
            if args.list_models_compact:
                print("model\tversion\tversionId\tcategory")
                for row in rows:
                    print(
                        f"{row.get('modelName') or ''}\t{row.get('versionName') or ''}\t"
                        f"{ascii_safe(row.get('versionId'))}\t{row.get('categoryName') or ''}"
                    )
            else:
                print(json.dumps({"phone": client.phone, "count": len(rows), "data": rows}, ensure_ascii=False, indent=2))
            return

        if args.search_model:
            matches = find_draw_models(client, args.search_model)
            print(json.dumps({"query": args.search_model, "count": len(matches), "data": matches}, ensure_ascii=False, indent=2))
            return

        prompt = read_text_argument(args.prompt, args.prompt_file).strip()
        auto_reason = ""
        if str(args.model or "").strip().lower() == AUTO_DRAW_MODEL:
            selected, auto_reason = auto_select_draw_version(client, prompt, args.reference_image)
        else:
            selected = resolve_draw_version(client, args.model)
        version_id = int(selected["versionId"])
        manifest = client.draw_manifest(version_id)

        if args.manifest:
            print(json.dumps({"model": selected, "manifest": manifest}, ensure_ascii=False, indent=2))
            return

        if not prompt and not args.poll_task_id:
            raise RuntimeError("--prompt, --prompt-file, or stdin is required")
        prompt = ensure_prompt_limit(guarded_prompt(prompt, enabled=not args.no_text_guard))

        submitted_at = time.time()
        payload: dict[str, Any] = {}
        response: dict[str, Any] | None = None
        responses: list[dict[str, Any]] = []
        reference_images = list(args.reference_image)
        uploaded_reference_images: list[dict[str, str]] = []
        if args.poll_task_id:
            task_ids = list(dict.fromkeys(str(item) for item in args.poll_task_id if str(item or "").strip()))
        else:
            reference_images, uploaded_reference_images = resolve_reference_images(
                client,
                args.reference_image,
                args.request_timeout,
            )
            overrides = parse_overrides(args.overrides_json)
            payload = build_draw_payload(
                manifest,
                prompt,
                resolution=args.resolution,
                ratio=args.ratio,
                reference_images=reference_images,
                overrides=overrides,
            )
            requested_count = max(1, args.count)
            submit_count = min(requested_count, max_parallel_count_for_model(selected), 2)

            def invoke_once(_: int) -> dict[str, Any]:
                worker_client = ImyaiClient(Path(args.config), phone=args.phone, timeout=args.request_timeout)
                return worker_client.invoke_draw(version_id, payload)

            if submit_count == 1:
                response = client.invoke_draw(version_id, payload)
                responses = [response]
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=submit_count) as executor:
                    responses = list(executor.map(invoke_once, range(submit_count)))
                response = responses[0] if responses else None
            task_ids = []
            for item in responses:
                task_ids.extend(response_task_ids(item))
            task_ids = list(dict.fromkeys(task_ids))

        rows: list[dict[str, Any]] = []
        poll_payload: dict[str, Any] | None = None
        if not args.submit_only:
            rows, poll_payload = poll_images(
                client,
                task_ids,
                timeout=args.timeout,
                interval=args.interval,
                submitted_after=submitted_at,
                prompt=prompt,
            )

        urls: list[str] = []
        for row in rows:
            urls.extend(image_urls(row))
        urls = list(dict.fromkeys(urls))

        files: list[str] = []
        if urls and not args.no_download:
            out_dir = output_dir(args.output_dir or None)
            id_part = safe_filename("-".join(str(item) for item in task_ids[:2]), default=str(int(submitted_at)))
            prefix = f"{selected.get('versionName') or 'imyai-image'}-{id_part}"
            for index, url in enumerate(urls, start=1):
                try:
                    files.append(
                        str(
                            download_url(
                                url,
                                out_dir,
                                prefix,
                                index,
                                timeout=args.request_timeout,
                                network_config=client.config,
                            )
                        )
                    )
                except (OSError, urllib.error.URLError) as exc:
                    files.append(f"DOWNLOAD_ERROR: {url}: {exc}")

        result = {
            "ok": True,
            "phone": client.phone,
            "model": selected,
            "autoModelReason": auto_reason,
            "versionId": version_id,
            "prompt": prompt,
            "referenceImages": reference_images,
            "uploadedReferenceImages": uploaded_reference_images,
            "payload": payload,
            "submitResponse": response,
            "submitResponses": responses,
            "taskIds": task_ids,
            "rows": rows,
            "imageUrls": urls,
            "files": files,
        }
        if args.include_poll_response:
            result["pollResponse"] = poll_payload
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"ok": True, "taskIds": task_ids, "imageUrls": urls, "files": files}, ensure_ascii=False, indent=2))
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
