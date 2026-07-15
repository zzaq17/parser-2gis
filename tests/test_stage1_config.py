import pytest

from stage1_2gis.cli import build_parser
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


def test_google_cli_reads_dotenv_before_resolving_argument_defaults(tmp_path, monkeypatch):
    credentials_path = tmp_path / "google.json"
    (tmp_path / ".env").write_text(
        "STAGE1_SPREADSHEET_ID=sheet-from-dotenv\n"
        f"GOOGLE_APPLICATION_CREDENTIALS={credentials_path}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    args = build_parser().parse_args(["sync-google-domains"])

    assert args.spreadsheet_id == "sheet-from-dotenv"
    assert args.credentials_path == str(credentials_path)
