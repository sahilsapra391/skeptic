"""Railway Serverless is on, and nothing turns it off without a sound.

The backend only costs money while it is awake (CLAUDE.md, "The backend sleeps
when idle"). Each test here guards one quiet way that goes wrong. The setting
can vanish from the deploy config. A timer can start pinging the API and hold
the container awake around the clock, which is exactly what the retired VM
keep-warm timer did. Or a background job can ship without telling Railway it is
working, and get its container stopped mid-run.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RAILWAY_JSON = REPO / "backend" / "railway.json"
DEPLOY = REPO / "collector" / "deploy"
APP = REPO / "backend" / "app"

# Every in-process background job. Adding one means decorating it with
# @holds_awake AND listing it here; the source scan below fails otherwise.
JOBS = {
    "_execute_run": "app.api.runs",
    "_execute_audit": "app.api.runs",
    "_execute_reproduce": "app.api.notebook",
}


def test_railway_sleeps_the_backend_when_idle() -> None:
    config = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))
    assert config["deploy"].get("sleepApplication") is True, (
        "backend/railway.json no longer turns on Railway Serverless, so the "
        "backend bills for its memory around the clock again"
    )


def test_no_scheduled_unit_calls_the_api() -> None:
    timers = sorted(DEPLOY.glob("skeptic-*.timer"))
    assert timers, f"no timers under {DEPLOY}; if the units moved, point this guard at them"
    offenders = []
    for timer in timers:
        service = timer.with_suffix(".service")
        for line in service.read_text(encoding="utf-8").splitlines():
            if line.startswith("ExecStart") and ("/api/" in line or "skeptic.fyi" in line):
                offenders.append(f"{service.name}: {line.strip()}")
    assert not offenders, (
        "a scheduled unit calls the API directly, which keeps the sleeping "
        "backend awake on every fire: " + "; ".join(offenders)
    )


def test_bootstrap_enables_no_keep_warm_timer() -> None:
    text = (DEPLOY / "bootstrap.sh").read_text(encoding="utf-8")
    enabled = [
        line for line in text.splitlines()
        if line.strip().startswith("systemctl enable") and "keepwarm" in line
    ]
    assert not enabled, f"bootstrap.sh re-enables a keep-warm timer: {enabled}"


@pytest.mark.parametrize(("job", "module_name"), sorted(JOBS.items()))
def test_every_in_process_job_holds_the_container_awake(job: str, module_name: str) -> None:
    fn = getattr(importlib.import_module(module_name), job)
    assert getattr(fn, "__holds_awake__", False) is True, (
        f"{module_name}.{job} runs after its request returns but is not "
        "decorated with @holds_awake, so Railway can stop the container mid-run"
    )


def test_every_background_task_is_a_known_job() -> None:
    """A new `add_task(...)` target would run without holding the container
    awake. Scanning the source means a new job cannot ship undecorated."""
    targets = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        targets.update(re.findall(r"add_task\(\s*([A-Za-z_]\w*)", text))
    assert targets == set(JOBS), (
        f"background task targets {sorted(targets)} differ from the known jobs "
        f"{sorted(JOBS)}. Decorate the new job with @holds_awake and add it to JOBS."
    )
