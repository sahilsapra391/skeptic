/** Standing disclaimer — required on every results surface (CLAUDE.md rails). */
export function Disclaimer({ short = false }: { short?: boolean }) {
  return (
    <div className="mt-3 text-center text-[11px] text-ink-4">
      {short
        ? "Research tool, not financial advice."
        : "Research tool, not financial advice. Backtests run on approximate, self-collected data."}
    </div>
  );
}

/** Chip marking demo-fixture content — removed the day M2–M4 land. */
export function DemoBadge({ text = "demo data — engine lands at M2" }: { text?: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-warn/50 px-2.5 py-1 font-mono text-[10px] tracking-[.08em] text-warn">
      {text}
    </span>
  );
}
