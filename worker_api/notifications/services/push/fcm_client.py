import asyncio
import logging
from uuid import UUID

from firebase_admin import messaging
from firebase_admin.exceptions import FirebaseError
from firebase_admin.messaging import UnregisteredError

from worker_api.notifications.services.push.firebase_init import initialize_firebase

logger = logging.getLogger(__name__)


class PermanentPushTokenError(Exception):
    """Raised when FCM reports a device token as permanently invalid."""


def build_routine_notification_data(
    *,
    session_type: str,
    source_id: UUID | None,
    title: str,
    body: str,
    image_url: str | None = None,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "session_type": session_type,
        "source_id": str(source_id) if source_id else "",
        "title": title,
        "body": body,
        "image_url": image_url or "",
    }


def build_chat_notification_data(
    *,
    room_id: UUID,
    message_id: UUID,
    sender_id: UUID,
    chat_kind: str,
    group_id: UUID | None,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "CHAT_MESSAGE",
        "session_type": "CHAT",
        "chat_kind": chat_kind,
        "room_id": str(room_id),
        "message_id": str(message_id),
        "sender_id": str(sender_id),
        "group_id": str(group_id) if group_id else "",
        "source_id": str(room_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


def build_prayer_notification_data(
    *,
    room_id: UUID,
    message_id: UUID,
    prayer_id: UUID,
    chat_kind: str,
    group_id: UUID | None,
    event_id: UUID | None,
    prayer_count: int,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values.

    Deep-links to the prayer request itself (message_id in its room), so the
    tap lands on the request rather than the bottom of the room."""
    return {
        "notification_type": "PRAYER_RECEIVED",
        "session_type": "CHAT",
        "chat_kind": chat_kind,
        "room_id": str(room_id),
        "message_id": str(message_id),
        "prayer_id": str(prayer_id),
        "group_id": str(group_id) if group_id else "",
        "event_id": str(event_id) if event_id else "",
        "prayer_count": str(prayer_count),
        "source_id": str(room_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


def build_group_post_notification_data(
    *,
    post_id: UUID,
    group_id: UUID,
    author_id: UUID,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "GROUP_POST",
        "session_type": "GROUP_POST",
        "post_id": str(post_id),
        "group_id": str(group_id),
        "author_id": str(author_id),
        "source_id": str(post_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


def build_event_notification_data(
    *,
    event_id: UUID,
    group_id: UUID,
    author_id: UUID,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "EVENT",
        "session_type": "EVENT",
        "event_id": str(event_id),
        "group_id": str(group_id),
        "author_id": str(author_id),
        "source_id": str(event_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


async def send_routine_push_notification(
    *,
    device_token: str,
    session_type: str,
    source_id: UUID | None,
    title: str,
    body: str,
    image_url: str | None = None,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        image_url=image_url,
        data=build_routine_notification_data(
            session_type=session_type,
            source_id=source_id,
            title=title,
            body=body,
            image_url=image_url,
        ),
    )


def build_verse_of_day_notification_data(
    *,
    title: str,
    body: str,
    image_url: str | None = None,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "VERSE_OF_DAY",
        "session_type": "VERSE_OF_DAY",
        "title": title,
        "body": body,
        "image_url": image_url or "",
    }


async def send_verse_of_day_push_notification(
    *,
    device_token: str,
    title: str,
    body: str,
    image_url: str | None = None,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        image_url=image_url,
        data=build_verse_of_day_notification_data(
            title=title,
            body=body,
            image_url=image_url,
        ),
    )


async def send_chat_push_notification(
    *,
    device_token: str,
    room_id: UUID,
    message_id: UUID,
    sender_id: UUID,
    chat_kind: str,
    group_id: UUID | None,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_chat_notification_data(
            room_id=room_id,
            message_id=message_id,
            sender_id=sender_id,
            chat_kind=chat_kind,
            group_id=group_id,
            title=title,
            body=body,
        ),
    )


async def send_prayer_push_notification(
    *,
    device_token: str,
    room_id: UUID,
    message_id: UUID,
    prayer_id: UUID,
    chat_kind: str,
    group_id: UUID | None,
    event_id: UUID | None,
    prayer_count: int,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_prayer_notification_data(
            room_id=room_id,
            message_id=message_id,
            prayer_id=prayer_id,
            chat_kind=chat_kind,
            group_id=group_id,
            event_id=event_id,
            prayer_count=prayer_count,
            title=title,
            body=body,
        ),
    )


async def send_group_post_push_notification(
    *,
    device_token: str,
    post_id: UUID,
    group_id: UUID,
    author_id: UUID,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_group_post_notification_data(
            post_id=post_id,
            group_id=group_id,
            author_id=author_id,
            title=title,
            body=body,
        ),
    )


async def send_event_push_notification(
    *,
    device_token: str,
    event_id: UUID,
    group_id: UUID,
    author_id: UUID,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_event_notification_data(
            event_id=event_id,
            group_id=group_id,
            author_id=author_id,
            title=title,
            body=body,
        ),
    )


def build_event_reminder_notification_data(
    *,
    event_id: UUID,
    reminder_type: str,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "EVENT_REMINDER",
        "session_type": "EVENT_REMINDER",
        "reminder_type": reminder_type,
        "event_id": str(event_id),
        "source_id": str(event_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


async def send_event_reminder_push_notification(
    *,
    device_token: str,
    event_id: UUID,
    reminder_type: str,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_event_reminder_notification_data(
            event_id=event_id,
            reminder_type=reminder_type,
            title=title,
            body=body,
        ),
    )


def build_event_announcement_notification_data(
    *,
    event_id: UUID,
    announcement_id: str,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": "EVENT_ANNOUNCEMENT",
        "session_type": "EVENT",
        "event_id": str(event_id),
        "announcement_id": announcement_id,
        "source_id": str(event_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


async def send_event_announcement_push_notification(
    *,
    device_token: str,
    event_id: UUID,
    announcement_id: str,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_event_announcement_notification_data(
            event_id=event_id,
            announcement_id=announcement_id,
            title=title,
            body=body,
        ),
    )


def build_join_request_notification_data(
    *,
    event_type: str,
    join_request_id: UUID,
    group_id: UUID,
    status: str,
    title: str,
    body: str,
) -> dict[str, str]:
    """FCM data payloads require string values."""
    return {
        "notification_type": event_type,
        "session_type": "GROUP",
        "join_request_id": str(join_request_id),
        "group_id": str(group_id),
        "status": status,
        "source_id": str(group_id),
        "title": title,
        "body": body,
        "image_url": "",
    }


async def send_join_request_push_notification(
    *,
    device_token: str,
    event_type: str,
    join_request_id: UUID,
    group_id: UUID,
    status: str,
    title: str,
    body: str,
) -> None:
    await send_fcm_notification(
        device_token=device_token,
        title=title,
        body=body,
        data=build_join_request_notification_data(
            event_type=event_type,
            join_request_id=join_request_id,
            group_id=group_id,
            status=status,
            title=title,
            body=body,
        ),
    )


def _is_permanent_token_error(exc: Exception) -> bool:
    if isinstance(exc, UnregisteredError):
        return True
    if isinstance(exc, messaging.SenderIdMismatchError):
        return True
    if isinstance(exc, FirebaseError):
        code = str(getattr(exc, "code", "") or "").lower()
        message = str(exc).lower()
        permanent_markers = (
            "registration-token-not-registered",
            "invalid-registration-token",
            "invalid-argument",
            "requested entity was not found",
            "not found",
        )
        return any(marker in code or marker in message for marker in permanent_markers)
    return False


async def send_fcm_notification(
    *,
    device_token: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    image_url: str | None = None,
) -> None:
    initialize_firebase()
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
            image=image_url,
        ),
        data=data or {},
        token=device_token,
    )
    try:
        await asyncio.to_thread(messaging.send, message)
    except Exception as exc:
        logger.exception("FCM send failed for token %s", device_token[:8])
        if _is_permanent_token_error(exc):
            raise PermanentPushTokenError(str(exc)) from exc
        raise
