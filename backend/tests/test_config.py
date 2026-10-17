import pytest

from argus_api.config import (
    AGENT_MODEL_ENV,
    AGENT_THINKING,
    AUTH_EMULATOR_ENV,
    FALLBACK_MODEL_ENV,
    FALLBACK_MODELS,
    LOCAL_PROVIDER_KEYS,
    MONITOR_MODEL_ENV,
    MONITOR_THINKING,
    ORIGINS_ENV,
    PINNED_MODELS,
    ConfigError,
    Settings,
    model_name,
    parse_origins,
)

LOCAL_OPENROUTER = {
    "ARGUS_MODE": "local",
    "ARGUS_LLM_PROVIDER": "openrouter",
    "FIRESTORE_EMULATOR_HOST": "localhost:8081",
    AUTH_EMULATOR_ENV: "localhost:9099",
    "GOOGLE_CLOUD_PROJECT": "argus-local",
    "OPENROUTER_API_KEY": "or-test-key",
}
LOCAL_AISTUDIO = {
    **{k: v for k, v in LOCAL_OPENROUTER.items() if k != "OPENROUTER_API_KEY"},
    "ARGUS_LLM_PROVIDER": "aistudio",
    "GEMINI_API_KEY": "ai-test-key",
}
CLOUD = {
    "ARGUS_MODE": "cloud",
    "GOOGLE_CLOUD_PROJECT": "argus-prod",
    "GOOGLE_CLOUD_LOCATION": "asia-south1",
    ORIGINS_ENV: "https://argus-test.web.app,https://argus-test.firebaseapp.com",
}
ALL_NAMES = (
    set(LOCAL_OPENROUTER)
    | set(LOCAL_AISTUDIO)
    | set(CLOUD)
    | {AGENT_MODEL_ENV, MONITOR_MODEL_ENV, FALLBACK_MODEL_ENV}
)


def _set(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "env", [LOCAL_OPENROUTER, LOCAL_AISTUDIO, CLOUD], ids=["openrouter", "aistudio", "cloud"]
)
def test_each_required_variable_fails_loudly(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for missing in env:
        _set(monkeypatch, {k: v for k, v in env.items() if k != missing})
        with pytest.raises(ConfigError, match=missing):
            Settings.from_env()


@pytest.mark.parametrize("env", [LOCAL_OPENROUTER, LOCAL_AISTUDIO], ids=["openrouter", "aistudio"])
def test_local_provider_uses_its_own_key(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    _set(monkeypatch, env)
    s = Settings.from_env()
    assert s.api_key == env[LOCAL_PROVIDER_KEYS[env["ARGUS_LLM_PROVIDER"]]]
    assert s.provider == env["ARGUS_LLM_PROVIDER"]


def test_cloud_mode_is_vertex_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, CLOUD)
    assert Settings.from_env().provider == "vertex"
    _set(monkeypatch, {**CLOUD, "ARGUS_LLM_PROVIDER": "openrouter"})
    with pytest.raises(ConfigError, match="Vertex"):
        Settings.from_env()


def test_cloud_mode_refuses_the_auth_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**CLOUD, AUTH_EMULATOR_ENV: "localhost:9099"})
    with pytest.raises(ConfigError, match=AUTH_EMULATOR_ENV):
        Settings.from_env()


def test_unknown_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**LOCAL_OPENROUTER, "ARGUS_MODE": "staging"})
    with pytest.raises(ConfigError, match="staging"):
        Settings.from_env()


def test_cloud_origins_are_parsed_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, CLOUD)
    assert Settings.from_env().allowed_origins == parse_origins(CLOUD[ORIGINS_ENV])
    assert len(Settings.from_env().allowed_origins) == CLOUD[ORIGINS_ENV].count(",") + 1
    for bad in ("*", "https://x.web.app/", "argus.web.app", "ftp://x.web.app", "https://*.web.app"):
        _set(monkeypatch, {**CLOUD, ORIGINS_ENV: bad})
        with pytest.raises(ConfigError, match=ORIGINS_ENV):
            Settings.from_env()


def test_local_origins_are_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, LOCAL_OPENROUTER)
    assert Settings.from_env().allowed_origins == ()


@pytest.mark.parametrize(
    "env", [LOCAL_OPENROUTER, LOCAL_AISTUDIO, CLOUD], ids=["openrouter", "aistudio", "cloud"]
)
def test_model_ids_come_from_the_pinned_table(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    _set(monkeypatch, env)
    s = Settings.from_env()
    assert (s.agent_model, s.monitor_model) == PINNED_MODELS[s.provider]
    assert (s.agent_thinking, s.monitor_thinking) == (AGENT_THINKING, MONITOR_THINKING)


@pytest.mark.parametrize("name", [AGENT_MODEL_ENV, MONITOR_MODEL_ENV])
def test_cloud_mode_refuses_model_overrides(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _set(monkeypatch, {**CLOUD, name: "some-other-model"})
    with pytest.raises(ConfigError, match=name):
        Settings.from_env()


def test_local_mode_may_override_a_model_for_an_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**LOCAL_OPENROUTER, AGENT_MODEL_ENV: "google/other-model"})
    s = Settings.from_env()
    assert s.agent_model == "google/other-model"
    assert s.monitor_model == PINNED_MODELS["openrouter"][1]


# ---- the fallback agent model ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env", [LOCAL_OPENROUTER, LOCAL_AISTUDIO, CLOUD], ids=["openrouter", "aistudio", "cloud"]
)
def test_the_fallback_model_is_on_and_pinned_by_default(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    _set(monkeypatch, env)
    s = Settings.from_env()
    assert s.fallback_model == FALLBACK_MODELS[s.provider] != s.agent_model


@pytest.mark.parametrize("value", ["", "other-model"])
def test_cloud_mode_refuses_a_fallback_override(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _set(monkeypatch, {**CLOUD, FALLBACK_MODEL_ENV: value})
    with pytest.raises(ConfigError, match=FALLBACK_MODEL_ENV):
        Settings.from_env()


def test_local_mode_may_change_or_switch_off_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**LOCAL_OPENROUTER, FALLBACK_MODEL_ENV: "google/other-model"})
    assert Settings.from_env().fallback_model == "google/other-model"
    _set(monkeypatch, {**LOCAL_OPENROUTER, FALLBACK_MODEL_ENV: ""})
    assert Settings.from_env().fallback_model is None


def test_the_fallback_cannot_be_the_agent_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**LOCAL_OPENROUTER, AGENT_MODEL_ENV: FALLBACK_MODELS["openrouter"]})
    with pytest.raises(ConfigError, match=FALLBACK_MODEL_ENV):
        Settings.from_env()


def test_models_are_named_in_words() -> None:
    assert model_name(PINNED_MODELS["vertex"][0]) == "Gemini 3.8 Flash"
    assert model_name(FALLBACK_MODELS["openrouter"]) == "Gemini 3.1 Flash-Lite"
    assert model_name("some-unknown-model") == "some-unknown-model"
