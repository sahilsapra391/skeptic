import type { Metadata } from "next";

import { Bullets, Lead, Section, Updated } from "@/components/landing/legal";
import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = {
  title: "Privacy Policy",
  alternates: { canonical: "/privacy" },
};

export default function PrivacyPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Privacy Policy">
      <Updated date="July 17, 2026" />
      <Lead>
        This Privacy Policy explains what SpecHawk Inc. (&ldquo;SpecHawk,&rdquo;
        &ldquo;we,&rdquo; &ldquo;us&rdquo;) collects when you use skeptic.fyi and the
        Skeptic application (the &ldquo;Service&rdquo;), how we use it, and the choices you
        have. We collect as little as we can to run the Service, and we do not sell your
        personal information.
      </Lead>

      <Section n={1} title="Information we collect">
        <Bullets
          items={[
            <>
              <span className="font-semibold text-ink">Account information</span> — your
              email address, and a salted <span className="font-mono text-[12.5px]">argon2id</span>{" "}
              hash of your password. We never store your password in readable form.
            </>,
            <>
              <span className="font-semibold text-ink">Your runs</span> — the strategy
              descriptions you submit and the backtest results and settings associated with
              your account.
            </>,
            <>
              <span className="font-semibold text-ink">Authentication data</span> — a
              session identifier stored as an httpOnly cookie so you stay signed in, and
              single-use email-verification tokens.
            </>,
            <>
              <span className="font-semibold text-ink">Limited technical logs</span> — for
              security, abuse prevention, and rate-limiting we process transient data such
              as IP address and request metadata. We do not use this to build advertising
              profiles.
            </>,
            <>
              <span className="font-semibold text-ink">Payment information</span> — if you
              purchase credits, our payment processor handles your card details directly.
              We receive a confirmation and a transaction reference; we never receive or
              store your full card number.
            </>,
          ]}
        />
      </Section>

      <Section n={2} title="How we use it">
        <Bullets
          items={[
            "To provide the Service — authenticate you, run your backtests, and keep your run library.",
            "To send transactional email such as address verification. We do not send marketing email without your consent.",
            "To secure the Service — detect and prevent abuse, fraud, and automated attacks, and enforce our Terms.",
            "To operate and improve the Service and fix problems.",
          ]}
        />
      </Section>

      <Section n={3} title="Cookies and analytics">
        <p>
          We use a single essential cookie: your httpOnly session cookie, required to keep
          you signed in. We use privacy-focused, cookieless product analytics to understand
          aggregate usage and performance; these do not track you across other websites. We
          do not use third-party advertising or cross-site tracking cookies.
        </p>
      </Section>

      <Section n={4} title="How we share information">
        <p>We do not sell your personal information. We share it only with:</p>
        <Bullets
          items={[
            "Service providers who process data on our behalf under contract — our hosting and database providers, our email sender, and our payment processor — solely to operate the Service;",
            "Authorities, if required by law, legal process, or to protect the rights, safety, and security of SpecHawk, our users, or the public; and",
            "A successor in the event of a merger, acquisition, or asset sale, subject to this Policy.",
          ]}
        />
      </Section>

      <Section n={5} title="Market data">
        <p>
          The historical market data the Service uses is collected from third-party and
          public sources for research purposes and is not personal information about you.
          We do not redistribute this data or expose it through public endpoints.
        </p>
      </Section>

      <Section n={6} title="Security">
        <p>
          We protect credentials with strong, salted password hashing (
          <span className="font-mono text-[12.5px]">argon2id</span>), transmit data over
          encrypted connections, and store session and verification tokens only as hashes.
          No system is perfectly secure, and we cannot guarantee absolute security.
        </p>
      </Section>

      <Section n={7} title="Data retention">
        <p>
          We keep your account information and runs for as long as your account is active
          and as needed to provide the Service, comply with our legal obligations, resolve
          disputes, and enforce our agreements. You may request deletion as described below.
        </p>
      </Section>

      <Section n={8} title="Your choices and rights">
        <p>
          You may request access to, correction of, or deletion of your personal
          information, and you may ask us to stop processing it, subject to legal limits.
          Depending on where you live, you may have additional rights under laws such as the
          GDPR or the CCPA/CPRA. To exercise any right, contact us at privacy@skeptic.fyi.
          We will not discriminate against you for exercising your rights.
        </p>
      </Section>

      <Section n={9} title="Children">
        <p>
          The Service is not directed to anyone under 18, and we do not knowingly collect
          personal information from children. If you believe a child has provided us
          information, contact us and we will delete it.
        </p>
      </Section>

      <Section n={10} title="Changes and contact">
        <p>
          We may update this Policy; material changes take effect when posted with a new
          date. Questions or requests: privacy@skeptic.fyi.
        </p>
      </Section>
    </SubpageShell>
  );
}
