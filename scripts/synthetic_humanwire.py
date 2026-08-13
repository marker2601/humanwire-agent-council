"""Repository wrapper for the installed HumanWire synthetic proof CLI."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from humanwire.__main__ import main as installed_main


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    return installed_main(["synthetic", *args])


if __name__ == "__main__":
    raise SystemExit(main())
