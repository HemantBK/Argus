"""Settings for the evaluation: which provider, which key, which model ids. Nothing else.

The API's Settings.from_env (config.py) also requires the Firestore and Auth emulator hosts in local mode,
which an offline evaluation does not use, so the eval reads only what it needs and builds a config.Settings
from it (the agent takes a Settings for its model ids and thinking levels). Missing values raise
EvalConfigError naming the variable.

  ARGUS_LLM_PROVIDER    openrouter | aistudio | vertex
  OPENROUTER_API_KEY    for openrouter;  GEMINI_API_KEY for aistudio
  GOOGLE_CLOUD_PROJECT  for vertex (and recorded for the others);  GOOGLE_CLOUD_LOCATION for vertex
  ARGUS_AGENT_MODEL / ARGUS_MONITOR_MODEL  optional overrides of config.PINNED_MODELS (recorded as unpinned)

load_env_file reads KEY=VALUE lines from a .env file into os.environ for keys not already set, so the CLI
works without `set -a; . ./.env`. Values are never printed or logged.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from argus_api.config import (
    AGENT_MODEL_ENV,
    LOCAL_PROVIDER_KEYS,
    MONITOR_MODEL_ENV,
    PINNED_MODELS,
    Provider,
    Settings,
)

PROVIDER_ENV = "ARGUS_LLM_PROVIDER"
PROVIDERS: tuple[Provider, ...] = ("openrouter", "aistudio", "vertex")


class EvalConfigError(RuntimeError):
    pass


def load_env_file(path: Path) -> list[str]:
    """Set os.environ from a .env file for keys not already set. Returns the names it set (never values)."""
    if not path.is_file():
        raise EvalConfigError(f"env file {path} not found")
    set_names: list[str] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not name.replace("_", "").isalnum():
            raise EvalConfigError(f"{path} line {n} is not KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name not in os.environ:
            os.environ[name] = value
            set_names.append(name)
    return set_names


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _need(name: str, why: str) -> str:
    value = _env(name)
    if value is None:
        raise EvalConfigError(f"missing environment variable {name} ({why})")
    return value


@dataclass(frozen=True)
class EvalSettings:
    provider: Provider
    agent_model: str
    monitor_model: str
    pinned: bool  # both model ids are the ones in config.PINNED_MODELS for this provider
    settings: Settings  # what the agent and the LLM factory take

    @classmethod
    def from_env(cls) -> "EvalSettings":
        raw = _need(PROVIDER_ENV, f"one of {', '.join(PROVIDERS)}")
        if raw not in PROVIDERS:
            raise EvalConfigError(f"{PROVIDER_ENV} must be one of {', '.join(PROVIDERS)}, got {raw!r}")
        provider = next(p for p in PROVIDERS if p == raw)
        pinned_agent, pinned_monitor = PINNED_MODELS[provider]
        agent = _env(AGENT_MODEL_ENV) or pinned_agent
        monitor = _env(MONITOR_MODEL_ENV) or pinned_monitor
        if provider == "vertex":
            settings = Settings(
                mode="cloud",
                provider="vertex",
                project=_need("GOOGLE_CLOUD_PROJECT", "Vertex AI project"),
                location=_need("GOOGLE_CLOUD_LOCATION", "Vertex AI location"),
                api_key=None,
                agent_model=agent,
                monitor_model=monitor,
            )
        else:
            key_name = LOCAL_PROVIDER_KEYS[provider]
            settings = Settings(
                mode="local",
                provider=provider,
                project=_env("GOOGLE_CLOUD_PROJECT") or "argus-eval",
                location=None,
                api_key=_need(key_name, f"API key for {provider}"),
                agent_model=agent,
                monitor_model=monitor,
            )
        return cls(
            provider=provider,
            agent_model=agent,
            monitor_model=monitor,
            pinned=(agent, monitor) == (pinned_agent, pinned_monitor),
            settings=settings,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "EvalSettings":
        pinned_agent, pinned_monitor = PINNED_MODELS[settings.provider]
        return cls(
            provider=settings.provider,
            agent_model=settings.agent_model,
            monitor_model=settings.monitor_model,
            pinned=(settings.agent_model, settings.monitor_model) == (pinned_agent, pinned_monitor),
            settings=settings,
        )
