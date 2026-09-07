import os

# src.sync_stats calls sys.exit(1) at import time when SPREADSHEET_ID is unset.
# Set it before any test module imports the package. load_dotenv() does not
# override values that are already in the environment, so a real .env cannot
# leak into the tests either.
os.environ.setdefault("SPREADSHEET_ID", "test-spreadsheet-id")

import pytest

from src import config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Run every test against a predictable config and a scratch working dir.

    sync_stats() resolves `.last_sync_timestamp` relative to the cwd, so tests
    must not run in the repo root.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setattr(config, "SYNC_SOURCE", "garmin")
    monkeypatch.setattr(config, "SPREADSHEET_ID", "test-spreadsheet-id")
    monkeypatch.setattr(config, "GARMIN_SHEET_SECRET_NAME", None)
