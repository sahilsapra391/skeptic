"""House punctuation for text the product did not write by hand.

The em-dash is banned from every surface. Sweeping and guarding the source
tree handles the text people type, but three runtime sources can still put
one on a page: the verdict narration LLM, the grounded-answer LLM, and the
parser's clarifying questions. A fourth already exists in the database:
runs saved before the ban carry em-dashes inside stored verdict text and
stored provenance, and `app/db.py` is explicit that a stored verdict is
never rewritten.

`normalize` is the one substitution every enforcement layer shares:

- WRITE time, the moment an LLM string is received and before validation,
  so the numeric validator and the English guard in `app/honesty/verdict.py`
  operate on exactly the text that ships.
- READ time, on outbound prose only. The stored row keeps the byte-exact
  record (provenance integrity), the wire gets clean text.
- EXPORT time, in `app/notebook/`, where the server renders a terminal
  artifact (the HTML report, the .ipynb) that no other layer can reach.

The canonical substitution table, which `frontend/lib/punctuation.ts`
implements identically so both sides produce the same bytes for the same
input:

  1. The HTML entity spellings fold into the character itself first: the
     named form, the decimal form and the hex form, case-insensitively.
     A page renders those as an em-dash, so a normalizer that only saw the
     glyph would wave them straight through.
  2. A SPACED double hyphen is the same defect wearing a different glyph
     and folds into the character too. Whitespace on BOTH sides is the
     whole test, which is what keeps a bare or leading one intact:
     `--project` and `--reload` are command-line flags, this repo's own
     prose is full of them, and a normalizer that edits a command line is
     a bug wearing a fix's clothes.
  3. Each remaining em-dash takes the horizontal whitespace on both sides
     with it and becomes: nothing at the start of the line, nothing at the
     end of it, a single space when the previous non-space character
     already closes the clause (one of , ; : . ! ?), and otherwise a comma
     and one space.

Two properties are pinned by tests. No numeric token moves, so guardrail #4
keeps holding over normalized verdict text. And the result is idempotent,
which the substitution earns by running to a fixed point rather than
assuming one pass reaches it: see `_MAX_PASSES` for the one input shape
where it does not.

Every dash spelling here is assembled from its code point. This module is
subject to the same CI guard as every other file, and the guard flags an
entity wherever it appears, including inside the pattern that removes it.
Only the escape naming U+2014 is spelled out, which the guard permits in
the two normalizer implementations and nowhere else.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

EM_DASH = "\u2014"

_CODEPOINT = ord(EM_DASH)
_AMPERSAND = chr(0x26)
_HASH = chr(0x23)
_DECIMAL = str(_CODEPOINT)
_HEX = format(_CODEPOINT, "04x")

# The three ways an em-dash reaches a reader without the character being
# present in the bytes. The trailing semicolon is optional and the numeric
# forms allow leading zeros, because a browser renders them that way and the
# reader sees a dash either way. Same reading the CI guard takes, and the
# same pattern `frontend/lib/punctuation.ts` compiles: the two are one
# contract, and a spelling folded on one side of the wire and not the other
# is a difference that only ever shows up on a page.
_ENTITY = re.compile(
    _AMPERSAND
    + "(?:mdash"
    + "|" + _HASH + "0*" + _DECIMAL
    + "|" + _HASH + "x0*" + _HEX
    + ");?",
    re.IGNORECASE,
)

# Horizontal whitespace only. A newline is a boundary, not padding: joining
# two lines because one of them happened to open with a dash would rewrite
# the layout of an answer, which is not this function's business.
_DASH_RUN = re.compile(r"[^\S\n]*" + EM_DASH + r"+[^\S\n]*")

# A double hyphen with horizontal whitespace on BOTH sides: prose, and
# handled as prose. `--project`, `--reload` and a bare `--` at the head of a
# line carry whitespace on one side only and never match, which is the whole
# point: the repo's own documented commands have to survive this function
# untouched. A run of three or more hyphens (a markdown rule) is excluded by
# the lookahead. The whitespace is consumed rather than looked at, so two of
# these in a row cannot share the space between them.
_SPACED_DOUBLE_HYPHEN = re.compile(r"[^\S\n]+--(?!-)[^\S\n]+")

# a comma placed after one of these would double the punctuation up
_CLAUSE_CLOSERS = frozenset(",;:.!?")

# a decimal point with a digit behind it: the one right-hand shape that turns
# two numbers into one when the dash between them simply disappears
_DECIMAL_TAIL = re.compile(r"\.\d")


def _substitute(text: str) -> str:
    """The em-dash rule itself, over text whose other spellings are folded."""

    def _replace(match: re.Match[str]) -> str:
        start, end = match.span()
        # the run swallowed the horizontal whitespace, so this IS the
        # previous non-space character (or a newline, or nothing at all)
        previous = text[start - 1] if start else ""
        following = text[end] if end < len(text) else ""
        if not previous or previous == "\n":
            return ""
        if not following or following == "\n":
            return ""
        # The clause is already closed on the RIGHT, so emitting a comma here
        # would double it up ("withheld, , then graded"). Dropping the dash
        # joins its neighbours, which is only ever wrong when that FORMS A
        # NUMBER: "1" + dash + ".1" would become the token 1.1, a figure that
        # exists in no stats payload, and guardrail #4 rejects exactly that.
        # A decimal point between digits is the one shape that can do it.
        if following in _CLAUSE_CLOSERS and not (
            previous.isdigit() and _DECIMAL_TAIL.match(text, end)
        ):
            return ""
        if previous in _CLAUSE_CLOSERS:
            return " "
        return ", "

    return _DASH_RUN.sub(_replace, text)


def _one_pass(text: str) -> str:
    """Fold the other spellings into the character, then substitute once."""
    if EM_DASH not in text and _AMPERSAND not in text and "--" not in text:
        return text
    folded = _ENTITY.sub(EM_DASH, text)
    folded = _SPACED_DOUBLE_HYPHEN.sub(EM_DASH, folded)
    if EM_DASH not in folded:
        return folded
    return _substitute(folded)


# One pass is not always a fixed point, so the passes run to one. The case is
# narrow and it is real: a dash sitting flush against a BARE double hyphen
# ("risk<dash>-- reward") has no whitespace on its left, so the double hyphen
# is a flag as far as pass one is concerned and is left alone, and then the
# comma-and-space the dash substitution puts there IS that missing whitespace.
# Pass two now reads a spaced double hyphen and folds it. Stopping after one
# pass would ship ", -- " to a page, which breaks rule (e) (never emit a double
# hyphen) and rule (d) (idempotence) in the same string.
#
# The bound is a hard stop, not an expectation: every input in the fuzz corpus
# settles by pass three, because each pass that changes anything consumes at
# least one dash-shaped token and the substitutions only ever insert ", ", " "
# or nothing. The cap is what makes termination a property of the code rather
# than of that argument.
_MAX_PASSES = 8


def normalize(text: str) -> str:
    """`text` with every em-dash spelling replaced by house punctuation.

    Between words it becomes a comma and one space, and the whitespace run
    around it collapses, so the result reads ", " and never " , ". At the
    start or the end of a line it simply goes away. After punctuation that
    already closes the clause it leaves a single space.

    No other dash character is ever emitted: not an en-dash, not a
    horizontal bar, not a double hyphen, not an HTML entity. Digits are
    never touched either, so a normalized string carries exactly the numeric
    tokens it arrived with and guardrail #4 keeps holding over it. (The one
    thing that can remove digits is folding a decimal or hex ENTITY, whose
    digits spell the dash rather than a number. That direction is safe: the
    validator's job is to reject numbers with no home in the stats, and this
    takes one away rather than inventing one.)

    Idempotent, because the passes run to a fixed point rather than stopping
    after one. See `_MAX_PASSES` for the case that needs the second pass.
    """
    result = text
    for _ in range(_MAX_PASSES):
        following = _one_pass(result)
        if following == result:
            return result
        result = following
    return result


def _normalized_items(value: dict[Any, Any]) -> dict[Any, Any]:
    """The dict branch, keys included.

    A key is prose on at least one payload: `skipReasons` is keyed by the
    engine's own skip labels and `app/notebook/report.py` renders those keys
    as table rows, so a dash in a key reaches a page exactly like a dash in a
    value. In practice this only ever rewrites a key that CONTAINS a dash
    spelling, and no contract key does: the tree is em-dash free and the CI
    guard keeps it that way, so no lookup against a literal can break.

    A rewrite that collided with another key would silently drop a row, so on
    a collision BOTH sides keep the key they arrived with. Losing a row is
    worse than keeping a dash in the one pathological case where two distinct
    keys normalize to the same string.
    """
    renamed = [
        (normalize(key) if isinstance(key, str) else key, key, item)
        for key, item in value.items()
    ]
    taken = Counter(new for new, _, _ in renamed)
    return {
        (new if taken[new] == 1 else old): normalize_tree(item)
        for new, old, item in renamed
    }


def normalize_tree(value: Any) -> Any:
    """Every string inside a decoded-JSON value, normalized, keys included.

    Structure and numbers are untouched. Nothing is mutated in place, so a
    caller can normalize a stored record for display and still hold the
    original bytes.
    """
    if isinstance(value, str):
        return normalize(value)
    if isinstance(value, dict):
        return _normalized_items(value)
    if isinstance(value, list):
        return [normalize_tree(item) for item in value]
    return value


def normalize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """`normalize_tree` for a JSON object, typed for callers that must keep
    a dict (payload blocks, run summaries)."""
    return _normalized_items(value)
