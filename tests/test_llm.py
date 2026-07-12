"""LLMClient behavior against a fake OpenAI-compatible client, no key needed."""

from typing import Any

import pytest

from insight_agent.config import Settings
from insight_agent.llm import LLMClient, MissingAPIKeyError


class FakeMessage:
    def __init__(self) -> None:
        self.content = "the answer"
        self.tool_calls = None


class FakeChoice:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 120
        self.completion_tokens = 30
        self.total_tokens = 150


class FakeResponse:
    def __init__(self, message: FakeMessage, usage: FakeUsage | None = None) -> None:
        self.choices = [FakeChoice(message)]
        self.usage = usage


class FakeCompletions:
    def __init__(self, message: FakeMessage, usage: FakeUsage | None = None) -> None:
        self.message = message
        self.usage = usage
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> FakeResponse:
        self.last_kwargs = kwargs
        return FakeResponse(self.message, self.usage)


class FakeClient:
    def __init__(self, usage: FakeUsage | None = None) -> None:
        self.message = FakeMessage()
        self.completions = FakeCompletions(self.message, usage)

    @property
    def chat(self) -> "FakeClient":
        return self


def make_settings() -> Settings:
    return Settings(deepseek_api_key="", llm_model="test-model", _env_file=None)


def test_chat_passes_model_and_messages_through() -> None:
    fake = FakeClient()
    client = LLMClient(make_settings(), client=fake)
    messages = [{"role": "user", "content": "hello"}]

    message = client.chat(messages)

    assert message is fake.message
    assert fake.completions.last_kwargs is not None
    assert fake.completions.last_kwargs["model"] == "test-model"
    assert fake.completions.last_kwargs["messages"] == messages
    assert "tools" not in fake.completions.last_kwargs
    assert "tool_choice" not in fake.completions.last_kwargs


def test_chat_passes_tools_with_auto_tool_choice() -> None:
    fake = FakeClient()
    client = LLMClient(make_settings(), client=fake)
    tools = [{"type": "function", "function": {"name": "run_sql", "parameters": {}}}]

    client.chat([{"role": "user", "content": "q"}], tools=tools)

    assert fake.completions.last_kwargs is not None
    assert fake.completions.last_kwargs["tools"] == tools
    assert fake.completions.last_kwargs["tool_choice"] == "auto"


def test_last_usage_captures_reported_tokens() -> None:
    client = LLMClient(make_settings(), client=FakeClient(usage=FakeUsage()))

    assert client.last_usage is None
    client.chat([{"role": "user", "content": "q"}])
    assert client.last_usage == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }


def test_last_usage_is_none_without_reported_usage() -> None:
    client = LLMClient(make_settings(), client=FakeClient())

    client.chat([{"role": "user", "content": "q"}])
    assert client.last_usage is None


def test_model_property_reflects_settings() -> None:
    client = LLMClient(make_settings(), client=FakeClient())
    assert client.model == "test-model"


def test_missing_key_raises_clear_error() -> None:
    with pytest.raises(MissingAPIKeyError, match="DEEPSEEK_API_KEY"):
        LLMClient(make_settings())
