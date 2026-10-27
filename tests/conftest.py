"""Test fixtures.

The whole suite runs against an isolated, temporary SQLite database built
from the small ``dev`` profile, so tests never touch the real database,
artefacts or reports. Environment variables are set before any project
module is imported.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="peoplelens_tests_"))
os.environ["PEOPLELENS_DATA_DIR"] = str(_TMP / "data")
os.environ["PEOPLELENS_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["PEOPLELENS_REPORT_DIR"] = str(_TMP / "reports")
os.environ["PEOPLELENS_LOG_DIR"] = str(_TMP / "logs")
os.environ["PEOPLELENS_DATABASE_URL"] = f"sqlite:///{(_TMP / 'data' / 'test.db').as_posix()}"
os.environ["PEOPLELENS_PROFILE"] = "dev"
os.environ.pop("PEOPLELENS_VIEWER_DATABASE_URL", None)
os.environ.pop("PEOPLELENS_ANALYST_DATABASE_URL", None)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def dev_tables():
    """Generated (un-exported) dev-profile tables and NLP labels."""
    from config.settings import get_generation_config
    from src.data_generation.simulator import generate

    return generate(get_generation_config("dev"))


@pytest.fixture(scope="session")
def built_db():
    """Full pipeline + model training on the dev profile (built once per session)."""
    from src.ingestion.pipeline import run_pipeline
    from src.models.training import train_all

    result = run_pipeline("dev")
    models = train_all()
    return {"pipeline": result, "models": models}


@pytest.fixture(scope="session")
def engine(built_db):
    from database.connection import get_engine

    return get_engine()
