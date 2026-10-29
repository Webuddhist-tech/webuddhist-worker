from typing import Any, Dict
from uuid import UUID

import httpx
from fastapi import HTTPException

from worker_api.config import get


def _backend_url() -> str:
    backend_url = get("BACKEND_API_URL").rstrip("/")
    if not backend_url:
        raise RuntimeError("BACKEND_API_URL is not configured")
    return backend_url


def _dispatch_headers() -> Dict[str, str]:
    dispatch_token = get("NOTIFICATION_DISPATCH_SECRET_TOKEN")
    if not dispatch_token:
        raise RuntimeError("NOTIFICATION_DISPATCH_SECRET_TOKEN is not configured")
    return {"X-Dispatch-Token": dispatch_token}


def _raise_for_backend_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail: Any
    try:
        detail = response.json()
    except ValueError:
        detail = response.text or f"Backend request failed with status {response.status_code}"
    raise HTTPException(status_code=response.status_code, detail=detail)


async def get_prayer_translation_payload(message_id: UUID) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/prayer-translations/{message_id}",
            headers=_dispatch_headers(),
        )
        if response.status_code == 404:
            return {}
        _raise_for_backend_response(response)
        return response.json()


async def post_prayer_translation_result(
    *,
    message_id: UUID,
    body_at_dispatch: str,
    source_language: str | None = None,
    translations: Dict[str, str] | None = None,
    failed: bool = False,
) -> None:
    payload: Dict[str, Any] = {
        "body_at_dispatch": body_at_dispatch,
        "failed": failed,
    }
    if source_language is not None:
        payload["source_language"] = source_language
    if translations is not None:
        payload["translations"] = translations

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{_backend_url()}/internal/prayer-translations/{message_id}/result",
            headers=_dispatch_headers(),
            json=payload,
        )
        _raise_for_backend_response(response)
