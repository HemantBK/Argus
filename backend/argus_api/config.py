"""Runtime configuration. Every value comes from the environment; missing values fail at startup."""

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    project: str
    location: str
    agent_model: str
    monitor_model: str
    embedding_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project=_require("GOOGLE_CLOUD_PROJECT"),
            location=_require("GOOGLE_CLOUD_LOCATION"),
            agent_model=_require("ARGUS_AGENT_MODEL"),
            monitor_model=_require("ARGUS_MONITOR_MODEL"),
            embedding_model=_require("ARGUS_EMBEDDING_MODEL"),
        )
