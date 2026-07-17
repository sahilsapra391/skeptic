"use client";

import Link from "next/link";
import clsx from "clsx";
import { useEffect, useState } from "react";

import { getCoverage } from "@/lib/api";
import type { Theme } from "@/lib/settings";
import type { LandingTheme } from "@/components/landing/use-landing-theme";

/**
 * Landing footer — navbg band + theme segmented control.
 * Design: docs/design/landing/Skeptic Landing.dc.html option 2a lines 300-335
 * (desktop: brand / PRODUCT / LEGAL grid + © row) / 2b lines 542-556 (mobile:
 * brand + flat link row + control + © line). Theme control writes ONLY the
 * landing preference (theme.set → sk-landing-theme, component-specs §8);
 * brand-mark swap uses theme.resolved. Day counter renders ONLY when
 * coverage.record_days is real — never a fallback number (reader D).
 * Standing disclaimer intentionally absent per owner Jul 16.
 */

const THEME_MODES: { id: Theme; label: string }[] = [
  { id: "market", label: "Market Hours" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

/* DS base.css: a { color: var(--ink-3) } → hover var(--ink) */
const LINK = "text-ink-3 transition-colors hover:text-ink";

export function LandingFooter({ theme }: { theme: LandingTheme }) {
  const [recordDays, setRecordDays] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    getCoverage()
      .then((c) => {
        if (alive && Number.isFinite(c.record_days) && c.record_days > 0) {
          setRecordDays(c.record_days);
        }
      })
      .catch(() => {
        /* counter line omitted — no invented numbers */
      });
    return () => {
      alive = false;
    };
  }, []);

  const wordmark =
    theme.resolved === "light"
      ? "/brand/wordmark-black.svg"
      : "/brand/wordmark-white.svg";

  return (
    <footer className="border-t border-line bg-navbg px-6 pb-[26px] pt-8 md:px-14 md:pb-[30px] md:pt-10 xl:px-[120px]">
      <div className="mx-auto max-w-[1440px]">
        <div className="md:grid md:grid-cols-[1.5fr_1fr_1fr] md:gap-12">
          <div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={wordmark}
              alt="Skeptic"
              className="block h-3 w-auto md:h-[13px]"
              draggable={false}
            />
            <p className="mt-3 text-[12.5px] leading-[1.6] text-ink-3 md:mt-3.5 md:max-w-[280px] md:text-[13px]">
              The backtester that argues with you.
              <span className="hidden md:inline">
                {" "}
                Pitch a trade; get the honest read — refusals included.
              </span>
            </p>
            {recordDays !== null && (
              <Link
                href="/data"
                className="mt-3.5 hidden font-mono text-[11px] text-ink-4 transition-colors hover:text-ink-2 md:block"
              >
                day {String(recordDays).padStart(3, "0")} of the nightly record →
              </Link>
            )}
          </div>

          <div className="hidden md:block">
            <div className="mb-3 font-mono text-[10.5px] tracking-[.12em] text-ink-4">
              PRODUCT
            </div>
            <div className="flex flex-col items-start gap-[9px] text-[13px]">
              <a className={LINK} href="#how-it-argues">
                How it argues
              </a>
              <a className={LINK} href="#verdict">
                The verdict block
              </a>
              <a className={LINK} href="#receipts">
                Data coverage
              </a>
              <a className={LINK} href="#pricing">
                Pricing
              </a>
            </div>
          </div>

          <div className="hidden md:block">
            <div className="mb-3 font-mono text-[10.5px] tracking-[.12em] text-ink-4">
              LEGAL
            </div>
            <div className="flex flex-col items-start gap-[9px] text-[13px]">
              <Link className={LINK} href="/terms">
                Terms of Service
              </Link>
              <Link className={LINK} href="/privacy">
                Privacy policy
              </Link>
              <Link className={LINK} href="/refunds">
                Refund policy
              </Link>
            </div>
          </div>
        </div>

        {/* 2b: one flat link row replaces the two desktop columns */}
        <div className="mt-4 flex flex-wrap gap-3.5 text-[12.5px] md:hidden">
          <a className={LINK} href="#how-it-argues">
            How it argues
          </a>
          <a className={LINK} href="#pricing">
            Pricing
          </a>
          <Link className={LINK} href="/terms">
            Terms
          </Link>
          <Link className={LINK} href="/privacy">
            Privacy
          </Link>
          <Link className={LINK} href="/refunds">
            Refunds
          </Link>
        </div>

        <div className="mt-4 flex flex-col items-start gap-3.5 md:mt-[26px] md:flex-row md:items-center md:justify-between">
          <span className="order-2 font-mono text-[10px] text-ink-4 md:order-1 md:text-[10.5px]">
            © 2026 Skeptic ·{" "}
            <span className="md:hidden">
              every number computed, none decorative
            </span>
            <span className="hidden md:inline">
              skeptic.fyi · every number on this page is computed — none
              decorative
            </span>
          </span>
          <div
            role="group"
            aria-label="Theme"
            className="order-1 inline-flex gap-[2px] rounded-[11px] border border-line bg-panel p-[3px] md:order-2"
          >
            {THEME_MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                aria-pressed={theme.pref === m.id}
                onClick={() => theme.set(m.id)}
                className={clsx(
                  "rounded-lg px-[13px] py-[7px] text-[12px] font-medium transition-colors md:px-3.5 md:py-1.5 md:text-[12.5px]",
                  theme.pref === m.id
                    ? "bg-raised-2 text-ink"
                    : "text-ink-3 hover:text-ink-2",
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
}
