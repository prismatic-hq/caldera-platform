# e2e

pytest suite run against a live preview environment. Skipped unless `PREVIEW_BASE_URL` is set to a URL template
with a `{service}` placeholder, e.g. `http://{service}:8000` in-cluster or
`https://{service}-<preview environment>.preview.<domain>` from a laptop.

```sh
PREVIEW_BASE_URL='https://{service}-quake-alerts.preview.example.com' uv run pytest e2e
```
