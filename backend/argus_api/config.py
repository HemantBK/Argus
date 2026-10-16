"""Runtime configuration. Every value comes from the environment; missing values fail at startup.

ARGUS_MODE=local  uses a Google AI Studio API key and the Firestore emulator (FIRESTORE_EMULATOR_HOST).
ARGUS_MODE=cloud  uses Gemini on Vertex AI and real Firestore in GOOGLE_CLOUD_PROJECT.
"""

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["local", "cloud"]


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    mode: Mode
    project: str
    location: str | None
    api_key: str | None
    agent_model: str
    monitor_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode = _require("ARGUS_MODE")
        if mode not in ("local", "cloud"):
            raise ConfigError(f"ARGUS_MODE must be 'local' or 'cloud', got {mode!r}")
        if mode == "local":
            _require("FIRESTORE_EMULATOR_HOST")
            return cls(
                mode="local",
                project=_require("GOOGLE_CLOUD_PROJECT"),
                location=None,
                api_key=_require("GEMINI_API_KEY"),
                agent_model=_require("ARGUS_AGENT_MODEL"),
                monitor_model=_require("ARGUS_MONITOR_MODEL"),
            )
        return cls(
            mode="cloud",
            project=_require("GOOGLE_CLOUD_PROJECT"),
            location=_require("GOOGLE_CLOUD_LOCATION"),
            api_key=None,
            agent_model=_require("ARGUS_AGENT_MODEL"),
            monitor_model=_require("ARGUS_MONITOR_MODEL"),
        )
