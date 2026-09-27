# golden-seeder

Writes the fixed dataset baked into the golden DB image (REQUIREMENTS.md FR-6.1).
No randomness: UUIDv5 IDs over a fixed namespace, fixed timestamps, stable insert order.

```sh
uv run golden-seeder --out seed.sql
uv run golden-seeder --digest
```
