"use client";

/**
 * Launch L5: the owner admin portal. Award (or claw back) credits and read
 * launch telemetry. Access is decided server-side (SKEPTIC_ADMIN_EMAILS); this
 * page mirrors it client-side — a non-admin is bounced to /new, and every
 * endpoint 404s for them regardless. Numbers are data → IBM Plex Mono.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiError,
  adminGrantCredits,
  adminMetrics,
  fetchMe,
  type AdminMetrics,
} from "@/lib/api";
import { notifyCreditsChanged } from "@/lib/credits-events";

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-[12px] border border-line-soft bg-panel px-4 py-3">
      <div className="font-mono text-[10.5px] uppercase tracking-[.1em] text-ink-4">{label}</div>
      <div className="mt-1 font-mono text-[19px] font-medium text-ink">{value}</div>
    </div>
  );
}

export default function AdminPage() {
  const router = useRouter();
  // null = access check in flight; false = not an admin (redirecting)
  const [ok, setOk] = useState<boolean | null>(null);
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);

  const [email, setEmail] = useState("");
  const [credits, setCredits] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadMetrics = useCallback(() => {
    adminMetrics()
      .then(setMetrics)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    fetchMe()
      .then((me) => {
        if (!me.admin) {
          setOk(false);
          router.replace("/new");
          return;
        }
        setOk(true);
        loadMetrics();
      })
      .catch(() => {
        setOk(false);
        router.replace("/signin?next=/admin");
      });
  }, [router, loadMetrics]);

  const grant = async (e: React.FormEvent) => {
    e.preventDefault();
    const n = Number(credits);
    if (!email.trim() || !Number.isInteger(n) || n === 0) {
      setError("enter an email and a non-zero whole number of credits");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await adminGrantCredits(email.trim(), n);
      setResult(`${r.email}: ${r.before} → ${r.after} (${r.delta >= 0 ? "+" : ""}${r.delta})`);
      setCredits("");
      notifyCreditsChanged(); // if you granted yourself, the nav balance refreshes
      loadMetrics();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "grant failed");
    } finally {
      setBusy(false);
    }
  };

  if (ok !== true) return null; // access check in flight or bouncing a non-admin

  return (
    <div className="mx-auto max-w-[880px] px-6 py-10">
      <h1 className="font-serif text-[28px] font-medium leading-tight">Admin</h1>
      <p className="mt-1 text-[13.5px] text-ink-3">
        Owner-only. Award credits and watch the launch numbers.
      </p>

      {/* award credits */}
      <section className="mt-8 rounded-[14px] border border-line-soft bg-panel p-5">
        <h2 className="text-[14px] font-semibold text-ink">Award credits</h2>
        <p className="mt-1 text-[12.5px] text-ink-3">
          Adds an audited ledger row. Use a negative amount to claw back.
        </p>
        <form onSubmit={grant} className="mt-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10.5px] uppercase tracking-[.1em] text-ink-4">
              Account email
            </span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-[280px] rounded-[10px] border border-line-hover bg-panel-deep px-3 py-2 text-[14px] text-ink focus:border-trust focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10.5px] uppercase tracking-[.1em] text-ink-4">
              Credits
            </span>
            <input
              type="number"
              value={credits}
              onChange={(e) => setCredits(e.target.value)}
              placeholder="3000"
              className="w-[130px] rounded-[10px] border border-line-hover bg-panel-deep px-3 py-2 font-mono text-[14px] text-ink focus:border-trust focus:outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="rounded-[10px] bg-trust px-5 py-2 text-[13.5px] font-bold text-on-accent disabled:opacity-60"
          >
            {busy ? "Awarding…" : "Award"}
          </button>
        </form>
        {result && (
          <p className="mt-3 font-mono text-[12.5px] text-trust">{result}</p>
        )}
        {error && <p className="mt-3 font-mono text-[12.5px] text-warn">{error}</p>}
      </section>

      {/* metrics */}
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-[14px] font-semibold text-ink">Launch metrics</h2>
        <button
          onClick={loadMetrics}
          className="font-mono text-[11.5px] text-ink-4 hover:text-ink-2"
        >
          refresh ↻
        </button>
      </div>
      {metrics && (
        <div className="mt-4 space-y-6">
          <div>
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[.12em] text-ink-4">
              Accounts
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Stat label="Total" value={metrics.accounts.total} />
              <Stat label="Verified" value={metrics.accounts.verified} />
              <Stat label="Signups · 7d" value={metrics.accounts.signups_7d} />
            </div>
          </div>
          <div>
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[.12em] text-ink-4">
              Runs
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Total" value={metrics.runs.total} />
              <Stat label="Signed-in" value={metrics.runs.signed_in} />
              <Stat label="Last 7d" value={metrics.runs.last_7d} />
              <Stat label="Done" value={metrics.runs.by_status.done ?? 0} />
            </div>
          </div>
          <div>
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[.12em] text-ink-4">
              Revenue &amp; credits
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Net USD" value={`$${metrics.revenue.net_usd.toLocaleString()}`} />
              <Stat label="Gross USD" value={`$${metrics.revenue.gross_usd.toLocaleString()}`} />
              <Stat label="Purchases" value={metrics.revenue.purchases} />
              <Stat label="Chargebacks" value={metrics.revenue.chargebacks} />
              <Stat label="Outstanding" value={metrics.credits.outstanding} />
              <Stat label="Spent" value={metrics.credits.spent} />
              <Stat label="Refunded" value={metrics.credits.refunded} />
              <Stat label="Admin-granted" value={metrics.credits.admin_adjusted} />
              <Stat label="Signup-granted" value={metrics.credits.signup_granted} />
            </div>
          </div>
          <div>
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[.12em] text-ink-4">
              Anonymous trials
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Stat label="Total" value={metrics.anon_trials.total} />
              <Stat label="Today" value={metrics.anon_trials.today} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
