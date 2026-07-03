"""Test env: run storage goes to a throwaway SQLite file, never the repo's
local runs.db (and never Neon). Must run before anything imports app.db."""

import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(prefix="skeptic-test-runs-", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
