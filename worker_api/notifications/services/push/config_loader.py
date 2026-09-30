import json
from pathlib import Path

from worker_api.config import get


def load_secret_value(raw_value: str) -> str:
    """Load a secret from inline content or a filesystem path."""
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith("{") or "BEGIN" in value:
        return value
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def load_json_secret(raw_value: str) -> dict:
    content = load_secret_value(raw_value)
    if not content:
        return {}
    return json.loads(content)


def load_service_account() -> dict:
    raw_value = get("GOOGLE_APPLICATION_CREDENTIALS").strip()
    if not raw_value:
        return {}

    if raw_value.startswith("{"):
        try:
            data = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        return data if data.get("type") == "service_account" else {}

    try:
        data = load_json_secret(raw_value)
    except json.JSONDecodeError:
        return {}
    if data.get("type") == "service_account":
        return data

    path = Path(raw_value)
    if not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if data.get("type") == "service_account" else {}


def is_fcm_configured() -> bool:
    account = load_service_account()
    if account.get("project_id"):
        return True
    return bool(get("GOOGLE_CLOUD_PROJECT").strip())


def _normalize_platform(platform: str) -> str:
    value = platform
    if hasattr(platform, "value"):
        value = platform.value
    value = str(value)
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value.lower()


def is_push_configured(platform: str) -> bool:
    normalized = _normalize_platform(platform)
    if normalized in ("android", "ios"):
        return is_fcm_configured()
    return False
