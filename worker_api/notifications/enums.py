from enum import StrEnum


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"


class PushPlatform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class SessionType(StrEnum):
    PLAN = "PLAN"
    SERIES = "SERIES"
    RECITATION = "RECITATION"
    RECITATION_COLLECTION = "RECITATION_COLLECTION"
    ACCUMULATION = "ACCUMULATION"
    TIMER = "TIMER"
    CHAT = "CHAT"
    GROUP = "GROUP"
    EVENT = "EVENT"
    EVENT_REMINDER = "EVENT_REMINDER"
