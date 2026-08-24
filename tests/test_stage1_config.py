import json

import pytest

from stage1_2gis import cli
from stage1_2gis.cli import build_parser
from stage1_2gis.config import ConfigurationError, PostgresSettings, SheetTaskSettings, WorkerSettings


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


def test_sheet_task_settings_require_a_separate_planning_spreadsheet():
    with pytest.raises(ConfigurationError):
        SheetTaskSettings.from_env({})
    with pytest.raises(ConfigurationError, match="STAGE1_TASKS_PHRASES_SHEET"):
        SheetTaskSettings.from_env({"STAGE1_TASKS_SPREADSHEET_ID": "planning-id"})

    settings = SheetTaskSettings.from_env({
        "STAGE1_TASKS_SPREADSHEET_ID": "planning-id",
        "STAGE1_TASKS_PHRASES_SHEET": "phrases",
        "STAGE1_TASKS_CITIES_SHEET": "cities",
        "STAGE1_TASKS_SUMMARY_SHEET": "summary",
        "STAGE1_TASKS_RESULTS_SHEET": "results",
    })
    assert settings.spreadsheet_id == "planning-id"
    assert settings.summary_sheet == "summary"


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


def test_run_status_prints_pretty_json(monkeypatch, capsys):
    class Repository:
        def get_run_status(self, run_id):
            assert run_id == "run-123"
            return {"status": "completed", "result_counts": {"companies": 3}}

    monkeypatch.setattr(cli, "_build_runtime", lambda: (Repository(), object(), object()))

    assert cli.main(["run-status", "--run-id", "run-123"]) == 0

    output = capsys.readouterr().out
    assert output == '{\n  "run_id": "run-123",\n  "status": "completed",\n  "result_counts": {\n    "companies": 3\n  }\n}\n'
    assert json.loads(output)["result_counts"]["companies"] == 3
