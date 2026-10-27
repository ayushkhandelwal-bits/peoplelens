"""Generate the synthetic HRMS export (CSV files + manifest) without loading it.

    python scripts/generate_data.py                 # full profile, seed 42
    python scripts/generate_data.py --profile dev   # small dataset for quick runs
    python scripts/generate_data.py --seed 7
    python scripts/generate_data.py --publish       # refresh the committed dataset in data/dataset
"""

import argparse
import json

import _bootstrap  # noqa: F401

from src.ingestion.pipeline import configure_logging, generate_export


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="full", choices=["full", "dev"])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--publish", action="store_true", help="Write to data/dataset (the dataset kept in Git).")
    args = p.parse_args()
    configure_logging()
    from config.settings import DATASET_DIR

    manifest = generate_export(args.profile, args.seed, DATASET_DIR if args.publish else None)
    rows = {k: v["rows"] for k, v in manifest["files"].items()}
    print(json.dumps({"profile": args.profile, "rows": rows, "total_rows": sum(rows.values()),
                      "noise_injected": manifest["noise_injected"]}, indent=2))


if __name__ == "__main__":
    main()
