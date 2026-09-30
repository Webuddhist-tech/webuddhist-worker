import re

# Tibetan shey (།) and nyi-shey (༎) mark phrase/sentence boundaries.
_SHEY_SPLIT = re.compile(r"(?<=[།༎])")


def chunk_tibetan_text(text: str) -> list[str]:
    """
    Split Tibetan text on shey boundaries for TTS.

    Each chunk keeps its trailing shey. Text with no shey is returned as a
    single chunk.
    """
    if not text or not text.strip():
        return []

    return [part.strip() for part in _SHEY_SPLIT.split(text.strip()) if part.strip()]
