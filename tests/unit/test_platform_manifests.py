from cdk.stacks.addons import PLATFORM_PRIORITY, platform_manifests
from cdk.stacks.cluster import PREVIEW_TAINT

PREVIEW_POOL = {"karpenter.sh/nodepool": "preview-environments"}


def by_name(documents: list[dict]) -> dict[str, dict]:
    return {doc["metadata"]["name"]: doc for doc in documents}


def test_platform_priority_outranks_every_environment_class() -> None:
    classes = by_name(platform_manifests()["priorityclasses"])

    assert classes[PLATFORM_PRIORITY]["value"] == 10000
    assert classes[PLATFORM_PRIORITY]["globalDefault"] is False
    assert all(
        classes[PLATFORM_PRIORITY]["value"] > classes[name]["value"]
        for name in ("preview-headroom", "preview-environment", "baseline")
    )


def test_headroom_holds_capacity_on_the_tainted_preview_pool() -> None:
    [headroom] = platform_manifests()["headroom"]
    spec = headroom["spec"]["template"]["spec"]

    assert spec["nodeSelector"] == PREVIEW_POOL
    assert spec["tolerations"] == [{**PREVIEW_TAINT, "operator": "Equal"}]


def test_headroom_is_sized_by_the_working_hours_cron_only() -> None:
    [scaled_object] = platform_manifests()["headroom-scaledobject"]

    assert [trigger["type"] for trigger in scaled_object["spec"]["triggers"]] == ["cron"]
