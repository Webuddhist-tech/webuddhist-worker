from datetime import datetime, timezone

import pytest

from worker_api.notifications.services.reminder_schedule_service import compute_next_trigger_at


class TestComputeNextTriggerAt:
    def test_returns_next_time_today(self):
        routine = {"times": ["09:00", "18:00"]}
        after = datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc)
        result = compute_next_trigger_at(routine, "UTC", after=after)
        assert result == datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)

    def test_returns_next_day_when_all_times_passed(self):
        routine = {"times": ["09:00"]}
        after = datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc)
        result = compute_next_trigger_at(routine, "UTC", after=after)
        assert result == datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc)

    def test_respects_days_of_week(self):
        routine = {"times": ["09:00"], "days_of_week": [0]}
        # 2026-06-22 is Monday
        after = datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc)
        result = compute_next_trigger_at(routine, "UTC", after=after)
        assert result == datetime(2026, 6, 29, 9, 0, tzinfo=timezone.utc)

    def test_invalid_timezone_raises(self):
        routine = {"times": ["09:00"]}
        with pytest.raises(Exception) as exc_info:
            compute_next_trigger_at(routine, "Not/A_Timezone")
        assert exc_info.value.status_code == 400
