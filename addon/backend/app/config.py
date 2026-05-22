from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Tuple, Type

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_HA_OPTIONS_PATH = Path("/data/options.json")


class HaOptionsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: Type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = (
            json.loads(_HA_OPTIONS_PATH.read_text())
            if _HA_OPTIONS_PATH.exists()
            else {}
        )

    def get_field_value(self, field: FieldInfo, field_name: str) -> Tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v not in (None, "")}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ghostfolio"
    postgres_db_bi: str = "investments_bi"
    postgres_user_rw: str = "reporter_rw"
    postgres_password_rw: str = ""
    postgres_user_ro: str = "reporter_ro"
    postgres_password_ro: str = ""
    ghostfolio_owner_id: str = ""
    # WORKAROUND: ghostfolio_owner_id won't appear in the HA config UI due to
    # Supervisor schema caching. ghostfolio_account_id IS visible and is
    # temporarily used to hold the Ghostfolio userId. Will be split into
    # separate user_id / account_id fields in a future milestone once the
    # caching issue is resolved.
    ghostfolio_account_id: str = ""

    @property
    def ghostfolio_owner_id_or_none(self) -> str | None:
        # Prefer explicit ghostfolio_owner_id; fall back to ghostfolio_account_id
        return self.ghostfolio_owner_id or self.ghostfolio_account_id or None

    @property
    def ghostfolio_account_id_or_none(self) -> str | None:
        # Only use as account filter when ghostfolio_owner_id is also set
        if self.ghostfolio_owner_id and self.ghostfolio_account_id:
            return self.ghostfolio_account_id or None
        return None
    base_currency: str = "EUR"
    snapshot_local_time: str = "00:00"
    log_level: str = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            kwargs["init_settings"],
            kwargs["env_settings"],
            HaOptionsSource(settings_cls),
        )

    @property
    def reporting_db_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user_rw}:{self.postgres_password_rw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db_bi}"
        )

    @property
    def ghostfolio_db_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user_ro}:{self.postgres_password_ro}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
