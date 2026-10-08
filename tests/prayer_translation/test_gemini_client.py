from worker_api.prayer_translation.services.gemini_client import (
    _parse_prayer_translation_payload,
)


def test_accepts_valid_shape():
    result = _parse_prayer_translation_payload(
        {
            "source_language": "EN",
            "translations": {"EN": "Hi", "BO": "བོད", "ZH": "你好"},
        }
    )

    assert result is not None
    source, translations = result
    assert source == "EN"
    assert translations["ZH"] == "你好"


def test_accepts_hindi_source():
    result = _parse_prayer_translation_payload(
        {
            "source_language": "HI",
            "translations": {"EN": "Hello", "BO": "བོད", "ZH": "你好"},
        }
    )

    assert result is not None
    source, _translations = result
    assert source == "HI"


def test_rejects_empty_translations_object():
    assert _parse_prayer_translation_payload(
        {"source_language": "EN", "translations": {}}
    ) is None


def test_rejects_missing_language_key():
    assert _parse_prayer_translation_payload(
        {
            "source_language": "EN",
            "translations": {"EN": "Hi", "BO": "བོད"},
        }
    ) is None


def test_rejects_blank_translation_text():
    assert _parse_prayer_translation_payload(
        {
            "source_language": "EN",
            "translations": {"EN": "Hi", "BO": "   ", "ZH": "你好"},
        }
    ) is None
