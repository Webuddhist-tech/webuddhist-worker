import struct
from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from worker_api.audio.enums import ContentType, PlanAudioType, MonlamVoiceName
from worker_api.audio.services.backend_client import (
    apply_day_audio_generation_result,
    apply_sub_task_audio_generation_result,
    get_day_audio_generation_payload,
    get_sub_task_audio_generation_payload,
)
from worker_api.audio.services.tts_service import generate_tts_audio
from worker_api.uploads.S3_utils import upload_bytes, download_bytes, generate_presigned_access_url

WAV_CONTENT_TYPE = "audio/wav"


def _is_tts_content_type(content_type: Any) -> bool:
    if isinstance(content_type, ContentType):
        return content_type in {ContentType.TEXT, ContentType.SOURCE_REFERENCE}
    return str(content_type) in {"TEXT", "SOURCE_REFERENCE"}


def _generate_audio_segments(
    subtasks: List[Dict[str, Any]],
    audio_type: PlanAudioType,
    language: str,
    voice_name: Optional[str] = None,
) -> tuple[List[bytes], List[Dict[str, Any]]]:
    wav_header_size = 44
    audio_segments: List[bytes] = []
    subtask_refs: List[Dict[str, Any]] = []

    for subtask in subtasks:
        if not _is_tts_content_type(subtask.get("content_type")):
            continue

        audio_url = subtask.get("audio_url")
        if audio_url:
            existing_wav = download_bytes(key=audio_url)
            raw_pcm = existing_wav[wav_header_size:]
        else:
            wav_bytes = generate_tts_audio(
                subtask.get("content") or "",
                audio_type,
                language,
                voice_name=voice_name,
            )
            raw_pcm = wav_bytes[wav_header_size:]

        audio_segments.append(raw_pcm)
        subtask_refs.append(subtask)

    return audio_segments, subtask_refs


def _build_subtask_timestamps(
    audio_segments: List[bytes],
    subtask_refs: List[Dict[str, Any]],
    sample_rate: int,
    bytes_per_sample: int,
) -> tuple[int, List[Dict[str, Any]]]:
    current_offset_ms = 0
    timestamps: List[Dict[str, Any]] = []
    for i, raw_pcm in enumerate(audio_segments):
        segment_samples = len(raw_pcm) // bytes_per_sample
        segment_duration_ms = int((segment_samples / sample_rate) * 1000)
        timestamps.append(
            {
                "sub_task_id": str(subtask_refs[i]["id"]),
                "start_ms": current_offset_ms,
                "end_ms": current_offset_ms + segment_duration_ms,
            }
        )
        current_offset_ms += segment_duration_ms
    return current_offset_ms, timestamps


def _build_combined_wav(audio_segments: List[bytes]) -> tuple[bytes, int]:
    sample_rate = 24000
    bits_per_sample = 16
    num_channels = 1
    bytes_per_sample = bits_per_sample // 8

    combined_pcm = b"".join(audio_segments)
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    data_size = len(combined_pcm)
    chunk_size = 36 + data_size

    wav_header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16, 1, num_channels,
        sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return wav_header + combined_pcm, data_size


def _upload_day_audio(
    combined_wav: bytes,
    plan_id: UUID,
    plan_item_id: UUID,
) -> str:
    s3_key = f"audio/plan_days/{plan_id}/{plan_item_id}/{uuid4()}.wav"
    upload_bytes(
        file_bytes=BytesIO(combined_wav),
        key=s3_key,
        content_type=WAV_CONTENT_TYPE,
    )
    return s3_key


async def _generate_audio_from_text(
    text: str,
    language: str,
    audio_type: PlanAudioType = PlanAudioType.TEXT_READING,
    voice_name: MonlamVoiceName = MonlamVoiceName.DOLKAR_LHASA_FEMALE,
    s3_key_prefix: Optional[str] = None,
):
    SAMPLE_RATE = 24000
    BYTES_PER_SAMPLE = 2
    WAV_HEADER_SIZE = 44

    wav_bytes = generate_tts_audio(
        text, audio_type, language, voice_name=voice_name
    )
    raw_pcm = wav_bytes[WAV_HEADER_SIZE:]

    segment_samples = len(raw_pcm) // BYTES_PER_SAMPLE
    duration_ms = int((segment_samples / SAMPLE_RATE) * 1000)

    combined_wav, _ = _build_combined_wav([raw_pcm])

    if s3_key_prefix:
        s3_key = f"{s3_key_prefix}/{uuid4()}.wav"
    else:
        s3_key = f"audio/generated/{uuid4()}.wav"

    upload_bytes(
        file_bytes=BytesIO(combined_wav),
        key=s3_key,
        content_type=WAV_CONTENT_TYPE,
    )

    audio_url = generate_presigned_access_url(
        key=s3_key,
    )

    return {
        "audio_url": audio_url,
        "audio_duration_ms": duration_ms,
        "s3_key": s3_key,
    }


async def generate_plan_audio_service(
    language: str,
    text: Optional[str] = None,
    day_id: Optional[UUID] = None,
    sub_task_id: Optional[UUID] = None,
    audio_type: PlanAudioType = PlanAudioType.TEXT_READING,
    voice_name: MonlamVoiceName = MonlamVoiceName.DOLKAR_LHASA_FEMALE,
    s3_key_prefix: Optional[str] = None,
):
    if text:
        return await _generate_audio_from_text(
            text=text,
            language=language,
            audio_type=audio_type,
            voice_name=voice_name,
            s3_key_prefix=s3_key_prefix,
        )

    if sub_task_id:
        return await _generate_subtask_audio(
            sub_task_id=sub_task_id,
            audio_type=audio_type,
            language=language,
            voice_name=voice_name,
        )

    SAMPLE_RATE = 24000
    BYTES_PER_SAMPLE = 2

    day_payload = await get_day_audio_generation_payload(day_id=day_id)
    audio_segments, subtask_refs = _generate_audio_segments(
        day_payload.get("subtasks") or [],
        audio_type,
        language,
        voice_name,
    )
    if not audio_segments:
        return []

    duration_ms, timestamps = _build_subtask_timestamps(
        audio_segments=audio_segments,
        subtask_refs=subtask_refs,
        sample_rate=SAMPLE_RATE,
        bytes_per_sample=BYTES_PER_SAMPLE,
    )

    combined_wav, _ = _build_combined_wav(audio_segments)
    s3_key = _upload_day_audio(
        combined_wav=combined_wav,
        plan_id=UUID(str(day_payload["plan_id"])),
        plan_item_id=UUID(str(day_payload["id"])),
    )

    await apply_day_audio_generation_result(
        day_id=UUID(str(day_payload["id"])),
        audio_key=s3_key,
        duration_ms=duration_ms,
        file_size_bytes=len(combined_wav),
        timestamps=timestamps,
        mime_type=WAV_CONTENT_TYPE,
    )

    audio_url = generate_presigned_access_url(key=s3_key)
    return {
        "audio_url": audio_url,
        "audio_duration_ms": duration_ms,
        "s3_key": s3_key,
    }


async def _generate_subtask_audio(
    sub_task_id: UUID,
    audio_type: PlanAudioType,
    language: str,
    voice_name: Optional[str] = None,
):
    SAMPLE_RATE = 24000
    BYTES_PER_SAMPLE = 2
    WAV_HEADER_SIZE = 44

    subtask = await get_sub_task_audio_generation_payload(sub_task_id=sub_task_id)

    wav_bytes = generate_tts_audio(
        subtask.get("content") or "",
        audio_type,
        language,
        voice_name=voice_name,
    )
    raw_pcm = wav_bytes[WAV_HEADER_SIZE:]

    segment_samples = len(raw_pcm) // BYTES_PER_SAMPLE
    duration_ms = int((segment_samples / SAMPLE_RATE) * 1000)

    combined_wav, _ = _build_combined_wav([raw_pcm])

    task_id = UUID(str(subtask["task_id"]))
    s3_key = f"audio/plan_subtasks/{task_id}/{sub_task_id}/{uuid4()}.wav"
    upload_bytes(
        file_bytes=BytesIO(combined_wav),
        key=s3_key,
        content_type=WAV_CONTENT_TYPE,
    )

    await apply_sub_task_audio_generation_result(
        sub_task_id=sub_task_id,
        audio_key=s3_key,
        duration_ms=duration_ms,
    )

    audio_url = generate_presigned_access_url(key=s3_key)
    return {
        "audio_url": audio_url,
        "audio_duration_ms": duration_ms,
        "s3_key": s3_key,
    }
