import struct

import httpx

from worker_api.config import get
from worker_api.audio.enums import MonlamVoiceName
from worker_api.audio.services.tibetan_chunker import chunk_tibetan_text

DEFAULT_MONLAM_VOICE_NAME = MonlamVoiceName.DOLKAR_LHASA_FEMALE.value
WAV_HEADER_SIZE = 44
SAMPLE_RATE = 24000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1
SILENCE_DURATION_MS = 100


def _generate_silence(duration_ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Generate raw PCM silence bytes."""
    num_samples = int((duration_ms / 1000) * sample_rate)
    return b"\x00\x00" * num_samples


def _call_monlam_api(text: str, voice_name: str | None = None) -> bytes:
    """Make a single TTS API call to Monlam."""
    base_url = get("MONLAM_BASE_URL").rstrip("/")
    api_key = get("MONLAM_API_KEY")
    if not api_key:
        raise RuntimeError("MONLAM_API_KEY is not configured")

    payload = {
        "text": text,
        "provider": get("MONLAM_TTS_PROVIDER"),
        "model_name": get("MONLAM_TTS_MODEL_NAME"),
    }
    resolved_voice_name = voice_name or get("MONLAM_TTS_VOICE_NAME") or DEFAULT_MONLAM_VOICE_NAME
    payload["voice_name"] = resolved_voice_name

    try:
        response = httpx.post(
            f"{base_url}/api/v1/text-to-speech/stream",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise RuntimeError(
            f"Monlam TTS request failed with status {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Monlam TTS request failed: {exc}") from exc

    wav_bytes = response.content
    if not wav_bytes or wav_bytes[:4] != b"RIFF":
        raise RuntimeError("Monlam TTS generation returned invalid audio data")

    return wav_bytes


def _build_wav_from_pcm(pcm_data: bytes) -> bytes:
    """Build a WAV file from raw PCM data."""
    bytes_per_sample = BITS_PER_SAMPLE // 8
    block_align = NUM_CHANNELS * bytes_per_sample
    byte_rate = SAMPLE_RATE * block_align
    data_size = len(pcm_data)
    chunk_size = 36 + data_size

    wav_header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16, 1, NUM_CHANNELS,
        SAMPLE_RATE, byte_rate, block_align, BITS_PER_SAMPLE,
        b"data", data_size,
    )
    return wav_header + pcm_data


def generate_monlam_tts_audio(content: str, voice_name: str | None = None) -> bytes:
    if not content.strip():
        raise ValueError("Content cannot be empty")

    chunks = chunk_tibetan_text(content)
    if not chunks:
        raise ValueError("Content cannot be empty")

    audio_segments: list[bytes] = []
    silence_pcm = _generate_silence(SILENCE_DURATION_MS)

    for chunk in chunks:
        try:
            wav_bytes = _call_monlam_api(chunk, voice_name)
            raw_pcm = wav_bytes[WAV_HEADER_SIZE:]
            audio_segments.append(raw_pcm)
        except Exception as exc:
            chunk_preview = chunk[:30] + "..." if len(chunk) > 30 else chunk
            raise RuntimeError(
                f"TTS generation failed at: '{chunk_preview}'"
            ) from exc

    combined_pcm_parts = []
    for i, segment in enumerate(audio_segments):
        combined_pcm_parts.append(segment)
        # Uncomment below to add silence padding between chunks
        # if i < len(audio_segments) - 1:
        #     combined_pcm_parts.append(silence_pcm)

    combined_pcm = b"".join(combined_pcm_parts)
    return _build_wav_from_pcm(combined_pcm)
