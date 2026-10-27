"""Central configuration for PeopleLens.

All runtime settings come from environment variables (optionally loaded from a
local ``.env`` file) so that credentials are never hard-coded or committed.
Simulation parameters live in :class:`GenerationConfig`; two named profiles are
provided: ``full`` (portfolio-scale) and ``dev`` (small, used by tests).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (avoids an extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(os.environ.get("PEOPLELENS_DATA_DIR", PROJECT_ROOT / "data"))
GENERATED_DIR = DATA_DIR / "generated"
# The published synthetic dataset committed to the repository (full profile, seed 42).
DATASET_DIR = Path(os.environ.get("PEOPLELENS_DATASET_DIR", PROJECT_ROOT / "data" / "dataset"))
ARTIFACT_DIR = Path(os.environ.get("PEOPLELENS_ARTIFACT_DIR", PROJECT_ROOT / "artifacts"))
REPORT_DIR = Path(os.environ.get("PEOPLELENS_REPORT_DIR", PROJECT_ROOT / "reports"))
LOG_DIR = Path(os.environ.get("PEOPLELENS_LOG_DIR", PROJECT_ROOT / "logs"))


def default_sqlite_url() -> str:
    return f"sqlite:///{(DATA_DIR / 'peoplelens.db').as_posix()}"


@dataclass(frozen=True)
class AppSettings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("PEOPLELENS_DATABASE_URL", default_sqlite_url())
    )
    # Optional separate credentials for the read-only viewer role (PostgreSQL).
    viewer_database_url: str | None = field(
        default_factory=lambda: os.environ.get("PEOPLELENS_VIEWER_DATABASE_URL") or None
    )
    analyst_readonly_database_url: str | None = field(
        default_factory=lambda: os.environ.get("PEOPLELENS_ANALYST_DATABASE_URL") or None
    )
    profile: str = field(default_factory=lambda: os.environ.get("PEOPLELENS_PROFILE", "full"))
    # Build the database automatically on first launch (useful for free hosting).
    auto_bootstrap: bool = field(
        default_factory=lambda: os.environ.get("PEOPLELENS_AUTO_BOOTSTRAP", "true").lower()
        in {"1", "true", "yes"}
    )
    sql_row_limit: int = 5000
    sql_timeout_seconds: int = 10
    min_group_size: int = 10  # small-cell suppression threshold for survey data

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@dataclass(frozen=True)
class GenerationConfig:
    """Parameters of the workforce simulation. Every random draw uses ``seed``."""

    seed: int = 42
    start_date: date = date(2023, 10, 1)
    months: int = 36  # historical window: Oct-2023 .. Sep-2026
    initial_headcount: int = 5400
    n_departments: int = 24  # operating departments (+1 Executive Office)
    locations: tuple[str, ...] = (
        "Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad", "Pune", "Chennai", "Jaipur",
    )
    annual_voluntary_attrition: float = 0.155  # organisation-wide calibration target
    annual_involuntary_attrition: float = 0.03
    annual_growth: float = 0.065  # realised headcount growth target (actuals)
    plan_growth: float = 0.085  # growth assumed in the annual operating plan (AOP)
    hiring_volume_multiplier: float = 1.0  # scales requisition opening
    survey_frequency_months: int = 3  # quarterly pulse
    survey_response_rate: float = 0.58
    feedback_comment_rate: float = 0.33
    performance_review_frequency_months: int = 12  # annual cycle, April
    forecast_plan_months: int = 6  # forward AOP months beyond history
    currency: str = "INR"

    @property
    def end_date(self) -> date:
        y = self.start_date.year + (self.start_date.month - 1 + self.months) // 12
        m = (self.start_date.month - 1 + self.months) % 12 + 1
        from datetime import timedelta

        return date(y, m, 1) - timedelta(days=1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat()
        d["end_date"] = self.end_date.isoformat()
        d["locations"] = list(self.locations)
        return d


PROFILES: dict[str, GenerationConfig] = {
    "full": GenerationConfig(),
    "dev": GenerationConfig(initial_headcount=700, months=24, start_date=date(2024, 10, 1)),
}


def get_generation_config(profile: str | None = None, **overrides) -> GenerationConfig:
    profile = profile or os.environ.get("PEOPLELENS_PROFILE", "full")
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Choose from {sorted(PROFILES)}")
    cfg = PROFILES[profile]
    return replace(cfg, **overrides) if overrides else cfg


def get_settings() -> AppSettings:
    return AppSettings()
