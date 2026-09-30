"""Tests for SQS audio job consumer."""
import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from worker_api.audio.enums import AudioJobStatus, MonlamVoiceName, PlanAudioType
from worker_api.audio.services.audio_job_consumer import (
    _error_detail,
    _normalize_result,
    _parse_audio_type,
    _parse_uuid,
    _parse_voice_name,
    process_audio_job_message,
    run_audio_sqs_consumer,
)


class TestParseHelpers:
    def test_parse_uuid_none_and_empty(self):
        assert _parse_uuid(None) is None
        assert _parse_uuid("") is None

    def test_parse_uuid_valid(self):
        value = uuid4()
        assert _parse_uuid(str(value)) == value

    def test_parse_audio_type_defaults_and_enum(self):
        assert _parse_audio_type(None) == PlanAudioType.TEXT_READING
        assert _parse_audio_type("") == PlanAudioType.TEXT_READING
        assert _parse_audio_type(PlanAudioType.RECITATION) == PlanAudioType.RECITATION
        assert _parse_audio_type("INSTRUCTION") == PlanAudioType.INSTRUCTION

    def test_parse_voice_name_defaults_and_enum(self):
        assert _parse_voice_name(None) == MonlamVoiceName.DOLKAR_LHASA_FEMALE
        assert _parse_voice_name("") == MonlamVoiceName.DOLKAR_LHASA_FEMALE
        assert _parse_voice_name(MonlamVoiceName.YANGCHEN_LHASA_FEMALE) == MonlamVoiceName.YANGCHEN_LHASA_FEMALE
        assert _parse_voice_name("yangchen_lhasa_female") == MonlamVoiceName.YANGCHEN_LHASA_FEMALE

    def test_normalize_result_success(self):
        assert _normalize_result({"s3_key": "a.wav", "audio_url": "u", "audio_duration_ms": 1}) == {
            "audio_url": "u",
            "audio_duration_ms": 1,
            "s3_key": "a.wav",
        }

    def test_normalize_result_rejects_non_dict(self):
        with pytest.raises(ValueError, match="no result"):
            _normalize_result([])

    def test_normalize_result_rejects_empty(self):
        with pytest.raises(ValueError, match="empty result"):
            _normalize_result({"audio_duration_ms": 1})

    def test_error_detail_http_dict_and_string(self):
        assert _error_detail(HTTPException(status_code=400, detail={"message": "bad"})) == "bad"
        assert _error_detail(HTTPException(status_code=400, detail={"code": "x"})) == "{'code': 'x'}"
        assert _error_detail(HTTPException(status_code=400, detail="plain")) == "plain"
        assert _error_detail(ValueError("oops")) == "oops"


class TestProcessAudioJobMessage:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_processes_day_job_successfully(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        day_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.side_effect = [
            {"job_id": str(job_id), "status": AudioJobStatus.PROCESSING.value},
            {"job_id": str(job_id), "status": AudioJobStatus.COMPLETED.value},
        ]
        mock_generate.return_value = {
            "audio_url": "https://example.com/a.wav",
            "audio_duration_ms": 1200,
            "s3_key": "audio/plan_days/a.wav",
        }

        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps(
                {
                    "job_id": str(job_id),
                    "day_id": str(day_id),
                    "sub_task_id": None,
                    "language": "bo",
                    "type": "TEXT_READING",
                    "voice_name": "dolkar_lhasa_female",
                }
            ),
        }

        await process_audio_job_message(message)

        assert mock_update_status.await_count == 2
        assert mock_update_status.await_args_list[0].kwargs["status"] == AudioJobStatus.PROCESSING
        assert mock_update_status.await_args_list[1].kwargs["status"] == AudioJobStatus.COMPLETED
        mock_generate.assert_awaited_once()
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_marks_failed_on_generation_error(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.side_effect = [
            {"job_id": str(job_id), "status": AudioJobStatus.PROCESSING.value},
            {"job_id": str(job_id), "status": AudioJobStatus.FAILED.value},
        ]
        mock_generate.side_effect = HTTPException(status_code=404, detail={"message": "Sub task not found"})

        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps(
                {
                    "job_id": str(job_id),
                    "day_id": None,
                    "sub_task_id": str(uuid4()),
                    "language": "en",
                    "type": "TEXT_READING",
                    "voice_name": "dolkar_lhasa_female",
                }
            ),
        }

        await process_audio_job_message(message)

        assert mock_update_status.await_args_list[-1].kwargs["status"] == AudioJobStatus.FAILED
        assert "Sub task not found" in mock_update_status.await_args_list[-1].kwargs["error_message"]
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_skips_completed_job(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.COMPLETED.value}

        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps({"job_id": str(job_id), "language": "en"}),
        }

        await process_audio_job_message(message)

        mock_generate.assert_not_called()
        mock_update_status.assert_not_awaited()
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_skips_when_claim_conflicts(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.side_effect = HTTPException(
            status_code=409,
            detail={"error": "Bad request", "message": "Audio job is already being processed"},
        )

        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps(
                {
                    "job_id": str(job_id),
                    "day_id": str(uuid4()),
                    "language": "bo",
                    "type": "TEXT_READING",
                }
            ),
        }

        await process_audio_job_message(message)

        mock_generate.assert_not_called()
        mock_update_status.assert_awaited_once()
        assert mock_update_status.await_args.kwargs["status"] == AudioJobStatus.PROCESSING
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_skips_when_claim_returns_terminal(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.return_value = {
            "job_id": str(job_id),
            "status": AudioJobStatus.COMPLETED.value,
        }

        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps(
                {
                    "job_id": str(job_id),
                    "day_id": str(uuid4()),
                    "language": "bo",
                    "type": "TEXT_READING",
                }
            ),
        }

        await process_audio_job_message(message)

        mock_generate.assert_not_called()
        mock_update_status.assert_awaited_once()
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.parse_audio_job_message_body", return_value=None)
    async def test_deletes_invalid_body(self, _parse, mock_delete):
        await process_audio_job_message({"ReceiptHandle": "abc", "Body": "bad"})
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.parse_audio_job_message_body")
    async def test_deletes_invalid_job_id(self, mock_parse, mock_delete):
        mock_parse.return_value = {"job_id": ""}
        await process_audio_job_message({"ReceiptHandle": "abc", "Body": "{}"})
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_deletes_when_job_missing(self, mock_get_job, mock_delete):
        job_id = uuid4()
        mock_get_job.return_value = None
        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps({"job_id": str(job_id), "language": "en"}),
        }
        await process_audio_job_message(message)
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_fails_when_language_missing(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.side_effect = [
            {"job_id": str(job_id), "status": AudioJobStatus.PROCESSING.value},
            {"job_id": str(job_id), "status": AudioJobStatus.FAILED.value},
        ]
        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps(
                {
                    "job_id": str(job_id),
                    "day_id": str(uuid4()),
                    "language": "",
                    "type": "TEXT_READING",
                }
            ),
        }

        await process_audio_job_message(message)

        assert mock_update_status.await_args_list[-1].kwargs["status"] == AudioJobStatus.FAILED
        assert "language is required" in mock_update_status.await_args_list[-1].kwargs["error_message"]
        mock_generate.assert_not_called()
        mock_delete.assert_called_once_with("abc")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.delete_audio_job_message")
    @patch("worker_api.audio.services.audio_job_consumer.generate_plan_audio_service", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.update_audio_job_status", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.get_audio_job_status", new_callable=AsyncMock)
    async def test_fails_when_target_ids_missing(
        self,
        mock_get_job,
        mock_update_status,
        mock_generate,
        mock_delete,
    ):
        job_id = uuid4()
        mock_get_job.return_value = {"job_id": str(job_id), "status": AudioJobStatus.PENDING.value}
        mock_update_status.side_effect = [
            {"job_id": str(job_id), "status": AudioJobStatus.PROCESSING.value},
            {"job_id": str(job_id), "status": AudioJobStatus.FAILED.value},
        ]
        message = {
            "ReceiptHandle": "abc",
            "Body": json.dumps({"job_id": str(job_id), "language": "en"}),
        }

        await process_audio_job_message(message)

        assert mock_update_status.await_args_list[-1].kwargs["status"] == AudioJobStatus.FAILED
        assert "Exactly one of day_id or sub_task_id" in mock_update_status.await_args_list[-1].kwargs["error_message"]
        mock_generate.assert_not_called()


class TestRunAudioSqsConsumer:
    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer._POLL_IDLE_SECONDS", 0.01)
    @patch("worker_api.audio.services.audio_job_consumer.is_audio_sqs_poll_enabled", return_value=False)
    async def test_idle_loop_until_stopped(self, _poll_enabled):
        stop_event = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.create_task(stop_soon())
        await run_audio_sqs_consumer(stop_event)

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer.process_audio_job_message", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_job_consumer.receive_audio_job_messages")
    @patch("worker_api.audio.services.audio_job_consumer.is_audio_sqs_poll_enabled", return_value=True)
    async def test_processes_received_messages(self, _poll_enabled, mock_receive, mock_process):
        stop_event = asyncio.Event()
        mock_receive.side_effect = [[], [{"ReceiptHandle": "1"}, {"ReceiptHandle": "2"}]]

        async def process_then_stop(_message):
            stop_event.set()

        mock_process.side_effect = process_then_stop
        await run_audio_sqs_consumer(stop_event)
        mock_process.assert_awaited_once()
        assert mock_receive.call_count >= 2

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_job_consumer._POLL_ERROR_SECONDS", 0.01)
    @patch("worker_api.audio.services.audio_job_consumer.receive_audio_job_messages", side_effect=RuntimeError("boom"))
    @patch("worker_api.audio.services.audio_job_consumer.is_audio_sqs_poll_enabled", return_value=True)
    async def test_error_loop_until_stopped(self, _poll_enabled, _receive):
        stop_event = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.create_task(stop_soon())
        await run_audio_sqs_consumer(stop_event)
