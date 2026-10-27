# Deployment

The local installation is the complete reference implementation. A hosted demo is optional and free.

## Option 1 — Streamlit Community Cloud (free)

Streamlit Community Cloud hosts public Streamlit apps from a GitHub repository at no cost (check the current terms
at <https://streamlit.io/cloud> before deploying; no payment details are required at the time of writing).

1. Push the repository to GitHub (see the README).
2. At <https://share.streamlit.io> choose **Create app** → your repository → branch `main` → main file `app.py`.
3. *Advanced settings → Python version*: 3.13 (or 3.12). *Secrets* (optional):
   ```toml
   PEOPLELENS_PROFILE = "full"          # or "dev" for a faster first start
   PEOPLELENS_AUTO_BOOTSTRAP = "true"
   ```
4. Deploy. No database file is committed, so on first start the app runs the pipeline (generate → validate → load
   → train) into a local SQLite file — about 1–3 minutes for `full` on the free tier, ~15 seconds for `dev`. The
   container's disk is ephemeral; if the app is restarted the data is regenerated identically from the seed.

Limitations of the hosted demo: SQLite instead of PostgreSQL (role separation is demonstrated with the SQLite
authorizer), cold-start build time, shared CPU/memory limits. Do not connect a public demo to any real data.

## Option 2 — Docker (self-hosted, free)

```bash
docker compose up --build        # PostgreSQL 16 + app on http://localhost:8501
```

The app container builds the database on first launch; the PostgreSQL volume persists it. Read-only roles are
created by the pipeline from the passwords in the compose file (change them).

## Option 3 — any VM / laptop

Follow the README installation, then `python scripts/run_pipeline.py` and
`streamlit run app.py --server.address 0.0.0.0`.

## Pre-deployment checklist
* `pytest` and `ruff check .` pass; `python scripts/validate_project.py` reports 0 failed checks.
* `.env` is not committed (it is in `.gitignore`); secrets go in the platform's secret store.
* The synthetic-data notice is visible (sidebar *About* and Data & Governance).
