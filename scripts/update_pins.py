"""Rewrite requirements.txt pins to the versions installed in this Python.

Used by the weekly "Library updates" workflow: it installs the newest
versions in a scratch environment, runs every test there, and only then
writes the new pins with this script.

Usage: <scratch-python> scripts/update_pins.py requirements.txt > new.txt
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version

PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.\-\[\]]+)==(?P<version>[^\s#]+)(?P<rest>.*)$")


def rewrite(lines: list[str]) -> list[str]:
    output: list[str] = []
    for line in lines:
        match = PIN.match(line.rstrip("\n"))
        if not match:
            output.append(line.rstrip("\n"))
            continue
        name = match.group("name")
        try:
            installed = version(name.split("[")[0])
        except PackageNotFoundError:
            output.append(line.rstrip("\n"))
            continue
        output.append(f"{name}=={installed}{match.group('rest')}")
    return output


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as handle:
        lines = handle.readlines()
    print("\n".join(rewrite(lines)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
