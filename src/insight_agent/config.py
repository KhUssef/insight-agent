"""Application configuration.

All settings are environment-driven through pydantic-settings. A local .env
file is read when present; environment variables take precedence. Swapping the
LLM provider is a configuration change only: set LLM_BASE_URL, LLM_MODEL, and
DEEPSEEK_API_KEY to values for any OpenAI-compatible endpoint.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, sourced from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_models: str = "deepseek-chat,deepseek-reasoner"

    def available_models(self) -> list[str]:
        """The model names a caller may select for a run.

        Parsed from the comma-separated llm_models setting, with the default
        llm_model always included first. Whitespace around names is ignored
        and blank entries are dropped.
        """
        names = [name.strip() for name in self.llm_models.split(",")]
        models = [self.llm_model]
        for name in names:
            if name and name not in models:
                models.append(name)
        return models

    data_dir: Path = Path("data")
    charts_dir: Path = Path("charts")

    # Whether a missing or empty data_dir is populated with the generated
    # sample datasets. Disabled for runs scoped to a user-supplied folder, so
    # sample files are only ever written into the configured default
    # directory.
    generate_sample_data: bool = True

    max_tool_rounds: int = 12
    max_result_rows: int = 200


def get_settings() -> Settings:
    """Build a Settings instance from the current environment."""
    return Settings()
