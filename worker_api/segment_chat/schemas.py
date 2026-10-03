from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class SourceType(str, Enum):
    ROOT_TEXT = "root_text"
    TRANSLATION = "translation"
    COMMENTARY = "commentary"


class ChatHistoryMessage(BaseModel):
    role: ChatRole
    content: str = Field(..., min_length=1, max_length=8000)


class SegmentChatRequest(BaseModel):
    # Interpolated into backend URL paths, so only id characters are allowed.
    segment_id: str = Field(..., min_length=1, max_length=255, pattern=r"^\s*[A-Za-z0-9_-]+\s*$")
    question: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatHistoryMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Earlier turns of this conversation, oldest first.",
    )
    language: Optional[str] = Field(
        default=None,
        max_length=16,
        description="Language code to answer in (e.g. `en`, `bo`). Defaults to the language of the question.",
    )

    @field_validator("segment_id", "question")
    @classmethod
    def strip_and_require(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class SegmentText(BaseModel):
    text_id: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = None


class ChatSegment(BaseModel):
    segment_id: str
    content: str
    text: Optional[SegmentText] = None


class ChatSource(BaseModel):
    ref: int = Field(..., description="Citation number used in the answer as [ref].")
    type: SourceType
    segment_id: str
    text_id: str
    title: str
    language: Optional[str] = None
    source_link: Optional[str] = None
    license: Optional[str] = None
    content: str
    truncated: bool = False

    def to_public(self, snippet_chars: int = 280) -> dict:
        """Shape sent to the client: the full content stays server-side."""
        data = self.model_dump(mode="json", exclude={"content", "truncated"})
        snippet = self.content[:snippet_chars].rstrip()
        if len(self.content) > snippet_chars:
            snippet += "…"
        data["snippet"] = snippet
        return data


class SegmentChatContext(BaseModel):
    segment: ChatSegment
    sources: list[ChatSource]
