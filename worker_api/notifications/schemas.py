from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from worker_api.config import TIME_FORMAT_PATTERN
from worker_api.notifications.enums import PushPlatform, SessionType


class RoutineConfig(BaseModel):
    times: list[str] = Field(..., min_length=1)
    days_of_week: list[int] | None = Field(
        default=None,
        description="ISO weekday numbers (0=Monday). Omit for every day.",
    )
    plan_name: str | None = None
    message_template: str | None = None
    current_day_number: int | None = None

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: list[str]) -> list[str]:
        for time_value in value:
            if not TIME_FORMAT_PATTERN.match(time_value):
                raise ValueError(f"Invalid time format: {time_value}. Expected HH:MM")
        return value

    @field_validator("days_of_week")
    @classmethod
    def validate_days_of_week(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        for day in value:
            if day < 0 or day > 6:
                raise ValueError("days_of_week values must be between 0 and 6")
        return value


class EnrollReminderRequest(BaseModel):
    user_id: UUID
    plan_id: UUID
    timezone: str
    device_token: str
    platform: PushPlatform
    routine: RoutineConfig


class UpdateReminderRequest(BaseModel):
    timezone: str | None = None
    device_token: str | None = None
    platform: PushPlatform | None = None
    routine: RoutineConfig | None = None


class ReminderResponse(BaseModel):
    id: UUID
    user_id: UUID
    plan_id: UUID
    trigger_at: datetime
    timezone: str
    status: str
    platform: str
    routine_config: dict[str, Any]

    model_config = {"from_attributes": True}


class DispatchDueNotificationsResponse(BaseModel):
    processed: int
    sent: int
    failed: int
    skipped: int


class PushDeviceTarget(BaseModel):
    token: str
    platform: str


class NotificationContent(BaseModel):
    title: str
    body: str
    image_url: str | None = None


class RoutineNotificationUserTarget(BaseModel):
    user_id: UUID
    notification: NotificationContent
    push_devices: list[PushDeviceTarget]


class RoutineNotificationGroup(BaseModel):
    session_type: str
    source_id: UUID | None
    source_image_url: str | None = None
    users: list[RoutineNotificationUserTarget]


class RoutineNotificationTargetsResponse(BaseModel):
    generated_at: datetime
    matched_time_utc: str
    groups: list[RoutineNotificationGroup]


class DispatchRoutineNotificationsResponse(BaseModel):
    generated_at: datetime
    matched_time_utc: str
    groups: list[RoutineNotificationGroup]
    processed: int
    sent: int
    failed: int
    skipped: int


class SendTestNotificationRequest(BaseModel):
    """Send a push notification directly for testing."""

    title: str | None = Field(
        default=None,
        description="Notification title shown to the user. Defaults to NOTIFICATION_DEFAULT_TITLE.",
    )
    body: str | None = Field(
        default=None,
        description="Notification body shown to the user. Defaults to a random NOTIFICATION_DEFAULT_BODIES message.",
    )
    session_type: SessionType = Field(
        ...,
        description="Routine session type included in the FCM data payload.",
    )
    source_id: UUID | None = Field(
        default=None,
        description="UUID of the related entity. Omit or null for session types without a linked entity.",
    )
    device_token: str | None = Field(
        default=None,
        description="FCM device token to send the notification to.",
    )
    email: str | None = Field(
        default=None,
        description="User email; sends to all active push devices registered for that user.",
    )

    @model_validator(mode="after")
    def validate_recipient(self) -> "SendTestNotificationRequest":
        has_token = bool(self.device_token and self.device_token.strip())
        has_email = bool(self.email and self.email.strip())
        if has_token == has_email:
            raise ValueError("Provide exactly one of device_token or email")
        return self


class SendTestNotificationDelivery(BaseModel):
    device_token_prefix: str
    platform: str | None = None
    status: str
    error: str | None = None


class SendTestNotificationResponse(BaseModel):
    title: str
    body: str
    session_type: str
    source_id: str
    sent: int
    failed: int
    deliveries: list[SendTestNotificationDelivery]


class ChatPushDeviceTarget(BaseModel):
    id: UUID
    token: str
    platform: str


class ChatNotificationRecipient(BaseModel):
    user_id: UUID
    push_devices: list[ChatPushDeviceTarget]


class ChatNotificationTargetsResponse(BaseModel):
    message_id: UUID
    room_id: UUID
    sender_id: UUID
    chat_kind: str
    group_id: UUID | None = None
    title: str
    body: str
    recipients: list[ChatNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class PrayerNotificationTargetsResponse(BaseModel):
    """Targets for "someone prayed for your request".

    Recipients is the requester alone; the pagination fields mirror the chat
    targets contract so both can share one consumer."""
    prayer_id: UUID
    message_id: UUID
    room_id: UUID
    chat_kind: str
    group_id: UUID | None = None
    event_id: UUID | None = None
    requester_id: UUID
    prayer_count: int
    title: str
    body: str
    recipients: list[ChatNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class GroupPostPushDeviceTarget(BaseModel):
    id: UUID
    token: str
    platform: str


class GroupPostNotificationRecipient(BaseModel):
    user_id: UUID
    push_devices: list[GroupPostPushDeviceTarget]


class GroupPostNotificationTargetsResponse(BaseModel):
    post_id: UUID
    group_id: UUID
    author_id: UUID
    title: str
    body: str
    recipients: list[GroupPostNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class EventPushDeviceTarget(BaseModel):
    id: UUID
    token: str
    platform: str


class EventNotificationRecipient(BaseModel):
    user_id: UUID
    push_devices: list[EventPushDeviceTarget]


class EventNotificationTargetsResponse(BaseModel):
    event_id: UUID
    group_id: UUID
    author_id: UUID
    title: str
    body: str
    recipients: list[EventNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class EventReminderTargetsResponse(BaseModel):
    event_id: UUID
    reminder_type: str
    title: str
    body: str
    recipients: list[EventNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class EventAnnouncementTargetsResponse(BaseModel):
    event_id: UUID
    audience: str
    recipients: list[EventNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class JoinRequestPushDeviceTarget(BaseModel):
    id: UUID
    token: str
    platform: str


class JoinRequestNotificationRecipient(BaseModel):
    user_id: UUID
    push_devices: list[JoinRequestPushDeviceTarget]


class JoinRequestNotificationTargetsResponse(BaseModel):
    join_request_id: UUID
    group_id: UUID
    event_type: str
    status: str
    group_name: str
    requester_name: str
    title: str
    body: str
    recipients: list[JoinRequestNotificationRecipient]
    skip: int
    limit: int
    total: int
    has_more: bool


class DeactivatePushDeviceRequest(BaseModel):
    push_device_id: UUID


class DeactivatePushDeviceResponse(BaseModel):
    push_device_id: UUID
    deactivated: bool


class VerseOfDayPushDeviceTarget(BaseModel):
    token: str
    platform: str


class VerseOfDayNotificationContent(BaseModel):
    title: str
    body: str
    image_url: str | None = None


class VerseOfDayNotificationUserTarget(BaseModel):
    user_id: UUID
    notification: VerseOfDayNotificationContent
    push_devices: list[VerseOfDayPushDeviceTarget]


class VerseOfDayNotificationTargetsResponse(BaseModel):
    generated_at: datetime
    users: list[VerseOfDayNotificationUserTarget]


class DispatchVerseOfDayNotificationsResponse(BaseModel):
    generated_at: datetime
    users: list[VerseOfDayNotificationUserTarget]
    processed: int
    sent: int
    failed: int
    skipped: int
