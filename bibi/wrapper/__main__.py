"""``python -m bibi.wrapper`` — generischer Wrapper-Entrypoint (DESIGN §7.5)."""

from __future__ import annotations

import sys

from bibi.wrapper import main

if __name__ == "__main__":
    sys.exit(main())
