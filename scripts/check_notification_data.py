from worker_api.config import get
from worker_api.db.database import SessionLocal
from sqlalchemy import text

print("DATABASE_URL:", get("DATABASE_URL")[:50])

with SessionLocal() as db:
    queries = {
        "pending_reminders": "SELECT COUNT(*) FROM upcoming_reminders WHERE status='pending'",
        "timeblocks": "SELECT COUNT(*) FROM routine_time_blocks WHERE deleted_at IS NULL AND notification_enabled",
        "push_devices": "SELECT COUNT(*) FROM push_device_tokens WHERE is_active",
        "sessions": "SELECT COUNT(*) FROM routine_sessions",
    }
    for label, query in queries.items():
        try:
            with SessionLocal() as session:
                count = session.execute(text(query)).scalar()
                print(f"{label}: {count}")
        except Exception as exc:
            print(f"{label}: ERROR {exc}")

    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT rtb.time, rtb.created_at, rs.session_type, rs.source_id, r.user_id
                    FROM routine_time_blocks rtb
                    JOIN routines r ON r.id = rtb.routine_id
                    JOIN routine_sessions rs ON rs.time_block_id = rtb.id
                    WHERE rtb.deleted_at IS NULL AND rtb.notification_enabled AND r.deleted_at IS NULL
                    LIMIT 5
                    """
                )
            ).all()
            print("sample_timeblocks:", len(rows))
            for row in rows:
                print(" ", row)
    except Exception as exc:
        print("sample_timeblocks: ERROR", exc)
