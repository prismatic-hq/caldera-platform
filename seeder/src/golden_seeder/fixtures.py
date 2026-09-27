from dataclasses import dataclass
from datetime import datetime, timedelta

from golden_seeder import GOLDEN_EPOCH, stable_id

Value = str | float | int | datetime | None


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Value, ...], ...]


STATIONS = ("KIL-01", "KIL-02", "MLO-01", "MLO-02", "HUA-01")
SEVERITIES = ("info", "warning", "critical")
ALERT_STATUSES = ("open", "acknowledged", "resolved")
SITES = ("summit-camp", "east-rift", "south-flank")
CREWS = ("crew-a", "crew-b", None)
PRIORITIES = ("low", "medium", "high")
WORK_ORDER_STATUSES = ("open", "in_progress", "done")


def _at(index: int) -> datetime:
    return GOLDEN_EPOCH + timedelta(hours=index)


def alerts(count: int = 12) -> Table:
    rows = []
    for index in range(count):
        key = f"alert-{index:04d}"
        created = _at(index)
        rows.append(
            (
                str(stable_id("alert", key)),
                STATIONS[index % len(STATIONS)],
                SEVERITIES[index % len(SEVERITIES)],
                ALERT_STATUSES[index % len(ALERT_STATUSES)],
                round(1.5 + (index % 7) * 0.4, 1),
                f"Golden alert {key}",
                created,
                created,
            )
        )
    columns = (
        "id",
        "station",
        "severity",
        "status",
        "magnitude",
        "message",
        "created_at",
        "updated_at",
    )
    return Table("tremor.alerts", columns, tuple(rows))


def work_orders(count: int = 12) -> Table:
    rows = []
    for index in range(count):
        key = f"work-order-{index:04d}"
        created = _at(index) + timedelta(minutes=30)
        rows.append(
            (
                str(stable_id("work_order", key)),
                SITES[index % len(SITES)],
                f"Inspect sensors for {key}",
                f"Follow-up on alert {stable_id('alert', f'alert-{index:04d}')}",
                PRIORITIES[index % len(PRIORITIES)],
                WORK_ORDER_STATUSES[index % len(WORK_ORDER_STATUSES)],
                CREWS[index % len(CREWS)],
                created,
                created,
            )
        )
    columns = (
        "id",
        "site",
        "title",
        "description",
        "priority",
        "status",
        "assigned_crew",
        "created_at",
        "updated_at",
    )
    return Table("steward.work_orders", columns, tuple(rows))


def dataset() -> tuple[Table, ...]:
    return (alerts(), work_orders())
