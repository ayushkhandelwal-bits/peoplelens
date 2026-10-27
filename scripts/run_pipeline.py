"""Run everything: generate data, load and validate, train models, write the monthly report.

    python scripts/run_pipeline.py                      # full profile
    python scripts/run_pipeline.py --profile dev        # fast development dataset
    python scripts/run_pipeline.py --skip-models        # data only
"""

import argparse

import _bootstrap  # noqa: F401

from src.ingestion.pipeline import run_pipeline


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="full", choices=["full", "dev"])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--reset", action="store_true")
    p.add_argument("--skip-models", action="store_true")
    p.add_argument("--skip-report", action="store_true")
    args = p.parse_args()
    r = run_pipeline(args.profile, args.seed, regenerate=True, reset=args.reset)
    print(f"Data pipeline {r.status}: {sum(r.row_counts.values()):,} rows; quality {r.quality}")
    if not args.skip_models:
        from src.models.training import train_all

        print("Models:", train_all())
    if not args.skip_report:
        from src.reporting.monthly_report import write_report

        paths = write_report()
        print("Report:", {k: str(v) for k, v in paths.items()})


if __name__ == "__main__":
    main()
