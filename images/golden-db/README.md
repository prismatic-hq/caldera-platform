# golden-db

Postgres image with the golden dataset baked into its data directory, tagged
`golden-db:<dataset-version>` (REQUIREMENTS.md FR-6). Not built yet; this describes the build.

## Build (`golden-image` workflow, nightly and on `main` merges that change migrations)

1. Start Postgres in the build container with an empty data directory under `/var/lib/postgresql/data`.
2. Run `alembic upgrade head` from `main` of `tremor-api` and `steward-api`; each creates its own
   schema (`tremor`, `steward`) and Alembic version table.
3. Load `golden-seeder` output (`uv run golden-seeder | psql`).
4. Compute a per-table checksum (ordered `SELECT` hashed with `sha256`) and fail on mismatch
   with the expected value for the same inputs.
5. Stop Postgres cleanly (`pg_ctl stop -m smart`) and copy the data directory into the final image.
6. Push to ECR as `golden-db:<dataset-version>`.

`dataset-version` = hash of the `golden-seeder --digest` output plus both Alembic heads, so the
same inputs always give the same tag and checksum (FR-6.3).

## Runtime contract

- Runs as a non-root user (uid 10001) so the `vent` chart security context applies unchanged.
- The entrypoint sets the role password from `POSTGRES_PASSWORD` with `ALTER ROLE` before
  accepting connections (FR-5.8), because every vent starts from the same data directory.
- The data directory is copied to the vent's `emptyDir` at start; a pod restart resets the vent.
- Compressed size stays under 1 GB (FR-6.4).
