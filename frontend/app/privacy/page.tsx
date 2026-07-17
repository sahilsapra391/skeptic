import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = { title: "Privacy Policy", alternates: { canonical: "/privacy" } };

/** Placeholder — the owner supplies the final text before launch (PRD H). */
export default function PrivacyPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Privacy Policy">
      <p>The full Privacy Policy is being finalized and will be published
      here before public launch.</p>
      <p className="mt-4">
        The short version of what will be stored once accounts open: your
        email address, a salted hash of your password (never the password
        itself), and the runs you create. No third-party analytics, no ad
        trackers, no selling data — ever.
      </p>
    </SubpageShell>
  );
}
