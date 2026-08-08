import Link from "next/link";
import clsx from "clsx";

/**
 * Landing §6 — pricing ("That's the whole price list.").
 * Design: docs/design/landing/Skeptic Landing.dc.html option 2a lines 271-298
 * (desktop: ONE panel, three columns) / 2b lines 522-541 (mobile: rows with
 * hairline separators — never three tier cards). Honesty lines verbatim per
 * copy-deck §6 incl. the OWNER OVERRIDE (Jul 16): "not enough evidence"
 * REFUNDS the credit. Primary CTA anchors back to the live composer
 * (#composer lives on the hero's composer block).
 *
 * Owner 2026-08-08: the $10 pack isn't built, so its cell wears a COMING
 * SOON strip and drops to panel-deep. Neutral ink only. The trust hue is
 * the verdict family and never advertises an unshipped feature, and `warn`
 * would read as a fault. Drop `comingSoon` when the pack ships.
 */

type PriceRow = {
  value: string;
  /* desktop line 2 (Archivo) */
  main: string;
  /* desktop line 3 (mono) */
  sub: string;
  /* 2b single-line form */
  mobile: string;
  /* not purchasable yet: dims the cell and adds the COMING SOON strip */
  comingSoon?: boolean;
};

const ROWS: PriceRow[] = [
  {
    value: "1",
    main: "free backtest, right now",
    sub: "no account, no card",
    mobile: "free backtest — no account, no card",
  },
  {
    value: "5",
    main: "free with an account",
    sub: "yes account, still no card",
    // 2b's "your first run comes with you" is the claim flow — not built yet
    // (CONTEXT); no promises the product can't keep today
    mobile: "free with an account — still no card",
  },
  {
    value: "$10 → 50",
    main: "one-time, fifty more",
    sub: "no subscription, no tiers",
    mobile: "one-time — no subscription, no tiers",
    comingSoon: true,
  },
];

export function Pricing() {
  return (
    <section
      id="pricing"
      className="border-t border-line-softer px-6 pb-14 pt-[52px] md:px-14 md:py-[84px] xl:px-[120px]"
    >
      {/* one content-width column, centered as a UNIT with its text
          left-aligned inside (owner 2026-07-17) */}
      <div className="mx-auto w-full max-w-[1000px]">
        <div className="font-mono text-[10.5px] font-medium tracking-[.14em] text-trust md:text-[11.5px]">
          PRICING
        </div>
        <h2 className="mt-3 font-serif text-[27px] font-medium leading-[1.2] md:mt-3.5 md:text-[40px] md:leading-[1.15]">
          Yup, this is the whole price list.
        </h2>

        <div className="mt-5 grid w-full overflow-hidden rounded-[14px] border border-line bg-panel md:mt-[38px] md:grid-cols-3">
          {ROWS.map((row, i) => (
            <div
              key={row.value}
              className={clsx(
                i > 0 && "border-t border-line-softer md:border-l md:border-t-0",
                row.comingSoon && "bg-panel-deep",
              )}
            >
              {/* flush strip at the cell's top edge; the panel's own
                  overflow-hidden clips it into the rounded corner. Its label
                  sits left of the content indent so it hugs the column
                  divider and reads as a band, not a fourth content line. */}
              {row.comingSoon && (
                <div className="border-b border-line-softer py-1 pl-3 pr-5 font-mono text-[11.5px] font-medium tracking-[.14em] text-ink-3 md:py-1.5 md:pl-4 md:pr-7 md:text-[13px]">
                  COMING SOON
                </div>
              )}
              {/* the strip's height comes OUT of this block's padding so the
                  cell's natural height still matches its siblings. Desktop
                  cells are grid siblings, so any growth here stretches all
                  three (owner 2026-08-08). */}
              <div
                className={clsx(
                  "flex items-baseline gap-3.5 px-5 md:block md:px-7",
                  row.comingSoon ? "py-[13px] md:py-[9px]" : "py-[18px] md:py-[26px]",
                )}
              >
                <span
                  className={clsx(
                    "min-w-[86px] shrink-0 whitespace-nowrap font-mono text-[24px] font-semibold md:block md:min-w-0 md:text-[30px]",
                    row.comingSoon && "text-ink-3",
                  )}
                >
                  {row.value}
                </span>
                <span
                  className={clsx(
                    "text-[13.5px] md:mt-1.5 md:block md:text-[14.5px]",
                    row.comingSoon ? "text-ink-4" : "text-ink-2",
                  )}
                >
                  <span className="md:hidden">{row.mobile}</span>
                  <span className="hidden md:inline">{row.main}</span>
                </span>
                <span
                  className={clsx(
                    "hidden font-mono text-[11.5px] leading-[1.6] md:mt-2.5 md:block",
                    row.comingSoon ? "text-ink-5" : "text-ink-4",
                  )}
                >
                  {row.sub}
                </span>
              </div>
            </div>
          ))}
        </div>

        {/* honesty lines — VERBATIM (owner override Jul 16); never reword */}
        <div className="mt-3.5 flex flex-col gap-[5px] md:mt-[18px]">
          <div className="font-mono text-[12px] leading-[1.6] text-ink-2">
            “Not enough evidence” refunds the credit — you only spend on a
            graded verdict.
          </div>
          <div className="font-mono text-[11px] leading-[1.6] text-ink-4">
            same if a run fails on our end — the credit comes back on its own
          </div>
        </div>

        <div className="mt-5 flex flex-col gap-3 md:mt-7 md:flex-row md:items-center">
          <a
            href="#composer"
            className="flex h-[46px] items-center justify-center rounded-[10px] bg-trust px-5 text-[13.5px] font-semibold text-on-accent transition-opacity hover:opacity-90 md:inline-flex md:h-[42px] md:text-sm md:font-bold"
          >
            Run your free backtest
          </a>
          <Link
            href="/signup"
            className="flex h-[46px] items-center justify-center rounded-[10px] border border-line bg-raised-2 px-[18px] text-[13px] font-semibold text-ink-2 transition-colors hover:border-trust-border hover:bg-raised-3 hover:text-ink md:inline-flex md:h-[42px]"
          >
            Create an account — 5 free
          </Link>
        </div>
      </div>
    </section>
  );
}
