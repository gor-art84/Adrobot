from pathlib import Path

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    keitaro_base_url: HttpUrl = HttpUrl("https://tlgk.host")
    keitaro_api_key: SecretStr = SecretStr("")
    database_path: Path = PROJECT_ROOT / "adrobot.sqlite3"
