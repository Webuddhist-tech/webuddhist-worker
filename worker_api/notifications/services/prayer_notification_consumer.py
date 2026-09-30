import asyncio
import logging
from typing import Any, Dict

from worker_api.config import get_bool, get_int
from worker_api.notifications.prayer_sqs_client import (
    PRAYER_RECEIVED_EVENT,
    delete_prayer_notification_message,
    is_prayer_notification_sqs_poll_enabled,
    parse_prayer_notification_message_body,
    receive_prayer_notification_messages,
)
from worker_api.notifications.services.chat_notification_consumer import (
    TransientChatNotificationError,
    process_chat_message_event,
)
from worker_api.notifications.services.prayer_notification_service import (
    TransientPrayerNotificationError,
    process_prayer_event,
)

logger = logging.getLogger(__name__)

_POLL_IDLE_SECONDS = 5
_POLL_ERROR_SECONDS = 10


async def process_prayer_notification_message(message: Dict[str, Any]) -> None:
    """The prayer queue carries both prayer pushes: PRAYER_RECEIVED ("someone
    prayed for you") and prayer requests, which are CHAT_MESSAGE_CREATED events
    for a PRAYER message and go to the whole room."""
    receipt_handle = message.get("ReceiptHandle")
    body = parse_prayer_notification_message_body(message.get("Body", ""))
    if not body:
        if receipt_handle:
            delete_prayer_notification_message(receipt_handle)
        return

    if not get_bool("NOTIFICATION_DISPATCH_ENABLED"):
        logger.info("Prayer notification dispatch disabled; deleting event %s", body)
        if receipt_handle:
            delete_prayer_notification_message(receipt_handle)
        return

    if body.get("event_type") == PRAYER_RECEIVED_EVENT:
        await process_prayer_event(
            body=body,
            receipt_handle=receipt_handle,
            delete_message=delete_prayer_notification_message,
        )
        return

    await process_chat_message_event(
        body=body,
        receipt_handle=receipt_handle,
        delete_message=delete_prayer_notification_message,
    )


async def _handle_prayer_notification_message(
    message: Dict[str, Any], slots: asyncio.Semaphore
) -> None:
    try:
        await process_prayer_notification_message(message)
    except (TransientPrayerNotificationError, TransientChatNotificationError):
        logger.warning(
            "Leaving prayer notification SQS message for retry: %s",
            message.get("MessageId"),
        )
    except Exception:
        logger.exception("Unexpected prayer notification consumer error")
    finally:
        slots.release()


async def run_prayer_notification_sqs_consumer(stop_event: asyncio.Event) -> None:
    logger.info("Prayer notification SQS consumer started")
    slots = asyncio.Semaphore(max(get_int("PRAYER_NOTIFICATION_MESSAGE_CONCURRENCY"), 1))
    in_flight: set[asyncio.Task] = set()

    while not stop_event.is_set():
        if not is_prayer_notification_sqs_poll_enabled():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            messages = await asyncio.to_thread(receive_prayer_notification_messages)
            for message in messages:
                if stop_event.is_set():
                    break
                await slots.acquire()
                task = asyncio.create_task(_handle_prayer_notification_message(message, slots))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        except Exception:
            logger.exception("Prayer notification SQS consumer loop error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_ERROR_SECONDS)
            except asyncio.TimeoutError:
                pass

    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)
    logger.info("Prayer notification SQS consumer stopped")
