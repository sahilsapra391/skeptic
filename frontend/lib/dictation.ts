/**
 * Dictation polish for the strategy composer.
 *
 * The browser's speech engine transcribes for prose — capitalized
 * sentence starts, numbers often spelled out, tickers heard as ordinary
 * words. Strategy dictation wants the opposite: lowercase flow, digits
 * everywhere ("thirty delta" → "30 delta", "fifty percent" → "50%"),
 * and the domain vocabulary (SPY, QQQ, IWM, DTE, RSI…) in canonical
 * shape. Every transcript chunk — interim and final — passes through
 * polishDictation before it reaches the composer.
 */

// ---- domain phrase repairs -------------------------------------------------
// Applied first, on the lowercased text. Ordered: letter-by-letter
// spellings must merge before single-word canonicalization runs.
const PHRASE_FIXES: [RegExp, string][] = [
  // tickers spelled out or misheard
  [/\bs[. ]?p[. ]?y\b/g, "spy"],
  [/\bq[. ]?q[. ]?q\b/g, "qqq"],
  [/\bi[. ]?w[. ]?m\b/g, "iwm"],
  [/\b(?:the )?(?:q's|qs|cues|triple q(?:ueue)?)\b/g, "qqq"],
  [/\bspyders?\b/g, "spy"],
  [/\bspdr\b/g, "spy"],
  // acronyms spelled out
  [/\bd[. ]?t[. ]?e\b/g, "dte"],
  [/\br[. ]?s[. ]?i\b/g, "rsi"],
  [/\bs[. ]?m[. ]?a\b/g, "sma"],
  [/\be[. ]?m[. ]?a\b/g, "ema"],
  // options vocabulary the engine mangles
  [/\biron condos?\b/g, "iron condor"],
  [/\bdealt a\b/g, "delta"],
  [/\bputts?\b/g, "put"],
  [/\broles?\b/g, "roll"],
  [/\b(?:cellar|seller)\b/g, "sell a"],
  [/\bby (a|an|the|one|two) (put|call|spread|condor|straddle|strangle)\b/g, "buy $1 $2"],
  [/\bdays? to expirations?\b/g, "days to expiration"],
  [/\bstop[- ]?losses\b/g, "stop losses"],
  [/\bstoploss\b/g, "stop loss"],
];

// words always rendered uppercase — tickers and indicator acronyms
const FORCE_UPPER = new Set([
  "spy", "qqq", "iwm", "spx", "dte", "rsi", "sma", "ema", "macd",
  "vwap", "vix", "etf", "atm", "otm", "itm",
]);

// ---- spoken numbers → digits ----------------------------------------------
const UNITS: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13,
  fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
  nineteen: 19,
};
const TENS: Record<string, number> = {
  twenty: 20, thirty: 30, forty: 40, fifty: 50,
  sixty: 60, seventy: 70, eighty: 80, ninety: 90,
};
const SCALES: Record<string, number> = { hundred: 100, thousand: 1000 };
// single digits after "point" — "point oh five" → .05
const DECIMAL_DIGITS: Record<string, string> = {
  zero: "0", oh: "0", o: "0", one: "1", two: "2", three: "3", four: "4",
  five: "5", six: "6", seven: "7", eight: "8", nine: "9",
};

const isNumberWord = (w: string): boolean =>
  w in UNITS || w in TENS || w in SCALES;

/** Collapse one run of spoken-number tokens starting at `i`.
 * Returns the digits string and the index just past the run. */
function parseNumberRun(tokens: string[], i: number): { text: string; next: number } {
  let total = 0;
  let current = 0;
  let sawInt = false;
  let j = i;

  while (j < tokens.length) {
    const w = tokens[j];
    if (w in UNITS) {
      current += UNITS[w];
      sawInt = true;
      j++;
    } else if (w in TENS) {
      current += TENS[w];
      sawInt = true;
      j++;
    } else if (w in SCALES) {
      if (SCALES[w] === 100) {
        current = (current || 1) * 100;
      } else {
        total += (current || 1) * 1000;
        current = 0;
      }
      sawInt = true;
      j++;
    } else if (w === "a" && j + 1 < tokens.length && tokens[j + 1] in SCALES) {
      j++; // "a hundred" — the scale branch supplies the 1
    } else if (
      w === "and" &&
      sawInt &&
      j + 1 < tokens.length &&
      isNumberWord(tokens[j + 1]) &&
      !(tokens[j + 1] in SCALES)
    ) {
      j++; // "one hundred and five"
    } else {
      break;
    }
  }

  let text = sawInt ? String(total + current) : "";

  // decimals: "point three" → .3 · "one point five" → 1.5 · "point oh five" → .05
  if (j < tokens.length && tokens[j] === "point" && tokens[j + 1] in DECIMAL_DIGITS) {
    let frac = "";
    let k = j + 1;
    while (k < tokens.length && tokens[k] in DECIMAL_DIGITS) {
      frac += DECIMAL_DIGITS[tokens[k]];
      k++;
    }
    text = `${sawInt ? text : "0"}.${frac}`;
    j = k;
  }

  return { text, next: j };
}

function convertNumberWords(s: string): string {
  // hyphenated compounds split first: "twenty-one" → "twenty one"
  const tokens = s
    .replace(/\b([a-z]+)-([a-z]+)\b/g, (m, a: string, b: string) =>
      a in TENS && b in UNITS ? `${a} ${b}` : m,
    )
    .split(" ");

  const out: string[] = [];
  let i = 0;
  while (i < tokens.length) {
    const w = tokens[i];
    const startsRun =
      isNumberWord(w) ||
      (w === "point" && tokens[i + 1] in DECIMAL_DIGITS) ||
      (w === "a" && tokens[i + 1] in SCALES);
    if (startsRun) {
      const run = parseNumberRun(tokens, i);
      if (run.text) {
        out.push(run.text);
        i = run.next;
        continue;
      }
    }
    out.push(w);
    i++;
  }
  return out.join(" ");
}

// ---- entry point ------------------------------------------------------------
export function polishDictation(raw: string): string {
  // lowercase kills the engine's mid-dictation sentence capitalization;
  // the canonical-caps pass below restores tickers and acronyms
  let s = raw.toLowerCase().replace(/\s+/g, " ").trim();
  if (!s) return "";

  for (const [re, to] of PHRASE_FIXES) s = s.replace(re, to);

  s = convertNumberWords(s);

  // digits already spoken as digits get the same unit treatment
  s = s.replace(/(\d+(?:\.\d+)?) ?(?:percent|per cent|percentage)\b/g, "$1%");
  s = s.replace(/(\d+(?:\.\d+)?) dollars?\b/g, "$$$1");

  s = s.replace(/[a-z]+/g, (w) => (FORCE_UPPER.has(w) ? w.toUpperCase() : w));

  return s.replace(/\s+/g, " ").trim();
}
