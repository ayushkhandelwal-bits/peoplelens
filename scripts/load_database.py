"""Load the existing HRMS export into the database (staging -> clean -> validate -> publish).

    python scripts/load_database.py          # load the export (data/dataset by default) into the configured database
    python scripts/load_database.py --reset  # drop and recreate all tables first
"""

import argparse

import _bootstrap  # noqa: F401

from src.ingestion.pipeline import run_pipeline


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reset", action="store_true", help="drop and recreate all tables")
    p.add_argument("--keep-staging", action="store_true", help="keep stg_* tables for inspection")
    args = p.parse_args()
    r = run_pipeline(regenerate=False, reset=args.reset, keep_staging=args.keep_staging)
    print(f"{r.status}: {sum(r.row_counts.values()):,} rows published; quality {r.quality}")


if __name__ == "__main__":
    main()
