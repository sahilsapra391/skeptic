"""No em-dash survives in tracked source, in any of the forms it can hide in.

The owner's punctuation rule is absolute: the em-dash is not house punctuation
and must never appear on a Skeptic surface. A one-time sweep can clean the tree
that exists today. It cannot stop the next contributor, the next paste from a
chat window, or the next generated docstring from putting one back.

Why this is a TEST and not only the PreToolUse hook next to it: the hook fires
only for someone running Claude Code with this repo's settings loaded, and it
escalates to a prompt a human can approve through. CI can do neither. It runs
for every contributor and every automated branch, and it fails the build
instead of asking. The hook is the fast local signal; this is the gate.

Why more than a plain character search. Several ASCII spellings render as an
em-dash to a reader while a character-level grep sees nothing: the HTML
entities (named, decimal, hexadecimal), the CSS escape that ships through a
``content:`` rule, and the percent-encoded UTF-8 bytes inside a URL. Each of
those is checked in every file, because in every file they are content.

The backslash-u escapes are checked differently, and the difference is the one
judgement call in here. An escape decodes to a real em-dash for every consumer
of the file it sits in: a JSON parser turns it back into the glyph, which is how
the sweep found one hiding inside a fixture, and so does every Python and
TypeScript string literal, which is how one reaches a page through source that
a character-level grep calls clean.

So the escape is a violation like the literal, and the exemption is a PATH, not
a file type. A file-type rule ("escapes are fine in code") let any of several
hundred .ts and .py files name the character and ship it to a browser. The only
files whose job is to name U+2014 are the two normalizer implementations and
the tests that drive them, and they are listed below by path, the same way the
design-export exclusion is keyed on path rather than on a banner in the file
(V-221). Anything else that needs the character builds it from a code point,
which is what this file does.

This file spells out no sequence it hunts for. Every pattern is assembled at
runtime from the codepoint, because a guard whose own source trips the guard
needs an exemption, and an exemption is the hole the guard exists to close.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# backend/tests/ -> backend/ -> repo root. Resolved from __file__ rather than
# from the process cwd, because CI runs pytest with working-directory: backend
# while the files to walk live at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

CODEPOINT = 0x2014
EM_DASH = chr(CODEPOINT)

# Pattern fragments built from the codepoint, so this file never spells out a
# sequence it rejects. _BS is a backslash, _AMP an ampersand.
_BS = chr(0x5C)
_AMP = chr(0x26)
_HEX4 = format(CODEPOINT, "04x")
_HEX8 = format(CODEPOINT, "08x")
_DEC = str(CODEPOINT)
_PCT = "".join(f"%{byte:02X}" for byte in EM_DASH.encode("utf-8"))

# Spellings that render as an em-dash wherever they appear. Checked in every
# tracked file. The trailing semicolon on the entities is optional because
# browsers render them without it.
_RENDERED_FORMS: dict[str, str] = {
    "html named entity": _AMP + "mdash;?",
    "html decimal entity": _AMP + "#0*" + _DEC + ";?",
    "html hex entity": _AMP + "#[xX]0*" + _HEX4 + ";?",
    # CSS escape, as in a content: rule. Bounded on the right so a longer
    # codepoint that merely starts with these digits is not misread as one.
    "css escape": re.escape(_BS) + _HEX4 + "(?![0-9a-fA-F])",
    # The form an em-dash takes inside a URL.
    "percent encoded": re.escape(_PCT),
}

# String escapes. A parser decodes each of these back into the character, so
# outside the files whose job is to name it, an escape is the glyph.
_ESCAPE_FORMS: dict[str, str] = {
    "unicode escape": re.escape(_BS) + "u" + _HEX4,
    "unicode brace escape": re.escape(_BS) + r"u\{0*" + _HEX4 + r"\}",
    "wide unicode escape": re.escape(_BS) + "U" + _HEX8,
}

# The only paths permitted to spell the escape, because naming U+2014 is what
# they are for: the two normalizer implementations, and the tests that execute
# them. Repo-relative and posix, matching `git ls-files` output.
#
# This list is the whole exemption. It is short on purpose and pinned by
# `test_escape_allowlist_stays_narrow`, because "escapes are allowed in code"
# is how a .ts string puts an em-dash on a page while a grep for the glyph
# reports the tree clean.
ESCAPE_MAY_NAME_THE_CHARACTER = frozenset(
    {
        "backend/app/text.py",
        "frontend/lib/punctuation.ts",
        "backend/tests/test_em_dash_runtime.py",
        "backend/tests/test_normalizer_parity.py",
    }
)


def _compile(forms: dict[str, str]) -> re.Pattern[str]:
    named = (f"(?P<{name.replace(' ', '_')}>{pattern})" for name, pattern in forms.items())
    return re.compile("|".join(named), re.IGNORECASE)


_RENDERED_RE = _compile(_RENDERED_FORMS)
_ESCAPE_RE = _compile(_ESCAPE_FORMS)

FIX = (
    "Replace it with house punctuation: a comma, a period, or parentheses. "
    "Never an en-dash, a horizontal bar, or a double hyphen, which are the "
    "same problem wearing a different glyph. Code that has to handle the "
    "character builds it from its code point (chr(0x2014) in Python, "
    "String.fromCharCode(0x2014) in TypeScript). Only the two normalizers and "
    "their direct tests may spell the backslash-u escape."
)


def escapes_are_content(path: str) -> bool:
    """True when a string escape in this file counts as an em-dash.

    Which is everywhere except the handful of paths whose job is to name the
    character. The rule is the path, never the suffix: a .ts or .py string
    escape decodes to the glyph and renders as one, exactly like a .json one.
    """
    return path not in ESCAPE_MAY_NAME_THE_CHARACTER


def _is_design_export(path: str) -> bool:
    """The only path exclusion, and it is deliberately narrow.

    ``docs/design/`` holds artifacts a design tool wrote: the vendored design
    system bundle under ``_ds/`` and the ``support.js`` canvas runtimes. No
    frontend source imports either of them, nothing bundles them, and no code
    path renders their text, so a dash inside one never reaches a page. They
    are also regenerated wholesale by the tool, so editing them is work the
    next export throws away.

    Two properties matter. The exclusion keys on the PATH, not on the GENERATED
    banner those files carry on line one, because a banner is prose and prose
    gets reworded, which is how a guard in this repo died silently once already
    (V-221). And it is anchored at ``docs/design/``, so it cannot grow to cover
    application source: a ``_ds`` directory or a ``support.js`` anywhere else in
    the tree is checked like anything else.
    """
    parts = Path(path).parts
    if parts[:2] != ("docs", "design"):
        return False
    return "_ds" in parts or parts[-1] == "support.js"


def _tracked_files() -> list[str]:
    """Repo-relative paths of every file in the checkout, from git.

    TRACKED FILES PLUS UNTRACKED ONES GIT WOULD ADD (``--others
    --exclude-standard``), and the second half is not a nicety. A guard keyed on
    ``git ls-files`` alone cannot see a file until somebody stages it, which
    means the newest code in the tree is the code it has never read. That is
    not hypothetical: this very guard, both normalizer implementations and the
    commit hook were all untracked while this branch was being written, so the
    scan below ran on every file in the repository except the seven the work
    added. A guard that exempts new files exempts exactly the files most likely
    to carry the defect.

    ``--exclude-standard`` honours .gitignore, so build output, virtualenvs and
    the rest never reach the walk. In CI the checkout is clean and this term
    adds nothing; locally it means a file is checked the moment it exists
    rather than the moment it is staged.

    A failure here is reported, never skipped. This test's whole value is that
    it runs; one that quietly passes when it cannot enumerate the tree is worse
    than no test, because the build goes green on a repository nobody checked
    (V-58: fail, never skip).
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        pytest.fail(
            f"could not list tracked files under {REPO_ROOT} ({exc}). This guard "
            "cannot do its job, so it fails rather than passing on an unchecked "
            "tree. Run the suite from a git checkout with git on PATH."
        )
    return [path for path in proc.stdout.split("\0") if path]


def scan_text(text: str, path: str = "") -> list[tuple[int, str, str]]:
    """Find every em-dash in ``text``, literal or encoded.

    ``path`` decides whether string escapes count, per the module docstring.
    Returns ``(line number, form, the offending line)`` tuples.
    """
    patterns = [_RENDERED_RE]
    if escapes_are_content(path):
        patterns.append(_ESCAPE_RE)

    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if EM_DASH in line:
            found.append((number, "literal character", line))
        for pattern in patterns:
            for match in pattern.finditer(line):
                form = (match.lastgroup or "encoded").replace("_", " ")
                found.append((number, form, line))
    return found


def _read_text(path: Path) -> str | None:
    """File content, or None for a binary file.

    Only images fail to decode in this repository. A PNG cannot put an em-dash
    on a page as text, and forcing a byte-level match on one invents failures,
    so undecodable files are skipped on purpose.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def test_no_file_in_the_checkout_contains_an_em_dash() -> None:
    tracked = _tracked_files()
    assert tracked, (
        f"git listed no files under {REPO_ROOT}. Something is wrong with "
        "the checkout, and this guard reports that rather than passing."
    )

    offenders: list[str] = []
    for relative in tracked:
        if _is_design_export(relative):
            continue
        path = REPO_ROOT / relative
        if not path.is_file():
            continue  # staged deletion, or a submodule entry
        text = _read_text(path)
        if text is None:
            continue
        for number, form, line in scan_text(text, relative):
            snippet = line.strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            offenders.append(f"  {relative}:{number}  [{form}]\n      {snippet}")

    assert not offenders, (
        f"{len(offenders)} em-dash(es) found in the checkout:\n\n"
        + "\n".join(offenders[:40])
        + ("\n  ... and more" if len(offenders) > 40 else "")
        + f"\n\n{FIX}\n\nThe house rule is absolute and applies to code, comments, "
        "docs, fixtures, and UI copy alike. If a file listed here is a generated "
        "design-tool export rather than application source, it belongs under "
        "docs/design/ with the others, not in a new exemption. If it is source "
        "that genuinely has to handle the character, build it from the code "
        "point; the escape allowlist is not open for new entries."
    )


# --- the guard's own guard ------------------------------------------------
#
# A detector nobody has watched catch anything is a decoration. These build
# each spelling from the codepoint at runtime and prove the scanner sees it, so
# a regression in the pattern list fails here loudly instead of failing
# silently by waving the whole repository through.


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(EM_DASH, id="literal"),
        pytest.param(_BS + _HEX4 + " ", id="css-escape"),
        pytest.param(_AMP + "mdash;", id="named-entity"),
        pytest.param(_AMP + "mdash", id="named-entity-no-semicolon"),
        pytest.param(_AMP + "#" + _DEC + ";", id="decimal-entity"),
        pytest.param(_AMP + "#x" + _HEX4 + ";", id="hex-entity"),
        pytest.param(_AMP + "#X" + _HEX4.upper() + ";", id="hex-entity-upper"),
        pytest.param(_PCT, id="percent-encoded"),
    ],
)
def test_scanner_catches_every_rendered_spelling(sample: str) -> None:
    assert scan_text(f"prose {sample} more prose", "any/file.ts"), (
        "the scanner missed a spelling that renders as an em-dash"
    )


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(_BS + "u" + _HEX4, id="unicode-escape"),
        pytest.param(_BS + "u{" + _HEX4 + "}", id="brace-escape"),
        pytest.param(_BS + "U" + _HEX8, id="wide-escape"),
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        # the JSON-fixture case the sweep actually found
        "backend/tests/fixtures/x.json",
        "collector/config.yaml",
        # the hole this narrowing closes: a string escape in ordinary source
        # decodes to the glyph and renders as one on a page
        "frontend/components/verdict/verdict-block.tsx",
        "frontend/lib/format.ts",
        "backend/app/honesty/verdict.py",
        "docs/TECH-SPEC.md",
    ],
)
def test_escapes_are_a_violation_outside_the_normalizers(path: str, sample: str) -> None:
    """An escape is the character. Only its declared home is exempt."""
    assert scan_text(f'note = "a {sample} b"', path), (
        f"an escape slipped through in {path}, which is not a normalizer"
    )


@pytest.mark.parametrize(
    "sample",
    [
        pytest.param(_BS + "u" + _HEX4, id="unicode-escape"),
        pytest.param(_BS + "U" + _HEX8, id="wide-escape"),
    ],
)
@pytest.mark.parametrize("path", sorted(ESCAPE_MAY_NAME_THE_CHARACTER))
def test_only_the_normalizers_may_spell_the_escape(path: str, sample: str) -> None:
    """Code that removes em-dashes has to be able to name one.

    The normalizer on both sides of the wire spells the character exactly this
    way. Flagging it here would force an allowlist for the very files doing the
    cleaning, and the literal glyph is still a violation even in these files.
    """
    assert not scan_text(f'EM_DASH = "{sample}"', path)
    assert scan_text(f"EM_DASH = {EM_DASH}", path), (
        "the exemption covers the escape only. The literal character is a "
        f"violation in {path} like anywhere else."
    )


def test_escape_allowlist_stays_narrow() -> None:
    """The exemption is four paths, and each one has to be a real file.

    Pinned for the same reason the design-export exclusion is pinned. An
    allowlist that can grow by one entry per inconvenience is how "no em-dash
    anywhere" becomes "no em-dash where it was easy", and a stale entry is a
    live hole pointed at a path nobody is watching any more.
    """
    assert ESCAPE_MAY_NAME_THE_CHARACTER == {
        "backend/app/text.py",
        "frontend/lib/punctuation.ts",
        "backend/tests/test_em_dash_runtime.py",
        "backend/tests/test_normalizer_parity.py",
    }
    for relative in sorted(ESCAPE_MAY_NAME_THE_CHARACTER):
        assert (REPO_ROOT / relative).is_file(), (
            f"{relative} is exempt from the escape check but does not exist. "
            "Delete the entry rather than leaving an exemption aimed at a path "
            "anyone can create."
        )


@pytest.mark.parametrize(
    "sample",
    [
        "an en-dash " + chr(0x2013) + " is a different character",
        "a hyphen - is fine",
        _BS + "u2015",  # the horizontal bar's escape, not ours
        _BS + _HEX4 + "5",  # a longer CSS codepoint that merely starts with ours
        _AMP + "ndash;",
        _AMP + "#8211;",
    ],
)
def test_scanner_does_not_invent_hits(sample: str) -> None:
    assert not scan_text(sample, "fixture.json"), f"false positive on {sample!r}"


def test_exclusion_stays_narrow() -> None:
    """The path exclusion must never quietly widen past the design exports.

    An exemption that can grow is how "no em-dash anywhere" becomes "no em-dash
    except wherever someone found it inconvenient". These pin the shape.
    """
    assert _is_design_export("docs/design/landing/_ds/bundle/tokens/colors.css")
    assert _is_design_export("docs/design/landing/support.js")
    assert _is_design_export("docs/design/support.js")

    # Application source is never excluded, whatever it happens to be named.
    assert not _is_design_export("frontend/lib/_ds/thing.ts")
    assert not _is_design_export("frontend/public/support.js")
    assert not _is_design_export("backend/app/honesty/verdict.py")
    assert not _is_design_export("docs/TECH-SPEC.md")
    assert not _is_design_export("docs/design/landing/index.html")


def test_every_excluded_file_is_a_real_design_export() -> None:
    """The exclusion rests on a claim about the tree, so check the claim.

    If a file starts matching the excluded shapes for some other reason, the
    justification (a design-tool export that no frontend source imports) has
    stopped being true, and the exclusion has to be revisited rather than
    inherited.
    """
    excluded = [path for path in _tracked_files() if _is_design_export(path)]
    assert excluded, (
        "nothing matches the design-export exclusion any more. If those exports "
        "are gone, delete the exclusion instead of leaving a hole open."
    )
    for path in excluded:
        assert path.startswith("docs/design/"), path
        assert "/_ds/" in path or path.endswith("/support.js"), path

    # The exclusion covers three things and only three: the vendored design
    # system bundle, and the two generated canvas runtimes. Naming them means a
    # fourth kind of file arriving under docs/design/ is a decision somebody
    # makes in review rather than a silent inheritance.
    bundled = [path for path in excluded if "/_ds/" in path]
    runtimes = sorted(path for path in excluded if path.endswith("/support.js"))
    assert bundled, "the vendored design-system bundle is no longer excluded"
    assert runtimes == ["docs/design/landing/support.js", "docs/design/support.js"], (
        "the excluded support.js runtimes moved. There were two, both generated "
        f"by the design tool; now: {runtimes}"
    )
    assert set(excluded) == set(bundled) | set(runtimes), (
        "something under docs/design/ is excluded that is neither the vendored "
        "bundle nor a support.js runtime"
    )
