import Link from "next/link";
import type { Metadata } from "next";

import { Bullets, Lead, Section, Updated } from "@/components/landing/legal";
import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = {
  title: "Refund Policy",
  alternates: { canonical: "/refunds" },
};

export default function RefundsPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Refund Policy">
      <Updated date="July 17, 2026" />
      <Lead>
        This Refund Policy explains how credits work on skeptic.fyi, operated by SpecHawk
        Inc., and when they are refunded. It is part of our{" "}
        <Link href="/terms" className="font-semibold text-ink-2 underline">
          Terms of Service
        </Link>
        .
      </Lead>

      <Section n={1} title="How credits work">
        <Bullets
          items={[
            "Your first backtest is free, with no account required.",
            "Creating an account grants a fixed number of free credits.",
            "Additional credits are available as a one-time purchase (currently $10 for 50 credits). There is no subscription.",
            "One credit is spent when you submit a backtest.",
          ]}
        />
      </Section>

      <Section n={2} title="When a credit is refunded">
        <p>A credit is automatically returned to your balance when:</p>
        <Bullets
          items={[
            "A run fails because of a problem on our end — for example an engine error, a crash, or a data-layer fault; or",
            "A run returns a “not enough evidence” verdict. You only spend a credit on a graded verdict; a refusal for insufficient evidence is returned to you.",
          ]}
        />
        <p>
          A completed, graded backtest — whether the verdict is favorable or unfavorable —
          is the product you paid for, and the credit for it is used. The engine ran and
          delivered its honest read; that is not grounds for a refund.
        </p>
      </Section>

      <Section n={3} title="Purchased credits">
        <p>
          Purchases of credit packs are final and non-refundable except (a) as required by
          applicable law, or (b) where a charge was made in error or a purchase was not
          delivered. Free credits have no cash value and are not refundable or redeemable
          for cash. Credits are not transferable.
        </p>
      </Section>

      <Section n={4} title="Payments and billing issues">
        <p>
          Payments are handled by our third-party payment processor; your card receipt from
          the processor serves as your invoice. If you were charged in error, believe a
          purchase did not deliver, or have any billing question, contact us at
          support@skeptic.fyi and we will make it right where the above applies.
        </p>
      </Section>

      <Section n={5} title="Changes">
        <p>
          We may update this Policy; material changes take effect when posted with a new
          date and apply to purchases made afterward.
        </p>
      </Section>
    </SubpageShell>
  );
}
