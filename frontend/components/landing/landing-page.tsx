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

import { useWordmarkDraw } from "@/components/landing/landing-wordmark";
import { useLandingTheme } from "@/components/landing/use-landing-theme";

import { CopilotDemo } from "@/components/landing/copilot-demo";
import { LandingHero, LandingTopbar } from "@/components/landing/hero";
import { HowItArgues } from "@/components/landing/how-it-argues";
import { LandingFooter } from "@/components/landing/landing-footer";
import { Pricing } from "@/components/landing/pricing";
import { Receipts } from "@/components/landing/receipts";
import { VerdictShowcase } from "@/components/landing/verdict-showcase";

export function LandingPage() {
  const theme = useLandingTheme();
  const draw = useWordmarkDraw();

  return (
    <div className="min-h-screen bg-ground">
      <LandingTopbar theme={theme} draw={draw} />
      <LandingHero theme={theme} draw={draw} />
      <main>
        <HowItArgues resolved={theme.resolved} />
        <VerdictShowcase />
        <Receipts />
        <CopilotDemo />
        <Pricing />
      </main>
      <LandingFooter theme={theme} />
    </div>
  );
}
