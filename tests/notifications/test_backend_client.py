from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from worker_api.notifications.services import backend_client


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "http://backend.test/internal"),
    )


def _patch_config(backend_url: str = "http://backend.test", dispatch_token: str = "dispatch-token"):
    values = {
        "BACKEND_API_URL": backend_url,
        "NOTIFICATION_DISPATCH_SECRET_TOKEN": dispatch_token,
    }
    return patch.object(backend_client, "get", side_effect=lambda key: values[key])


def _patch_async_client(response: httpx.Response):
    """Patch httpx.AsyncClient so the request helper returns a canned response."""
    http_client = AsyncMock()
    http_client.get.return_value = response
    http_client.post.return_value = response
    context = MagicMock()
    context.__aenter__.return_value = http_client
    context.__aexit__.return_value = False
    return patch.object(backend_client.httpx, "AsyncClient", return_value=context), http_client


class TestBackendHeaders:
    def test_uses_configured_dispatch_token(self):
        with patch.object(backend_client, "get", return_value="secret-token"):
            assert backend_client._backend_headers() == {"X-Dispatch-Token": "secret-token"}

    def test_raises_when_dispatch_token_missing(self):
        with patch.object(backend_client, "get", return_value=""):
            with pytest.raises(RuntimeError, match="NOTIFICATION_DISPATCH_SECRET_TOKEN"):
                backend_client._backend_headers()


class TestBackendUrl:
    def test_strips_trailing_slash(self):
        with patch.object(backend_client, "get", return_value="http://backend.test/api/v1/"):
            assert backend_client._backend_url() == "http://backend.test/api/v1"

    def test_raises_when_backend_url_missing(self):
        with patch.object(backend_client, "get", return_value=""):
            with pytest.raises(RuntimeError, match="BACKEND_API_URL"):
                backend_client._backend_url()


class TestFetchRoutineNotificationTargets:
    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        user_id = uuid4()
        source_id = uuid4()
        response = _json_response(
            {
                "generated_at": "2026-01-01T06:00:00Z",
                "matched_time_utc": "06:00",
                "groups": [
                    {
                        "session_type": "PLAN",
                        "source_id": str(source_id),
                        "users": [
                            {
                                "user_id": str(user_id),
                                "notification": {"title": "Day 1", "body": "Practice"},
                                "push_devices": [{"token": "token-1", "platform": "android"}],
                            }
                        ],
                    }
                ],
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_routine_notification_targets()

        assert targets.matched_time_utc == "06:00"
        assert targets.groups[0].users[0].user_id == user_id
        assert http_client.get.await_args.args[0] == (
            "http://backend.test/internal/routine-notification-targets"
        )
        assert http_client.get.await_args.kwargs["headers"] == {"X-Dispatch-Token": "dispatch-token"}

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self):
        client_patch, _ = _patch_async_client(_json_response({}, status_code=500))

        with client_patch, _patch_config():
            with pytest.raises(httpx.HTTPStatusError):
                await backend_client.fetch_routine_notification_targets()


class TestFetchPlanNotificationContent:
    @pytest.mark.asyncio
    async def test_sends_user_and_plan_params(self):
        user_id = uuid4()
        plan_id = uuid4()
        response = _json_response(
            {"title": "Day 3", "body": "Keep going", "image_url": "https://cdn.test/plan.png"}
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            content = await backend_client.fetch_plan_notification_content(
                user_id=user_id,
                plan_id=plan_id,
            )

        assert content.title == "Day 3"
        assert content.image_url == "https://cdn.test/plan.png"
        assert http_client.get.await_args.kwargs["params"] == {
            "user_id": str(user_id),
            "plan_id": str(plan_id),
        }


class TestFetchChatNotificationTargets:
    @pytest.mark.asyncio
    async def test_sends_pagination_params_and_parses_recipients(self):
        message_id = uuid4()
        room_id = uuid4()
        sender_id = uuid4()
        device_id = uuid4()
        response = _json_response(
            {
                "message_id": str(message_id),
                "room_id": str(room_id),
                "sender_id": str(sender_id),
                "chat_kind": "GROUP",
                "group_id": None,
                "title": "Sangha",
                "body": "Alice: Hello",
                "recipients": [
                    {
                        "user_id": str(uuid4()),
                        "push_devices": [
                            {"id": str(device_id), "token": "token-1", "platform": "ios"}
                        ],
                    }
                ],
                "skip": 100,
                "limit": 50,
                "total": 120,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_chat_notification_targets(
                message_id=message_id,
                skip=100,
                limit=50,
            )

        assert targets.total == 120
        assert targets.recipients[0].push_devices[0].id == device_id
        assert http_client.get.await_args.args[0] == (
            f"http://backend.test/internal/chat-notification-targets/{message_id}"
        )
        assert http_client.get.await_args.kwargs["params"] == {"skip": 100, "limit": 50}

    @pytest.mark.asyncio
    async def test_defaults_to_first_page(self):
        message_id = uuid4()
        response = _json_response(
            {
                "message_id": str(message_id),
                "room_id": str(uuid4()),
                "sender_id": str(uuid4()),
                "chat_kind": "PRIVATE",
                "title": "Alice",
                "body": "Hi",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_chat_notification_targets(message_id=message_id)

        assert http_client.get.await_args.kwargs["params"] == {"skip": 0, "limit": 100}


class TestFetchGroupPostNotificationTargets:
    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        post_id = uuid4()
        device_id = uuid4()
        response = _json_response(
            {
                "post_id": str(post_id),
                "group_id": str(uuid4()),
                "author_id": str(uuid4()),
                "title": "Sangha",
                "body": "Alice shared a new post",
                "recipients": [
                    {
                        "user_id": str(uuid4()),
                        "push_devices": [
                            {"id": str(device_id), "token": "token-1", "platform": "ios"}
                        ],
                    }
                ],
                "skip": 100,
                "limit": 50,
                "total": 120,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_group_post_notification_targets(
                post_id=post_id,
                skip=100,
                limit=50,
            )

        assert targets.total == 120
        assert targets.recipients[0].push_devices[0].id == device_id
        assert http_client.get.await_args.args[0] == (
            f"http://backend.test/internal/group-post-notification-targets/{post_id}"
        )
        assert http_client.get.await_args.kwargs["params"] == {"skip": 100, "limit": 50}

    @pytest.mark.asyncio
    async def test_defaults_to_first_page(self):
        post_id = uuid4()
        response = _json_response(
            {
                "post_id": str(post_id),
                "group_id": str(uuid4()),
                "author_id": str(uuid4()),
                "title": "Sangha",
                "body": "Alice shared a new post",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_group_post_notification_targets(post_id=post_id)

        assert http_client.get.await_args.kwargs["params"] == {"skip": 0, "limit": 100}


class TestFetchEventNotificationTargets:
    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        event_id = uuid4()
        device_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "group_id": str(uuid4()),
                "author_id": str(uuid4()),
                "title": "Sangha",
                "body": "Full Moon Meditation",
                "recipients": [
                    {
                        "user_id": str(uuid4()),
                        "push_devices": [
                            {"id": str(device_id), "token": "token-1", "platform": "ios"}
                        ],
                    }
                ],
                "skip": 100,
                "limit": 50,
                "total": 120,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_event_notification_targets(
                event_id=event_id,
                skip=100,
                limit=50,
            )

        assert targets.total == 120
        assert targets.recipients[0].push_devices[0].id == device_id
        assert http_client.get.await_args.args[0] == (
            f"http://backend.test/internal/event-notification-targets/{event_id}"
        )
        assert http_client.get.await_args.kwargs["params"] == {"skip": 100, "limit": 50}

    @pytest.mark.asyncio
    async def test_defaults_to_first_page(self):
        event_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "group_id": str(uuid4()),
                "author_id": str(uuid4()),
                "title": "Sangha",
                "body": "Full Moon Meditation",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_event_notification_targets(event_id=event_id)

        assert http_client.get.await_args.kwargs["params"] == {"skip": 0, "limit": 100}


class TestFetchEventReminderTargets:
    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        event_id = uuid4()
        device_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "reminder_type": "T_MINUS_10",
                "title": "Sangha",
                "body": "Starting in 10 minutes",
                "recipients": [
                    {
                        "user_id": str(uuid4()),
                        "push_devices": [
                            {"id": str(device_id), "token": "token-1", "platform": "ios"}
                        ],
                    }
                ],
                "skip": 100,
                "limit": 50,
                "total": 120,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_event_reminder_targets(
                event_id=event_id,
                reminder_type="T_MINUS_10",
                skip=100,
                limit=50,
            )

        assert targets.total == 120
        assert targets.reminder_type == "T_MINUS_10"
        assert targets.recipients[0].push_devices[0].id == device_id
        assert http_client.get.await_args.args[0] == (
            f"http://backend.test/internal/event-reminder-targets/{event_id}"
        )
        assert http_client.get.await_args.kwargs["params"] == {
            "reminder_type": "T_MINUS_10",
            "skip": 100,
            "limit": 50,
        }

    @pytest.mark.asyncio
    async def test_defaults_to_first_page(self):
        event_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "reminder_type": "T_ZERO",
                "title": "Sangha",
                "body": "Starting now",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_event_reminder_targets(
                event_id=event_id, reminder_type="T_ZERO"
            )

        assert http_client.get.await_args.kwargs["params"] == {
            "reminder_type": "T_ZERO",
            "skip": 0,
            "limit": 100,
        }

    @pytest.mark.asyncio
    async def test_includes_fire_at_when_provided(self):
        event_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "reminder_type": "T_ZERO",
                "title": "Sangha",
                "body": "Starting now",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_event_reminder_targets(
                event_id=event_id,
                reminder_type="T_ZERO",
                fire_at="2026-06-15T05:50:00+00:00",
            )

        assert http_client.get.await_args.kwargs["params"] == {
            "reminder_type": "T_ZERO",
            "fire_at": "2026-06-15T05:50:00+00:00",
            "skip": 0,
            "limit": 100,
        }


class TestFetchEventAnnouncementTargets:
    """The announcement consumer mocks _fetch_all_announcement_targets, so
    nothing else exercises this route, its query parameters or its response
    shape. A drift in any of them would surface only as every announcement
    retrying forever in production."""

    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        event_id = uuid4()
        user_id = uuid4()
        device_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "audience": "PARTICIPANTS",
                "recipients": [
                    {
                        "user_id": str(user_id),
                        "push_devices": [
                            {"id": str(device_id), "token": "token-1", "platform": "ios"}
                        ],
                    }
                ],
                "skip": 100,
                "limit": 50,
                "total": 120,
                "has_more": True,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_event_announcement_targets(
                event_id=event_id,
                audience="PARTICIPANTS",
                skip=100,
                limit=50,
            )

        assert targets.event_id == event_id
        assert targets.audience == "PARTICIPANTS"
        assert targets.total == 120
        assert targets.has_more is True
        assert targets.recipients[0].user_id == user_id
        assert targets.recipients[0].push_devices[0].id == device_id
        assert targets.recipients[0].push_devices[0].token == "token-1"
        assert http_client.get.await_args.args[0] == (
            f"http://backend.test/internal/event-announcement-targets/{event_id}"
        )
        assert http_client.get.await_args.kwargs["params"] == {
            "audience": "PARTICIPANTS",
            "skip": 100,
            "limit": 50,
        }
        assert http_client.get.await_args.kwargs["headers"] == {
            "X-Dispatch-Token": "dispatch-token"
        }

    @pytest.mark.asyncio
    async def test_defaults_to_first_page(self):
        event_id = uuid4()
        response = _json_response(
            {
                "event_id": str(event_id),
                "audience": "GROUP",
                "recipients": [],
                "skip": 0,
                "limit": 100,
                "total": 0,
                "has_more": False,
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            await backend_client.fetch_event_announcement_targets(
                event_id=event_id, audience="GROUP"
            )

        assert http_client.get.await_args.kwargs["params"] == {
            "audience": "GROUP",
            "skip": 0,
            "limit": 100,
        }

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self):
        event_id = uuid4()
        response = _json_response({"detail": "Event not found"}, status_code=404)
        client_patch, _ = _patch_async_client(response)

        with client_patch, _patch_config():
            with pytest.raises(httpx.HTTPStatusError):
                await backend_client.fetch_event_announcement_targets(
                    event_id=event_id, audience="PARTICIPANTS"
                )


class TestFetchVerseOfDayNotificationTargets:
    @pytest.mark.asyncio
    async def test_returns_parsed_targets(self):
        user_id = uuid4()
        response = _json_response(
            {
                "generated_at": "2026-01-01T10:00:00Z",
                "users": [
                    {
                        "user_id": str(user_id),
                        "notification": {"title": "WebBuddhist", "body": "May all beings be happy."},
                        "push_devices": [{"token": "token-1", "platform": "android"}],
                    }
                ],
            }
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            targets = await backend_client.fetch_verse_of_day_notification_targets()

        assert targets.users[0].user_id == user_id
        assert targets.users[0].notification.body == "May all beings be happy."
        assert http_client.get.await_args.args[0] == (
            "http://backend.test/internal/verse-of-day-notification-targets"
        )
        assert http_client.get.await_args.kwargs["headers"] == {"X-Dispatch-Token": "dispatch-token"}

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self):
        client_patch, _ = _patch_async_client(_json_response({}, status_code=500))

        with client_patch, _patch_config():
            with pytest.raises(httpx.HTTPStatusError):
                await backend_client.fetch_verse_of_day_notification_targets()


class TestDeactivatePushDevice:
    @pytest.mark.asyncio
    async def test_posts_push_device_id(self):
        push_device_id = uuid4()
        response = _json_response(
            {"push_device_id": str(push_device_id), "deactivated": True}
        )
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config():
            result = await backend_client.deactivate_push_device(push_device_id=push_device_id)

        assert result.deactivated is True
        assert result.push_device_id == push_device_id
        assert http_client.post.await_args.args[0] == (
            "http://backend.test/internal/push-devices/deactivate"
        )
        assert http_client.post.await_args.kwargs["json"] == {
            "push_device_id": str(push_device_id)
        }
