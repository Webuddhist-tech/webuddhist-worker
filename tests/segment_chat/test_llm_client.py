from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_api.segment_chat.schemas import ChatHistoryMessage
from worker_api.segment_chat.services import llm_client


def _config(**overrides):
    values = {
        "SEGMENT_CHAT_LLM_PROVIDER": "gemini",
        "SEGMENT_CHAT_LLM_MODEL": "gemini-2.5-flash",
        "SEGMENT_CHAT_LLM_API_KEY": "",
        "GEMINI_API_KEY": "gemini-key",
        "SEGMENT_CHAT_LLM_TEMPERATURE": "0.3",
        "SEGMENT_CHAT_LLM_MAX_OUTPUT_TOKENS": "1024",
        "SEGMENT_CHAT_LLM_THINKING_BUDGET": "0",
    }
    values.update(overrides)
    getter = lambda key: values[key]  # noqa: E731
    return [
        patch.object(llm_client, "get", side_effect=getter),
        patch.object(llm_client, "get_int", side_effect=lambda key: int(getter(key))),
        patch.object(llm_client, "get_float", side_effect=lambda key: float(getter(key))),
    ]


class _Patched:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class TestGetLlmSettings:
    def test_uses_gemini_key_as_fallback(self):
        with _Patched(_config()):
            assert llm_client.get_llm_settings() == {"provider": "gemini", "model": "gemini-2.5-flash"}
            assert llm_client._api_key() == "gemini-key"

    def test_dedicated_key_wins(self):
        with _Patched(_config(SEGMENT_CHAT_LLM_API_KEY="chat-key")):
            assert llm_client._api_key() == "chat-key"

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"SEGMENT_CHAT_LLM_PROVIDER": "other"}, "Unsupported"),
            ({"SEGMENT_CHAT_LLM_MODEL": " "}, "SEGMENT_CHAT_LLM_MODEL"),
            ({"GEMINI_API_KEY": ""}, "API_KEY"),
        ],
    )
    def test_rejects_incomplete_config(self, overrides, message):
        with _Patched(_config(**overrides)):
            with pytest.raises(llm_client.LLMNotConfiguredError, match=message):
                llm_client.get_llm_settings()


class TestStreamCompletion:
    @pytest.mark.asyncio
    async def test_streams_text_chunks_with_history(self):
        async def chunks():
            for text in ["Hello", None, " world"]:
                yield MagicMock(text=text)

        client = MagicMock()
        client.aio.models.generate_content_stream = AsyncMock(return_value=chunks())

        with _Patched(_config()), patch("google.genai.Client", return_value=client) as client_class:
            result = [
                text
                async for text in llm_client.stream_completion(
                    system_prompt="system",
                    history=[
                        ChatHistoryMessage(role="user", content="first question"),
                        ChatHistoryMessage(role="assistant", content="first answer"),
                    ],
                    user_message="follow up",
                )
            ]

        assert result == ["Hello", " world"]
        client_class.assert_called_once_with(api_key="gemini-key")
        kwargs = client.aio.models.generate_content_stream.await_args.kwargs
        assert kwargs["model"] == "gemini-2.5-flash"
        assert [content.role for content in kwargs["contents"]] == ["user", "model", "user"]
        assert kwargs["config"].system_instruction == "system"
        assert kwargs["config"].thinking_config.thinking_budget == 0

    @pytest.mark.asyncio
    async def test_negative_thinking_budget_keeps_model_default(self):
        async def chunks():
            yield MagicMock(text="ok")

        client = MagicMock()
        client.aio.models.generate_content_stream = AsyncMock(return_value=chunks())
        with _Patched(_config(SEGMENT_CHAT_LLM_THINKING_BUDGET="-1")), patch(
            "google.genai.Client", return_value=client
        ):
            _ = [text async for text in llm_client.stream_completion(
                system_prompt="s", history=[], user_message="q"
            )]

        assert client.aio.models.generate_content_stream.await_args.kwargs["config"].thinking_config is None
