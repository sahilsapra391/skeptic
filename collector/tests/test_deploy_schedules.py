"""Guards for the scheduled jobs that moved off GitHub Actions onto the
collector VM (2026-07-27, after a billing block refused to start the nightly
EOD job at all).

The failure these prevent is drift. The VM chain and the workflow it replaced
are now two descriptions of the same nightly job in different files, and
nothing but these assertions keeps them in step. That matters more than usual
here: the outage that forced the move went unnoticed for a day precisely
because the job never ran far enough to report anything, and a silently
reordered or half-migrated chain would be exactly as quiet.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "collector" / "deploy"
WORKFLOWS = REPO / ".github" / "workflows"

# The exact order collect-eod.yml ran these in before the move. collect.py is
# first because it is the only step Healthchecks observes; ledger.py is last
# because the resolution-map rebuild depends on every derivation above it.
EXPECTED_CHAIN = [
    "collect.py",
    "alpaca.py",
    "coverage.py",
    "derive_cboe_eod.py",
    "derive_inhouse_signals.py",
    "derive_ivs_signals.py",
    "derive_flow_signals.py",
    "derive_flow_inhouse.py",
    "derive_cross_validation.py",
    "derive_fill_calibration.py",
    "ledger.py",
]

# Vars the VM needs live (not commented out) now that it owns the schedules.
# APCA_* sat commented in .env.example for months, which is exactly why the
# VM's copy of .env lacked them when the EOD chain moved over.
VM_REQUIRED_VARS = [
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "ALPHAVANTAGE_API_KEY",
    "HEALTHCHECK_URL",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "DATABASE_URL",
    "SKEPTIC_API_URL",
    "SKEPTIC_ACCESS_TOKEN",
]


def _read(rel: Path) -> str:
    return rel.read_text()


def _on_block(text: str) -> str:
    """The indented body under a workflow's top-level `on:` key, so prose in
    surrounding comments can't be mistaken for a live trigger."""
    m = re.search(r"^on:\n((?:[ \t].*\n|\n)*)", text, re.M)
    assert m, "no on: block found"
    return m.group(1)


def _oncalendar(unit: str) -> list[str]:
    return re.findall(r"^OnCalendar=(.+)$", _read(DEPLOY / unit), re.M)


def test_collect_eod_chain_matches_the_workflow_it_replaced() -> None:
    got = re.findall(r'^\s*step\s+"[^"]*"\s+(\S+\.py)', _read(DEPLOY / "collect-eod.sh"), re.M)
    assert got == EXPECTED_CHAIN


def test_every_chain_script_exists() -> None:
    for name in EXPECTED_CHAIN:
        assert (REPO / "collector" / name).is_file(), name


def test_chain_records_failures_instead_of_aborting() -> None:
    """Every derivation ran under `if: always()` on Actions: one failure never
    skipped the rest, but the job still ended red. `set -e` would silently drop
    the tail of the chain, and the healthcheck would not notice because
    collect.py has already pinged by then."""
    sh = _read(DEPLOY / "collect-eod.sh")
    assert "set -uo pipefail" in sh
    assert not re.search(r"^set -e", sh, re.M)
    assert re.search(r"^exit \"\$rc\"", sh, re.M)


def test_collect_eod_timer_keeps_the_healthchecks_slots() -> None:
    """Both slots stay in UTC on purpose: they are the crons the check on
    healthchecks.io was configured against, so the move needs no dashboard
    edit and cannot produce a false DOWN."""
    assert _oncalendar("skeptic-collect-eod.timer") == [
        "Mon-Fri *-*-* 21:30:00 UTC",
        "Mon-Fri *-*-* 22:30:00 UTC",
    ]


def test_collect_eod_timer_does_not_replay_at_boot() -> None:
    """Persistent=true replays a missed run at boot, which can be any hour.
    Mid-session that would write a partial chain as if it were the close."""
    assert re.search(r"^Persistent=false$", _read(DEPLOY / "skeptic-collect-eod.timer"), re.M)


def test_quality_and_improve_timers_keep_their_slots() -> None:
    assert _oncalendar("skeptic-quality.timer") == ["Sat *-*-* 13:00:00 UTC"]
    assert _oncalendar("skeptic-improve.timer") == ["Tue-Sat *-*-* 07:00:00 UTC"]


def test_timers_are_enableable() -> None:
    """A [Timer] with no [Install] silently refuses `systemctl enable`."""
    for unit in sorted(DEPLOY.glob("skeptic-*.timer")):
        text = _read(unit)
        assert "[Install]" in text, unit.name
        assert "WantedBy=timers.target" in text, unit.name


def test_vm_owned_workflows_carry_no_cron() -> None:
    """Re-adding a cron to either of these double-runs the chain against the
    same lake, on two hosts, with two Healthchecks pings."""
    for wf in ("collect-eod.yml", "quality-weekly.yml"):
        block = _on_block(_read(WORKFLOWS / wf))
        assert "cron:" not in block, wf
        assert "schedule:" not in block, wf
        assert "workflow_dispatch:" in block, f"{wf} lost its manual fallback"


def test_nightly_improve_keeps_only_the_saturday_pr_pass() -> None:
    """The Tue-Sat scan moved to the VM. This workflow stayed behind solely for
    the weekly pass, which opens a proposal PR and so needs repo write the VM's
    read-only deploy key deliberately does not have."""
    crons = re.findall(r'cron:\s*"([^"]+)"', _on_block(_read(WORKFLOWS / "nightly-improve.yml")))
    assert crons == ["30 7 * * 6"]


def test_nightly_improve_scan_is_manual_only() -> None:
    """On the Saturday schedule the VM already scanned; running the scan here
    too would double-submit that night's capped re-runs."""
    text = _read(WORKFLOWS / "nightly-improve.yml")
    m = re.search(r"- name: Unlock scan.*?(?=\n      - name:)", text, re.S)
    assert m, "Unlock scan step not found"
    assert "if: github.event_name == 'workflow_dispatch'" in m.group(0)


def test_saturday_weekly_pass_runs_after_the_vm_scan() -> None:
    """The workflow used to run scan-then-weekly inside one job. Split across
    two hosts, the clock is the only thing preserving that order."""
    vm = _oncalendar("skeptic-improve.timer")
    vm_h, vm_m = (int(x) for x in re.search(r"(\d{2}):(\d{2}):00 UTC", vm[0]).groups())
    cron = re.findall(r'cron:\s*"([^"]+)"', _on_block(_read(WORKFLOWS / "nightly-improve.yml")))[0]
    gh_m, gh_h = (int(x) for x in cron.split()[:2])
    assert (gh_h, gh_m) > (vm_h, vm_m), "the GitHub weekly pass must trail the VM scan"


def _run_chain(tmp_path: Path, failing: frozenset[str] = frozenset()) -> tuple[int, list[str]]:
    """Drive collect-eod.sh against a stub `uv` that records every script it is
    asked to run and exits non-zero for the named ones."""
    calls = tmp_path / "calls.log"
    stub = tmp_path / "uv"
    fail_arm = f'    {"|".join(sorted(failing))}) exit 3 ;;\n' if failing else ""
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'script=""\n'
        'for a in "$@"; do case "$a" in *.py) script="$a" ;; esac; done\n'
        f'echo "$script" >> "{calls}"\n'
        'case "$script" in\n'
        f"{fail_arm}"
        "    *) exit 0 ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(DEPLOY / "collect-eod.sh")],
        env={**os.environ, "UV": str(stub)},
        capture_output=True,
        text=True,
    )
    ran = calls.read_text().split() if calls.exists() else []
    return proc.returncode, ran


def test_chain_runs_every_step_in_order_when_all_pass(tmp_path: Path) -> None:
    rc, ran = _run_chain(tmp_path)
    assert rc == 0
    assert ran == EXPECTED_CHAIN


def test_collector_failure_skips_only_the_minute_top_up(tmp_path: Path) -> None:
    """alpaca.py sat behind a custom `if:` on Actions, which GitHub implicitly
    ANDs with success(), so it was skipped when the collector failed. Every
    derivation still ran, because those carried `if: always()`."""
    rc, ran = _run_chain(tmp_path, frozenset({"collect.py"}))
    assert rc != 0
    assert "alpaca.py" not in ran
    assert ran == [s for s in EXPECTED_CHAIN if s != "alpaca.py"]


def test_a_failing_derivation_never_truncates_the_chain(tmp_path: Path) -> None:
    """The regression `set -e` would cause: everything after the first failure
    silently disappears, and the healthcheck stays green because collect.py
    already pinged success long before."""
    rc, ran = _run_chain(tmp_path, frozenset({"derive_ivs_signals.py"}))
    assert rc != 0
    assert ran == EXPECTED_CHAIN


def test_unit_execstart_paths_exist_in_the_repo() -> None:
    for unit in sorted(DEPLOY.glob("skeptic-*.service")):
        for line in re.findall(r"^ExecStart=(.*)$", _read(unit), re.M):
            for tok in line.split():
                if not tok.startswith("/opt/skeptic/"):
                    continue
                rel = tok[len("/opt/skeptic/") :]
                if rel.endswith(".env"):
                    # gitignored and supplied out of band; the template is what
                    # the repo can actually guarantee.
                    assert (REPO / f"{rel}.example").is_file(), f"{unit.name}: {tok}"
                    continue
                assert (REPO / rel).exists(), f"{unit.name}: {tok}"


def test_vm_required_vars_are_documented_uncommented() -> None:
    """autoupdate.sh can never deliver a secret, so .env.example is the only
    place that tells the owner what a fresh VM still needs by hand."""
    example = _read(REPO / "collector" / ".env.example")
    for var in VM_REQUIRED_VARS:
        assert re.search(rf"^{var}=", example, re.M), f"{var} missing or commented out"
