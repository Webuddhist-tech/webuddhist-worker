"""Trigger a test push notification via the routine-notification-targets flow.

Usage:
  poetry run python scripts/trigger_test_notification.py
  poetry run python scripts/trigger_test_notification.py --send

Set TEST_FCM_DEVICE_TOKEN in .env to a real FCM token to deliver a push.
Without --send, only prints matched targets.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from worker_api.db.database import SessionLocal
from worker_api.notifications.services.push.fcm_client import send_routine_push_notification
from worker_api.notifications.services.routine_notification_service import (
    get_routine_notification_targets,
)


def _align_test_timeblock() -> UUID | None:
    """Set one enabled timeblock to the current local minute for its stored offset."""
    utc_now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT rtb.id, rtb.created_at, r.user_id
                FROM routine_time_blocks rtb
                JOIN routines r ON r.id = rtb.routine_id
                WHERE rtb.deleted_at IS NULL
                  AND rtb.notification_enabled
                  AND r.deleted_at IS NULL
                ORDER BY rtb.created_at DESC
                LIMIT 1
                """
            )
        ).first()
        if row is None:
            print("No routine timeblocks found to align.")
            return None

        created_at = row.created_at
        offset = created_at.utcoffset() or timezone.utc.utcoffset(created_at)
        local_now = utc_now + offset
        hhmm = local_now.strftime("%H:%M")
        db.execute(
            text("UPDATE routine_time_blocks SET time = :time WHERE id = :id"),
            {"time": hhmm, "id": row.id},
        )
        db.commit()
        print(f"Aligned timeblock {row.id} to {hhmm} for user {row.user_id}")
        return row.user_id


def _ensure_test_push_device(token: str, user_id: UUID | None = None) -> UUID | None:
    with SessionLocal() as db:
        if user_id is None:
            user_row = db.execute(
                text(
                    """
                    SELECT r.user_id
                    FROM routines r
                    WHERE r.deleted_at IS NULL
                    LIMIT 1
                    """
                )
            ).first()
            if user_row is None:
                print("No routine users found.")
                return None
            user_id = user_row.user_id
        existing = db.execute(
            text(
                """
                SELECT id FROM push_device_tokens
                WHERE user_id = :user_id AND is_active = true
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).first()

        if existing:
            db.execute(
                text(
                    """
                    UPDATE push_device_tokens
                    SET token = :token, platform = 'ANDROID', updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"token": token, "id": existing.id},
            )
            db.commit()
            print(f"Updated push device for user {user_id}")
            return user_id

        token_owner = db.execute(
            text("SELECT id, user_id FROM push_device_tokens WHERE token = :token LIMIT 1"),
            {"token": token},
        ).first()
        if token_owner:
            db.execute(
                text(
                    """
                    UPDATE push_device_tokens
                    SET user_id = :user_id, platform = 'ANDROID', is_active = true, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"user_id": user_id, "id": token_owner.id},
            )
            db.commit()
            print(f"Reassigned existing token device to user {user_id}")
            return user_id

        device_id = db.execute(
            text(
                """
                INSERT INTO push_device_tokens (
                    id, user_id, token, platform, device_id, is_active, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :user_id, :token, 'ANDROID', 'test-device', true, NOW(), NOW()
                )
                RETURNING id
                """
            ),
            {"user_id": user_id, "token": token},
        ).scalar_one()
        db.commit()
        print(f"Inserted test push device {device_id} for user {user_id}")
        return user_id


async def _send_targets(send: bool) -> None:
    targets = await get_routine_notification_targets()
    print(f"generated_at={targets.generated_at.isoformat()}")
    print(f"matched_time_utc={targets.matched_time_utc}")
    print(f"groups={len(targets.groups)}")

    if not targets.groups:
        print("No notification targets matched right now.")
        return

    for group in targets.groups:
        print(
            f"- {group.session_type} source_id={group.source_id} "
            f"users={len(group.users)} image={group.source_image_url}"
        )
        for user in group.users:
            notification = user.notification
            print(
                f"  user={user.user_id} title={notification.title!r} "
                f"devices={len(user.push_devices)}"
            )
            if not send:
                continue

            for device in user.push_devices:
                await send_routine_push_notification(
                    device_token=device.token,
                    session_type=group.session_type,
                    source_id=group.source_id,
                    title=notification.title,
                    body=notification.body,
                    image_url=notification.image_url,
                )
                print(f"    sent to {device.platform} token={device.token[:12]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger a test routine notification")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send FCM push for matched targets (requires TEST_FCM_DEVICE_TOKEN)",
    )
    parser.add_argument(
        "--align-timeblock",
        action="store_true",
        default=True,
        help="Align one routine timeblock to the current minute (default: true)",
    )
    parser.add_argument(
        "--no-align-timeblock",
        action="store_false",
        dest="align_timeblock",
        help="Skip aligning a routine timeblock",
    )
    args = parser.parse_args()

    token = os.getenv("TEST_FCM_DEVICE_TOKEN", "").strip()
    if args.send and not token:
        print("Set TEST_FCM_DEVICE_TOKEN in .env before using --send.")
        sys.exit(1)

    if args.align_timeblock:
        aligned_user_id = _align_test_timeblock()
    else:
        aligned_user_id = None

    device_token = token or "test-token-placeholder"
    if token or not args.send:
        _ensure_test_push_device(device_token, user_id=aligned_user_id)

    asyncio.run(_send_targets(send=args.send))


if __name__ == "__main__":
    main()
