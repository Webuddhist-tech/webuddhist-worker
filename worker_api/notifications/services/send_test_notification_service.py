import logging

from worker_api.config import get, get_random_default_notification_body
from worker_api.db.database import SessionLocal
from worker_api.notifications.repositories import routine_notification_repository as repo
from worker_api.notifications.schemas import (
    SendTestNotificationDelivery,
    SendTestNotificationRequest,
    SendTestNotificationResponse,
)
from worker_api.notifications.services.push.config_loader import is_fcm_configured, is_push_configured
from worker_api.notifications.services.push.fcm_client import (
    build_routine_notification_data,
    send_fcm_notification,
)

logger = logging.getLogger(__name__)


def _token_prefix(token: str) -> str:
    return token[:12] if len(token) > 12 else token


async def send_test_notification_service(
    request: SendTestNotificationRequest,
) -> SendTestNotificationResponse:
    title = request.title or get("NOTIFICATION_DEFAULT_TITLE")
    body = request.body or get_random_default_notification_body()
    data = build_routine_notification_data(
        session_type=request.session_type.value,
        source_id=request.source_id,
        title=title,
        body=body,
    )

    if request.device_token:
        recipients = [("", request.device_token.strip())]
    else:
        with SessionLocal() as db:
            devices = repo.get_active_push_devices_by_email(db, request.email or "")
        if not devices:
            raise ValueError(f"No active push devices found for email: {request.email}")
        recipients = [(device.platform, device.token) for device in devices]

    if not is_fcm_configured():
        raise ValueError(
            "Firebase is not configured. Set GOOGLE_APPLICATION_CREDENTIALS to your "
            "Firebase service account JSON file path (or inline JSON)."
        )

    deliveries: list[SendTestNotificationDelivery] = []
    sent = 0
    failed = 0

    for platform, device_token in recipients:
        if platform and not is_push_configured(platform):
            failed += 1
            deliveries.append(
                SendTestNotificationDelivery(
                    device_token_prefix=_token_prefix(device_token),
                    platform=platform or None,
                    status="failed",
                    error=f"Push not configured for platform: {platform}",
                )
            )
            continue

        try:
            await send_fcm_notification(
                device_token=device_token,
                title=title,
                body=body,
                data=data,
            )
            sent += 1
            deliveries.append(
                SendTestNotificationDelivery(
                    device_token_prefix=_token_prefix(device_token),
                    platform=platform or None,
                    status="sent",
                )
            )
        except Exception as exc:
            logger.exception("Failed to send test notification to %s", _token_prefix(device_token))
            failed += 1
            deliveries.append(
                SendTestNotificationDelivery(
                    device_token_prefix=_token_prefix(device_token),
                    platform=platform or None,
                    status="failed",
                    error=str(exc),
                )
            )

    return SendTestNotificationResponse(
        title=title,
        body=body,
        session_type=request.session_type.value,
        source_id=data["source_id"],
        sent=sent,
        failed=failed,
        deliveries=deliveries,
    )
