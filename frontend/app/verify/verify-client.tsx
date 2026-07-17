"use client";

/**
 * Launch L1b: email-verification landing. The token is read from
 * window.location.search in a mount effect — NOT useSearchParams, which
 * would force a Suspense split through the page for one query param —
 * then POSTed once. Every terminal state is honest: verified names the
 * account, failure shows the backend's own refusal.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, verifyEmail } from "@/lib/api";

type State =
  | { phase: "checking" }
  | { phase: "verified"; email: string }
  | { phase: "failed"; detail: string };

export function VerifyClient() {
  const [state, setState] = useState<State>({ phase: "checking" });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setState({
        phase: "failed",
        detail:
          "this link is missing its verification token — open the full link from the email",
      });
      return;
    }
    let alive = true;
    verifyEmail(token)
      .then((a) => alive && setState({ phase: "verified", email: a.email }))
      .catch(
        (err) =>
          alive &&
          setState({
            phase: "failed",
            detail:
              err instanceof ApiError
                ? err.detail
                : "the server could not be reached — try the link again",
          }),
      );
    return () => {
      alive = false;
    };
  }, []);

  if (state.phase === "checking") {
    return <p>Checking your verification link…</p>;
  }

  if (state.phase === "verified") {
    return (
      <div>
        <p className="text-[15px] font-semibold text-ink">
          <span className="font-mono text-[14px]">{state.email}</span> is verified.
        </p>
        <Link
          href="/new"
          className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
        >
          Start a backtest →
        </Link>
      </div>
    );
  }

  return (
    <div>
      <p className="text-warn" role="alert">
        {state.detail}
      </p>
      <p className="mt-4">
        You can request a fresh link from your account once signed in.
      </p>
      <Link
        href="/new"
        className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
      >
        Back to the app →
      </Link>
    </div>
  );
}
