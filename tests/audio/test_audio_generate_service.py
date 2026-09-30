"""
Tests for audio generation service.
"""
import pytest
from uuid import uuid4
from unittest.mock import patch, MagicMock, AsyncMock

from worker_api.audio.enums import ContentType, PlanAudioType, MonlamVoiceName
from worker_api.audio.services.audio_generate_service import (
    generate_plan_audio_service,
    _generate_audio_segments,
    _build_subtask_timestamps,
    _build_combined_wav,
)


class TestGeneratePlanAudioService:
    """Tests for generate_plan_audio_service function."""

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.get_day_audio_generation_payload", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_generate_service._generate_audio_segments")
    @patch("worker_api.audio.services.audio_generate_service._build_subtask_timestamps")
    @patch("worker_api.audio.services.audio_generate_service._build_combined_wav")
    @patch("worker_api.audio.services.audio_generate_service._upload_day_audio")
    @patch("worker_api.audio.services.audio_generate_service.apply_day_audio_generation_result", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_day_audio_success(
        self,
        mock_presigned_url,
        mock_apply_result,
        mock_upload,
        mock_build_wav,
        mock_build_timestamps,
        mock_generate_segments,
        mock_get_day_payload,
    ):
        """Test successful audio generation for a plan day."""
        day_id = uuid4()
        plan_id = uuid4()
        sub_task_id = uuid4()

        mock_get_day_payload.return_value = {
            "id": str(day_id),
            "plan_id": str(plan_id),
            "subtasks": [{"id": str(sub_task_id), "content_type": "TEXT", "content": "Hello"}],
        }
        mock_generate_segments.return_value = ([b"audio_data"], [{"id": str(sub_task_id)}])
        mock_build_timestamps.return_value = (
            45000,
            [{"sub_task_id": str(sub_task_id), "start_ms": 0, "end_ms": 45000}],
        )
        mock_build_wav.return_value = (b"wav_data", 1000)
        mock_upload.return_value = "audio/test.wav"
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await generate_plan_audio_service(
            language="en",
            day_id=day_id,
            audio_type=PlanAudioType.TEXT_READING,
        )

        assert result["audio_url"] == "https://s3.example.com/audio.wav"
        assert result["audio_duration_ms"] == 45000
        assert result["s3_key"] == "audio/test.wav"
        mock_get_day_payload.assert_awaited_once_with(day_id=day_id)
        mock_apply_result.assert_awaited_once()
        mock_upload.assert_called_once()

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.get_day_audio_generation_payload", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_generate_service._generate_audio_segments")
    async def test_generate_day_audio_no_segments(
        self,
        mock_generate_segments,
        mock_get_day_payload,
    ):
        """Test audio generation returns empty when no segments are generated."""
        day_id = uuid4()
        mock_get_day_payload.return_value = {
            "id": str(day_id),
            "plan_id": str(uuid4()),
            "subtasks": [],
        }
        mock_generate_segments.return_value = ([], [])

        result = await generate_plan_audio_service(
            language="en",
            day_id=day_id,
        )

        assert result == []

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.get_sub_task_audio_generation_payload", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.apply_sub_task_audio_generation_result", new_callable=AsyncMock)
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_subtask_audio_success(
        self,
        mock_presigned_url,
        mock_apply_result,
        mock_upload,
        mock_tts,
        mock_get_subtask,
    ):
        """Test successful audio generation for a single subtask."""
        sub_task_id = uuid4()
        task_id = uuid4()

        mock_get_subtask.return_value = {
            "id": str(sub_task_id),
            "task_id": str(task_id),
            "content": "Test content",
            "content_type": "TEXT",
        }

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 1000
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await generate_plan_audio_service(
            language="en",
            sub_task_id=sub_task_id,
            audio_type=PlanAudioType.TEXT_READING,
        )

        assert "audio_url" in result
        assert "audio_duration_ms" in result
        assert "s3_key" in result
        mock_get_subtask.assert_awaited_once_with(sub_task_id=sub_task_id)
        mock_tts.assert_called_once()
        mock_upload.assert_called_once()
        mock_apply_result.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.get_sub_task_audio_generation_payload", new_callable=AsyncMock)
    async def test_generate_subtask_audio_not_found(self, mock_get_subtask):
        """Test error when subtask is not found on backend."""
        from fastapi import HTTPException

        sub_task_id = uuid4()
        mock_get_subtask.side_effect = HTTPException(status_code=404, detail="not found")

        with pytest.raises(HTTPException) as exc_info:
            await generate_plan_audio_service(
                language="en",
                sub_task_id=sub_task_id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.get_sub_task_audio_generation_payload", new_callable=AsyncMock)
    async def test_generate_subtask_audio_invalid_content_type(self, mock_get_subtask):
        """Test error when backend rejects invalid content type."""
        from fastapi import HTTPException

        sub_task_id = uuid4()
        mock_get_subtask.side_effect = HTTPException(status_code=400, detail="invalid type")

        with pytest.raises(HTTPException) as exc_info:
            await generate_plan_audio_service(
                language="en",
                sub_task_id=sub_task_id,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service._generate_audio_from_text")
    async def test_generate_audio_with_text_routes_correctly(
        self,
        mock_generate_from_text,
    ):
        """Test that text input routes to _generate_audio_from_text."""
        mock_generate_from_text.return_value = {
            "audio_url": "https://s3.example.com/audio.wav",
            "audio_duration_ms": 5000,
            "s3_key": "audio/generated/test.wav"
        }

        result = await generate_plan_audio_service(
            text="Test text input",
            language="en",
            audio_type=PlanAudioType.TEXT_READING,
            voice_name=MonlamVoiceName.DOLKAR_LHASA_FEMALE,
        )

        assert result["audio_url"] == "https://s3.example.com/audio.wav"
        assert result["audio_duration_ms"] == 5000

        mock_generate_from_text.assert_called_once_with(
            text="Test text input",
            language="en",
            audio_type=PlanAudioType.TEXT_READING,
            voice_name=MonlamVoiceName.DOLKAR_LHASA_FEMALE,
            s3_key_prefix=None,
        )

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service._generate_audio_from_text")
    async def test_generate_audio_with_text_and_prefix(
        self,
        mock_generate_from_text,
    ):
        """Test text input with custom S3 prefix."""
        mock_generate_from_text.return_value = {
            "audio_url": "https://s3.example.com/custom/audio.wav",
            "audio_duration_ms": 3000,
            "s3_key": "custom/prefix/test.wav"
        }

        result = await generate_plan_audio_service(
            text="Custom prefix text",
            language="bo",
            s3_key_prefix="custom/prefix",
        )

        assert result["s3_key"] == "custom/prefix/test.wav"

        mock_generate_from_text.assert_called_once()
        call_kwargs = mock_generate_from_text.call_args.kwargs
        assert call_kwargs["s3_key_prefix"] == "custom/prefix"

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service._generate_audio_from_text")
    @patch("worker_api.audio.services.audio_generate_service.get_day_audio_generation_payload", new_callable=AsyncMock)
    async def test_generate_audio_text_bypasses_backend_fetch(
        self,
        mock_get_day_payload,
        mock_generate_from_text,
    ):
        """Test that text input bypasses backend content fetch."""
        mock_generate_from_text.return_value = {
            "audio_url": "https://s3.example.com/audio.wav",
            "audio_duration_ms": 4000,
            "s3_key": "audio/generated/test.wav"
        }

        await generate_plan_audio_service(
            text="No backend content needed",
            language="en",
        )

        mock_get_day_payload.assert_not_called()
        mock_generate_from_text.assert_called_once()


class TestGenerateAudioSegments:
    """Tests for _generate_audio_segments helper function."""

    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    def test_generate_segments_with_text_content(self, mock_tts):
        """Test generating audio segments from text content."""
        wav_data = b"RIFF" + b"\x00" * 40 + b"audio_data"
        mock_tts.return_value = wav_data

        subtask = {
            "id": str(uuid4()),
            "content": "Test content",
            "content_type": ContentType.TEXT,
            "audio_url": None,
        }

        segments, refs = _generate_audio_segments(
            [subtask],
            PlanAudioType.TEXT_READING,
            "en",
        )

        assert len(segments) == 1
        assert len(refs) == 1
        assert refs[0] == subtask
        mock_tts.assert_called_once()

    @patch("worker_api.audio.services.audio_generate_service.download_bytes")
    def test_generate_segments_with_existing_audio(self, mock_download):
        """Test reusing existing audio from subtask."""
        wav_data = b"RIFF" + b"\x00" * 40 + b"existing_audio"
        mock_download.return_value = wav_data

        subtask = {
            "id": str(uuid4()),
            "content_type": "TEXT",
            "audio_url": "audio/existing.wav",
        }

        segments, refs = _generate_audio_segments(
            [subtask],
            PlanAudioType.TEXT_READING,
            "en",
        )

        assert len(segments) == 1
        mock_download.assert_called_once_with(key="audio/existing.wav")

    def test_generate_segments_skips_invalid_content_types(self):
        """Test that non-text/source_reference subtasks are skipped."""
        subtask = {
            "id": str(uuid4()),
            "content_type": ContentType.VIDEO,
        }

        segments, refs = _generate_audio_segments(
            [subtask],
            PlanAudioType.TEXT_READING,
            "en",
        )

        assert len(segments) == 0
        assert len(refs) == 0


class TestBuildCombinedWav:
    """Tests for _build_combined_wav helper function."""

    def test_build_wav_single_segment(self):
        """Test building WAV file from single audio segment."""
        audio_data = b"\x00" * 1000

        wav, size = _build_combined_wav([audio_data])

        assert len(wav) > len(audio_data)
        assert wav[:4] == b"RIFF"
        assert b"WAVE" in wav
        assert size == len(audio_data)

    def test_build_wav_multiple_segments(self):
        """Test building WAV file from multiple audio segments."""
        segment1 = b"\x00" * 500
        segment2 = b"\x01" * 500

        wav, size = _build_combined_wav([segment1, segment2])

        assert wav[:4] == b"RIFF"
        assert size == len(segment1) + len(segment2)

    def test_build_wav_empty_segments(self):
        """Test building WAV file with no segments."""
        wav, size = _build_combined_wav([])

        assert wav[:4] == b"RIFF"
        assert size == 0


class TestBuildSubtaskTimestamps:
    """Tests for _build_subtask_timestamps helper function."""

    def test_build_timestamps(self):
        """Test building subtask timestamps without DB writes."""
        audio_segment = b"\x00" * 1000
        sub_task_id = uuid4()

        duration, timestamps = _build_subtask_timestamps(
            [audio_segment],
            [{"id": str(sub_task_id)}],
            24000,
            2,
        )

        assert duration > 0
        assert len(timestamps) == 1
        assert timestamps[0]["sub_task_id"] == str(sub_task_id)
        assert timestamps[0]["start_ms"] == 0
        assert timestamps[0]["end_ms"] > 0


class TestUploadDayAudio:
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    def test_upload_day_audio_builds_key(self, mock_upload):
        from worker_api.audio.services.audio_generate_service import _upload_day_audio

        plan_id = uuid4()
        plan_item_id = uuid4()

        key = _upload_day_audio(b"wav", plan_id, plan_item_id)

        assert key.startswith(f"audio/plan_days/{plan_id}/{plan_item_id}/")
        assert key.endswith(".wav")
        mock_upload.assert_called_once()


class TestGenerateAudioFromText:
    """Tests for _generate_audio_from_text helper function."""

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_success(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test successful audio generation from text."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 1000
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await _generate_audio_from_text(
            text="Test text for audio generation",
            language="en",
            audio_type=PlanAudioType.TEXT_READING,
        )

        assert result["audio_url"] == "https://s3.example.com/audio.wav"
        assert result["audio_duration_ms"] > 0
        assert "s3_key" in result
        assert result["s3_key"].startswith("audio/generated/")
        assert result["s3_key"].endswith(".wav")

        mock_tts.assert_called_once_with(
            "Test text for audio generation",
            PlanAudioType.TEXT_READING,
            "en",
            voice_name=MonlamVoiceName.DOLKAR_LHASA_FEMALE,
        )
        mock_upload.assert_called_once()

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_with_custom_prefix(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test audio generation with custom S3 key prefix."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 500
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/custom/audio.wav"

        result = await _generate_audio_from_text(
            text="Custom prefix test",
            language="en",
            s3_key_prefix="custom/prefix",
        )

        assert result["s3_key"].startswith("custom/prefix/")
        assert result["s3_key"].endswith(".wav")

        upload_call = mock_upload.call_args
        uploaded_key = upload_call.kwargs["key"]
        assert uploaded_key.startswith("custom/prefix/")

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_with_voice_name(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test audio generation with specific voice name."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 800
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await _generate_audio_from_text(
            text="Voice test",
            language="bo",
            voice_name=MonlamVoiceName.YANGCHEN_LHASA_FEMALE,
        )

        assert "audio_url" in result
        mock_tts.assert_called_once_with(
            "Voice test",
            PlanAudioType.TEXT_READING,
            "bo",
            voice_name=MonlamVoiceName.YANGCHEN_LHASA_FEMALE,
        )

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_recitation_type(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test audio generation with RECITATION type."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 1200
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await _generate_audio_from_text(
            text="Recitation text",
            language="bo",
            audio_type=PlanAudioType.RECITATION,
        )

        assert result["audio_duration_ms"] > 0
        mock_tts.assert_called_once_with(
            "Recitation text",
            PlanAudioType.RECITATION,
            "bo",
            voice_name=MonlamVoiceName.DOLKAR_LHASA_FEMALE,
        )

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_instruction_type(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test audio generation with INSTRUCTION type."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 600
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await _generate_audio_from_text(
            text="Instruction text",
            language="en",
            audio_type=PlanAudioType.INSTRUCTION,
        )

        assert "s3_key" in result
        mock_tts.assert_called_once_with(
            "Instruction text",
            PlanAudioType.INSTRUCTION,
            "en",
            voice_name=MonlamVoiceName.DOLKAR_LHASA_FEMALE,
        )

    @pytest.mark.asyncio
    @patch("worker_api.audio.services.audio_generate_service.generate_tts_audio")
    @patch("worker_api.audio.services.audio_generate_service.upload_bytes")
    @patch("worker_api.audio.services.audio_generate_service.generate_presigned_access_url")
    async def test_generate_audio_from_text_duration_calculation(
        self,
        mock_presigned_url,
        mock_upload,
        mock_tts,
    ):
        """Test correct duration calculation from audio data."""
        from worker_api.audio.services.audio_generate_service import _generate_audio_from_text

        wav_header = b"RIFF" + b"\x00" * 40
        audio_data = b"\x00" * 48000
        mock_tts.return_value = wav_header + audio_data
        mock_presigned_url.return_value = "https://s3.example.com/audio.wav"

        result = await _generate_audio_from_text(
            text="Duration test",
            language="en",
        )

        assert result["audio_duration_ms"] == 1000
