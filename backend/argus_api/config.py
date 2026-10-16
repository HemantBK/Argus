"""Runtime configuration. Every value comes from the environment; missing values fail at startup.

ARGUS_MODE=local  Firestore and Firebase Auth emulators (FIRESTORE_EMULATOR_HOST,
                  FIREBASE_AUTH_EMULATOR_HOST) and a development LLM provider: ARGUS_LLM_PROVIDER=aistudio
                  (GEMINI_API_KEY) or openrouter (OPENROUTER_API_KEY).
ARGUS_MODE=cloud  real Firestore, real Firebase Auth and Gemini on Vertex AI only (rules B-01, B-02).
                  The Auth emulator variable must be absent: with it set, firebase-admin accepts unsigned
                  tokens.
"""

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["local", "cloud"]
Provider = Literal["vertex", "aistudio", "openrouter"]
LOCAL_PROVIDER_KEYS: dict[str, str] = {"aistudio": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
# Read by firebase-admin itself; when set, ID tokens are accepted without a signature check.
AUTH_EMULATOR_ENV = "FIREBASE_AUTH_EMULATOR_HOST"


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
    provider: Provider
    project: str
    location: str | None
    api_key: str | None
    agent_model: str
    monitor_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode = _require("ARGUS_MODE")
        if mode == "cloud":
            provider = os.environ.get("ARGUS_LLM_PROVIDER", "vertex").strip()
            if provider != "vertex":
                raise ConfigError(f"Cloud mode must use Gemini on Vertex AI, not {provider!r}")
            if os.environ.get(AUTH_EMULATOR_ENV, "").strip():
                raise ConfigError(
                    f"{AUTH_EMULATOR_ENV} is set in cloud mode; the API would accept unsigned ID tokens"
                )
            return cls(
                mode="cloud",
                provider="vertex",
                project=_require("GOOGLE_CLOUD_PROJECT"),
                location=_require("GOOGLE_CLOUD_LOCATION"),
                api_key=None,
                agent_model=_require("ARGUS_AGENT_MODEL"),
                monitor_model=_require("ARGUS_MONITOR_MODEL"),
            )
        if mode != "local":
            raise ConfigError(f"ARGUS_MODE must be 'local' or 'cloud', got {mode!r}")
        provider = _require("ARGUS_LLM_PROVIDER")
        if provider not in LOCAL_PROVIDER_KEYS:
            raise ConfigError(
                f"Local ARGUS_LLM_PROVIDER must be one of {sorted(LOCAL_PROVIDER_KEYS)}, got {provider!r}"
            )
        _require("FIRESTORE_EMULATOR_HOST")
        _require(AUTH_EMULATOR_ENV)
        return cls(
            mode="local",
            provider="aistudio" if provider == "aistudio" else "openrouter",
            project=_require("GOOGLE_CLOUD_PROJECT"),
            location=None,
            api_key=_require(LOCAL_PROVIDER_KEYS[provider]),
            agent_model=_require("ARGUS_AGENT_MODEL"),
            monitor_model=_require("ARGUS_MONITOR_MODEL"),
        )
