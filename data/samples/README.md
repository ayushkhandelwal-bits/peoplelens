# Sample data (synthetic)

A small, clearly labelled extract of the **dev** profile (seed 42): up to 300 rows per table, filtered to the first
150 employees where a table has an `employee_id`. It lets reviewers inspect the shape of every table without running
the generator. **Everything is simulated**; foreign keys outside the extract are not guaranteed to resolve.

The full datasets are reproducible with:

    python scripts/generate_data.py               # full profile
    python scripts/generate_data.py --profile dev  # small profile
