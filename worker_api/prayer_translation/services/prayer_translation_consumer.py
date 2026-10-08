import asyncio
import logging
from typing import Any, Dict
from uuid import UUID

from worker_api.prayer_translation.services.backend_client import (
    get_prayer_translation_payload,
    post_prayer_translation_result,
)
from worker_api.prayer_translation.services.gemini_client import (
    TransientPrayerTranslationError,
    prayer_translation_enabled,
    translate_prayer_request,
)
from worker_api.prayer_translation.sqs_client import (
    delete_prayer_translation_message,
    is_prayer_translation_sqs_poll_enabled,
    parse_prayer_translation_message_body,
    receive_prayer_translation_messages,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10


async def _delete_prayer_translation_message(receipt_handle: str) -> None:
    await asyncio.to_thread(delete_prayer_translation_message, receipt_handle)


async def process_prayer_translation_message(message: Dict[str, Any]) -> None:
    receipt_handle = message.get("ReceiptHandle")
    body = parse_prayer_translation_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            await _delete_prayer_translation_message(receipt_handle)
        return

    try:
        message_id = UUID(str(body["message_id"]))
    except (TypeError, ValueError):
        logger.error("Invalid message_id in prayer translation SQS message: %s", body)
        if receipt_handle:
            await _delete_prayer_translation_message(receipt_handle)
        return

    if not prayer_translation_enabled():
        logger.info(
            "Prayer translation disabled; leaving SQS message for retry: %s",
            message_id,
        )
        return

    payload = await get_prayer_translation_payload(message_id=message_id)
    if not payload or not payload.get("body"):
        logger.info("Skipping prayer translation for missing message %s", message_id)
        if receipt_handle:
            await _delete_prayer_translation_message(receipt_handle)
        return

    body_at_dispatch = str(payload["body"])
    try:
        source_language, translations = await translate_prayer_request(body_at_dispatch)
    except TransientPrayerTranslationError:
        logger.warning(
            "Leaving prayer translation SQS message for retry: %s",
            message_id,
        )
        return

    await post_prayer_translation_result(
        message_id=message_id,
        body_at_dispatch=body_at_dispatch,
        source_language=source_language,
        translations=translations,
    )

    if receipt_handle:
        await _delete_prayer_translation_message(receipt_handle)


async def run_prayer_translation_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Prayer translation SQS consumer started")
    while not stop_event.is_set():
        if not is_prayer_translation_sqs_poll_enabled() or not prayer_translation_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_prayer_translation_messages)
            if not messages:
                continue
            for message in messages:
                if stop_event.is_set():
                    break
                await process_prayer_translation_message(message)
        except Exception:
            logger.exception("Prayer translation SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info("Prayer translation SQS consumer stopped")
