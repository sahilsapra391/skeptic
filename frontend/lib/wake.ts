/**
 * Getting a request through to a backend that was asleep.
 *
 * The Railway backend runs with Serverless on (backend/railway.json): a few
 * idle minutes after its last outbound traffic, Railway stops the container,
 * and the next request boots a fresh one. While it boots, Railway's proxy holds
 * the request for about ten seconds and then answers 502 with its own HTML
 * error page. That request never reached the app, so sending it again is safe,
 * and the person should see a slower first answer rather than an error.
 *
 * Everything else passes straight through, and the rules below are strict on
 * purpose, because re-sending a POST that DID land would double a backtest:
 *  - a 502 the app sent itself is JSON (its only one is the Stripe route), so
 *    only a non-JSON 502 reads as the platform still booting;
 *  - a 502 that took far longer than the boot hold looks like a container that
 *    died mid-request, where the request may have landed, so it is not re-sent;
 *  - a timeout means the engine is up and still working, never a wake;
 *  - a dropped connection could come after a POST arrived, so only a GET or a
 *    HEAD is re-sent after one.
 *
 * Dependency-free on purpose: backend/tests/test_proxy_wake.py executes this
 * exact file under node, the same way the normalizer parity guard does.
 */

/** Total time spent re-sending before a failure is reported as it is. */
export const WAKE_BUDGET_MS = 45_000;

/** Pause between attempts while the container boots. */
export const WAKE_RETRY_DELAY_MS = 2_000;

/** Railway holds a request about ten seconds before its boot 502; anything much
 * slower than this is not a boot. */
export const WAKE_502_MAX_MS = 20_000;

export type Attempt =
  | { kind: "response"; status: number; contentType: string | null; elapsedMs: number }
  | { kind: "network-error"; timedOut: boolean; elapsedMs: number };

/** True when this attempt is Railway still booting the container, so the
 * request never reached the app and may be sent again. */
export function isWakeFailure(attempt: Attempt, method: string): boolean {
  const idempotent = method === "GET" || method === "HEAD";
  if (attempt.kind === "network-error") return !attempt.timedOut && idempotent;
  if (attempt.status !== 502) return false;
  if ((attempt.contentType ?? "").includes("application/json")) return false;
  return attempt.elapsedMs <= WAKE_502_MAX_MS;
}

export type WakeOptions = {
  budgetMs?: number;
  delayMs?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

export type WakeResult = {
  response: Response;
  /** true when the final answer is itself a boot failure, so the caller can
   * say the engine is waking instead of relaying Railway's HTML page */
  stillWaking: boolean;
};

/**
 * Send once, and again while the failure is a backend still waking, until the
 * budget runs out. Returns the last response, or rethrows the last error.
 */
export async function sendWaking(
  send: () => Promise<Response>,
  method: string,
  options: WakeOptions = {},
): Promise<WakeResult> {
  const budgetMs = options.budgetMs ?? WAKE_BUDGET_MS;
  const delayMs = options.delayMs ?? WAKE_RETRY_DELAY_MS;
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? defaultSleep;
  const deadline = now() + budgetMs;
  for (;;) {
    const started = now();
    let response: Response;
    try {
      response = await send();
    } catch (err) {
      const timedOut =
        err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
      const attempt: Attempt = { kind: "network-error", timedOut, elapsedMs: now() - started };
      if (!isWakeFailure(attempt, method) || now() + delayMs > deadline) throw err;
      await sleep(delayMs);
      continue;
    }
    const attempt: Attempt = {
      kind: "response",
      status: response.status,
      contentType: response.headers.get("content-type"),
      elapsedMs: now() - started,
    };
    if (!isWakeFailure(attempt, method)) return { response, stillWaking: false };
    if (now() + delayMs > deadline) return { response, stillWaking: true };
    await response.body?.cancel();
    await sleep(delayMs);
  }
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
