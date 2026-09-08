/**
 * House punctuation for prose the product did not write by hand: the browser
 * half of the rule, and the twin of `backend/app/text.py`.
 *
 * The two implementations are one contract, not two cousins. Every input must
 * come out of `stripEmDashes` byte-identical to what `normalize` returns in
 * Python, because both run over the same strings at different moments: the
 * backend cleans at write time and read time, this layer catches whatever
 * reaches a page around them (a verbatim payload the fetch layer must not
 * touch, a frontend deploy that lands ahead of the backend one, a value the
 * client itself holds and echoes back). Two normalizers that disagree are
 * worse than one, because the disagreement only shows up on the page.
 *
 * THE SUBSTITUTION, in the order it happens.
 *
 * 1. The HTML spellings fold into the character: the named entity, the
 *    decimal one, the hex one, matched case-insensitively. They render as a
 *    dash to a reader, so they are the same defect, and folding them first
 *    means there is exactly one code path that decides what replaces a dash.
 * 2. A SPACED double hyphen folds into the character too, for the same
 *    reason. A bare or leading one never does: `--project` and `--reload`
 *    are flags, and a normalizer that edits a command line is a bug wearing
 *    a fix's clothes. Whitespace on BOTH sides is the whole test.
 * 3. Each dash, with the horizontal whitespace hugging it, becomes:
 *      - nothing, at the start or the end (no clause to join),
 *      - one space, when the previous non-space character already closes a
 *        clause (`,;:.!?`), so the sentence never double-punctuates,
 *      - a comma and one space otherwise.
 *
 * Nothing else is emitted. No en-dash, no horizontal bar, no double hyphen,
 * no entity, and no digit is ever touched, so a normalized string carries
 * exactly the numeric tokens it arrived with and the verdict's numeric
 * validator keeps holding over it.
 *
 * Idempotent, and it is the substitution that runs to a fixed point rather
 * than one pass being trusted to reach one. A dash written flush against a
 * BARE double hyphen used to come out as ", --", because the comma and space
 * pass one inserts are exactly the left-hand whitespace that makes the double
 * hyphen spaced, and stopping there shipped one to a page. See `MAX_PASSES`.
 * The Python loops the same way over the same bound: the fix had to land in
 * both files at once or the two stop being one contract.
 *
 * TWO PLACES THE TABLE IS SILENT, resolved here the way the Python resolves
 * them, because parity beats taste:
 *   - A newline is a boundary, not padding. The whitespace run around a dash
 *     is horizontal only, and a dash sitting at the head or the tail of a
 *     LINE is treated as one at the head or tail of the string. Joining two
 *     lines because one opened with a dash would rewrite the layout of an
 *     answer, which is not this function's business.
 *   - The whitespace on both sides goes with the dash when the dash goes,
 *     rather than being left behind at a line edge.
 *
 * The character is written as an escape on purpose, here and in the Python.
 * A literal one would be the first thing the CI guard trips on, and the
 * allowlist it would need is the exact hole the guard exists to close. This
 * file and `backend/app/text.py` are the only two the guard lets name it.
 */

/** U+2014, as an escape. Never write the literal character. */
export const EM_DASH = "\u2014";

/** The same code point as a JSON escape sequence. A server that serialized
 * with ensure_ascii, or a proxy that re-encoded on the way through, sends
 * six ASCII characters instead of the glyph, and a raw-body scan that only
 * looked for the glyph would report a clean body and skip the walk. */
export const EM_DASH_JSON_ESCAPE = "\\u2014";

/** The HTML spellings, all three, case-insensitive. The trailing semicolon
 * is optional and leading zeros are allowed in the numeric forms, because
 * browsers render them that way and so the reader sees a dash either way.
 * Same reading the CI guard takes. */
const DASH_ENTITY = /&(?:mdash|#0*8212|#x0*2014);?/gi;

/** U+2014 inside a pattern source. */
const EM = "\\u2014";

/** Horizontal whitespace, spelled out rather than written as a shorthand,
 * because JS and Python do NOT agree on what the shorthand means. This is
 * exactly Python's `[^\S\n]`: its whitespace set, minus the newline. The
 * gaps between the two are real if exotic (Python counts U+0085 and
 * U+001C-001F, JS counts U+FEFF), and a normalizer that disagreed with its
 * twin over a stray U+0085 would disagree silently, on a page, months from
 * now. Cheaper to enumerate the set than to find that. */
const HWS =
  "[ \\t\\v\\f\\r\\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";

/** Whitespace, two hyphens, whitespace: prose, and handled as prose.
 * `--project`, `--reload` and a bare `--` ending a line are missing the
 * whitespace on one side or the other and never match, which is the whole
 * point, because the repo's own documented commands have to survive this
 * function untouched. Three or more hyphens (a markdown rule) fail the
 * lookahead. Both whitespace runs are CONSUMED, so two of these in a row
 * cannot share the space between them, and the dash rule below sees the
 * result exactly as it would see a typed dash. */
const SPACED_DOUBLE_HYPHEN = new RegExp(`${HWS}+--(?!-)${HWS}+`, "g");

/** One or more dashes with the horizontal whitespace on either side.
 * Newlines are excluded from the run so a dash never eats a line break. */
const DASH_RUN = new RegExp(`${HWS}*${EM}+${HWS}*`, "g");

/** A comma placed after one of these would double the punctuation up. */
const CLAUSE_CLOSERS = ",;:.!?";

/** A decimal point with a digit behind it: the one right-hand shape that
 * turns two numbers into one when the dash between them simply disappears. */
const DECIMAL_TAIL = /^\.\d/;

/** One pass is not always a fixed point, so the passes run to one. The case is
 * narrow and it is real: a dash written flush against a BARE double hyphen,
 * as in `risk` + the dash + `-- reward`, leaves that double hyphen without
 * whitespace on its left, so pass one reads it as a flag and leaves it alone,
 * and then the comma-and-space the dash substitution puts there IS that
 * missing whitespace.
 * Pass two now reads a spaced double hyphen and folds it. Stopping after one
 * pass would ship ", -- " to a page, which breaks rule (e) (never emit a double
 * hyphen) and rule (d) (idempotence) in the same string.
 *
 * The bound is a hard stop, not an expectation: every input in the fuzz corpus
 * settles by pass three, because each pass that changes anything consumes at
 * least one dash-shaped token and the substitutions only ever insert ", ", " "
 * or nothing. The cap is what makes termination a property of the code rather
 * than of that argument. `backend/app/text.py` carries the same loop and the
 * same bound; the two are one contract and this had to land in both at once. */
const MAX_PASSES = 8;

/** Pre-scan: does this string carry any spelling the substitution would
 * rewrite? It is the union of the three patterns above and nothing else, so
 * a miss really does mean the input is already house punctuation. Non-global
 * on purpose, so `test` never carries `lastIndex` from one call to the next. */
const DASH_DEFECT = new RegExp(
  `${EM}|&(?:mdash|#0*8212|#x0*2014);?|${HWS}--(?!-)${HWS}`,
  "i",
);

/**
 * True when `raw` holds an em-dash in any of the spellings that reach a page.
 *
 * For the fetch layer's gate on a RAW response body, where the JSON escape
 * counts as well: a body serialized with `ensure_ascii` carries six ASCII
 * characters that `JSON.parse` turns back into the glyph.
 */
export function hasDashDefect(raw: string): boolean {
  return raw.includes(EM_DASH_JSON_ESCAPE) || DASH_DEFECT.test(raw);
}

/**
 * `text` with every em-dash spelling replaced by house punctuation.
 *
 * Display only. A value the client sends back to the server as authoritative
 * is normalized at the render site on a COPY, never in the round trip: the
 * stored record keeps the bytes the person actually typed.
 */
export function stripEmDashes(text: string): string {
  let result = text;
  for (let pass = 0; pass < MAX_PASSES; pass += 1) {
    const next = onePass(result);
    if (next === result) return result;
    result = next;
  }
  return result;
}

/** Fold the other spellings into the character, then substitute once. */
function onePass(text: string): string {
  if (!DASH_DEFECT.test(text)) return text;
  const folded = text
    .replace(DASH_ENTITY, EM_DASH)
    .replace(SPACED_DOUBLE_HYPHEN, EM_DASH);
  return folded.replace(DASH_RUN, (match: string, offset: number) => {
    // the run swallowed the horizontal whitespace on both sides, so the
    // neighbours here ARE the previous and next non-space characters
    const previous = offset > 0 ? folded[offset - 1] : "";
    const end = offset + match.length;
    const following = end < folded.length ? folded[end] : "";
    if (previous === "" || previous === "\n") return "";
    if (following === "" || following === "\n") return "";
    // The clause is already closed on the RIGHT, so emitting a comma here
    // would double it up ("withheld, , then graded"). Dropping the dash joins
    // its neighbours, which is only ever wrong when that FORMS A NUMBER:
    // "1" + dash + ".1" would become the token 1.1, a figure that exists in no
    // stats payload, and the backend's guardrail #4 rejects exactly that. A
    // decimal point with a digit behind it is the one shape that can do it.
    const joinsANumber =
      previous >= "0" && previous <= "9" && DECIMAL_TAIL.test(folded.slice(end));
    if (CLAUSE_CLOSERS.includes(following) && !joinsANumber) return "";
    if (CLAUSE_CLOSERS.includes(previous)) return " ";
    return ", ";
  });
}

/**
 * Walk a parsed JSON value and normalize every string in it.
 *
 * Returns the ORIGINAL reference when nothing changed, so a clean payload
 * costs one pass and allocates nothing. Inputs are always `JSON.parse`
 * output, so there is nothing here but strings, numbers, booleans, null,
 * arrays and plain objects, and no cycles to guard against.
 */
export function stripEmDashesDeep<T>(value: T): T {
  return walk(value) as T;
}

function walk(value: unknown): unknown {
  if (typeof value === "string") return stripEmDashes(value);
  if (Array.isArray(value)) {
    let changed = false;
    const out = value.map((item) => {
      const next = walk(item);
      if (next !== item) changed = true;
      return next;
    });
    return changed ? out : value;
  }
  if (value !== null && typeof value === "object") {
    let changed = false;
    // Keys are normalized too. A key is prose on at least one payload:
    // `skipReasons` is keyed by the engine's own skip labels, and the
    // backend's HTML report renders those keys as table rows, so a dash in a
    // key reaches a page exactly like a dash in a value. This only ever
    // rewrites a key that CONTAINS a dash spelling, and no contract key does,
    // so no lookup against a literal can break.
    const entries = Object.entries(value as Record<string, unknown>).map(
      ([key, item]) => {
        const next = walk(item);
        if (next !== item) changed = true;
        return [stripEmDashes(key), key, next] as const;
      },
    );
    // A rewrite that collided with another key would silently drop a row, so
    // on a collision BOTH sides keep the key they arrived with. Same rule as
    // `_normalized_items` in backend/app/text.py; the two are one contract.
    const taken = new Map<string, number>();
    for (const [renamed] of entries) {
      taken.set(renamed, (taken.get(renamed) ?? 0) + 1);
    }
    const out: Record<string, unknown> = {};
    for (const [renamed, original, item] of entries) {
      const key = taken.get(renamed) === 1 ? renamed : original;
      if (key !== original) changed = true;
      out[key] = item;
    }
    return changed ? out : value;
  }
  return value;
}
