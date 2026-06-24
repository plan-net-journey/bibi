"""bibi-ctrl — CLI-Einstiegspunkt der bibi-Engine.

Phase 0: `init` (Bootstrap von ~/.config/bibi/env) und `status`. Weitere
Subkommandos (daemon, job, sync …) folgen in späteren Phasen.
"""

from __future__ import annotations

import argparse
import sys

from . import init_cmd, status_cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bibi-ctrl", description="bibi-Engine-Steuerung")
    sub = parser.add_subparsers(dest="cmd")

    init_cmd.register(sub)
    status_cmd.register(sub)

    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
