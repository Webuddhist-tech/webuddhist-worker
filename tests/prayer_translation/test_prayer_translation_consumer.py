from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.prayer_translation.services.gemini_client import (
    TransientPrayerTranslationError,
)
from worker_api.prayer_translation.services.prayer_translation_consumer import (
    process_prayer_translation_message,
)


@pytest.mark.asyncio
async def test_leaves_message_when_translation_disabled():
    message_id = uuid4()
    receipt_handle = "receipt-1"
    sqs_message = {
        "ReceiptHandle": receipt_handle,
        "Body": f'{{"message_id": "{message_id}"}}',
    }

    with patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.prayer_translation_enabled",
        return_value=False,
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer._delete_prayer_translation_message",
        new_callable=AsyncMock,
    ) as mock_delete, patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.get_prayer_translation_payload",
        new_callable=AsyncMock,
    ) as mock_get_payload:
        await process_prayer_translation_message(sqs_message)

    mock_get_payload.assert_not_called()
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_leaves_message_on_transient_gemini_error():
    message_id = uuid4()
    receipt_handle = "receipt-2"
    sqs_message = {
        "ReceiptHandle": receipt_handle,
        "Body": f'{{"message_id": "{message_id}"}}',
    }

    with patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.prayer_translation_enabled",
        return_value=True,
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.get_prayer_translation_payload",
        new_callable=AsyncMock,
        return_value={"body": "Please pray"},
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.translate_prayer_request",
        new_callable=AsyncMock,
        side_effect=TransientPrayerTranslationError("Gemini prayer translation failed"),
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.post_prayer_translation_result",
        new_callable=AsyncMock,
    ) as mock_post, patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer._delete_prayer_translation_message",
        new_callable=AsyncMock,
    ) as mock_delete:
        await process_prayer_translation_message(sqs_message)

    mock_post.assert_not_called()
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_deletes_message_after_success():
    message_id = uuid4()
    receipt_handle = "receipt-3"
    sqs_message = {
        "ReceiptHandle": receipt_handle,
        "Body": f'{{"message_id": "{message_id}"}}',
    }

    with patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.prayer_translation_enabled",
        return_value=True,
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.get_prayer_translation_payload",
        new_callable=AsyncMock,
        return_value={"body": "Please pray"},
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.translate_prayer_request",
        new_callable=AsyncMock,
        return_value=("EN", {"EN": "Hi", "BO": "བོད", "ZH": "你好"}),
    ), patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer.post_prayer_translation_result",
        new_callable=AsyncMock,
    ) as mock_post, patch(
        "worker_api.prayer_translation.services.prayer_translation_consumer._delete_prayer_translation_message",
        new_callable=AsyncMock,
    ) as mock_delete:
        await process_prayer_translation_message(sqs_message)

    mock_post.assert_awaited_once()
    mock_delete.assert_awaited_once_with(receipt_handle)
