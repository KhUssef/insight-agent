"""LLM client wrapper.

This module is the only place the openai SDK is touched. The rest of the
codebase talks to LLMClient, which points the SDK at whatever
OpenAI-compatible endpoint the settings name (DeepSeek by default). Swapping
providers is a configuration change: base URL, API key, and model name.
"""

from typing import Any

from insight_agent.config import Settings


class MissingAPIKeyError(RuntimeError):
    """Raised when no API key is configured for the LLM provider."""


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions client.

    A pre-built client can be injected for tests; otherwise one is
    constructed from the settings. The wrapper adds no retries, streaming,
    or prompt logic - it only carries configuration and dispatches calls.
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._last_usage: dict[str, int] | None = None
        if client is not None:
            self._client = client
        else:
            if not settings.deepseek_api_key:
                raise MissingAPIKeyError(
                    "no API key configured: set DEEPSEEK_API_KEY in the environment or .env"
                )
            import openai

            self._client = openai.OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.llm_base_url,
            )

    @property
    def model(self) -> str:
        """The model name every chat call is sent to."""
        return self._settings.llm_model

    @property
    def last_usage(self) -> dict[str, int] | None:
        """Token usage reported by the provider for the most recent chat call.

        A dict with prompt_tokens, completion_tokens, and total_tokens, or
        None when no call has been made yet or the provider reported no usage.
        """
        return self._last_usage

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Send a chat completion request and return the response message.

        The returned object exposes .content and .tool_calls. When tools are
        provided they are passed through with tool_choice set to auto so the
        model decides when to call them. The provider's reported token usage
        for the call, when present on the response, is captured and exposed
        through last_usage.
        """
        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self._client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is None:
            self._last_usage = None
        else:
            self._last_usage = {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
        return response.choices[0].message
