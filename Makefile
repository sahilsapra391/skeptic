# Skeptic — operational entry points (ENGINE-V3 D3).
# `make nightly` is the acceptance target: what the nightly Actions
# workflow runs, runnable locally against the same lake and DB.

.PHONY: nightly nightly-execute ledger unlock-scan test

nightly: ledger unlock-scan

# what the Actions workflow runs: the scan WITH capped re-run submission
nightly-execute: ledger
	cd backend && PYTHONPATH=. uv run python scripts/nightly_improve.py --execute

ledger:
	cd collector && uv run python ledger.py

unlock-scan:
	cd backend && PYTHONPATH=. uv run python scripts/nightly_improve.py

test:
	cd backend && uv run pytest -q && uv run ruff check . && uv run mypy app
