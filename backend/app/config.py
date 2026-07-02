"""Local-dev env loading.

In production (Railway) everything arrives as real env vars. For local dev
the repo already keeps R2 credentials in collector/.env (the intraday
recorder's pattern) — reuse that single source of truth instead of a second
secrets file. Values already present in the environment always win.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    for candidate in (
        Path(__file__).resolve().parents[1] / ".env",  # backend/.env
        Path(__file__).resolve().parents[2] / "collector" / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
