"""The Vercel proxy re-sends a request only when it never reached the app.

frontend/lib/wake.ts decides, for every proxied request, whether a failure is
Railway still booting a sleeping container (safe to send again) or anything
else (pass it through). Getting that wrong in one direction shows a person an
error for a backend that was merely waking. Getting it wrong in the other
direction re-sends a POST that already landed and doubles a backtest. So this
executes the REAL file under node, the way the normalizer parity guard does,
against a fake clock and scripted upstream answers.

node is a hard dependency here, as it is for the V-18 guard: if it is missing
this FAILS rather than skipping (V-58).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
WAKE_TS = REPO / "frontend" / "lib" / "wake.ts"

_HARNESS = r"""
import * as wake from "__WAKE_TS__";

const json502 = () => new Response('{"detail":"stripe unavailable"}',
  { status: 502, headers: { "content-type": "application/json" } });
const html502 = () => new Response("<html>Application failed to respond</html>",
  { status: 502, headers: { "content-type": "text/html" } });
const ok = () => new Response("{}",
  { status: 200, headers: { "content-type": "application/json" } });
const refused = () => { throw new TypeError("fetch failed"); };
const timedOut = () => { const e = new Error("timed out"); e.name = "TimeoutError"; throw e; };
const steps = { json502, html502, ok, refused, timedOut };

async function scenario(sequence, method, budgetMs = 45000, slowMs = 0) {
  let t = 0;
  let calls = 0;
  const send = async () => {
    const step = steps[sequence[Math.min(calls, sequence.length - 1)]];
    calls += 1;
    t += slowMs;
    return step();
  };
  try {
    const r = await wake.sendWaking(send, method, {
      budgetMs, delayMs: 2000, now: () => t, sleep: async (ms) => { t += ms; },
    });
    return { calls, status: r.response.status, stillWaking: r.stillWaking, threw: null };
  } catch (e) {
    return { calls, status: null, stillWaking: null, threw: e.name };
  }
}

// one answered attempt, for the classifier checks below
const answered = (status, contentType, elapsedMs = 1) =>
  ({ kind: "response", status, contentType, elapsedMs });
const limit = wake.WAKE_502_MAX_MS;

const results = {
  boot_then_ok_get: await scenario(["html502", "html502", "ok"], "GET"),
  boot_then_ok_post: await scenario(["html502", "ok"], "POST"),
  app_json_502_post: await scenario(["json502", "ok"], "POST"),
  slow_502_post: await scenario(["html502", "ok"], "POST", 45000, 25000),
  timeout_get: await scenario(["timedOut", "ok"], "GET"),
  dropped_get: await scenario(["refused", "ok"], "GET"),
  dropped_post: await scenario(["refused", "ok"], "POST"),
  budget_exhausted: await scenario(["html502"], "GET", 5000),
  local_backend: await scenario(["html502", "ok"], "GET", 0),
  classify: {
    html503: wake.isWakeFailure(answered(503, "text/html"), "GET"),
    json_charset_502: wake.isWakeFailure(answered(502, "application/json; charset=utf-8"), "GET"),
    no_type_502: wake.isWakeFailure(answered(502, null), "POST"),
    at_limit: wake.isWakeFailure(answered(502, "text/html", limit), "POST"),
    past_limit: wake.isWakeFailure(answered(502, "text/html", limit + 1), "POST"),
    dropped_head: wake.isWakeFailure(
      { kind: "network-error", timedOut: false, elapsedMs: 1 }, "HEAD"),
  },
};
process.stdout.write(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("the proxy wake guard could not run: node is not on PATH (see V-58)")
    if not WAKE_TS.is_file():
        pytest.fail(f"the proxy wake guard could not run: {WAKE_TS} not found. "
                    "If the module moved, point this guard at it.")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "wake_harness.mjs"
        script.write_text(_HARNESS.replace("__WAKE_TS__", WAKE_TS.as_posix()))
        proc = subprocess.run(
            [node, "--no-warnings", "--experimental-strip-types", str(script)],
            capture_output=True, text=True, timeout=120,
        )
    if proc.returncode != 0:
        pytest.fail(f"the proxy wake guard could not run: node exited "
                    f"{proc.returncode}\nstderr:\n{proc.stderr[:4000]}")
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def test_a_get_rides_out_the_boot(results: dict[str, Any]) -> None:
    assert results["boot_then_ok_get"] == {
        "calls": 3, "status": 200, "stillWaking": False, "threw": None}


def test_a_post_is_resent_after_a_boot_502_that_never_reached_the_app(
    results: dict[str, Any],
) -> None:
    assert results["boot_then_ok_post"]["calls"] == 2
    assert results["boot_then_ok_post"]["status"] == 200


def test_the_apps_own_json_502_is_never_resent(results: dict[str, Any]) -> None:
    assert results["app_json_502_post"] == {
        "calls": 1, "status": 502, "stillWaking": False, "threw": None}


def test_a_slow_502_may_have_landed_and_is_never_resent(results: dict[str, Any]) -> None:
    assert results["slow_502_post"]["calls"] == 1


def test_a_timeout_is_a_working_engine_not_a_wake(results: dict[str, Any]) -> None:
    assert results["timeout_get"] == {
        "calls": 1, "status": None, "stillWaking": None, "threw": "TimeoutError"}


def test_a_dropped_connection_resends_only_what_is_idempotent(
    results: dict[str, Any],
) -> None:
    assert results["dropped_get"]["calls"] == 2
    assert results["dropped_get"]["status"] == 200
    assert results["dropped_post"] == {
        "calls": 1, "status": None, "stillWaking": None, "threw": "TypeError"}
    assert results["classify"]["dropped_head"] is True


def test_an_exhausted_budget_reports_the_engine_as_still_waking(
    results: dict[str, Any],
) -> None:
    assert results["budget_exhausted"] == {
        "calls": 3, "status": 502, "stillWaking": True, "threw": None}


def test_a_local_backend_never_waits_out_a_wake(results: dict[str, Any]) -> None:
    assert results["local_backend"]["calls"] == 1


def test_only_a_quick_non_json_502_reads_as_a_boot(results: dict[str, Any]) -> None:
    c = results["classify"]
    assert c["html503"] is False
    assert c["json_charset_502"] is False
    assert c["no_type_502"] is True
    assert c["at_limit"] is True
    assert c["past_limit"] is False
