import hashlib
from datetime import datetime

from golden_seeder.fixtures import Table, Value


def literal(value: Value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + value.replace("'", "''") + "'"


def render_table(table: Table) -> str:
    columns = ", ".join(table.columns)
    values = ",\n".join(
        "  (" + ", ".join(literal(value) for value in row) + ")" for row in table.rows
    )
    return f"INSERT INTO {table.name} ({columns}) VALUES\n{values};\n"


def render(tables: tuple[Table, ...]) -> str:
    return "BEGIN;\n" + "".join(render_table(table) for table in tables) + "COMMIT;\n"


def digest(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()
