"""Simulated HRMS export ("landing zone").

The generator's output is written the way an HR system export typically
arrives: one CSV per entity plus a manifest with row counts and SHA-256
checksums. A small, documented amount of *realistic noise* is injected into the
raw files (inconsistent casing/whitespace, a few exact duplicate rows, mixed
date formats and a handful of invalid amounts) so that the cleaning stage has
real work to do and its effect can be audited. This is a simulation; PeopleLens
does not connect to any real HRMS.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import GenerationConfig

RAW_DIR_NAME = "raw"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inject_noise(tables: dict[str, pd.DataFrame], seed: int) -> dict[str, dict]:
    """Mutate copies of selected tables with realistic export noise. Returns a log."""
    rng = np.random.default_rng(seed + 7)
    log: dict[str, dict] = {}

    emp = tables["employees"]
    k = rng.choice(len(emp), size=max(1, len(emp) // 200), replace=False)
    emp.loc[emp.index[k], "current_status"] = emp.loc[emp.index[k], "current_status"].str.lower() + " "
    k2 = rng.choice(len(emp), size=max(1, len(emp) // 250), replace=False)
    emp.loc[emp.index[k2], "employment_type"] = " " + emp.loc[emp.index[k2], "employment_type"].str.upper()
    log["employees"] = {"status_case_whitespace": int(len(k)), "employment_type_case_whitespace": int(len(k2))}

    for name, frac in (("recruitment_events", 0.002), ("employee_events", 0.001)):
        df = tables[name]
        dup = df.sample(n=max(1, int(len(df) * frac)), random_state=seed)
        tables[name] = pd.concat([df, dup], ignore_index=True)
        log[name] = {"exact_duplicate_rows": int(len(dup))}

    sv = tables["engagement_surveys"]
    k3 = rng.choice(len(sv), size=max(1, len(sv) // 300), replace=False)
    sv["survey_date"] = sv["survey_date"].astype(object)
    sv.loc[sv.index[k3], "survey_date"] = [pd.Timestamp(d).strftime("%d/%m/%Y") for d in sv.loc[sv.index[k3], "survey_date"]]
    log["engagement_surveys"] = {"ddmmyyyy_dates": int(len(k3))}

    tr = tables["training_records"]
    k4 = rng.choice(len(tr), size=max(1, len(tr) // 2000), replace=False)
    tr.loc[tr.index[k4], "training_cost"] = -tr.loc[tr.index[k4], "training_cost"]
    log["training_records"] = {"negative_cost_rows": int(len(k4))}
    return log


def write_export(tables: dict[str, pd.DataFrame], labels: pd.DataFrame, cfg: GenerationConfig,
                 out_dir: Path, noise: bool = True, profile: str = "custom") -> dict:
    raw = out_dir / RAW_DIR_NAME
    raw.mkdir(parents=True, exist_ok=True)
    tables = {k: v.copy() for k, v in tables.items()}
    noise_log = inject_noise(tables, cfg.seed) if noise else {}
    files = {}
    for name, df in tables.items():
        path = raw / f"{name}.csv"
        df.to_csv(path, index=False)
        files[name] = {"file": path.name, "rows": int(len(df)), "sha256": _sha256(path)}
    # Ground-truth labels for NLP evaluation are kept OUTSIDE the export/database.
    labels.to_csv(out_dir / "feedback_labels.csv", index=False)
    manifest = {
        "source_system": "Simulated HRMS export (synthetic data)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "generation_config": cfg.to_dict(),
        "noise_injected": noise_log,
        "files": files,
    }
    (raw / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


class ManifestError(RuntimeError):
    pass


def read_export(out_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    """Read and verify an export (row counts and checksums must match the manifest)."""
    raw = out_dir / RAW_DIR_NAME
    mpath = raw / "manifest.json"
    if not mpath.exists():
        raise ManifestError(f"No manifest found in {raw}. Run scripts/generate_data.py first.")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    tables = {}
    for name, meta in manifest["files"].items():
        path = raw / meta["file"]
        if not path.exists():
            raise ManifestError(f"Missing export file {path.name}")
        if _sha256(path) != meta["sha256"]:
            raise ManifestError(f"Checksum mismatch for {path.name}: file changed after export")
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        if len(df) != meta["rows"]:
            raise ManifestError(f"Row count mismatch for {path.name}: {len(df)} != {meta['rows']}")
        tables[name] = df
    return tables, manifest
