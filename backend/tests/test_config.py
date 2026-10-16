import pytest

from argus_api.config import ConfigError, Settings

REQUIRED = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "ARGUS_AGENT_MODEL",
    "ARGUS_MONITOR_MODEL",
    "ARGUS_EMBEDDING_MODEL",
]


def test_missing_variable_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("ARGUS_MONITOR_MODEL")
    with pytest.raises(ConfigError, match="ARGUS_MONITOR_MODEL"):
        Settings.from_env()


def test_all_variables_present(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.setenv(name, f"value-{name}")
    s = Settings.from_env()
    assert s.monitor_model == "value-ARGUS_MONITOR_MODEL"
