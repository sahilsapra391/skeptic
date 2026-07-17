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

import { useCallback, useState } from "react";

import { useWordmarkDraw } from "@/components/landing/landing-wordmark";
import { useLandingTheme } from "@/components/landing/use-landing-theme";
import { clearActiveRun, getActiveRun, setActiveRun } from "@/lib/active-run";
import { fetchMe } from "@/lib/api";
import { myRunIds } from "@/lib/my-runs";

import { ActiveRunBanner } from "@/components/landing/active-run-banner";
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
  // visitor is never redirected into the app shell.
  // runFlow = the active run flow's input (kept MOUNTED from prompt
  // submission until the visitor dismisses it, so the run keeps its live
  // progress while minimized); runOpen = whether that popup is visible.
  const [runFlow, setRunFlow] = useState<{ pitch?: string; mode?: "chart" } | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [viewRunId, setViewRunId] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  // the background run this browser is tracking (survives popup close +
  // reloads) — the banner watches it to "ready" and back to viewing
  const [activeRunId, setActiveRunId] = useState<string | null>(() => getActiveRun());

  // the run flow reports its id the instant the backtest is created; a real
  // run becomes the tracked background run (demo-fallback runs don't persist
  // and are skipped — they'd 404 and wrongly burn the free run)
  const onRunStarted = useCallback((runId: string, demo: boolean) => {
    if (demo) return;
    setActiveRun(runId);
    setActiveRunId(runId);
  }, []);

  // clear the run flow entirely — dismissed, or a phantom that self-healed
  const clearRun = useCallback(() => {
    setRunFlow(null);
    setRunOpen(false);
    setActiveRunId(null);
    clearActiveRun();
  }, []);

  // one free run per DEVICE for anonymous visitors. Once a run is in flight
  // this session (runFlow mounted) a second attempt does NOT start another —
  // it brings the visitor back to the running popup (owner 2026-07-17). With
  // no active flow, the device gate applies (a signed-in account holder is
  // never gated — they have credits and the popup is their run surface).
  const tryRun = (req: { pitch?: string; mode?: "chart" }) => {
    if (runFlow) {
      setRunOpen(true); // a run is already going — take them to it
      return;
    }
    if (myRunIds().length === 0) {
      setRunFlow(req);
      setRunOpen(true);
      return;
    }
    fetchMe()
      .then(() => {
        setRunFlow(req); // signed in — run it
        setRunOpen(true);
      })
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

      {/* background-run banner: shown whenever a run is in flight/tracked
          but its popup is minimized (or after a reload, when only the
          persisted id survives). Clicking reopens the live popup if it's
          still mounted, else opens a read-only view. */}
      {(runFlow || activeRunId) && !runOpen && !(activeRunId && viewRunId === activeRunId) && (
        <ActiveRunBanner
          runId={activeRunId}
          hasFlow={!!runFlow}
          onReopen={() => setRunOpen(true)}
          onView={(id) => setViewRunId(id)}
          onDismiss={clearRun}
          onPhantom={clearRun}
        />
      )}

      {/* the run flow stays MOUNTED while runFlow is set; the popup X only
          minimizes it (runOpen=false) so the run keeps going and the banner
          takes over — the banner's dismiss fully clears it */}
      {runFlow && (
        <RunFlowModal
          pitch={runFlow.pitch}
          mode={runFlow.mode}
          hidden={!runOpen}
          onRunStarted={onRunStarted}
          onClose={() => setRunOpen(false)}
        />
      )}
      {viewRunId && <RunViewModal runId={viewRunId} onClose={() => setViewRunId(null)} />}
      {gated && <DeviceGateModal onClose={() => setGated(false)} />}
    </div>
  );
}
