from pathlib import Path

from scripts.render_charts import chart_dirs, has_dependencies, render_commands


def make_chart(root: Path, name: str, chart_yaml: str, examples: tuple[str, ...] = ()) -> Path:
    chart = root / name
    (chart / "ci").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(chart_yaml)
    for example in examples:
        (chart / "ci" / example).write_text("{}\n")
    return chart


def test_finds_every_chart_in_order(tmp_path: Path) -> None:
    make_chart(tmp_path, "services", "name: services\n")
    make_chart(tmp_path, "service", "name: service\n")
    (tmp_path / "notes").mkdir()

    assert [chart.name for chart in chart_dirs(tmp_path)] == ["service", "services"]


def test_detects_dependencies(tmp_path: Path) -> None:
    umbrella = make_chart(
        tmp_path, "services", "name: services\ndependencies:\n  - name: service\n"
    )
    leaf = make_chart(tmp_path, "service", "name: service\n")

    assert has_dependencies(umbrella)
    assert not has_dependencies(leaf)


def test_renders_each_ci_example_into_its_own_release(tmp_path: Path) -> None:
    chart = make_chart(
        tmp_path,
        "service",
        "name: service\n",
        ("default-values.yaml", "route-values.yaml", "notes.md"),
    )
    out = tmp_path / "out"

    commands = render_commands(chart, out)

    assert [target.name for _, target in commands] == [
        "service-default.yaml",
        "service-route.yaml",
    ]
    assert commands[0][0] == [
        "helm",
        "template",
        "service-default",
        str(chart),
        "-n",
        "service-default",
        "-f",
        str(chart / "ci" / "default-values.yaml"),
    ]
