# Skeptic — operational entry points (ENGINE-V3 D3).
# `make nightly` is the acceptance target: what the nightly Actions
# workflow runs, runnable locally against the same lake and DB.

.PHONY: nightly nightly-execute ledger unlock-scan weekly calibrate priorities test

nightly: ledger unlock-scan

# what the Actions workflow runs: the scan WITH capped re-run submission
nightly-execute: ledger
	cd backend && PYTHONPATH=. uv run python scripts/nightly_improve.py --execute

ledger:
	cd collector && uv run python ledger.py

ivs-signals:
	cd collector && uv run python derive_ivs_signals.py

unlock-scan:
	cd backend && PYTHONPATH=. uv run python scripts/nightly_improve.py

# D3d weekly pass, dry-run: evidence + ranking printed, nothing written
weekly: calibrate priorities

calibrate:
	cd backend && PYTHONPATH=. uv run python scripts/calibrate_fill_model.py

priorities:
	cd backend && PYTHONPATH=. uv run python scripts/build_priorities.py

test:
	cd backend && uv run pytest -q && uv run ruff check . && uv run mypy app
