"""Tests for audio backend HTTP client."""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from worker_api.audio.enums import AudioJobStatus
from worker_api.audio.services.backend_client import (
    _backend_url,
    _dispatch_headers,
    _raise_for_backend_response,
    apply_day_audio_generation_result,
    apply_sub_task_audio_generation_result,
    get_audio_job_status,
    get_day_audio_generation_payload,
    get_sub_task_audio_generation_payload,
    update_audio_job_status,
)


class TestBackendUrl:
    @patch("worker_api.audio.services.backend_client.get")
    def test_returns_stripped_url(self, mock_get):
        mock_get.return_value = "http://backend.example/api/v1/"
        assert _backend_url() == "http://backend.example/api/v1"

    @patch("worker_api.audio.services.backend_client.get")
    def test_raises_when_missing(self, mock_get):
        mock_get.return_value = ""
        with pytest.raises(RuntimeError, match="BACKEND_API_URL"):
            _backend_url()


class TestDispatchHeaders:
    @patch("worker_api.audio.services.backend_client.get")
    def test_returns_headers(self, mock_get):
        mock_get.return_value = "secret-token"
        assert _dispatch_headers() == {"X-Dispatch-Token": "secret-token"}

    @patch("worker_api.audio.services.backend_client.get")
    def test_raises_when_missing(self, mock_get):
        mock_get.return_value = ""
        with pytest.raises(RuntimeError, match="NOTIFICATION_DISPATCH_SECRET_TOKEN"):
            _dispatch_headers()


class TestRaiseForBackendResponse:
    def test_success_noop(self):
        response = MagicMock()
        response.is_success = True
        _raise_for_backend_response(response)

    def test_raises_with_json_detail(self):
        response = MagicMock()
        response.is_success = False
        response.status_code = 400
        response.json.return_value = {"message": "bad request"}

        with pytest.raises(HTTPException) as exc_info:
            _raise_for_backend_response(response)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == {"message": "bad request"}

    def test_raises_with_text_when_json_invalid(self):
        response = MagicMock()
        response.is_success = False
        response.status_code = 500
        response.json.side_effect = ValueError("not json")
        response.text = "server error"

        with pytest.raises(HTTPException) as exc_info:
            _raise_for_backend_response(response)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "server error"


def _mock_async_client(response: MagicMock):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.patch = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestGetAudioJobStatus:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_returns_json_on_success(self, mock_client_cls, _url, _headers):
        job_id = uuid4()
        response = MagicMock()
        response.status_code = 200
        response.is_success = True
        response.json.return_value = {"job_id": str(job_id), "status": "PENDING"}
        mock_client_cls.return_value = _mock_async_client(response)

        result = await get_audio_job_status(job_id)

        assert result["status"] == "PENDING"

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_returns_none_on_404(self, mock_client_cls, _url, _headers):
        response = MagicMock()
        response.status_code = 404
        response.is_success = False
        mock_client_cls.return_value = _mock_async_client(response)

        result = await get_audio_job_status(uuid4())

        assert result is None


class TestUpdateAudioJobStatus:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_sends_status_payload(self, mock_client_cls, _url, _headers):
        job_id = uuid4()
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"status": "COMPLETED"}
        client = _mock_async_client(response)
        mock_client_cls.return_value = client

        result = await update_audio_job_status(
            job_id=job_id,
            status=AudioJobStatus.COMPLETED,
            result={"s3_key": "a.wav"},
            error_message=None,
        )

        assert result["status"] == "COMPLETED"
        payload = client.patch.await_args.kwargs["json"]
        assert payload == {"status": "completed", "result": {"s3_key": "a.wav"}}

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_includes_error_message(self, mock_client_cls, _url, _headers):
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"status": "FAILED"}
        client = _mock_async_client(response)
        mock_client_cls.return_value = client

        await update_audio_job_status(
            job_id=uuid4(),
            status=AudioJobStatus.FAILED,
            error_message="boom",
        )

        payload = client.patch.await_args.kwargs["json"]
        assert payload == {"status": "failed", "error_message": "boom"}


class TestGenerationPayloads:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_get_day_payload(self, mock_client_cls, _url, _headers):
        day_id = uuid4()
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"id": str(day_id), "subtasks": []}
        mock_client_cls.return_value = _mock_async_client(response)

        result = await get_day_audio_generation_payload(day_id)

        assert result["id"] == str(day_id)

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_get_subtask_payload(self, mock_client_cls, _url, _headers):
        sub_task_id = uuid4()
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"id": str(sub_task_id), "content": "hi"}
        mock_client_cls.return_value = _mock_async_client(response)

        result = await get_sub_task_audio_generation_payload(sub_task_id)

        assert result["content"] == "hi"


class TestApplyGenerationResults:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_apply_day_result(self, mock_client_cls, _url, _headers):
        day_id = uuid4()
        response = MagicMock()
        response.is_success = True
        client = _mock_async_client(response)
        mock_client_cls.return_value = client

        await apply_day_audio_generation_result(
            day_id=day_id,
            audio_key="audio/day.wav",
            duration_ms=1000,
            file_size_bytes=2000,
            timestamps=[{"sub_task_id": "1", "start_ms": 0, "end_ms": 1000}],
        )

        client.post.assert_awaited_once()
        payload = client.post.await_args.kwargs["json"]
        assert payload["audio_key"] == "audio/day.wav"
        assert payload["duration_ms"] == 1000
        assert payload["file_size_bytes"] == 2000

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.backend_client._dispatch_headers", return_value={"X-Dispatch-Token": "t"})
    @patch("worker_api.audio.services.backend_client._backend_url", return_value="http://backend")
    @patch("worker_api.audio.services.backend_client.httpx.AsyncClient")
    async def test_apply_subtask_result(self, mock_client_cls, _url, _headers):
        sub_task_id = uuid4()
        response = MagicMock()
        response.is_success = True
        client = _mock_async_client(response)
        mock_client_cls.return_value = client

        await apply_sub_task_audio_generation_result(
            sub_task_id=sub_task_id,
            audio_key="audio/sub.wav",
            duration_ms=500,
        )

        payload = client.post.await_args.kwargs["json"]
        assert payload == {"audio_key": "audio/sub.wav", "duration_ms": 500}
