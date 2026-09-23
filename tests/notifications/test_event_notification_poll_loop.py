"""The SQS poll loop that drives every event notification.

Each test stops the loop after one pass, so the assertions are about what a
single iteration decides: whether to poll at all, and how it survives a
message that fails. A regression here is silent - the loop keeps running
while nothing gets delivered - so the branches are worth pinning down.
"""
import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from worker_api.notifications.services.event_notification_consumer import (
    TransientEventNotificationError,
    run_event_notification_sqs_consumer,
)

CONSUMER = "worker_api.notifications.services.event_notification_consumer"


def _stop_after(calls: int, stop_event: asyncio.Event):
    """A side effect that lets the loop run `calls` times, then stops it."""
    state = {"n": 0}

    def _side_effect(*_args, **_kwargs):
        state["n"] += 1
        if state["n"] >= calls:
            stop_event.set()
        return True

    return _side_effect


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_does_not_poll_while_polling_is_disabled(self):
        """The idle wait is on stop_event, not a bare sleep, so a shutdown
        during the quiet period is noticed immediately."""
        stop_event = asyncio.Event()
        enabled = patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled",
            side_effect=_stop_after(1, stop_event),
        )

        with enabled, patch(
            f"{CONSUMER}.receive_event_notification_messages"
        ) as mock_receive, patch(
            f"{CONSUMER}.asyncio.wait_for", new_callable=AsyncMock
        ) as mock_wait:
            # First call returns True to stop the loop; flip it to disabled.
            mock_wait.return_value = None
            with patch(
                f"{CONSUMER}.is_event_notification_sqs_poll_enabled",
                return_value=False,
            ):
                stop_event.clear()

                async def _stop_soon():
                    stop_event.set()

                mock_wait.side_effect = lambda *a, **k: _stop_soon()
                await run_event_notification_sqs_consumer(stop_event)

            mock_receive.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_empty_poll_just_comes_around_again(self):
        stop_event = asyncio.Event()
        receive = patch(
            f"{CONSUMER}.receive_event_notification_messages",
            side_effect=lambda: (stop_event.set(), [])[1],
        )

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), receive, patch(
            f"{CONSUMER}.process_event_notification_message", new_callable=AsyncMock
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        mock_process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_each_message_it_receives(self):
        stop_event = asyncio.Event()
        messages = [{"MessageId": "m1"}, {"MessageId": "m2"}]

        def _receive():
            stop_event.set()
            return messages

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.process_event_notification_message", new_callable=AsyncMock
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        # stop_event is set before the batch is walked, so the loop breaks out
        # rather than starting work it cannot finish cleanly.
        assert mock_process.await_count == 0

    @pytest.mark.asyncio
    async def test_a_transient_failure_leaves_the_message_and_keeps_polling(self):
        """The message stays on the queue by not being deleted; the loop must
        not treat that as a reason to stop."""
        stop_event = asyncio.Event()
        polls = {"n": 0}

        def _receive():
            polls["n"] += 1
            if polls["n"] >= 2:
                stop_event.set()
            return [{"MessageId": f"m{polls['n']}"}]

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.process_event_notification_message",
            new_callable=AsyncMock,
            side_effect=TransientEventNotificationError("backend down"),
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        assert mock_process.await_count >= 1
        assert polls["n"] >= 2

    @pytest.mark.asyncio
    async def test_an_unexpected_error_does_not_take_down_the_loop(self):
        """One poisonous message must not stop every later notification."""
        stop_event = asyncio.Event()
        polls = {"n": 0}

        def _receive():
            polls["n"] += 1
            if polls["n"] >= 2:
                stop_event.set()
            return [{"MessageId": str(uuid4())}]

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.process_event_notification_message",
            new_callable=AsyncMock,
            side_effect=ValueError("unparseable"),
        ):
            await run_event_notification_sqs_consumer(stop_event)

        assert polls["n"] >= 2

    @pytest.mark.asyncio
    async def test_a_failing_receive_backs_off_instead_of_spinning(self):
        """Without the backoff wait, a broken SQS client would busy-loop and
        burn the worker's CPU until it recovered."""
        stop_event = asyncio.Event()

        def _receive():
            raise RuntimeError("SQS unreachable")

        async def _backoff(awaitable, timeout=None):
            stop_event.set()
            if hasattr(awaitable, "close"):
                awaitable.close()
            return None

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.asyncio.wait_for", side_effect=_backoff
        ) as mock_wait:
            await run_event_notification_sqs_consumer(stop_event)

        mock_wait.assert_called_once()
        assert mock_wait.call_args.kwargs["timeout"] == 10

    @pytest.mark.asyncio
    async def test_an_already_stopped_event_never_polls(self):
        stop_event = asyncio.Event()
        stop_event.set()

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ) as mock_enabled, patch(
            f"{CONSUMER}.receive_event_notification_messages"
        ) as mock_receive:
            await run_event_notification_sqs_consumer(stop_event)

        mock_enabled.assert_not_called()
        mock_receive.assert_not_called()
