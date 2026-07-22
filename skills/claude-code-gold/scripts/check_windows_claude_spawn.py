#!/usr/bin/env python3
"""Probe whether Python can spawn the npm `claude` CLI on Windows.

This script is meant to be run by an agent that suspects the Windows WinError 193
spawn issue. It tries `shell=True` and `shell=False` and prints the result.
"""
from __future__ import annotations

import shutil
import subprocess
import sys


def probe(label: str, args: list[str], shell: bool) -> None:
    print(f"--- {label} (shell={shell}) ---")
    try:
        proc = subprocess.run(
            args, shell=shell, capture_output=True, text=True, timeout=30
        )
        print(f"returncode: {proc.returncode}")
        print(f"stdout: {proc.stdout.strip()[:200]}")
        print(f"stderr: {proc.stderr.strip()[:200]}")
    except Exception as exc:
        print(f"exception: {type(exc).__name__}: {exc}")
    print()


def main() -> int:
    if sys.platform != "win32":
        print("This probe is Windows-specific; on other platforms both methods work.")
    claude = shutil.which("claude")
    print(f"shutil.which('claude') = {claude!r}\n")
    probe("claude via shell", ["claude", "--version"], shell=True)
    probe("claude via shell=False", ["claude", "--version"], shell=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
