# e2e

pytest suite (run with `pytest-xdist`) against a live preview environment: both APIs and the
cross-service flow where a critical tremor alert opens a steward work order (FR-8.1).

In-cluster it runs as the `services` chart's Helm test hook from the ECR `e2e` image
(`images/e2e/Dockerfile`, pushed on `main`), with `<SERVICE>_URL` set to each in-cluster Service:

```sh
helm test preview-<env> -n preview-<env> --logs --timeout 5m
```

From a laptop, set `PREVIEW_BASE_URL` to a template with a `{service}` placeholder:

```sh
cd e2e && PREVIEW_BASE_URL='https://{service}-quake-alerts.preview.example.com' uv run pytest
```

Tests are skipped when neither is set.
