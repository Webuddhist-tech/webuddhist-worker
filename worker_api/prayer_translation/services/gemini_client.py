"""Gemini prayer translation (worker-owned; uses GEMINI_API_KEY)."""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional, Tuple

from worker_api.config import get, get_bool

logger = logging.getLogger(__name__)

PRAYER_TRANSLATION_LANGUAGE_CODES = ("EN", "BO", "ZH")
PRAYER_REQUEST_BODY_MAX_LENGTH = 280

_PROMPT = """You translate Buddhist prayer requests into English (EN), Tibetan (BO), and Chinese (ZH).

Given the prayer text below:
1. Detect which language the prayer is written in (any language).
2. Set source_language to that language as an ISO 639-1 code (two letters, uppercase).
   When the prayer is in EN, BO, ZH, HI, NE, MN, or LA, use those exact codes.
3. Always provide translations for EN, BO, and ZH (all three keys).

Rules:
- Preserve personal names and place names.
- Keep a warm, respectful tone suitable for a prayer request.
- Output only the translation text for each target language, no commentary.
- Each translation must be at most {max_len} characters.
- If the text is already in a target language, copy it faithfully (still count as translation).

Respond with JSON only, in this exact shape:
{{
  "source_language": "<ISO 639-1 code>",
  "translations": {{
    "EN": "...",
    "BO": "...",
    "ZH": "..."
  }}
}}

Prayer text:
{text}
"""


def _parse_detected_source_language(value: object) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        return None
    return normalized


def _parse_prayer_translation_payload(
    payload: object,
) -> Optional[Tuple[str, Dict[str, str]]]:
    if not isinstance(payload, dict):
        return None
    source_language = _parse_detected_source_language(payload.get("source_language"))
    if source_language is None:
        return None

    translations_raw = payload.get("translations")
    if not isinstance(translations_raw, dict):
        return None
    translations: Dict[str, str] = {}
    for code in PRAYER_TRANSLATION_LANGUAGE_CODES:
        text = translations_raw.get(code)
        if text is None:
            continue
        cleaned = str(text).strip()
        if not cleaned:
            continue
        if len(cleaned) > PRAYER_REQUEST_BODY_MAX_LENGTH:
            cleaned = cleaned[:PRAYER_REQUEST_BODY_MAX_LENGTH]
        translations[code] = cleaned

    return source_language, translations


def prayer_translation_enabled() -> bool:
    if not get_bool("PRAYER_TRANSLATION_ENABLED"):
        return False
    return bool((get("GEMINI_API_KEY") or "").strip())


async def translate_prayer_request(body: str) -> Optional[Tuple[str, Dict[str, str]]]:
    if not prayer_translation_enabled():
        return None
    api_key = (get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None

    model = get("GEMINI_PRAYER_TRANSLATION_MODEL")
    prompt = _PROMPT.format(
        max_len=PRAYER_REQUEST_BODY_MAX_LENGTH,
        text=body.strip(),
    )

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.exception("google-genai is not installed")
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        raw = (response.text or "").strip()
        if not raw:
            return None
        payload = json.loads(raw)
        return _parse_prayer_translation_payload(payload)
    except Exception:
        logger.exception("Gemini prayer translation failed")
        return None
