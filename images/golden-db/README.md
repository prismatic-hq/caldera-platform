# golden-db

Postgres with the golden dataset baked into the image, tagged `golden-db:<dataset-version>`
(REQUIREMENTS.md FR-6). Built by `.github/workflows/golden-image.yml` nightly, on dispatch and on
`main` pushes that change the seeder or this directory.

## Build

1. `dataset-version` = hash of the `golden-seeder` digest plus the single Alembic head of
   `tremor-api` and `steward-api` on `main` (`scripts/golden_image.py version`). If ECR already
   has that tag, the build is skipped and only `latest` and SSM are updated.
2. `prepare-dataset.sh` runs `main` migrations of both services and `golden-seeder` against two
   fresh Postgres containers, fails if their per-table checksums (`checksums.sql`) differ, and
   dumps the first one.
3. The Dockerfile restores the dump per platform (amd64, arm64), fails unless the checksums
   match the workflow's, and stops Postgres cleanly before copying the data directory.
4. The workflow fails if any platform is over 1 GiB compressed, then pushes the image and writes
   the version to SSM `/prismatic/golden-db/dataset-version`.

Local build (arm64 or amd64 only):

```sh
images/golden-db/prepare-dataset.sh ../tremor-api ../steward-api /tmp/golden
docker buildx build --load -t golden-db:local --build-context dataset=/tmp/golden images/golden-db
```

## Runtime contract

- Runs as uid 10001 with a read-only root filesystem; `PGDATA` must be writable (`emptyDir`).
- On start the entrypoint copies the golden data into an empty `PGDATA`, sets the role password
  from `POSTGRES_PASSWORD` with `ALTER ROLE` while listening on the socket only (FR-5.8), then
  listens on TCP. It refuses to start without `POSTGRES_PASSWORD`; the baked role has no password.
- A pod restart gives a fresh `emptyDir`, so the preview environment resets to golden data.
