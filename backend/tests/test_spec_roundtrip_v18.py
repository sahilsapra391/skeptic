"""V-18: the zero-edit round-trip guard.

A spec pushed through `spec_to_draft` (Python) and back through the REAL
`draftToSpec` (TypeScript, `frontend/lib/spec.ts`) with ZERO dial edits must
come back byte-identical. Any field that changes without a corresponding user
edit is a hard failure, because that is precisely the bug class V-17 fixed: an
`offset_pct` strike silently becoming delta 0.30, a $10-wide spread becoming
$5-wide, the parser's tenor band replaced by target-10 / target+15.

HOW IT RUNS (V-59)
    Node imports `frontend/lib/spec.ts` directly, by its real path, using
    native TypeScript type stripping. No build step, no bundler, no committed
    intermediate fixture, and no second copy of draftToSpec — a copy would mean
    this test measures the copy.

IT NEVER SKIPS (V-58)
    If node is unavailable or the harness cannot reach the module, this FAILS
    with "V-18 round-trip guard could not run". A guardrail test that can
    silently skip is worse than no test, because the suite still reads green.
    Same posture as the lookahead canary.

THE CORPUS (V-60)
    Enumerated below, so adding a new spec shape to the repo without adding it
    here is visible in review.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.models.spec import StrategySpec
from app.parser.parse import spec_to_draft

from .test_spec_roundtrip import CANONICAL

REPO = Path(__file__).resolve().parents[2]
SPEC_TS = REPO / "frontend" / "lib" / "spec.ts"
CONFIRM_TS = REPO / "frontend" / "lib" / "confirm.ts"

# the D3d-earned defaults (233M tape prints) — what an untouched Settings and
# every anonymous caller resolve to
DEFAULT_COSTS = {
    "commission_per_contract": 0.65,
    "slippage_half_spread_fraction": 0.85,
    "slippage_half_spread_fraction_sell": 0.9,
}

# Node reads [{draft, base}, ...] on stdin and returns [spec, ...]. It imports
# the real module — the whole point of the guard.
_HARNESS = """
import {{ draftToSpec }} from "{spec_ts}";
import {{ confirmDefaults }} from "{confirm_ts}";
let raw = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) raw += chunk;
const cases = JSON.parse(raw);
const out = cases.map(({{ draft, base, settings }}) => {{
  try {{
    // when `settings` is present this is the real client path: Settings seed
    // the draft ONCE at parse time, then the spec is built from the draft
    const d = settings ? confirmDefaults(draft, base, settings) : draft;
    return {{ ok: true, spec: draftToSpec(d, base), draft: d }};
  }} catch (e) {{
    return {{ ok: false, error: String(e && e.message ? e.message : e) }};
  }}
}});
process.stdout.write(JSON.stringify(out));
"""


def _offset_pct_spec() -> dict[str, Any]:
    """A strike rule the dials cannot express. Pre-V-17 this came back as
    delta 0.30 the moment any unrelated dial moved."""
    spec = copy.deepcopy(CANONICAL)
    spec["meta"]["name"] = "SPY 2% OTM weekly short put"
    spec["position"]["legs"][0]["strike_selection"] = {
        "method": "offset_pct",
        "value": -0.02,
    }
    return spec


def _custom_width_spec() -> dict[str, Any]:
    """A $10-wide put credit spread. `legs()` only knows the hardcoded $5."""
    spec = copy.deepcopy(CANONICAL)
    spec["meta"]["name"] = "SPY 30-delta $10-wide put credit spread"
    spec["position"]["structure"] = "put_credit_spread"
    spec["position"]["legs"] = [
        {
            "right": "put",
            "side": "short",
            "ratio": 1,
            "strike_selection": {"method": "delta", "value": 0.30},
        },
        {
            "right": "put",
            "side": "long",
            "ratio": 1,
            "strike_selection": {
                "method": "width_from_leg",
                "value": 10,
                "reference_leg": 0,
            },
        },
    ]
    return spec


def _ladder_spec() -> dict[str, Any]:
    """A scale-in ladder: parser-only vocabulary the dials show but cannot
    edit. Already passed through pre-V-17 (the D5d fix); guarded here so it
    stays that way."""
    spec = copy.deepcopy(CANONICAL)
    spec["spec_version"] = 3  # scale_in is v3 vocabulary
    spec["meta"]["name"] = "SPY laddered long call"
    # ladders are single-leg long_call / long_put only in this build
    spec["position"]["structure"] = "long_call"
    spec["position"]["legs"] = [
        {
            "right": "call",
            "side": "long",
            "ratio": 1,
            "strike_selection": {"method": "delta", "value": 0.30},
        }
    ]
    spec["entry"]["schedule"] = {"frequency": "signal_only", "day_of_week": None}
    spec["entry"]["conditions"] = []
    spec["entry"]["scale_in"] = {
        "mode": "signal_ladder",
        "basket": True,
        "rungs": [
            {
                "indicator": "drawdown_from_high_pct",
                "operator": ">=",
                "value": 2,
                "add_contracts": 1,
            },
            {
                "indicator": "drawdown_from_high_pct",
                "operator": ">=",
                "value": 4,
                "add_contracts": 1,
            },
        ],
        "max_total_contracts": 5,
        "rearm": {
            "indicator": "drawdown_from_high_pct",
            "operator": "<=",
            "value": 1,
        },
    }
    return spec


def _multi_condition_spec() -> dict[str, Any]:
    """Entry conditions beyond the first — the trigger dial edits only the
    first, and the rest render as "& …" chips."""
    spec = copy.deepcopy(CANONICAL)
    spec["meta"]["name"] = "SPY conditioned short put"
    spec["entry"]["schedule"] = {"frequency": "signal_only", "day_of_week": None}
    spec["entry"]["conditions"] = [
        {"indicator": "rsi", "operator": "<", "value": 30, "period": 14},
        {"indicator": "vix_level", "operator": ">", "value": 20},
    ]
    return spec


def _overfit_fixture_spec() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "overfit_strategy.json"
    return dict(json.loads(path.read_text())["spec"])


# V-60: the corpus, enumerated.
CORPUS: dict[str, Any] = {
    "canonical": copy.deepcopy(CANONICAL),
    "offset_pct_strike": _offset_pct_spec(),
    "custom_spread_width": _custom_width_spec(),
    "scale_in_ladder": _ladder_spec(),
    "multi_condition_entry": _multi_condition_spec(),
    "overfit_fixture": _overfit_fixture_spec(),
}


def _validated(spec: dict[str, Any]) -> dict[str, Any]:
    """Every corpus entry is a REAL spec, not hand-waved JSON — it goes through
    the same pydantic model the engine validates against."""
    model = StrategySpec.model_validate(spec)
    return model.model_dump(mode="json", exclude_none=True)


def _canonical(spec: dict[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)


def _draft_for(spec: dict[str, Any]) -> dict[str, Any]:
    """The dial surface as the user would confirm it having changed nothing.

    `spec_to_draft` projects the dials but not costs or the seed; in the real
    flow `confirmDefaults` stamps those on before the spec screen renders, and
    V-93 makes a draft without them illegal at spec-build. Here they are the
    parser's own values, which is what "confirmed exactly what was proposed"
    means and what keeps the round trip byte-identical.
    """
    draft = spec_to_draft(spec, spec["meta"]["description_raw"])
    draft["costs"] = dict(spec["costs"])
    draft["seed"] = (spec.get("backtest") or {}).get("seed", 42)
    return draft


def _rebuild_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Push every (draft, base) pair through the real draftToSpec via node."""
    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "V-18 round-trip guard could not run: node is not on PATH. "
            "This guard must never skip — see V-58."
        )
    for path in (SPEC_TS, CONFIRM_TS):
        if not path.is_file():
            pytest.fail(
                f"V-18 round-trip guard could not run: {path} not found. "
                "This guard must never skip — see V-58."
            )

    # the harness needs a real file: stdin carries the payload, not the script
    harness = _HARNESS.format(
        spec_ts=SPEC_TS.as_posix(), confirm_ts=CONFIRM_TS.as_posix()
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "v18_harness.mjs"
            script.write_text(harness)
            proc = subprocess.run(
                [node, "--no-warnings", "--experimental-strip-types", str(script)],
                input=json.dumps(cases),
                capture_output=True,
                text=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"V-18 round-trip guard could not run: {exc}")

    if proc.returncode != 0:
        pytest.fail(
            "V-18 round-trip guard could not run: node exited "
            f"{proc.returncode}\nstderr:\n{proc.stderr[:4000]}"
        )
    try:
        return list(json.loads(proc.stdout))
    except json.JSONDecodeError:
        pytest.fail(
            "V-18 round-trip guard could not run: harness produced no JSON.\n"
            f"stdout:\n{proc.stdout[:2000]}\nstderr:\n{proc.stderr[:2000]}"
        )


@pytest.fixture(scope="module")
def rebuilt() -> dict[str, dict[str, Any]]:
    names = list(CORPUS)
    cases = []
    for name in names:
        spec = _validated(CORPUS[name])
        cases.append({"draft": _draft_for(spec), "base": spec})
    results = _rebuild_all(cases)
    assert len(results) == len(names), "harness dropped cases"
    out: dict[str, dict[str, Any]] = {}
    for name, res in zip(names, results, strict=True):
        if not res.get("ok"):
            pytest.fail(f"draftToSpec threw on {name}: {res.get('error')}")
        out[name] = res["spec"]
    return out


@pytest.mark.parametrize("name", list(CORPUS))
def test_zero_edit_round_trip_is_byte_identical(
    name: str, rebuilt: dict[str, dict[str, Any]]
) -> None:
    """The whole guard. Zero dial edits in, byte-identical spec out."""
    original = _validated(CORPUS[name])
    result = _validated(rebuilt[name])
    if _canonical(original) != _canonical(result):
        diff = _field_diff(original, result)
        pytest.fail(
            f"{name}: the zero-edit rebuild changed {len(diff)} field(s) with no "
            "user edit behind them.\n"
            + "\n".join(f"  {p}: {a!r} -> {b!r}" for p, a, b in diff)
        )


def test_one_edited_dial_changes_only_what_it_owns() -> None:
    """The other half of the rule. Moving the DTE dial must move the tenor and
    NOTHING else — not the $10 spread width, not the strike method."""
    spec = _validated(CORPUS["custom_spread_width"])
    draft = _draft_for(spec)
    draft["dte"] = 30  # the single user edit

    rebuilt = _rebuild_all([{"draft": draft, "base": spec}])[0]
    assert rebuilt["ok"], rebuilt.get("error")
    result = _validated(rebuilt["spec"])

    changed = {p for p, _, _ in _field_diff(spec, result)}
    assert changed == {
        ".position.expiration_selection.target_dte",
        ".position.expiration_selection.min_dte",
        ".position.expiration_selection.max_dte",
    }, f"a DTE edit touched more than the tenor: {sorted(changed)}"
    # the width the user never touched survived
    assert result["position"]["legs"][1]["strike_selection"]["value"] == 10


def test_the_guard_actually_detects_a_rewrite() -> None:
    """A guard that cannot fail is not a guard. Force the pre-V-17 condition —
    a strike dial the user DID move — and prove the diff reports it."""
    spec = _validated(CORPUS["offset_pct_strike"])
    draft = _draft_for(spec)
    # moving the STRIKE dial nulls the label, exactly as the select does
    draft["strikeLabel"] = None
    draft["strikeDelta"] = 25

    rebuilt = _rebuild_all([{"draft": draft, "base": spec}])[0]
    assert rebuilt["ok"], rebuilt.get("error")
    result = _validated(rebuilt["spec"])

    changed = {p for p, _, _ in _field_diff(spec, result)}
    assert changed, "the diff reported no change on a spec that was rewritten"
    assert ".position.legs[0].strike_selection.method" in changed
    assert (
        result["position"]["legs"][0]["strike_selection"]["method"] == "delta"
    ), "a moved strike dial should produce the delta the user chose"


def test_v78_a_name_only_difference_is_unreachable() -> None:
    """V-78, settled here so A1's zero-edit credit guard does not have to
    special-case it.

    `meta.name` regenerates only when ticker, structure or strike moves. On a
    variant ticker and structure are LOCKED (V-06), so the only reachable
    trigger is a strike move — and a strike move also rewrites the lead leg.
    A name-only delta therefore cannot occur, which means it can never read as
    a real edit and spend a credit, and `meta.name` needs no exclusion from the
    canonicalized V-10/V-19 comparison.

    If this test ever fails, V-19 must name `meta.name` in its loud failure
    rather than letting the run through.
    """
    spec = _validated(CORPUS["canonical"])
    draft = _draft_for(spec)
    draft["strikeDelta"] = 20  # the only name-regenerating move a variant has

    res = _rebuild_all([{"draft": draft, "base": spec}])[0]
    assert res["ok"], res.get("error")
    changed = {p for p, _, _ in _field_diff(spec, _validated(res["spec"]))}

    assert ".meta.name" in changed, "the premise moved: a strike move should rename"
    assert changed != {".meta.name"}, (
        "a name-only delta became reachable — V-19 must now name meta.name in "
        "its loud failure instead of treating this as a legitimate variant"
    )
    assert ".position.legs[0].strike_selection.value" in changed


def _client_path(spec: dict[str, Any], settings: dict[str, float]) -> dict[str, Any]:
    """The real ingress: parse -> spec_to_draft -> confirmDefaults(Settings) ->
    draftToSpec. Zero dial edits."""
    draft = spec_to_draft(spec, spec["meta"]["description_raw"])
    res = _rebuild_all([{"draft": draft, "base": spec, "settings": settings}])[0]
    assert res["ok"], res.get("error")
    return res


# --- V-68 / V-37: costs and seed after the V-36 contract change --------------


def test_v68_default_settings_produce_the_same_costs_as_before() -> None:
    """PR-0 is a no-op here. Untouched Settings resolve to the D3d defaults,
    exactly what startBacktest used to stamp on at submit."""
    spec = _validated(CORPUS["canonical"])
    res = _client_path(
        spec, {"commission": 0.65, "slippage": 0.85, "slippageSell": 0.9}
    )
    assert res["spec"]["costs"] == DEFAULT_COSTS


def test_v68_anon_path_falls_back_to_the_d3d_defaults() -> None:
    """An anonymous caller has no stored Settings, so `getSettings()` returns
    DEFAULT_SETTINGS. Read the real literal rather than restating it here: if
    those defaults ever move, the anon path moves with them and that should be
    a deliberate edit, not a surprise."""
    src = (REPO / "frontend" / "lib" / "settings.ts").read_text()
    block = re.search(
        r"export const DEFAULT_SETTINGS[^=]*=\s*\{(.*?)\n\};", src, re.S
    )
    assert block, "could not find DEFAULT_SETTINGS in settings.ts"
    found = {
        key: float(re.search(rf"\b{key}:\s*([0-9.]+)", block.group(1)).group(1))  # type: ignore[union-attr]
        for key in ("commission", "slippage", "slippageSell")
    }
    assert found == {
        "commission": DEFAULT_COSTS["commission_per_contract"],
        "slippage": DEFAULT_COSTS["slippage_half_spread_fraction"],
        "slippageSell": DEFAULT_COSTS["slippage_half_spread_fraction_sell"],
    }


def test_v37_non_default_settings_reach_the_spec() -> None:
    """Settings still govern a normal run — they are just applied at parse time
    now instead of reaching past the confirmed spec at submit."""
    spec = _validated(CORPUS["canonical"])
    res = _client_path(
        spec, {"commission": 0.5, "slippage": 0.4, "slippageSell": 0.45}
    )
    assert res["spec"]["costs"] == {
        "commission_per_contract": 0.5,
        "slippage_half_spread_fraction": 0.4,
        "slippage_half_spread_fraction_sell": 0.45,
    }
    # and the confirmed draft carries them, so the FILLS tile shows what runs
    assert res["draft"]["costs"] == res["spec"]["costs"]


def test_v68_costs_are_the_only_thing_settings_move() -> None:
    """The deliberate blast radius. Two runs at different Settings differ in
    `costs` and nowhere else."""
    spec = _validated(CORPUS["custom_spread_width"])
    a = _client_path(spec, {"commission": 0.65, "slippage": 0.85, "slippageSell": 0.9})
    b = _client_path(spec, {"commission": 0.5, "slippage": 0.4, "slippageSell": 0.45})
    changed = {p for p, _, _ in _field_diff(a["spec"], b["spec"])}
    assert changed == {
        ".costs.commission_per_contract",
        ".costs.slippage_half_spread_fraction",
        ".costs.slippage_half_spread_fraction_sell",
    }, f"Settings moved more than costs: {sorted(changed)}"


def test_v37_a_non_42_seed_survives_the_round_trip() -> None:
    """The ONE deliberate behaviour change in PR-0, asserted on its own so a
    results shift on these runs is known rather than discovered: a spec whose
    seed is not 42 now runs on its own seed instead of being reset to 42."""
    spec = _validated(CORPUS["canonical"])
    spec["backtest"]["seed"] = 777
    res = _client_path(
        spec, {"commission": 0.65, "slippage": 0.85, "slippageSell": 0.9}
    )
    assert res["draft"]["seed"] == 777, "the confirmed draft lost the parser's seed"
    assert res["spec"]["backtest"]["seed"] == 777, "the rebuild reset the seed to 42"


def test_v68_seed_42_stays_42() -> None:
    """The unchanged majority: a spec already on the default seed is untouched
    by the exception above."""
    spec = _validated(CORPUS["canonical"])
    res = _client_path(
        spec, {"commission": 0.65, "slippage": 0.85, "slippageSell": 0.9}
    )
    assert res["spec"]["backtest"]["seed"] == 42


def _field_diff(
    a: Any, b: Any, path: str = ""
) -> list[tuple[str, Any, Any]]:
    """Leaf-level differences, so a failure names the field rather than dumping
    two specs and leaving the reader to find it."""
    out: list[tuple[str, Any, Any]] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            out += _field_diff(a.get(key, "<absent>"), b.get(key, "<absent>"), f"{path}.{key}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path or ".", f"<{len(a)} items>", f"<{len(b)} items>"))
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                out += _field_diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append((path or ".", a, b))
    return out
