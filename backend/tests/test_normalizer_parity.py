"""The two normalizers are one substitution table, so prove they are one.

`backend/app/text.py` and `frontend/lib/punctuation.ts` implement the same
rule on opposite sides of the wire. Nothing kept them honest: the Python side
had unit tests, the TypeScript side had none, and they drifted. The drift is
not academic. The frontend normalizer is the last layer before a page, so a
case the backend handles and the frontend does not is a visible em-dash, and a
case they handle DIFFERENTLY is a page whose text depends on which layer got
there first.

HOW IT RUNS
    Node imports the REAL `frontend/lib/punctuation.ts` by its path, using
    native TypeScript type stripping, exactly as the V-18 round-trip guard
    imports the real `frontend/lib/spec.ts`. No build step, no bundler, and no
    second copy of the TypeScript logic. A copy would mean this test measures
    the copy, which is the failure it exists to catch.

IT NEVER SKIPS (V-58)
    No node, no module, no parse: this FAILS. A parity guard that skips reads
    green on precisely the machine where the two sides were never compared.

THE TABLE
    `CASES` below is the canonical substitution table, one row per rule, and it
    is the input to BOTH implementations. Rows carrying an `expected` string
    pin the behaviour the table specifies; rows with `expected=None` are cases
    the table leaves open (line boundaries, brackets, the semicolon-less entity
    spellings) where agreement is still required but the answer is the
    implementations' to choose. Two identically wrong normalizers would pass a
    parity check alone, which is why the pinned rows exist.

The character never appears literally here. It is built from chr(0x2014) and
reaches node inside a JSON payload, so this file is subject to the same guard
as everything else.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.text import normalize

REPO = Path(__file__).resolve().parents[2]
PUNCTUATION_TS = REPO / "frontend" / "lib" / "punctuation.ts"

DASH = chr(0x2014)
EN_DASH = chr(0x2013)
HORIZONTAL_BAR = chr(0x2015)

# The entity spellings, assembled from the code point rather than typed out.
# The source guard flags a literal named entity in every file, this one included,
# and an exemption for the test that names the spellings is the hole the guard
# exists to close. `_AMP` is an ampersand.
_AMP = chr(0x26)
ENTITY_NAMED = _AMP + "mdash;"
ENTITY_DECIMAL = _AMP + "#" + str(0x2014) + ";"
ENTITY_HEX = _AMP + "#x" + format(0x2014, "04x") + ";"
ENTITY_HEX_UPPER = _AMP + "#X" + format(0x2014, "04x") + ";"
ENTITY_NAMED_UPPER = _AMP + "MDASH;"
ENTITY_NAMED_MIXED = _AMP + "MdAsH;"
ENTITY_NAMED_BARE = _AMP + "mdash"
ENTITY_DECIMAL_PADDED = _AMP + "#0" + str(0x2014) + ";"

# The exported name the frontend normalizer goes by. Tried in order so a
# rename does not silently turn this guard into a no-op: if none of them is a
# function the harness reports every export it found and the test fails.
TS_EXPORT_CANDIDATES = ("stripEmDashes", "normalize", "normalizeText")

# The defect itself. None of these may survive either normalizer, whatever the
# input was. A double hyphen counts only when it is SPACED, because a bare one
# is a command-line flag and rule (c) says to leave it alone.
MUST_BE_ABSENT = (
    DASH,
    " -- ",
    ENTITY_NAMED,
    ENTITY_DECIMAL,
    ENTITY_HEX,
)

# Characters a normalizer must never REACH FOR as a replacement. An input that
# already carried one keeps it (rewriting an author's en-dash is not this
# function's business), so these are counted rather than banned outright.
MUST_NOT_BE_INTRODUCED = (EN_DASH, HORIZONTAL_BAR)


@dataclass(frozen=True)
class Case:
    """One row of the substitution table.

    `expected` is the output the canonical table specifies, or None when the
    table does not speak to the case and only agreement is required.
    """

    id: str
    text: str
    expected: str | None = None


def _unchanged(id: str, text: str) -> Case:
    """A row the normalizer must leave exactly as it found it."""
    return Case(id, text, text)


CASES: list[Case] = [
    # (a) the HTML entity spellings fold into the character first, so a page
    # that would have rendered a dash gets the substitution instead.
    Case("entity-named", f"risk{ENTITY_NAMED}reward", "risk, reward"),
    Case("entity-named-uppercase", f"risk{ENTITY_NAMED_UPPER}reward", "risk, reward"),
    Case("entity-named-mixed-case", f"risk{ENTITY_NAMED_MIXED}reward", "risk, reward"),
    Case("entity-decimal", f"risk{ENTITY_DECIMAL}reward", "risk, reward"),
    Case("entity-hex-lower", f"risk{ENTITY_HEX}reward", "risk, reward"),
    Case("entity-hex-upper", f"risk{ENTITY_HEX_UPPER}reward", "risk, reward"),
    Case("entity-spaced", f"risk {ENTITY_NAMED} reward", "risk, reward"),
    Case("entity-at-start", f"{ENTITY_NAMED} the edge fades", "the edge fades"),
    Case("entity-at-end", f"the edge fades {ENTITY_NAMED}", "the edge fades"),
    Case(
        "entity-after-comma",
        f"withheld, {ENTITY_NAMED} then graded",
        "withheld, then graded",
    ),
    # the semicolon is optional to a browser, but the table names the three
    # spellings with it. Agreement is still mandatory.
    Case("entity-named-no-semicolon", f"risk{ENTITY_NAMED_BARE} reward", None),
    Case("entity-decimal-padded", f"risk{ENTITY_DECIMAL_PADDED}reward", None),
    # (b) placement. Whitespace on both sides collapses first.
    Case("mid-sentence", f"in-sample {DASH} out-of-sample", "in-sample, out-of-sample"),
    Case("tight", f"in-sample{DASH}out-of-sample", "in-sample, out-of-sample"),
    Case("wide-whitespace", f"in-sample   {DASH}   out", "in-sample, out"),
    Case("at-start", f"{DASH} the edge fades", "the edge fades"),
    Case("at-start-after-whitespace", f"   {DASH} the edge fades", "the edge fades"),
    Case("at-end", f"the edge fades {DASH}", "the edge fades"),
    Case("at-end-trailing-space", f"the edge fades {DASH}   ", "the edge fades"),
    Case("lone-dash", DASH, ""),
    # never double-punctuate: a single space after a closer, not a comma
    Case("after-comma", f"withheld, {DASH} then graded", "withheld, then graded"),
    Case("after-comma-tight", f"withheld,{DASH}then graded", "withheld, then graded"),
    Case("after-semicolon", f"withheld; {DASH} then graded", "withheld; then graded"),
    Case("after-colon", f"note: {DASH} then graded", "note: then graded"),
    Case("after-period", f"Done. {DASH} Next up", "Done. Next up"),
    Case("after-bang", f"Stop! {DASH} Now", "Stop! Now"),
    Case("after-question", f"Why? {DASH} Because", "Why? Because"),
    Case("run-of-two", f"a double dash {DASH}{DASH} here", "a double dash, here"),
    Case("two-separate-dashes", f"a {DASH} b {DASH} c", "a, b, c"),
    # Cases the table does not settle: agreement only, deliberately.
    #
    # The table speaks about the character BEFORE the dash and says nothing
    # about the one after, so a dash sitting in front of punctuation is the
    # implementations' call. Pinning an answer here would settle by test what
    # the owner has not settled by policy. Agreeing on it is still mandatory,
    # because the frontend layer is the last one before a page.
    Case("line-start", f"one line\n{DASH} a bullet", None),
    Case("line-end", f"a trailing dash {DASH}\nand a new line", None),
    Case("before-comma", f"withheld{DASH}, then graded", "withheld, then graded"),
    Case("before-period", f"the edge fades{DASH}. Next", "the edge fades. Next"),
    Case("before-close-paren", f"(a {DASH}) b", None),
    Case("after-open-paren", f"({DASH} a) b", None),
    Case("before-quote", f'she said "a {DASH}" and left', None),
    # (c) the spaced double hyphen is the same defect in a different glyph.
    Case("spaced-double-hyphen", "risk -- reward", "risk, reward"),
    Case("spaced-double-hyphen-run", "a -- b -- c", "a, b, c"),
    # The case that needed a SECOND pass, and the reason both implementations
    # run the substitution to a fixed point. A dash flush against a bare double
    # hyphen leaves that hyphen pair unspaced, so pass one reads it as a flag;
    # the comma and space pass one inserts are then the missing left-hand
    # whitespace, and pass two folds it. One pass shipped ", -- " to a page,
    # breaking rule (e) and rule (d) in the same string.
    Case("dash-flush-against-a-bare-pair", f"risk{DASH}-- reward", "risk, reward"),
    Case(
        "entity-flush-against-a-bare-pair",
        f"risk{ENTITY_NAMED}-- reward",
        "risk, reward",
    ),
    Case("dash-flush-against-a-pair-then-tab", f"a{DASH}--\tb", "a, b"),
    Case("spaced-double-hyphen-tabs", "risk\t--\treward", None),
    Case("spaced-double-hyphen-newline", "risk\n--\nreward", None),
    Case("double-hyphen-at-end", "a trailing pair --", None),
    # ... and a bare or leading one is a command-line flag, never touched.
    _unchanged("flag-project", "uv run --project backend python -m pytest"),
    _unchanged("flag-reload", "uvicorn app.main:app --reload"),
    _unchanged("flag-leading", "--project backend"),
    _unchanged("flag-several", "node --no-warnings --experimental-strip-types"),
    _unchanged("flag-with-value", "npm --prefix frontend run lint"),
    _unchanged("double-hyphen-bare", "a--b"),
    # (f) numeric tokens survive untouched, so guardrail #4 keeps holding.
    Case(
        "numbers",
        f"Sharpe 1.20 in-sample {DASH} 0.30 out-of-sample",
        "Sharpe 1.20 in-sample, 0.30 out-of-sample",
    ),
    Case("date-range", f"2024-01-03 {DASH} 2026-08-31", "2024-01-03, 2026-08-31"),
    _unchanged("negative-number", "a drawdown of -12.4% over 3 windows"),
    _unchanged("hyphenated-words", "an out-of-sample well-behaved result"),
    # nothing to do
    _unchanged("clean", "nothing to do here"),
    _unchanged("empty", ""),
    Case("en-dash-left-alone", f"an en-dash {EN_DASH} is another character", None),
    # The clause is already closed on the RIGHT. Emitting a comma here doubles
    # the punctuation up, which shipped once as "withheld, , then graded".
    Case("before-comma-spaced", f"withheld {DASH}, then graded", "withheld, then graded"),
    Case("before-period-spaced", f"a run {DASH}. Next one", "a run. Next one"),
    Case("before-semicolon", f"fills at the quote {DASH}; never mid",
         "fills at the quote; never mid"),
    Case("before-colon", f"the cost {DASH}: two cents a contract",
         "the cost: two cents a contract"),
    Case("before-question", f"is it evidence {DASH}? no", "is it evidence? no"),
    # Dropping the dash joins its neighbours, and there is exactly one shape
    # where that INVENTS A NUMBER: a digit on the left, a decimal point and a
    # digit on the right. 1 and .1 must not become the token 1.1, which exists
    # in no stats payload and which guardrail #4 would reject on sight.
    Case("before-decimal-keeps-both-numbers", f"1{DASH}.1", "1, .1"),
    Case("before-decimal-spaced", f"2.5 {DASH} .75", "2.5, .75"),
    # a period that ends a sentence still collapses, because no number forms
    Case("before-period-after-digit", f"only 15 {DASH}. Thin", "only 15. Thin"),
]

CASE_IDS = [case.id for case in CASES]

# Node reads a JSON array of strings on stdin and returns one pass and two.
# It imports the real module, which is the whole point.
_HARNESS = """
import * as punctuation from "{punctuation_ts}";
const candidates = {candidates};
const name = candidates.find((n) => typeof punctuation[n] === "function");
if (!name) {{
  process.stdout.write(
    JSON.stringify({{ ok: false, exports: Object.keys(punctuation) }})
  );
}} else {{
  const fn = punctuation[name];
  let raw = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) raw += chunk;
  const inputs = JSON.parse(raw);
  process.stdout.write(
    JSON.stringify({{
      ok: true,
      name,
      once: inputs.map((text) => fn(text)),
      twice: inputs.map((text) => fn(fn(text))),
    }})
  );
}}
"""


def _run_typescript(
    inputs: list[str], module: Path | None = None
) -> dict[str, list[str]]:
    """One pass and two through the real TypeScript normalizer, via node.

    `module` overrides the file under test. Only the mutation check below
    passes it, so that the guard can be watched catching a wrong answer
    instead of being trusted to.
    """
    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "the normalizer parity guard could not run: node is not on PATH. "
            "It executes the real frontend/lib/punctuation.ts and must never "
            "skip (see V-58)."
        )
    target = module or PUNCTUATION_TS
    if not target.is_file():
        pytest.fail(
            f"the normalizer parity guard could not run: {target} not "
            "found. If the frontend normalizer moved, point this guard at it "
            "rather than letting the comparison disappear."
        )

    harness = _HARNESS.format(
        punctuation_ts=target.as_posix(),
        candidates=json.dumps(list(TS_EXPORT_CANDIDATES)),
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "parity_harness.mjs"
            script.write_text(harness)
            proc = subprocess.run(
                [node, "--no-warnings", "--experimental-strip-types", str(script)],
                input=json.dumps(inputs),
                capture_output=True,
                text=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"the normalizer parity guard could not run: {exc}")

    if proc.returncode != 0:
        pytest.fail(
            "the normalizer parity guard could not run: node exited "
            f"{proc.returncode}\nstderr:\n{proc.stderr[:4000]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "the normalizer parity guard could not run: the harness produced no "
            f"JSON.\nstdout:\n{proc.stdout[:2000]}\nstderr:\n{proc.stderr[:2000]}"
        )

    if not result.get("ok"):
        pytest.fail(
            "frontend/lib/punctuation.ts exports no normalizer under any of "
            f"{list(TS_EXPORT_CANDIDATES)}. It exports: {result.get('exports')}. "
            "Add the new name to TS_EXPORT_CANDIDATES; a renamed export must not "
            "quietly stop being compared."
        )
    for key in ("once", "twice"):
        assert len(result[key]) == len(inputs), "the harness dropped cases"
    return {"once": result["once"], "twice": result["twice"], "name": result["name"]}


@pytest.fixture(scope="module")
def typescript() -> dict[str, list[str]]:
    return _run_typescript([case.text for case in CASES])


@pytest.fixture(scope="module")
def python_once() -> list[str]:
    return [normalize(case.text) for case in CASES]


def _show(value: str) -> str:
    """A repr that names the dash instead of printing it, since this file may
    not carry the character and a failure message is still source output."""
    return repr(value).replace(DASH, "<U+2014>").replace(EN_DASH, "<U+2013>")


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_python_and_typescript_agree_byte_for_byte(
    index: int, python_once: list[str], typescript: dict[str, list[str]]
) -> None:
    """The guard. One table, two implementations, identical bytes."""
    case = CASES[index]
    got_py = python_once[index]
    got_ts = typescript["once"][index]
    assert got_py == got_ts, (
        f"{case.id}: the two normalizers disagree on the same input.\n"
        f"  input:  {_show(case.text)}\n"
        f"  python: {_show(got_py)}\n"
        f"  node:   {_show(got_ts)}\n"
        "One page renders the backend's answer and another renders the "
        "frontend's. Fix the substitution table on whichever side is wrong; do "
        "not relax this row."
    )


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_python_matches_the_canonical_table(index: int, python_once: list[str]) -> None:
    """Parity alone would bless two identically wrong implementations."""
    case = CASES[index]
    if case.expected is None:
        pytest.skip("the canonical table leaves this case to the implementations")
    assert python_once[index] == case.expected, (
        f"{case.id}: app/text.py does not implement the canonical table.\n"
        f"  input:    {_show(case.text)}\n"
        f"  expected: {_show(case.expected)}\n"
        f"  got:      {_show(python_once[index])}"
    )


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_typescript_matches_the_canonical_table(
    index: int, typescript: dict[str, list[str]]
) -> None:
    case = CASES[index]
    if case.expected is None:
        pytest.skip("the canonical table leaves this case to the implementations")
    assert typescript["once"][index] == case.expected, (
        f"{case.id}: frontend/lib/punctuation.ts does not implement the "
        "canonical table.\n"
        f"  input:    {_show(case.text)}\n"
        f"  expected: {_show(case.expected)}\n"
        f"  got:      {_show(typescript['once'][index])}"
    )


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_both_normalizers_are_idempotent(
    index: int, python_once: list[str], typescript: dict[str, list[str]]
) -> None:
    """normalize(normalize(x)) == normalize(x), on both sides.

    Read-time normalization runs on text that may already have been normalized
    at write time. A second pass that changes anything means the bytes a viewer
    sees depend on how many layers a string happened to cross.
    """
    case = CASES[index]
    assert normalize(python_once[index]) == python_once[index], (
        f"{case.id}: a second Python pass changed the string"
    )
    assert typescript["twice"][index] == typescript["once"][index], (
        f"{case.id}: a second TypeScript pass changed the string"
    )


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_no_dash_defect_survives_either_normalizer(
    index: int, python_once: list[str], typescript: dict[str, list[str]]
) -> None:
    """Rule (e). Nothing the normalizer emits may still read as a dash.

    A bare double hyphen is exempt because it is a command-line flag, which is
    why the forbidden list carries the SPACED form only.
    """
    case = CASES[index]
    for label, got in (("python", python_once[index]), ("node", typescript["once"][index])):
        for bad in MUST_BE_ABSENT:
            assert bad not in got, (
                f"{case.id}: the {label} normalizer left {_show(bad)} in its "
                f"output {_show(got)}"
            )
        for bad in MUST_NOT_BE_INTRODUCED:
            assert got.count(bad) <= case.text.count(bad), (
                f"{case.id}: the {label} normalizer reached for {_show(bad)} as "
                f"a replacement.\n  input:  {_show(case.text)}\n"
                f"  output: {_show(got)}"
            )


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_numeric_tokens_survive(
    index: int, python_once: list[str], typescript: dict[str, list[str]]
) -> None:
    """Rule (f), and guardrail #4 downstream of it.

    The verdict validator rejects any number in verdict text that is not in the
    stats payload. A normalizer that edited a digit would turn a grounded
    verdict into a rejected one, or worse, a wrong one.
    """
    case = CASES[index]
    digits = _numeric_tokens(case.text)
    for label, got in (("python", python_once[index]), ("node", typescript["once"][index])):
        assert _numeric_tokens(got) == digits, (
            f"{case.id}: the {label} normalizer changed the numeric tokens.\n"
            f"  input:  {_show(case.text)} -> {digits}\n"
            f"  output: {_show(got)} -> {_numeric_tokens(got)}"
        )


# The digits inside a numeric entity spell the CHARACTER, not a number, and
# folding one away is the whole job. Strip the entity spellings before counting
# so a decimal entity does not read as the number it spells going missing.
_ENTITY_RE = re.compile(
    _AMP + "(?:mdash|#0*" + str(0x2014) + "|#[xX]0*" + format(0x2014, "04x") + ");?",
    re.IGNORECASE,
)


def _numeric_tokens(text: str) -> list[str]:
    """Every run of digits, in order. Deliberately crude: it is the digits that
    must survive, not the punctuation around them."""
    tokens: list[str] = []
    current: list[str] = []
    for char in _ENTITY_RE.sub("", text):
        if char.isdigit():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def test_the_table_covers_every_rule() -> None:
    """A shrinking table is how a parity guard stops guarding.

    Each id below anchors one row of the canonical substitution table. Losing
    one should be a visible edit here rather than a quiet drop in coverage.
    """
    required = {
        "entity-named",
        "entity-decimal",
        "entity-hex-lower",
        "at-start",
        "at-end",
        "after-comma",
        "after-period",
        "mid-sentence",
        "spaced-double-hyphen",
        "flag-project",
        "flag-reload",
        "flag-leading",
        "numbers",
        "dash-flush-against-a-bare-pair",
    }
    missing = required - set(CASE_IDS)
    assert not missing, f"the parity table lost coverage of: {sorted(missing)}"
    assert len(CASE_IDS) == len(set(CASE_IDS)), "duplicate case ids"


def test_the_parity_guard_can_fail(tmp_path: Path) -> None:
    """A guard nobody has watched catch anything is a decoration.

    The first version of this check compared the Python normalizer against a
    string it built itself. It passed whether or not node ran at all, so a
    harness that silently echoed its input would have read as agreement,
    which is the one failure a parity guard exists to notice.

    This runs the REAL harness against a deliberately broken COPY of the
    TypeScript and asserts the comparison reports the difference. If the
    mutation stops matching the implementation the assertion below says so,
    rather than the check quietly testing nothing again.
    """
    source = PUNCTUATION_TS.read_text(encoding="utf-8")
    broken = source.replace('return ", ";', 'return "; ";')
    assert broken != source, (
        "the mutation no longer matches frontend/lib/punctuation.ts, so this "
        "check is testing nothing. Point it at the current substitution."
    )
    mutant = tmp_path / "punctuation.ts"
    mutant.write_text(broken, encoding="utf-8")

    inputs = [case.text for case in CASES]
    mutated = _run_typescript(inputs, module=mutant)
    expected = [normalize(text) for text in inputs]
    assert mutated["once"] != expected, (
        "the parity harness reported agreement with a normalizer that "
        "substitutes a semicolon, so it is not executing the file it claims "
        "to execute"
    )
