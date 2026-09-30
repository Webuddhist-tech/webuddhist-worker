from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException

from worker_api.audio.enums import AudioJobStatus
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


async def get_audio_job_status(job_id: UUID) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/audio/jobs/{job_id}",
            headers=_dispatch_headers(),
        )
        if response.status_code == 404:
            return None
        _raise_for_backend_response(response)
        return response.json()


async def update_audio_job_status(
    *,
    job_id: UUID,
    status: AudioJobStatus,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": status.value}
    if result is not None:
        payload["result"] = result
    if error_message is not None:
        payload["error_message"] = error_message

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{_backend_url()}/internal/audio/jobs/{job_id}",
            headers=_dispatch_headers(),
            json=payload,
        )
        _raise_for_backend_response(response)
        return response.json()


async def get_day_audio_generation_payload(day_id: UUID) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/audio/days/{day_id}/generation-payload",
            headers=_dispatch_headers(),
        )
        _raise_for_backend_response(response)
        return response.json()


async def get_sub_task_audio_generation_payload(sub_task_id: UUID) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/audio/sub-tasks/{sub_task_id}/generation-payload",
            headers=_dispatch_headers(),
        )
        _raise_for_backend_response(response)
        return response.json()


async def apply_day_audio_generation_result(
    *,
    day_id: UUID,
    audio_key: str,
    duration_ms: int,
    file_size_bytes: int,
    timestamps: List[Dict[str, Any]],
    mime_type: str = "audio/wav",
) -> None:
    payload = {
        "audio_key": audio_key,
        "duration_ms": duration_ms,
        "mime_type": mime_type,
        "file_size_bytes": file_size_bytes,
        "timestamps": timestamps,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{_backend_url()}/internal/audio/days/{day_id}/generation-result",
            headers=_dispatch_headers(),
            json=payload,
        )
        _raise_for_backend_response(response)


async def apply_sub_task_audio_generation_result(
    *,
    sub_task_id: UUID,
    audio_key: str,
    duration_ms: int,
) -> None:
    payload = {
        "audio_key": audio_key,
        "duration_ms": duration_ms,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{_backend_url()}/internal/audio/sub-tasks/{sub_task_id}/generation-result",
            headers=_dispatch_headers(),
            json=payload,
        )
        _raise_for_backend_response(response)
