import logging
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from pydantic import SecretStr, ValidationError

from links_garden.config import Settings, load_settings


def test_defaults_apply_when_environment_is_empty(monkeypatch: MonkeyPatch) -> None:
    for key in Settings.model_fields:
        monkeypatch.delenv(key.upper(), raising=False)

    settings = Settings(_env_file=None)

    assert settings.beeper_access_token == SecretStr("")
    assert settings.beeper_api_url == "http://127.0.0.1:23373"
    assert settings.beeper_chat_id == ""
    assert settings.ollama_url == "http://127.0.0.1:11434"
    assert settings.embedding_model == "bge-m3"
    assert settings.extraction_model == "qwen3:8b"
    assert settings.vault_path is None
    assert settings.vault_exclude == ("wiki", ".obsidian")
    assert settings.backfill_start_date is None
    assert settings.fetch_backend == "firecrawl"
    assert settings.firecrawl_api_key == SecretStr("")
    assert settings.api_token == SecretStr("")
    assert settings.database_path == Path("data/garden.db")
    assert settings.fetch_cache_dir == Path(".cache/fetch")
    assert settings.max_fetches_per_run == 50


def test_secrets_never_appear_in_repr_str_or_dump(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BEEPER_ACCESS_TOKEN", "beeper-secret-value")
    monkeypatch.setenv("API_TOKEN", "api-secret-value")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-secret-value")

    settings = Settings(_env_file=None)

    for rendered in (repr(settings), str(settings), str(settings.model_dump())):
        assert "beeper-secret-value" not in rendered
        assert "api-secret-value" not in rendered
        assert "firecrawl-secret-value" not in rendered


def test_vault_exclude_parses_comma_separated_string(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_EXCLUDE", "wiki,.obsidian, foo")

    settings = Settings(_env_file=None)

    assert settings.vault_exclude == ("wiki", ".obsidian", "foo")


def test_vault_exclude_drops_empty_entries(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_EXCLUDE", "")
    assert Settings(_env_file=None).vault_exclude == ()

    monkeypatch.setenv("VAULT_EXCLUDE", "wiki,,foo,")
    assert Settings(_env_file=None).vault_exclude == ("wiki", "foo")


def test_vault_exclude_passes_through_tuple_unchanged() -> None:
    settings = Settings(_env_file=None, vault_exclude=("a", "b"))

    assert settings.vault_exclude == ("a", "b")


def test_effective_fetch_backend_falls_back_to_direct_when_key_empty(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FETCH_BACKEND", "firecrawl")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.effective_fetch_backend == "direct"


def test_effective_fetch_backend_is_firecrawl_when_key_present(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FETCH_BACKEND", "firecrawl")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "some-key")

    settings = Settings(_env_file=None)

    assert settings.effective_fetch_backend == "firecrawl"


def test_effective_fetch_backend_is_direct_when_configured_direct(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FETCH_BACKEND", "direct")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "some-key")

    settings = Settings(_env_file=None)

    assert settings.effective_fetch_backend == "direct"


def test_load_settings_warns_once_when_key_missing(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FETCH_BACKEND", "firecrawl")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")

    with caplog.at_level(logging.WARNING, logger="links_garden.config"):
        load_settings()

    records = [r for r in caplog.records if r.name == "links_garden.config"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].message == (
        "FIRECRAWL_API_KEY is empty. Falling back to direct fetching, "
        "which sends requests from this machine's IP."
    )


def test_load_settings_logs_nothing_when_key_present(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FETCH_BACKEND", "firecrawl")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "some-key")

    with caplog.at_level(logging.WARNING, logger="links_garden.config"):
        load_settings()

    records = [r for r in caplog.records if r.name == "links_garden.config"]
    assert records == []


def test_invalid_fetch_backend_raises_validation_error(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("FETCH_BACKEND", "carrier-pigeon")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_max_fetches_per_run_of_zero_raises_validation_error(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FETCHES_PER_RUN", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_fetch_cache_dir_and_max_fetches_per_run_read_from_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FETCH_CACHE_DIR", "/tmp/cache")
    monkeypatch.setenv("MAX_FETCHES_PER_RUN", "10")

    settings = Settings(_env_file=None)

    assert settings.fetch_cache_dir == Path("/tmp/cache")
    assert settings.max_fetches_per_run == 10
