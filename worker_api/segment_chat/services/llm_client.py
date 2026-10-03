from typing import AsyncIterator, List

from worker_api.config import get, get_float, get_int
from worker_api.segment_chat.schemas import ChatHistoryMessage, ChatRole

SUPPORTED_PROVIDERS = ("gemini",)


class LLMNotConfiguredError(RuntimeError):
    """The worker config does not allow an LLM call."""


def _api_key() -> str:
    return get("SEGMENT_CHAT_LLM_API_KEY").strip() or get("GEMINI_API_KEY").strip()


def get_llm_settings() -> dict:
    provider = get("SEGMENT_CHAT_LLM_PROVIDER").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMNotConfiguredError(
            f"Unsupported SEGMENT_CHAT_LLM_PROVIDER '{provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    model = get("SEGMENT_CHAT_LLM_MODEL").strip()
    if not model:
        raise LLMNotConfiguredError("SEGMENT_CHAT_LLM_MODEL is not configured")
    if not _api_key():
        raise LLMNotConfiguredError("SEGMENT_CHAT_LLM_API_KEY (or GEMINI_API_KEY) is not configured")
    return {"provider": provider, "model": model}


async def stream_completion(
    *,
    system_prompt: str,
    history: List[ChatHistoryMessage],
    user_message: str,
) -> AsyncIterator[str]:
    """Yield the answer's text as the model produces it."""
    settings = get_llm_settings()

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_api_key())
    contents = [
        types.Content(
            role="model" if message.role == ChatRole.ASSISTANT else "user",
            parts=[types.Part.from_text(text=message.content)],
        )
        for message in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

    # Thinking delays the first streamed token and its tokens count against
    # max_output_tokens. A negative budget leaves the model's default.
    thinking_budget = get_int("SEGMENT_CHAT_LLM_THINKING_BUDGET")
    thinking_config = (
        types.ThinkingConfig(thinking_budget=thinking_budget) if thinking_budget >= 0 else None
    )

    stream = await client.aio.models.generate_content_stream(
        model=settings["model"],
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=get_float("SEGMENT_CHAT_LLM_TEMPERATURE"),
            max_output_tokens=get_int("SEGMENT_CHAT_LLM_MAX_OUTPUT_TOKENS"),
            thinking_config=thinking_config,
        ),
    )
    async for chunk in stream:
        text = chunk.text
        if text:
            yield text
