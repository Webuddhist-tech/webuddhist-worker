"""The SQS poll loop that drives every event notification.

Each test arranges for stop_event to be set during the iteration it cares
about, so the loop runs a bounded number of passes and then returns. Nothing
here patches asyncio itself: the loop waits on stop_event, so setting the
event is enough to make every wait return at once.

A regression in this loop is silent - the worker stays up while nothing gets
delivered - which is why the branches are pinned down here.
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


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_an_already_stopped_event_never_polls(self):
        stop_event = asyncio.Event()
        stop_event.set()

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled"
        ) as mock_enabled, patch(
            f"{CONSUMER}.receive_event_notification_messages"
        ) as mock_receive:
            await run_event_notification_sqs_consumer(stop_event)

        mock_enabled.assert_not_called()
        mock_receive.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_poll_while_polling_is_disabled(self):
        """The idle wait is on stop_event rather than a bare sleep, so a
        shutdown during the quiet period is noticed at once."""
        stop_event = asyncio.Event()

        def _disabled():
            stop_event.set()
            return False

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", side_effect=_disabled
        ), patch(f"{CONSUMER}.receive_event_notification_messages") as mock_receive:
            await run_event_notification_sqs_consumer(stop_event)

        mock_receive.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_empty_poll_just_comes_around_again(self):
        stop_event = asyncio.Event()

        def _receive():
            stop_event.set()
            return []

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.process_event_notification_message", new_callable=AsyncMock
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        mock_process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_every_message_in_a_batch(self):
        stop_event = asyncio.Event()
        polls = {"n": 0}

        def _receive():
            polls["n"] += 1
            if polls["n"] == 1:
                return [{"MessageId": "m1"}, {"MessageId": "m2"}]
            stop_event.set()
            return []

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ), patch(
            f"{CONSUMER}.process_event_notification_message", new_callable=AsyncMock
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        assert mock_process.await_count == 2

    @pytest.mark.asyncio
    async def test_stops_mid_batch_once_shutdown_is_requested(self):
        """A shutdown between two messages leaves the rest on the queue for
        the next worker rather than half-processing the batch."""
        stop_event = asyncio.Event()

        async def _process(_message):
            stop_event.set()

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages",
            side_effect=lambda: [{"MessageId": "m1"}, {"MessageId": "m2"}],
        ), patch(
            f"{CONSUMER}.process_event_notification_message", side_effect=_process
        ) as mock_process:
            await run_event_notification_sqs_consumer(stop_event)

        assert mock_process.call_count == 1

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
                return []
            return [{"MessageId": "m1"}]

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

        assert mock_process.await_count == 1
        assert polls["n"] == 2

    @pytest.mark.asyncio
    async def test_an_unexpected_error_does_not_take_down_the_loop(self):
        """One poisonous message must not stop every later notification."""
        stop_event = asyncio.Event()
        polls = {"n": 0}

        def _receive():
            polls["n"] += 1
            if polls["n"] >= 2:
                stop_event.set()
                return []
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

        assert polls["n"] == 2

    @pytest.mark.asyncio
    async def test_a_failing_receive_backs_off_instead_of_spinning(self):
        """Without the backoff wait a broken SQS client would busy-loop and
        burn the worker's CPU until it recovered."""
        stop_event = asyncio.Event()
        polls = {"n": 0}

        def _receive():
            polls["n"] += 1
            stop_event.set()
            raise RuntimeError("SQS unreachable")

        with patch(
            f"{CONSUMER}.is_event_notification_sqs_poll_enabled", return_value=True
        ), patch(
            f"{CONSUMER}.receive_event_notification_messages", side_effect=_receive
        ):
            await run_event_notification_sqs_consumer(stop_event)

        # Returned rather than spun: the backoff wait saw the stop and exited.
        assert polls["n"] == 1
