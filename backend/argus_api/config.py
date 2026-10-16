"""Runtime configuration. Every value comes from the environment; missing values fail at startup.

ARGUS_MODE=local  Firestore and Firebase Auth emulators (FIRESTORE_EMULATOR_HOST,
                  FIREBASE_AUTH_EMULATOR_HOST) and a development LLM provider: ARGUS_LLM_PROVIDER=aistudio
                  (GEMINI_API_KEY) or openrouter (OPENROUTER_API_KEY).
ARGUS_MODE=cloud  real Firestore, real Firebase Auth and Gemini on Vertex AI only (rules B-01, B-02).
                  The Auth emulator variable must be absent: with it set, firebase-admin accepts unsigned
                  tokens.

Model ids are pinned here, per provider (PINNED_MODELS; E2), never aliases. In local mode
ARGUS_AGENT_MODEL / ARGUS_MONITOR_MODEL may override them for an experiment; in cloud mode setting either is
an error, so the deployed app always runs the pinned versions. AGENT_THINKING and MONITOR_THINKING are the
pinned thinking levels, measured live on 2026-10-16: the agent's default thinking took 17-36 s per draft and
broke the run budget at no gain in accuracy ("low" drafts in 3-8 s); the monitor's default made it ask for
needless revisions of correct drafts, which "medium" stopped at about 1-3 s per judgement.

ARGUS_ALLOWED_ORIGINS  comma-separated browser origins allowed to call the API (CORS), for example
                  "https://argus-demo.web.app,https://argus-demo.firebaseapp.com". Required in cloud mode;
                  optional locally (unset: no cross-origin browser calls are allowed).
"""

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["local", "cloud"]
Provider = Literal["vertex", "aistudio", "openrouter"]
ThinkingLevel = Literal["minimal", "low", "medium", "high"]

# Gemini model ids as Vertex AI and AI Studio name them, and as OpenRouter does ("google/" prefix).
GEMINI_AGENT_MODEL = "gemini-3.8-flash"
GEMINI_MONITOR_MODEL = "gemini-3.5-flash-lite"
OPENROUTER_PREFIX = "google/"
PINNED_MODELS: dict[Provider, tuple[str, str]] = {  # provider -> (agent model, monitor model)
    "vertex": (GEMINI_AGENT_MODEL, GEMINI_MONITOR_MODEL),
    "aistudio": (GEMINI_AGENT_MODEL, GEMINI_MONITOR_MODEL),
    "openrouter": (OPENROUTER_PREFIX + GEMINI_AGENT_MODEL, OPENROUTER_PREFIX + GEMINI_MONITOR_MODEL),
}
AGENT_THINKING: ThinkingLevel = "low"
MONITOR_THINKING: ThinkingLevel = "medium"
AGENT_MODEL_ENV = "ARGUS_AGENT_MODEL"
MONITOR_MODEL_ENV = "ARGUS_MONITOR_MODEL"
LOCAL_PROVIDER_KEYS: dict[str, str] = {"aistudio": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
ORIGINS_ENV = "ARGUS_ALLOWED_ORIGINS"
# Read by firebase-admin itself; when set, ID tokens are accepted without a signature check.
AUTH_EMULATOR_ENV = "FIREBASE_AUTH_EMULATOR_HOST"


class ConfigError(RuntimeError):
    pass


def parse_origins(raw: str) -> tuple[str, ...]:
    """Origins from a comma list; each an exact http(s) origin: no wildcard, path or trailing slash."""
    origins = tuple(o.strip() for o in raw.split(",") if o.strip())
    for o in origins:
        scheme, sep, host = o.partition("://")
        if scheme not in ("http", "https") or not sep or not host or "/" in host or "*" in o:
            raise ConfigError(
                f"{ORIGINS_ENV} entry {o!r} is not an exact http(s) origin such as https://x.web.app"
            )
    return origins


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


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
    allowed_origins: tuple[str, ...] = ()
    agent_thinking: ThinkingLevel = AGENT_THINKING
    monitor_thinking: ThinkingLevel = MONITOR_THINKING

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
            overrides = [n for n in (AGENT_MODEL_ENV, MONITOR_MODEL_ENV) if _optional(n) is not None]
            if overrides:
                raise ConfigError(
                    f"{', '.join(overrides)} set in cloud mode; the deployed app uses the model ids "
                    "pinned in config.PINNED_MODELS"
                )
            agent, monitor = PINNED_MODELS["vertex"]
            return cls(
                mode="cloud",
                provider="vertex",
                project=_require("GOOGLE_CLOUD_PROJECT"),
                location=_require("GOOGLE_CLOUD_LOCATION"),
                api_key=None,
                agent_model=agent,
                monitor_model=monitor,
                allowed_origins=parse_origins(_require(ORIGINS_ENV)),
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
        local: Provider = "aistudio" if provider == "aistudio" else "openrouter"
        agent, monitor = PINNED_MODELS[local]
        return cls(
            mode="local",
            provider=local,
            project=_require("GOOGLE_CLOUD_PROJECT"),
            location=None,
            api_key=_require(LOCAL_PROVIDER_KEYS[provider]),
            agent_model=_optional(AGENT_MODEL_ENV) or agent,
            monitor_model=_optional(MONITOR_MODEL_ENV) or monitor,
            allowed_origins=parse_origins(os.environ.get(ORIGINS_ENV, "")),
        )
