# e2e

pytest suite run against a live vent. Skipped unless `VENT_BASE_URL` is set to a URL template
with a `{service}` placeholder, e.g. `http://{service}:8000` in-cluster or
`https://{service}-<vent>.preview.<domain>` from a laptop.

```sh
VENT_BASE_URL='https://{service}-quake-alerts.preview.example.com' uv run pytest e2e
```
