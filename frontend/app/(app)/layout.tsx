import type { Metadata } from "next";

import { AccountGate } from "@/components/account-gate";
import { BootSplash } from "@/components/boot-splash";
import { NavRail } from "@/components/nav-rail";

// the app surfaces are an application, not content — the landing + legal
// pages are the indexable face (launch L4). Public run pages become
// indexable when a server-rendered share surface exists, not before:
// client-rendered payload screens index as thin/duplicate content.
export const metadata: Metadata = {
  robots: { index: false, follow: true },
};

/**
 * The app shell (launch L4): nav rail + scroll-caged main. This wrapper
 * used to live on <body> in the root layout; it moved here so `/` (the
 * public landing) can scroll the window like a normal document while the
 * app keeps its viewport-locked shell. Print rules ride along: the scroll
 * cage would clip a Save-PDF printout to one viewport.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    // AccountGate (L1b): the app is account-gated — signed-out visitors are
    // walked to /signup. Soft here (children render while it checks); the
    // backend path matrix is the hard gate.
    <AccountGate>
      <div className="flex h-screen overflow-hidden print:block print:h-auto print:overflow-visible">
        <BootSplash />
        <NavRail />
        <main className="flex-1 overflow-auto print:overflow-visible">
          <div className="mx-auto max-w-shell px-[34px] pb-10 pt-8">{children}</div>
        </main>
      </div>
    </AccountGate>
  );
}
