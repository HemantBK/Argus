import pytest

from argus_api.config import ConfigError, Settings

LOCAL = {
    "ARGUS_MODE": "local",
    "FIRESTORE_EMULATOR_HOST": "localhost:8081",
    "GOOGLE_CLOUD_PROJECT": "argus-local",
    "GEMINI_API_KEY": "test-key",
    "ARGUS_AGENT_MODEL": "agent-model",
    "ARGUS_MONITOR_MODEL": "monitor-model",
}
CLOUD = {
    "ARGUS_MODE": "cloud",
    "GOOGLE_CLOUD_PROJECT": "argus-prod",
    "GOOGLE_CLOUD_LOCATION": "asia-south1",
    "ARGUS_AGENT_MODEL": "agent-model",
    "ARGUS_MONITOR_MODEL": "monitor-model",
}


def _set(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name in set(LOCAL) | set(CLOUD):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("env", [LOCAL, CLOUD], ids=["local", "cloud"])
def test_each_required_variable_fails_loudly(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for missing in env:
        _set(monkeypatch, {k: v for k, v in env.items() if k != missing})
        with pytest.raises(ConfigError, match=missing):
            Settings.from_env()


def test_local_mode_uses_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, LOCAL)
    s = Settings.from_env()
    assert (s.mode, s.api_key, s.location) == ("local", LOCAL["GEMINI_API_KEY"], None)


def test_cloud_mode_has_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, CLOUD)
    s = Settings.from_env()
    assert (s.mode, s.api_key, s.location) == ("cloud", None, CLOUD["GOOGLE_CLOUD_LOCATION"])


def test_unknown_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**LOCAL, "ARGUS_MODE": "staging"})
    with pytest.raises(ConfigError, match="staging"):
        Settings.from_env()
