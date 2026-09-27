# golden-seeder

Writes the fixed dataset for the nightly golden snapshot (REQUIREMENTS.md Section 5).
No randomness: UUIDv5 IDs over a fixed namespace, fixed timestamps, stable insert order.

```sh
uv run pytest
```
