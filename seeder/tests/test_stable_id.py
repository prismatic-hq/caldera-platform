import uuid

from golden_seeder import GOLDEN_NAMESPACE, stable_id


def test_stable_id_is_deterministic_uuid5_over_golden_namespace() -> None:
    assert stable_id("alert", "KIL-01") == stable_id("alert", "KIL-01")
    assert stable_id("alert", "KIL-01") == uuid.uuid5(GOLDEN_NAMESPACE, "alert:KIL-01")
    assert stable_id("alert", "KIL-01") != stable_id("work_order", "KIL-01")
