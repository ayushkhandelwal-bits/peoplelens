"""Train and evaluate the predictive models (deliberate refresh; the app never retrains on page load).

    python scripts/train_models.py
"""

import json

import _bootstrap  # noqa: F401

from src.ingestion.pipeline import configure_logging
from src.models.training import train_all


def main() -> None:
    configure_logging()
    summary = train_all()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
