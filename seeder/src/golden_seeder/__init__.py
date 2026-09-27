import uuid
from datetime import UTC, datetime

GOLDEN_NAMESPACE = uuid.UUID("7f3c9a52-4e1b-4d0f-8a6e-2b9d5c1e0a47")
GOLDEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def stable_id(entity: str, natural_key: str) -> uuid.UUID:
    return uuid.uuid5(GOLDEN_NAMESPACE, f"{entity}:{natural_key}")
