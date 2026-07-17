"use client";

/**
 * Launch L1b: the real sign-in form (self-rolled accounts). The backend
 * sets the httpOnly session cookie on success; errors surface its own
 * words (uniform "wrong email or password", per-account 429 with the
 * retry message). Success honors ?next= (the account gate stamps it),
 * falling back to /new.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, login } from "@/lib/api";
import { nextTarget } from "@/lib/next-param";

const FIELD =
  "w-full rounded-[9px] border border-line bg-panel-deep px-3 py-2 text-[14px] " +
  "text-ink focus:border-trust-border focus:outline-none";
const LABEL = "mb-1.5 block text-[12.5px] font-semibold text-ink-3";

export function SigninForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  useEffect(() => setSearch(window.location.search), []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      location.assign(nextTarget());
      // no setBusy(false): the busy state honestly covers the navigation
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "the server could not be reached — try again",
      );
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <div className="mt-1">
        <label htmlFor="signin-email" className={LABEL}>
          Email
        </label>
        <input
          id="signin-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={FIELD}
        />
      </div>
      <div className="mt-4">
        <label htmlFor="signin-password" className={LABEL}>
          Password
        </label>
        <input
          id="signin-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={FIELD}
        />
      </div>
      {error && (
        <p className="mt-4 text-[13px] text-warn" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent disabled:opacity-60"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="mt-8 text-[13px] text-ink-4">
        No account yet?{" "}
        <Link href={`/signup${search}`} className="font-semibold text-ink-2 underline">
          Create one
        </Link>
      </p>
    </form>
  );
}
