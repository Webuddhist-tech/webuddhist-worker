import json
import os
import random
import re

DEFAULTS = dict(
    DATABASE_URL="postgresql://admin:pechaAdmin@localhost:5434/pecha-worker",
    MONGO_CONNECTION_STRING="mongodb://admin:pechaAdmin@localhost:27017/pecha?authSource=admin",
    MONGO_DATABASE_NAME="webuddhist",

    AWS_ACCESS_KEY="",
    AWS_SECRET_KEY="",
    AWS_REGION="eu-central-1",
    AWS_BUCKET_NAME="app-pecha-backend",
    IMAGE_EXPIRATION_IN_SEC=3600,

    CACHE_CONNECTION_STRING="redis://localhost:6379",

    # Request observability (per-endpoint memory and latency logging)
    REQUEST_OBSERVABILITY_ENABLED="true",
    REQUEST_OBSERVABILITY_MEMORY_WARN_MB=50,
    REQUEST_OBSERVABILITY_SKIP_PATHS="/health,/internal/dispatch-due-notifications,/internal/dispatch-routine-notifications",

    # TTS Configuration
    GEMINI_API_KEY="",
    MONLAM_BASE_URL="",
    MONLAM_API_KEY="",
    MONLAM_TTS_PROVIDER="",
    MONLAM_TTS_MODEL_NAME="",
    MONLAM_TTS_VOICE_NAME="",

    # Audio job SQS consumer (backend producer → worker consumer)
    AUDIO_SQS_QUEUE_URL="",
    AUDIO_SQS_WAIT_TIME_SECONDS=20,
    AUDIO_SQS_VISIBILITY_TIMEOUT_SECONDS=900,
    AUDIO_SQS_MAX_MESSAGES=1,
    AUDIO_SQS_POLL_ENABLED="true",

    # Chat notification SQS consumer (backend producer → worker consumer)
    CHAT_NOTIFICATION_SQS_QUEUE_URL="",
    CHAT_NOTIFICATION_SQS_WAIT_TIME_SECONDS=20,
    CHAT_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS=300,
    CHAT_NOTIFICATION_SQS_MAX_MESSAGES=5,
    CHAT_NOTIFICATION_SQS_POLL_ENABLED="true",
    CHAT_NOTIFICATION_SEND_CONCURRENCY=10,
    CHAT_NOTIFICATION_TARGET_PAGE_SIZE=100,
    CHAT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS=86400,
    CHAT_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX="worker:chat-notifications:sent:",

    # Join request notification SQS consumer (backend producer → worker consumer)
    JOIN_REQUEST_NOTIFICATION_SQS_QUEUE_URL="",
    JOIN_REQUEST_NOTIFICATION_SQS_WAIT_TIME_SECONDS=20,
    JOIN_REQUEST_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS=300,
    JOIN_REQUEST_NOTIFICATION_SQS_MAX_MESSAGES=5,
    JOIN_REQUEST_NOTIFICATION_SQS_POLL_ENABLED="true",
    JOIN_REQUEST_NOTIFICATION_SEND_CONCURRENCY=10,
    JOIN_REQUEST_NOTIFICATION_TARGET_PAGE_SIZE=100,
    JOIN_REQUEST_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS=86400,
    JOIN_REQUEST_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX="worker:join-request-notifications:sent:",

    # Group post notification SQS consumer (backend producer → worker consumer)
    GROUP_POST_NOTIFICATION_SQS_QUEUE_URL="",
    GROUP_POST_NOTIFICATION_SQS_WAIT_TIME_SECONDS=20,
    GROUP_POST_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS=300,
    GROUP_POST_NOTIFICATION_SQS_MAX_MESSAGES=5,
    GROUP_POST_NOTIFICATION_SQS_POLL_ENABLED="true",
    GROUP_POST_NOTIFICATION_SEND_CONCURRENCY=10,
    GROUP_POST_NOTIFICATION_TARGET_PAGE_SIZE=100,
    GROUP_POST_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS=86400,
    GROUP_POST_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX="worker:group-post-notifications:sent:",

    # Event notification SQS consumer (backend producer → worker consumer)
    EVENT_NOTIFICATION_SQS_QUEUE_URL="",
    EVENT_NOTIFICATION_SQS_WAIT_TIME_SECONDS=20,
    EVENT_NOTIFICATION_SQS_VISIBILITY_TIMEOUT_SECONDS=300,
    EVENT_NOTIFICATION_SQS_MAX_MESSAGES=5,
    EVENT_NOTIFICATION_SQS_POLL_ENABLED="true",
    EVENT_NOTIFICATION_SEND_CONCURRENCY=10,
    EVENT_NOTIFICATION_TARGET_PAGE_SIZE=100,
    EVENT_NOTIFICATION_IDEMPOTENCY_TTL_SECONDS=86400,
    EVENT_NOTIFICATION_IDEMPOTENCY_KEY_PREFIX="worker:event-notifications:sent:",
    # Reminder dedup keys gained fire_at, so a retry handled by this
    # release must still recognize what the previous release recorded
    # under the fire_at-less key. Turn off once every worker is on this
    # release, and before enabling the backend per-day reminder flags.
    EVENT_NOTIFICATION_IDEMPOTENCY_LEGACY_KEY_FALLBACK="true",

    # Notification dispatch (Cloud Scheduler -> worker)
    NOTIFICATION_DISPATCH_SECRET_TOKEN="Dispatch",
    NOTIFICATION_DISPATCH_BATCH_SIZE=100,
    NOTIFICATION_DISPATCH_ENABLED="true",
    BACKEND_API_URL="http://127.0.0.1:8000/api/v1",

    GOOGLE_APPLICATION_CREDENTIALS="secrets/webuddhist-app-firebase-adminsdk-fbsvc-a4e82f9837.json",
    GOOGLE_CLOUD_PROJECT="",

    # Notification content defaults
    NOTIFICATION_DEFAULT_TITLE="WebBuddhist",
    NOTIFICATION_DEFAULT_BODIES=[
        "Time for your daily practice.",
        "Your practice awaits.",
        "Pause. Breathe. Practice.",
        "Even a few minutes counts. Show up for yourself today.",
        "Come back to the present. Your practice begins now.",
    ],

    # Optional Redis idempotency during dispatch
    NOTIFICATION_IDEMPOTENCY_ENABLED="false",
    NOTIFICATION_IDEMPOTENCY_TTL_SECONDS=3600,
    NOTIFICATION_IDEMPOTENCY_KEY_PREFIX="worker:notifications:sent:",
)

TIME_FORMAT_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def get(key: str) -> str:
    if key in os.environ:
        return os.environ[key]
    else:
        return str(DEFAULTS[key])


def get_default_notification_bodies() -> list[str]:
    raw = os.environ.get("NOTIFICATION_DEFAULT_BODIES", "").strip()
    if raw:
        if raw.startswith("["):
            bodies = json.loads(raw)
        else:
            bodies = [part.strip() for part in raw.split("|") if part.strip()]
        if isinstance(bodies, list) and bodies:
            return [str(body) for body in bodies]
    return list(DEFAULTS["NOTIFICATION_DEFAULT_BODIES"])


def get_random_default_notification_body() -> str:
    return random.choice(get_default_notification_bodies())


def get_float(key: str) -> float:
    try:
        return float(get(key))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Could not convert the value for key '{key}' to float: {e}")


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Could not convert the value for key '{key}' to int: {e}")


def get_bool(key: str) -> bool:
    return get(key).lower() in {"1", "true", "yes", "on"}
