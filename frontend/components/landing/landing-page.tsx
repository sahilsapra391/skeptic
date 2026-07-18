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

import { useCallback, useRef, useState } from "react";

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
  // the backend's honest 402 detail, so the gate distinguishes "this device's
  // run is spent" from "the global daily budget is booked up"
  const [gateReason, setGateReason] = useState<string | undefined>(undefined);
  // the background run this browser is tracking (survives popup close +
  // reloads) — the banner watches it to "ready" and back to viewing
  const [activeRunId, setActiveRunId] = useState<string | null>(() => getActiveRun());
  // the run the MOUNTED flow itself started (null for an idle flow). The
  // banner needs the distinction: reopening the popup is only right when
  // the popup actually contains the tracked run — an ungated chart flow can
  // now be mounted while an unrelated finished run is being tracked.
  const [flowRunId, setFlowRunId] = useState<string | null>(null);
  // bumped whenever a NEW flow request mounts, keying RunFlow so a replaced
  // request (pitch over an idle chart) boots fresh instead of being ignored
  // by the flow's one-shot boot guard
  const flowGen = useRef(0);

  // the run flow reports its id the instant the backtest is created; a real
  // run becomes the tracked background run (demo-fallback runs don't persist
  // and are skipped — they'd 404 and wrongly burn the free run)
  const onRunStarted = useCallback((runId: string, demo: boolean) => {
    if (demo) return;
    setActiveRun(runId);
    setActiveRunId(runId);
    setFlowRunId(runId);
  }, []);

  // clear the run flow entirely — dismissed, or a phantom that self-healed
  const clearRun = useCallback(() => {
    setRunFlow(null);
    setRunOpen(false);
    setActiveRunId(null);
    setFlowRunId(null);
    clearActiveRun();
  }, []);

  // the server armor refused this device's free run (a cleared-cookie repeat,
  // or the global daily budget) — the client gate can't see that, so the run
  // popup hands off to the create-an-account gate. No run started, so there's
  // nothing to track.
  const onTrialExhausted = useCallback((reason?: string) => {
    setRunFlow(null);
    setRunOpen(false);
    // the flow is gone — drop its run id too, or the next mounted flow
    // inherits it (a stale flowRunId makes an idle chart look like it holds
    // the tracked run: hasFlow/idleChartFlow misfire)
    setFlowRunId(null);
    setGateReason(reason);
    setGated(true);
  }, []);

  // one free run per DEVICE for anonymous visitors. Once a run is in flight
  // this session (runFlow mounted) a second attempt does NOT start another —
  // it brings the visitor back to the running popup (owner 2026-07-17). With
  // no active flow, the device gate applies (a signed-in account holder is
  // never gated — they have credits and the popup is their run surface).
  const tryRun = (req: { pitch?: string; mode?: "chart" }) => {
    // an idle chart flow that never started a run is browsing, not a run in
    // flight — a typed pitch supersedes it (its pins have produced nothing
    // yet) instead of being swallowed by the reopen shortcut below
    const idleChartFlow = runFlow?.mode === "chart" && flowRunId === null;
    if (runFlow && !(idleChartFlow && req.pitch)) {
      setRunOpen(true); // a run is already going — take them to it
      return;
    }
    const mount = () => {
      flowGen.current += 1;
      setRunFlow(req);
      setRunOpen(true);
      // a freshly mounted flow has started no run yet — null the id so it
      // can't inherit a prior flow's run (the flowRunId===null invariant an
      // idle flow relies on)
      setFlowRunId(null);
    };
    if (req.mode === "chart") {
      // chart-teach opens ungated — pinning examples is browsing, not
      // spending (owner 2026-07-17). The device gate applies inside the
      // flow when "That's the idea" compiles the pins.
      mount();
      return;
    }
    if (myRunIds().length === 0) {
      mount();
      return;
    }
    fetchMe()
      .then(() => {
        mount(); // signed in — run it
      })
      .catch(() => {
        // anonymous repeat — the CLIENT device gate, always the "used this
        // device's run" message. Clear any stale reason from a prior
        // budget-402 so this gate never inherits the "busy" copy.
        setGateReason(undefined);
        setGated(true);
      });
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
      {(runFlow || activeRunId) &&
        !runOpen &&
        !(activeRunId && viewRunId === activeRunId) &&
        // an idle chart flow with no tracked run has nothing in progress —
        // a "being set up…" pill for it would be a false claim
        !(runFlow?.mode === "chart" && flowRunId === null && !activeRunId) && (
        <ActiveRunBanner
          runId={activeRunId}
          // reopening the popup is only right when the popup contains the
          // tracked run (or a text flow still working toward one) — an
          // unrelated idle chart flow must not intercept "view results"
          hasFlow={!!runFlow && flowRunId === activeRunId}
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
          key={flowGen.current}
          pitch={runFlow.pitch}
          mode={runFlow.mode}
          hidden={!runOpen}
          onRunStarted={onRunStarted}
          onTrialExhausted={onTrialExhausted}
          onClose={() => setRunOpen(false)}
        />
      )}
      {viewRunId && <RunViewModal runId={viewRunId} onClose={() => setViewRunId(null)} />}
      {gated && <DeviceGateModal reason={gateReason} onClose={() => setGated(false)} />}
    </div>
  );
}
