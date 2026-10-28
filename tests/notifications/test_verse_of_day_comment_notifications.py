"""Verse-of-the-day comment pushes: each liker's device gets their own copy."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from worker_api.notifications.chat_sqs_client import parse_chat_notification_message_body
from worker_api.notifications.schemas import (
    VerseOfDayCommentNotificationTargetsResponse,
    VerseOfDayCommentPushDevice,
    VerseOfDayCommentRecipient,
)
from worker_api.notifications.services import verse_of_day_comment_notification_service as service
from worker_api.notifications.services.chat_notification_consumer import (
    TransientChatNotificationError,
    process_chat_notification_message,
)
from worker_api.notifications.services.push.fcm_client import build_verse_of_day_comment_notification_data

MODULE = "worker_api.notifications.services.verse_of_day_comment_notification_service"


def _event(window_id):
    return {
        "ReceiptHandle": "r1",
        "Body": json.dumps({"event_type": "VERSE_OF_DAY_COMMENTS", "version": 1, "window_id": str(window_id)}),
    }


def _targets(window_id, recipients):
    return VerseOfDayCommentNotificationTargetsResponse(
        window_id=window_id, verse_id=uuid4(), image_url=None, recipients=recipients,
        skip=0, limit=200, total=len(recipients), has_more=False,
    )


def _recipient(title, body, *tokens):
    return VerseOfDayCommentRecipient(
        user_id=uuid4(), title=title, body=body,
        push_devices=[VerseOfDayCommentPushDevice(id=uuid4(), token=token, platform="android") for token in tokens],
    )


def test_the_chat_queue_accepts_verse_comment_events():
    window_id = uuid4()
    body = parse_chat_notification_message_body(_event(window_id)["Body"])

    assert body["window_id"] == str(window_id)
    assert parse_chat_notification_message_body(
        json.dumps({"event_type": "VERSE_OF_DAY_COMMENTS", "version": 1})
    ) is None


def test_the_push_opens_the_verse_and_says_it_is_about_comments():
    verse_id = uuid4()
    data = build_verse_of_day_comment_notification_data(verse_id=verse_id, title="t", body="b")

    assert data["session_type"] == "VERSE_OF_DAY"
    assert data["notification_type"] == "VERSE_OF_DAY_COMMENT"
    assert data["screen"] == "comments" and data["verse_id"] == str(verse_id)
    assert all(isinstance(value, str) for value in data.values())


@pytest.mark.asyncio
@patch(f"{MODULE}._mark_sent")
@patch(f"{MODULE}._already_sent", return_value=False)
@patch(f"{MODULE}.is_push_configured", return_value=True)
@patch(f"{MODULE}.get_int", return_value=5)
@patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
@patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
async def test_each_device_gets_its_recipients_own_copy(mock_delete, _bool, _int, _configured, _already, _mark):
    window_id = uuid4()
    targets = _targets(window_id, [
        _recipient("Tashi commented on today's verse", "Om · 1 more person commented", "me-phone", "me-tablet"),
        _recipient("Tashi commented on today's verse", "Om · 3 more people commented", "dawa-phone"),
    ])
    with patch(f"{MODULE}._fetch_all_targets", new=AsyncMock(return_value=targets)), \
            patch(f"{MODULE}.send_verse_of_day_comment_push_notification", new=AsyncMock()) as send:
        await process_chat_notification_message(_event(window_id))

    sent = {call.kwargs["device_token"]: call.kwargs["body"] for call in send.await_args_list}
    assert sent == {
        "me-phone": "Om · 1 more person commented",
        "me-tablet": "Om · 1 more person commented",
        "dawa-phone": "Om · 3 more people commented",
    }
    assert {call.kwargs["verse_id"] for call in send.await_args_list} == {targets.verse_id}
    mock_delete.assert_called_once_with("r1")


@pytest.mark.asyncio
@patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
@patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
async def test_a_window_the_backend_no_longer_has_is_dropped(mock_delete, _bool):
    request = httpx.Request("GET", "http://backend")
    not_found = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    with patch(f"{MODULE}.fetch_verse_of_day_comment_notification_targets", new=AsyncMock(side_effect=not_found)), \
            patch(f"{MODULE}.get_int", return_value=200):
        await process_chat_notification_message(_event(uuid4()))

    mock_delete.assert_called_once_with("r1")


@pytest.mark.asyncio
@patch(f"{MODULE}._mark_sent")
@patch(f"{MODULE}._already_sent", return_value=False)
@patch(f"{MODULE}.is_push_configured", return_value=True)
@patch(f"{MODULE}.get_int", return_value=5)
@patch("worker_api.notifications.services.chat_notification_consumer.get_bool", return_value=True)
@patch("worker_api.notifications.services.chat_notification_consumer.delete_chat_notification_message")
async def test_a_transient_send_failure_keeps_the_event_for_retry(mock_delete, _bool, _int, _configured, _already, _mark):
    window_id = uuid4()
    targets = _targets(window_id, [_recipient("t", "b", "phone")])
    with patch(f"{MODULE}._fetch_all_targets", new=AsyncMock(return_value=targets)), \
            patch(f"{MODULE}.send_verse_of_day_comment_push_notification", new=AsyncMock(side_effect=RuntimeError("fcm"))):
        with pytest.raises(TransientChatNotificationError):
            await process_chat_notification_message(_event(window_id))

    mock_delete.assert_not_called()
