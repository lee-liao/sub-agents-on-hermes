#!/usr/bin/env python3
"""Download an ADO work-item attachment. Usage: ado_download.py <url> <out>

Why this helper exists
----------------------
The ADO MCP server (`@tiberriver256/mcp-server-azure-devops`) has no
attachment-download tool, so fetching a work-item attachment means an
authenticated GET against the ADO REST endpoint. The obvious one-liner
``curl -u ":$AZURE_DEVOPS_PAT" <url>`` is *blocked* under headless
`claude -p`, which rejects any Bash command containing `$VAR`
("Contains simple_expansion") — a second security layer beyond the
`Bash(prefix *)` allow-list. See the workspace-template README's
"Headless task-prompt rules" for the full story.

This script sidesteps that: it reads the PAT from `os.environ` (never from
argv, so the secret never appears on a command line), so the Bash invocation
`python3 .claude/ado_download.py <url> <out>` has no `$` and no secret in it.
"""

from __future__ import annotations

import base64
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: ado_download.py <url> <out>", file=sys.stderr)
        return 2

    url, out = sys.argv[1], sys.argv[2]
    pat = os.environ.get("AZURE_DEVOPS_PAT", "").strip()
    if not pat:
        print("AZURE_DEVOPS_PAT not set", file=sys.stderr)
        return 2

    if "?" not in url:
        url += "?api-version=7.1"
    token = base64.b64encode(f":{pat}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        print(f"download failed: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        print(exc.read().decode()[:500], file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"download failed: {exc.reason}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"downloaded {len(data)} bytes -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
