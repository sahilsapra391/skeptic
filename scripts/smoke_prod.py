"""M6 production smoke: the canonical strategy end-to-end against prod.

    python scripts/smoke_prod.py https://<railway-app>.up.railway.app

Reads SKEPTIC_ACCESS_TOKEN from the environment (required when the
backend has one set). Exercises health → parse (NL → spec) → backtest →
poll to done → verdict sanity. Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

CANONICAL = "Sell a 30 delta put on SPY every Monday, close at 50% profit or 21 DTE."
TIMEOUT_S = 240  # cold start pulls the chain lake from R2 + two LLM verdict calls


def call(base: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **(
                {"Authorization": f"Bearer {os.environ['SKEPTIC_ACCESS_TOKEN']}"}
                if os.environ.get("SKEPTIC_ACCESS_TOKEN")
                else {}
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")

    print("1) health…")
    health = call(base, "/api/health")
    assert health["status"] == "ok", health
    print(f"   engine: {health.get('engine')}")
    print(f"   parser: {health.get('parser')}")
    print(f"   db:     {health.get('db')}")
    assert health.get("r2_configured") is True, "R2 creds missing in prod"

    print("2) parse (NL → spec)…")
    parsed = call(base, "/api/parse", {"text": CANONICAL})
    assert parsed["status"] == "spec", f"parser asked questions on the canonical case: {parsed}"
    assert parsed["demo"] is False
    spec = parsed["spec"]
    assert spec["meta"]["description_raw"] == CANONICAL

    print("3) backtest…")
    started = call(base, "/api/backtest", {"spec": spec})
    run_id = started["run_id"]
    assert started["demo"] is False

    print(f"4) polling run {run_id} (≤{TIMEOUT_S}s)…")
    t0 = time.time()
    payload: dict = {}
    while time.time() - t0 < TIMEOUT_S:
        payload = call(base, f"/api/runs/{run_id}")
        if payload["status"] in ("done", "error"):
            break
        stage = payload.get("stage", 0)
        previews = payload.get("previews") or []
        print(f"   stage {stage}" + (f" · {previews[-1]}" if previews else ""))
        time.sleep(4)
    assert payload.get("status") == "done", f"run did not finish: {payload.get('error', payload)}"

    print("5) verdict sanity…")
    verdict = payload["verdict"]
    assert verdict["headline"], "empty verdict headline"
    assert payload["demo"] is False
    assert payload.get("recommendations"), "no grounded recommendations"
    assert payload.get("retail"), "no retail register"
    took = time.time() - t0
    print(f"   “{verdict['headline'][:90]}…”")
    print(f"\nSMOKE PASS — end-to-end in {took:.0f}s on {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
