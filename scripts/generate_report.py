"""Generate the monthly workforce report (HTML + Markdown + CSV) from the database.

    python scripts/generate_report.py                 # latest month in the data
    python scripts/generate_report.py --month 2026-06
"""

import argparse

import _bootstrap  # noqa: F401

from src.reporting.monthly_report import write_report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--month", help="reporting month as YYYY-MM (default: latest)")
    args = p.parse_args()
    for kind, path in write_report(args.month).items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
