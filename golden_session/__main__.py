"""Enable `python -m golden_session ...` as a CLI entry point (bind-mount, no pip)."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
