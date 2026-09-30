import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException

from worker_api.audio.enums import AudioJobStatus, MonlamVoiceName, PlanAudioType
from worker_api.audio.services.audio_generate_service import generate_plan_audio_service
from worker_api.audio.services.backend_client import (
    get_audio_job_status,
    update_audio_job_status,
)
from worker_api.audio.sqs_client import (
    delete_audio_job_message,
    is_audio_sqs_poll_enabled,
    parse_audio_job_message_body,
    receive_audio_job_messages,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10
_TERMINAL_STATUSES = {
    AudioJobStatus.COMPLETED.value,
    AudioJobStatus.FAILED.value,
}


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return UUID(str(value))


def _parse_audio_type(value: Any) -> PlanAudioType:
    if isinstance(value, PlanAudioType):
        return value
    if value is None or value == "":
        return PlanAudioType.TEXT_READING
    return PlanAudioType(str(value))


def _parse_voice_name(value: Any) -> MonlamVoiceName:
    if isinstance(value, MonlamVoiceName):
        return value
    if value is None or value == "":
        return MonlamVoiceName.DOLKAR_LHASA_FEMALE
    return MonlamVoiceName(str(value))


def _normalize_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("Audio generation returned no result")
    if not result.get("s3_key") and not result.get("audio_url"):
        raise ValueError("Audio generation returned an empty result")
    return {
        "audio_url": result.get("audio_url"),
        "audio_duration_ms": result.get("audio_duration_ms"),
        "s3_key": result.get("s3_key"),
    }


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        return str(detail)
    return str(exc)


async def process_audio_job_message(message: Dict[str, Any]) -> None:
    receipt_handle = message.get("ReceiptHandle")
    body = parse_audio_job_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            delete_audio_job_message(receipt_handle)
        return

    job_id = _parse_uuid(body.get("job_id"))
    if not job_id:
        logger.error("Invalid job_id in audio SQS message: %s", body)
        if receipt_handle:
            delete_audio_job_message(receipt_handle)
        return

    existing = await get_audio_job_status(job_id=job_id)
    if not existing:
        logger.error("Audio job not found on backend: %s", job_id)
        if receipt_handle:
            delete_audio_job_message(receipt_handle)
        return

    existing_status = str(existing.get("status") or "")
    if existing_status in _TERMINAL_STATUSES:
        logger.info("Skipping already finished audio job %s (%s)", job_id, existing_status)
        if receipt_handle:
            delete_audio_job_message(receipt_handle)
        return

    try:
        claimed = await update_audio_job_status(job_id=job_id, status=AudioJobStatus.PROCESSING)
    except HTTPException as exc:
        # Another worker already claimed this job (duplicate SQS delivery).
        if exc.status_code == 409:
            logger.info("Skipping already claimed audio job %s", job_id)
            if receipt_handle:
                delete_audio_job_message(receipt_handle)
            return
        raise

    claimed_status = str(claimed.get("status") or "")
    if claimed_status != AudioJobStatus.PROCESSING.value:
        logger.info("Skipping audio job %s after claim (%s)", job_id, claimed_status)
        if receipt_handle:
            delete_audio_job_message(receipt_handle)
        return

    try:
        day_id = _parse_uuid(body.get("day_id"))
        sub_task_id = _parse_uuid(body.get("sub_task_id"))
        language = str(body.get("language") or "")
        if not language:
            raise ValueError("language is required")
        if not day_id and not sub_task_id:
            raise ValueError("Exactly one of day_id or sub_task_id is required")

        result = await generate_plan_audio_service(
            language=language,
            day_id=day_id,
            sub_task_id=sub_task_id,
            audio_type=_parse_audio_type(body.get("type")),
            voice_name=_parse_voice_name(body.get("voice_name")),
        )
        normalized = _normalize_result(result)

        await update_audio_job_status(
            job_id=job_id,
            status=AudioJobStatus.COMPLETED,
            result=normalized,
        )
        logger.info("Completed audio job %s", job_id)
    except Exception as exc:
        logger.exception("Failed audio job %s", job_id)
        await update_audio_job_status(
            job_id=job_id,
            status=AudioJobStatus.FAILED,
            error_message=_error_detail(exc),
        )

    if receipt_handle:
        delete_audio_job_message(receipt_handle)


async def run_audio_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Audio SQS consumer started")
    while not stop_event.is_set():
        if not is_audio_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_audio_job_messages)
            if not messages:
                continue
            for message in messages:
                if stop_event.is_set():
                    break
                await process_audio_job_message(message)
        except Exception:
            logger.exception("Audio SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Audio SQS consumer stopped")
