from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from worker_api.notifications.services.push import apns_client

APNS_CONFIG = {
    "APNS_AUTH_KEY": "secrets/apns.p8",
    "APNS_KEY_ID": "KEY123",
    "APNS_TEAM_ID": "TEAM123",
    "APNS_BUNDLE_ID": "com.webuddhist.app",
}


@pytest.fixture(autouse=True)
def reset_jwt_cache():
    apns_client._cached_jwt = None
    apns_client._jwt_expires_at = 0.0
    yield
    apns_client._cached_jwt = None
    apns_client._jwt_expires_at = 0.0


def _patch_config():
    return patch.object(apns_client, "get", side_effect=lambda key: APNS_CONFIG[key])


def _patch_async_client(response: httpx.Response):
    http_client = AsyncMock()
    http_client.post.return_value = response
    context = MagicMock()
    context.__aenter__.return_value = http_client
    context.__aexit__.return_value = False
    return patch.object(apns_client.httpx, "AsyncClient", return_value=context), http_client


class TestGetApnsJwt:
    def test_signs_token_with_key_id_and_team_id(self):
        with _patch_config(), patch.object(
            apns_client, "load_secret_value", return_value="-----BEGIN PRIVATE KEY-----"
        ) as mock_load_secret, patch.object(
            apns_client.jwt, "encode", return_value="signed-jwt"
        ) as mock_encode:
            token = apns_client._get_apns_jwt()

        assert token == "signed-jwt"
        mock_load_secret.assert_called_once_with("secrets/apns.p8")
        assert mock_encode.call_args.kwargs["algorithm"] == "ES256"
        assert mock_encode.call_args.kwargs["headers"] == {"alg": "ES256", "kid": "KEY123"}
        assert mock_encode.call_args.args[0]["iss"] == "TEAM123"

    def test_reuses_cached_token_until_expiry(self):
        with _patch_config(), patch.object(
            apns_client, "load_secret_value", return_value="key"
        ), patch.object(apns_client.jwt, "encode", return_value="signed-jwt") as mock_encode:
            first = apns_client._get_apns_jwt()
            second = apns_client._get_apns_jwt()

        assert first == second == "signed-jwt"
        mock_encode.assert_called_once()

    def test_resigns_when_cached_token_is_near_expiry(self):
        apns_client._cached_jwt = "stale-jwt"
        apns_client._jwt_expires_at = 0.0

        with _patch_config(), patch.object(
            apns_client, "load_secret_value", return_value="key"
        ), patch.object(apns_client.jwt, "encode", return_value="fresh-jwt") as mock_encode:
            token = apns_client._get_apns_jwt()

        assert token == "fresh-jwt"
        mock_encode.assert_called_once()


class TestApnsHost:
    def test_uses_sandbox_host_when_enabled(self):
        with patch.object(apns_client, "get_bool", return_value=True):
            assert apns_client._apns_host() == "https://api.sandbox.push.apple.com"

    def test_uses_production_host_by_default(self):
        with patch.object(apns_client, "get_bool", return_value=False):
            assert apns_client._apns_host() == "https://api.push.apple.com"


class TestSendApnsNotification:
    @pytest.mark.asyncio
    async def test_posts_alert_payload_to_device(self):
        response = httpx.Response(200, request=httpx.Request("POST", "https://api.push.apple.com"))
        client_patch, http_client = _patch_async_client(response)

        with client_patch, _patch_config(), patch.object(
            apns_client, "get_bool", return_value=False
        ), patch.object(apns_client, "_get_apns_jwt", return_value="signed-jwt"):
            await apns_client.send_apns_notification(
                device_token="device-token",
                title="Title",
                body="Body",
            )

        assert http_client.post.await_args.args[0] == (
            "https://api.push.apple.com/3/device/device-token"
        )
        assert http_client.post.await_args.kwargs["json"] == {
            "aps": {"alert": {"title": "Title", "body": "Body"}, "sound": "default"}
        }
        headers = http_client.post.await_args.kwargs["headers"]
        assert headers["authorization"] == "bearer signed-jwt"
        assert headers["apns-topic"] == "com.webuddhist.app"
        assert headers["apns-push-type"] == "alert"

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self):
        response = httpx.Response(
            410,
            text="BadDeviceToken",
            request=httpx.Request("POST", "https://api.push.apple.com"),
        )
        client_patch, _ = _patch_async_client(response)

        with client_patch, _patch_config(), patch.object(
            apns_client, "get_bool", return_value=False
        ), patch.object(apns_client, "_get_apns_jwt", return_value="signed-jwt"):
            with pytest.raises(httpx.HTTPStatusError):
                await apns_client.send_apns_notification(
                    device_token="device-token",
                    title="Title",
                    body="Body",
                )
