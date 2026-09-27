import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def chart_dirs(charts_root: Path) -> list[Path]:
    return sorted(path.parent for path in charts_root.glob("*/Chart.yaml"))


def has_dependencies(chart: Path) -> bool:
    return bool(yaml.safe_load((chart / "Chart.yaml").read_text()).get("dependencies"))


def render_commands(chart: Path, out_dir: Path) -> list[tuple[list[str], Path]]:
    commands = []
    for values in sorted((chart / "ci").glob("*-values.yaml")):
        release = f"{chart.name}-{values.name.removesuffix('-values.yaml')}"
        command = ["helm", "template", release, str(chart), "-n", release, "-f", str(values)]
        commands.append((command, out_dir / f"{release}.yaml"))
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render every chart's ci/ examples for validation")
    parser.add_argument("--charts", type=Path, default=Path("charts"))
    parser.add_argument("--platform", type=Path, default=Path("platform"))
    parser.add_argument("--out", type=Path, default=Path(".rendered"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for chart in chart_dirs(args.charts):
        if has_dependencies(chart):
            subprocess.run(["helm", "dependency", "build", str(chart)], check=True)
        for command, target in render_commands(chart, args.out):
            target.write_text(
                subprocess.run(command, check=True, capture_output=True, text=True).stdout
            )
            print(f"rendered {target}")
    for manifest in sorted(args.platform.glob("*.yaml")):
        shutil.copy(manifest, args.out / f"platform-{manifest.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
