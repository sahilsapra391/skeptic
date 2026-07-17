"use client";

/**
 * The landing assembly (launch L4, design 2a/2b). One client boundary for
 * the whole page: theme + wordmark-draw state live HERE, once, and flow
 * down as props — sections never call the hooks themselves (double mounts
 * would double the intervals and fight over <html data-theme>).
 *
 * Topbar and the morphing wordmark are top-level siblings of the sections:
 * position:sticky sticks within the nearest scrollport but is CONTAINED by
 * the parent box — caged inside the hero section they'd scroll away with it.
 */

import { useState } from "react";

import { useWordmarkDraw } from "@/components/landing/landing-wordmark";
import { useLandingTheme } from "@/components/landing/use-landing-theme";
import { fetchMe } from "@/lib/api";
import { myRunIds } from "@/lib/my-runs";

import { CopilotDemo } from "@/components/landing/copilot-demo";
import { LandingHero, LandingTopbar } from "@/components/landing/hero";
import { HowItArgues } from "@/components/landing/how-it-argues";
import { LandingFooter } from "@/components/landing/landing-footer";
import { Pricing } from "@/components/landing/pricing";
import { Receipts } from "@/components/landing/receipts";
import {
  DeviceGateModal,
  RunFlowModal,
  RunViewModal,
} from "@/components/landing/run-modals";
import { VerdictShowcase } from "@/components/landing/verdict-showcase";

export function LandingPage() {
  const theme = useLandingTheme();
  const draw = useWordmarkDraw();

  // the product opens in popups ON the landing (owner 2026-07-17) — a
  // visitor is never redirected into the app shell
  const [runReq, setRunReq] = useState<{ pitch?: string; mode?: "chart" } | null>(null);
  const [viewRunId, setViewRunId] = useState<string | null>(null);
  const [gated, setGated] = useState(false);

  // one free run per DEVICE for anonymous visitors — a second attempt is
  // asked to create an account instead (client-remembered; the signed
  // anon token + Turnstile server armor lands with the anon chunk). A
  // signed-in account holder is NEVER device-gated: they have credits and
  // the landing popup is their run surface (review finding — the gate
  // told account holders to make an account they already had).
  const tryRun = (req: { pitch?: string; mode?: "chart" }) => {
    if (myRunIds().length === 0) {
      setRunReq(req);
      return;
    }
    fetchMe()
      .then(() => setRunReq(req)) // signed in — run it
      .catch(() => setGated(true)); // anonymous repeat — gate to signup
  };

  return (
    <div className="min-h-screen bg-ground">
      <LandingTopbar theme={theme} draw={draw} />
      <LandingHero
        theme={theme}
        draw={draw}
        onPitch={(pitch) => tryRun({ pitch })}
        onChartTeach={() => tryRun({ mode: "chart" })}
      />
      <main>
        <HowItArgues />
        <VerdictShowcase onOpenRun={setViewRunId} />
        <Receipts />
        <CopilotDemo />
        <Pricing />
      </main>
      <LandingFooter theme={theme} />

      {runReq && <RunFlowModal pitch={runReq.pitch} mode={runReq.mode} onClose={() => setRunReq(null)} />}
      {viewRunId && <RunViewModal runId={viewRunId} onClose={() => setViewRunId(null)} />}
      {gated && <DeviceGateModal onClose={() => setGated(false)} />}
    </div>
  );
}
