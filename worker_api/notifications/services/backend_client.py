import httpx

from uuid import UUID

from worker_api.config import get
from worker_api.notifications.schemas import (
    ChatNotificationTargetsResponse,
    PrayerNotificationTargetsResponse,
    DeactivatePushDeviceResponse,
    EventAnnouncementTargetsResponse,
    EventNotificationTargetsResponse,
    EventReminderTargetsResponse,
    GroupPostNotificationTargetsResponse,
    JoinRequestNotificationTargetsResponse,
    NotificationContent,
    RoutineNotificationTargetsResponse,
    VerseOfDayNotificationTargetsResponse,
)


def _backend_headers() -> dict[str, str]:
    dispatch_token = get("NOTIFICATION_DISPATCH_SECRET_TOKEN")
    if not dispatch_token:
        raise RuntimeError("NOTIFICATION_DISPATCH_SECRET_TOKEN is not configured")
    return {"X-Dispatch-Token": dispatch_token}


def _backend_url() -> str:
    backend_url = get("BACKEND_API_URL").rstrip("/")
    if not backend_url:
        raise RuntimeError("BACKEND_API_URL is not configured")
    return backend_url


async def fetch_routine_notification_targets() -> RoutineNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/routine-notification-targets",
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return RoutineNotificationTargetsResponse.model_validate(response.json())


async def fetch_plan_notification_content(
    *,
    user_id: UUID,
    plan_id: UUID,
) -> NotificationContent:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/plan-notification-content",
            params={"user_id": str(user_id), "plan_id": str(plan_id)},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return NotificationContent.model_validate(response.json())


async def fetch_chat_notification_targets(
    *,
    message_id: UUID,
    skip: int = 0,
    limit: int = 100,
) -> ChatNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/chat-notification-targets/{message_id}",
            params={"skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return ChatNotificationTargetsResponse.model_validate(response.json())


async def fetch_prayer_notification_targets(
    *,
    prayer_id: UUID,
    skip: int = 0,
    limit: int = 100,
) -> PrayerNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/prayer-notification-targets/{prayer_id}",
            params={"skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return PrayerNotificationTargetsResponse.model_validate(response.json())


async def fetch_join_request_notification_targets(
    *,
    join_request_id: UUID,
    skip: int = 0,
    limit: int = 100,
) -> JoinRequestNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/join-request-notification-targets/{join_request_id}",
            params={"skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return JoinRequestNotificationTargetsResponse.model_validate(response.json())


async def fetch_verse_of_day_notification_targets() -> VerseOfDayNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/verse-of-day-notification-targets",
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return VerseOfDayNotificationTargetsResponse.model_validate(response.json())


async def fetch_group_post_notification_targets(
    *,
    post_id: UUID,
    skip: int = 0,
    limit: int = 100,
) -> GroupPostNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/group-post-notification-targets/{post_id}",
            params={"skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return GroupPostNotificationTargetsResponse.model_validate(response.json())


async def fetch_event_notification_targets(
    *,
    event_id: UUID,
    skip: int = 0,
    limit: int = 100,
) -> EventNotificationTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/event-notification-targets/{event_id}",
            params={"skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return EventNotificationTargetsResponse.model_validate(response.json())


async def fetch_event_reminder_targets(
    *,
    event_id: UUID,
    reminder_type: str,
    fire_at: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> EventReminderTargetsResponse:
    params: dict[str, str | int] = {"reminder_type": reminder_type, "skip": skip, "limit": limit}
    if fire_at is not None:
        # Lets the backend recognize a message that outlived a cancel or
        # reschedule of the same (event_id, reminder_type) row and was
        # superseded by a fresh dispatch before this one was processed.
        params["fire_at"] = fire_at
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/event-reminder-targets/{event_id}",
            params=params,
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return EventReminderTargetsResponse.model_validate(response.json())


async def fetch_event_announcement_targets(
    *,
    event_id: UUID,
    audience: str,
    skip: int = 0,
    limit: int = 100,
) -> EventAnnouncementTargetsResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{_backend_url()}/internal/event-announcement-targets/{event_id}",
            params={"audience": audience, "skip": skip, "limit": limit},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return EventAnnouncementTargetsResponse.model_validate(response.json())


async def deactivate_push_device(*, push_device_id: UUID) -> DeactivatePushDeviceResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{_backend_url()}/internal/push-devices/deactivate",
            json={"push_device_id": str(push_device_id)},
            headers=_backend_headers(),
        )
        response.raise_for_status()
        return DeactivatePushDeviceResponse.model_validate(response.json())
