"use client";

import { FormEvent, useState } from "react";
import { supabase } from "../lib/supabase";

/**
 * Sign in, or make an account.
 *
 * Shown only when this build has a Supabase project configured. Without one the
 * page renders exactly as it always has, which is what local development and
 * the browser tests use.
 *
 * One form for both, because the only difference is which call is made and
 * having two screens would mean an account-less visitor choosing a door before
 * they know which one they want.
 */
export function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setError(null);

    const { data, error: failure } = creating
      ? await supabase.auth.signUp({ email, password })
      : await supabase.auth.signInWithPassword({ email, password });

    if (failure) {
      setError(failure.message);
      setBusy(false);
      return;
    }
    // A project with email confirmation on returns a user and no session --
    // there is nothing to sign in with until the link is clicked, and saying
    // so beats a form that appears to have done nothing.
    if (!data.session) {
      setSent(true);
      setBusy(false);
      return;
    }
    // The page is watching for the session; nothing more to do here.
  }

  if (sent) {
    return (
      <div className="card signin">
        <h2>Check your email</h2>
        <p className="field-note">
          We sent a confirmation link to {email}. Follow it, then come back and
          sign in.
        </p>
        <button className="ghost" onClick={() => { setSent(false); setCreating(false); }}>
          Back
        </button>
      </div>
    );
  }

  return (
    <form className="card signin" onSubmit={onSubmit}>
      <h2>{creating ? "Create an account" : "Sign in"}</h2>

      <div className="field">
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          required
        />
      </div>

      <div className="field">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete={creating ? "new-password" : "current-password"}
          required
        />
      </div>

      <div className="compose-actions">
        <button type="submit" disabled={busy || !email || !password}>
          {busy ? "Working…" : creating ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() => { setCreating(!creating); setError(null); }}
        >
          {creating ? "I already have one" : "Create an account"}
        </button>
      </div>

      {error && (
        <p className="notice notice-bad" role="alert">
          <span>{error}</span>
        </p>
      )}
    </form>
  );
}
