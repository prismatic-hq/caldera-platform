import argparse
import sys
from pathlib import Path

from golden_seeder.fixtures import dataset
from golden_seeder.render import digest, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the golden dataset as SQL")
    parser.add_argument("--out", type=Path, help="Write SQL here instead of stdout")
    parser.add_argument("--digest", action="store_true", help="Print the dataset digest only")
    args = parser.parse_args(argv)
    sql = render(dataset())
    if args.digest:
        sys.stdout.write(digest(sql) + "\n")
    elif args.out:
        args.out.write_text(sql)
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
