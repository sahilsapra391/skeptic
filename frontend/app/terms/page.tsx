import Link from "next/link";
import type { Metadata } from "next";

import { Bullets, Lead, Section, Updated } from "@/components/landing/legal";
import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = {
  title: "Terms of Service",
  alternates: { canonical: "/terms" },
};

export default function TermsPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Terms of Service">
      <Updated date="July 17, 2026" />
      <Lead>
        These Terms of Service (the &ldquo;Terms&rdquo;) govern your access to and use
        of skeptic.fyi and the Skeptic application (together, the
        &ldquo;Service&rdquo;), which is owned and operated by SpecHawk Inc.
        (&ldquo;SpecHawk,&rdquo; &ldquo;we,&rdquo; &ldquo;us,&rdquo; or &ldquo;our&rdquo;).
        By accessing or using the Service, you agree to be bound by these Terms. If you do
        not agree, do not use the Service.
      </Lead>

      <Section n={1} title="What Skeptic is — and is not">
        <p>
          Skeptic is a research and education tool that lets you describe options
          strategies in plain language and evaluates them against approximate,
          self-collected historical market data using automated backtesting and
          statistical analysis.
        </p>
        <p className="font-semibold text-ink">
          Skeptic is not financial, investment, tax, legal, or trading advice, and it is
          not a broker-dealer, investment adviser, or fiduciary of any kind. The Service
          produces no recommendations to buy, sell, or hold any security, and nothing it
          outputs should be relied upon as a basis for any trading or investment decision.
        </p>
        <p>
          Trading options and other securities involves substantial risk, including the
          possible loss of your entire investment. You are solely responsible for your own
          decisions. Consult a licensed professional before making any financial decision.
        </p>
      </Section>

      <Section n={2} title="Hypothetical performance — inherent limitations">
        <p>
          All results the Service produces are hypothetical and are computed on
          approximate, self-collected data that may be incomplete, delayed, or contain
          errors. Hypothetical and backtested results have inherent limitations, including
          that they are prepared with the benefit of hindsight, do not represent actual
          trading, and cannot account for all market factors — such as liquidity, order
          execution, fees, taxes, or the financial risk of real capital — that affect
          live results. No representation is made that any account will or is likely to
          achieve results similar to those shown. Past performance does not predict future
          results.
        </p>
      </Section>

      <Section n={3} title="Eligibility and accounts">
        <Bullets
          items={[
            "You must be at least 18 years old and able to form a binding contract to use the Service.",
            "You are responsible for the accuracy of the information you provide and for maintaining the confidentiality of your credentials. You are responsible for all activity under your account.",
            "Accounts are for a single individual; do not share, sell, or transfer your account.",
            "Notify us promptly of any unauthorized use. We are not liable for losses arising from your failure to safeguard your account.",
          ]}
        />
      </Section>

      <Section n={4} title="Acceptable use">
        <p>You agree not to:</p>
        <Bullets
          items={[
            "Use the Service for any unlawful purpose or in violation of any applicable law or regulation;",
            "Scrape, harvest, resell, redistribute, or republish the Service's market data, outputs, or any part of the Service;",
            "Reverse engineer, decompile, or attempt to extract source code, models, or data pipelines, except to the extent this restriction is prohibited by law;",
            "Circumvent, disable, or interfere with security, rate-limiting, or access controls, or probe the Service for vulnerabilities without authorization;",
            "Access the Service through automated means except as we expressly permit; or",
            "Introduce malware, overload our infrastructure, or otherwise disrupt the Service or other users.",
          ]}
        />
      </Section>

      <Section n={5} title="Credits and payments">
        <p>
          Access to backtests is metered in credits. New accounts receive free credits;
          additional credits may be purchased as described on the Service. Credit
          semantics and refunds are governed by our{" "}
          <Link href="/refunds" className="font-semibold text-ink-2 underline">
            Refund Policy
          </Link>
          , which is incorporated into these Terms. Payments are processed by a
          third-party payment processor; we do not receive or store your full payment card
          details.
        </p>
      </Section>

      <Section n={6} title="Intellectual property">
        <p>
          The Service, including its software, models, design, and the market data we
          collect and derive, is owned by SpecHawk and protected by intellectual property
          laws. We grant you a limited, revocable, non-exclusive, non-transferable license
          to use the Service for your personal, non-commercial research. You retain
          ownership of the strategy descriptions you submit; you grant us a license to
          process them solely to operate and improve the Service.
        </p>
      </Section>

      <Section n={7} title="Third-party data and services">
        <p>
          The Service relies on third-party and self-collected data sources and on
          third-party infrastructure. We do not guarantee the accuracy, completeness, or
          availability of any data source, and we are not responsible for third-party
          services or their acts or omissions.
        </p>
      </Section>

      <Section n={8} title="Disclaimer of warranties">
        <p className="uppercase">
          The Service is provided &ldquo;as is&rdquo; and &ldquo;as available,&rdquo;
          without warranties of any kind, whether express, implied, or statutory,
          including any implied warranties of merchantability, fitness for a particular
          purpose, accuracy, and non-infringement. We do not warrant that the Service will
          be uninterrupted, error-free, secure, or that any result is accurate or
          reliable. You use the Service at your own risk.
        </p>
      </Section>

      <Section n={9} title="Limitation of liability">
        <p className="uppercase">
          To the maximum extent permitted by law, SpecHawk and its officers, directors,
          employees, and agents will not be liable for any indirect, incidental, special,
          consequential, exemplary, or punitive damages, or for any loss of profits,
          revenue, data, goodwill, or trading or investment losses, arising out of or
          related to your use of (or inability to use) the Service, even if advised of the
          possibility of such damages.
        </p>
        <p className="uppercase">
          Our total aggregate liability for all claims relating to the Service will not
          exceed the greater of (a) the amount you paid us in the twelve months before the
          claim arose, or (b) one hundred U.S. dollars ($100).
        </p>
        <p>
          Some jurisdictions do not allow certain limitations; in those places, the above
          limitations apply to the fullest extent permitted.
        </p>
      </Section>

      <Section n={10} title="Indemnification">
        <p>
          You agree to indemnify and hold harmless SpecHawk from any claims, damages,
          losses, liabilities, and expenses (including reasonable legal fees) arising out
          of your use of the Service, your violation of these Terms, or your violation of
          any law or the rights of any third party.
        </p>
      </Section>

      <Section n={11} title="Termination">
        <p>
          We may suspend or terminate your access at any time, with or without notice, for
          any reason, including violation of these Terms. You may stop using the Service at
          any time. Sections that by their nature should survive termination will survive.
        </p>
      </Section>

      <Section n={12} title="Changes to the Service and these Terms">
        <p>
          We may modify or discontinue the Service, and we may update these Terms, at any
          time. Material changes take effect when posted with an updated date. Your
          continued use after changes take effect constitutes acceptance.
        </p>
      </Section>

      <Section n={13} title="Governing law and dispute resolution">
        <p>
          These Terms are governed by the laws of the State of Delaware, United States,
          without regard to its conflict-of-laws rules. Any dispute arising out of or
          relating to these Terms or the Service will be resolved by binding individual
          arbitration, and you and SpecHawk waive any right to a jury trial and to
          participate in a class or representative action, to the extent permitted by law.
          Either party may bring an individual claim in small-claims court where eligible.
        </p>
      </Section>

      <Section n={14} title="Contact">
        <p>
          Questions about these Terms: support@spechawk.ai.
        </p>
      </Section>
    </SubpageShell>
  );
}
