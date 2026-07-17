/**
 * The interview card's sample clarifying questions — a different one each
 * visit, so the landing shows the range of what the parser actually asks.
 * Seeded with real questions harvested from past runs, expanded across the
 * parser's genuine clarification domains (exits, strikes, entry signals,
 * cadence, wing width, stops, sizing/ruin caps, DTE, rolls, resolution).
 * These mirror the product's real behavior — the parser never guesses an
 * ambiguous field, it asks (engine guardrail #3).
 */

export type InterviewQuestion = { q: string; options: string[] };

export const INTERVIEW_QUESTIONS: InterviewQuestion[] = [
  // ── real, harvested from stored run provenance ──
  {
    q: "What is the maximum total number of contracts you're willing to hold across all rungs?",
    options: ["20", "30", "50", "custom"],
  },
  {
    q: "How should we select the strike price for the call?",
    options: ["at-the-money (50Δ)", "25Δ out-of-the-money", "5% OTM"],
  },
  {
    q: "What is the maximum total number of contracts to allow, as a ruin cap?",
    options: ["20", "30", "50", "custom"],
  },
  {
    q: "What width for the wings — the dollar distance from each short strike to its long protection?",
    options: ["$5", "$10", "$15", "$20"],
  },
  {
    q: "How often should we enter the iron condor?",
    options: ["daily", "weekly", "monthly", "signal only"],
  },
  {
    q: "What entry conditions should trigger the trade?",
    options: ["RSI(14) < 30", "IV rank > 50", "price below 50-day SMA", "IV z-score > 1.5"],
  },
  {
    q: "What is the maximum total number of contracts you would allow?",
    options: ["20", "50", "100", "custom"],
  },
  {
    q: "Should this be a single ladder per day, or multiple independent ladders that re-enter after each reversal?",
    options: ["single ladder per day", "multiple ladders"],
  },
  // ── the parser's real clarification domains ──
  {
    q: "Two exits could apply at 21 days — take whichever hits first, or profit target only?",
    options: ["whichever hits first", "profit target only", "time exit only"],
  },
  {
    q: "At what profit should we close the position?",
    options: ["50% of max profit", "25%", "75%", "100%"],
  },
  {
    q: "Should there be a stop loss, and at what level?",
    options: ["2× credit received", "−50% of premium", "no stop"],
  },
  {
    q: "How many days to expiration at entry?",
    options: ["0DTE", "7 DTE", "30 DTE", "45 DTE"],
  },
  {
    q: "When should we roll the position?",
    options: ["at 21 DTE", "at 50% profit", "on a test of the short strike", "don't roll"],
  },
  {
    q: "How should we handle assignment on the short put?",
    options: ["take assignment", "roll to avoid", "close before expiry"],
  },
  {
    q: "Which leg do you want to trade?",
    options: ["the call", "the put", "both (a spread)"],
  },
  {
    q: "What delta should the short strike target?",
    options: ["16Δ", "30Δ", "45Δ"],
  },
  {
    q: "What resolution should the backtest run at?",
    options: ["daily (EOD)", "5-minute intraday"],
  },
  {
    q: "How much capital should the account start with?",
    options: ["$10,000", "$25,000", "$100,000"],
  },
  {
    q: "How should position size be determined?",
    options: ["fixed contracts", "% of capital", "fixed risk per trade"],
  },
  {
    q: "Should entries skip when the bid/ask spread is too wide to fill honestly?",
    options: ["skip wide spreads", "fill anyway"],
  },
  {
    q: "What time of day should the entry fire?",
    options: ["at the open", "midday", "near the close", "any time"],
  },
  {
    q: "How far out should the long protection sit?",
    options: ["$5 wide", "$10 wide", "10% OTM"],
  },
  {
    q: "What should trigger an early exit before the time limit?",
    options: ["profit target hit", "stop hit", "either"],
  },
  {
    q: "On a signal strategy, how long does an entry signal stay valid?",
    options: ["same day only", "until it reverses", "until filled"],
  },
  {
    q: "Should we re-enter after a stop-out on the same day?",
    options: ["re-enter once", "re-enter freely", "no re-entry"],
  },
  {
    q: "Which underlying should we run this on?",
    options: ["SPY", "QQQ", "IWM"],
  },
  {
    q: "How should the time exit count days?",
    options: ["calendar days", "trading days"],
  },
  {
    q: "At what drawdown from the recent high should we enter?",
    options: ["3%", "5%", "10%"],
  },
  {
    q: "What lookback for the RSI signal?",
    options: ["RSI(2)", "RSI(14)", "RSI(20)"],
  },
  {
    q: "Profit target as a multiple of the credit received?",
    options: ["1.5×", "2×", "3×"],
  },
  {
    q: "Should the strategy hold only one position at a time?",
    options: ["one at a time", "allow overlap"],
  },
  {
    q: "What implied-volatility condition should gate entries?",
    options: ["IV rank > 30", "IV rank > 50", "no IV filter"],
  },
  {
    q: "How should we size the scale-in as the position deepens?",
    options: ["equal adds", "double each rung", "+1 contract per rung"],
  },
  {
    q: "What defines the reversal that stops the ladder?",
    options: ["RSI back above 30", "price back above VWAP", "a green close"],
  },
  {
    q: "On an oversold signal, how many contracts on the first entry?",
    options: ["1", "2", "3"],
  },
  {
    q: "Should the covered call be rolled up on a big move?",
    options: ["roll up and out", "let it get called", "buy to close"],
  },
  {
    q: "What expiration cycle for the monthly?",
    options: ["standard monthly", "nearest weekly", "45 DTE nearest"],
  },
  {
    q: "How should we handle a gap through the short strike overnight?",
    options: ["exit at the open", "hold to target", "manage at the long"],
  },
  {
    q: "What's the maximum loss you'll accept per trade?",
    options: ["1% of capital", "2%", "5%"],
  },
  {
    q: "Should dividends near expiry change the exit?",
    options: ["close before ex-div", "ignore dividends"],
  },
  {
    q: "What confirmation is needed before the fade entry?",
    options: ["one red bar", "two red bars", "no confirmation"],
  },
  {
    q: "How should partial fills be treated?",
    options: ["fill what's available", "all-or-none"],
  },
  {
    q: "Scale out of winners, or exit the whole position at once?",
    options: ["exit all at once", "scale out in halves"],
  },
  {
    q: "What commission and slippage should we assume?",
    options: ["default ($0.65/ct)", "zero", "custom"],
  },
  {
    q: "For the credit spread, how do we choose the protection width?",
    options: ["$5 wide", "$10 wide", "width by delta"],
  },
  {
    q: "Should the entry require the trend to align?",
    options: ["only in an uptrend", "only in a downtrend", "any trend"],
  },
  {
    q: "Minimum number of trades before you'd trust a verdict?",
    options: ["15 (standard)", "30", "custom"],
  },
  {
    q: "Pick the short strike by fixed delta or a fixed offset each cycle?",
    options: ["fixed delta", "fixed % OTM", "fixed strike"],
  },
  {
    q: "Should we flatten before an FOMC or earnings event?",
    options: ["flatten before events", "hold through"],
  },
  {
    q: "How should we treat a day with no valid options chain?",
    options: ["skip the day", "carry the prior position"],
  },
];

const KEY = "sk-interview-q";

/** This visit's question WITHOUT advancing — safe to seed a useState. */
export function peekInterviewQuestion(): InterviewQuestion {
  if (typeof window === "undefined") return INTERVIEW_QUESTIONS[8]; // the canonical exit one
  try {
    const i = Number(localStorage.getItem(KEY) ?? "0") % INTERVIEW_QUESTIONS.length;
    return INTERVIEW_QUESTIONS[i];
  } catch {
    return INTERVIEW_QUESTIONS[8];
  }
}

/** Advance so the next visit shows a different question. */
export function bumpInterviewQuestion(): void {
  if (typeof window === "undefined") return;
  try {
    const i = Number(localStorage.getItem(KEY) ?? "0") % INTERVIEW_QUESTIONS.length;
    localStorage.setItem(KEY, String((i + 1) % INTERVIEW_QUESTIONS.length));
  } catch {
    /* private mode */
  }
}
