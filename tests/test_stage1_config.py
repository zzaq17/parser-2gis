import pytest

from stage1_2gis.config import ConfigurationError, PostgresSettings, WorkerSettings


def test_postgres_settings_from_env():
    settings = PostgresSettings.from_env(
        {
            "POSTGRES_DB": "pipeline",
            "POSTGRES_USER": "worker",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_PORT": "5433",
        }
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 5433
    assert "dbname=pipeline" in settings.dsn


def test_postgres_settings_reject_missing_credentials():
    with pytest.raises(ConfigurationError):
        PostgresSettings.from_env({})


def test_worker_settings_reject_non_positive_values():
    with pytest.raises(ConfigurationError):
        WorkerSettings.from_env({"STAGE1_MAX_ATTEMPTS": "0"})
