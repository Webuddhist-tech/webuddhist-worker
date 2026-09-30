import logging
import time

import httpx
import jwt

from worker_api.config import get, get_bool
from worker_api.notifications.services.push.config_loader import load_secret_value

logger = logging.getLogger(__name__)

_cached_jwt: str | None = None
_jwt_expires_at: float = 0.0


def _get_apns_jwt() -> str:
    global _cached_jwt, _jwt_expires_at

    if _cached_jwt and time.time() < _jwt_expires_at - 60:
        return _cached_jwt

    auth_key = load_secret_value(get("APNS_AUTH_KEY"))
    headers = {
        "alg": "ES256",
        "kid": get("APNS_KEY_ID"),
    }
    payload = {
        "iss": get("APNS_TEAM_ID"),
        "iat": int(time.time()),
    }
    _cached_jwt = jwt.encode(payload, auth_key, algorithm="ES256", headers=headers)
    _jwt_expires_at = time.time() + 3000
    return _cached_jwt


def _apns_host() -> str:
    if get_bool("APNS_USE_SANDBOX"):
        return "https://api.sandbox.push.apple.com"
    return "https://api.push.apple.com"


async def send_apns_notification(
    *,
    device_token: str,
    title: str,
    body: str,
) -> None:
    url = f"{_apns_host()}/3/device/{device_token}"
    payload = {
        "aps": {
            "alert": {
                "title": title,
                "body": body,
            },
            "sound": "default",
        }
    }

    async with httpx.AsyncClient(http2=True, timeout=30.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "authorization": f"bearer {_get_apns_jwt()}",
                "apns-topic": get("APNS_BUNDLE_ID"),
                "apns-push-type": "alert",
                "apns-priority": "10",
            },
        )

    if response.status_code >= 400:
        logger.error("APNs send failed: %s %s", response.status_code, response.text)
        response.raise_for_status()
