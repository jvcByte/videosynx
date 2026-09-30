"use client";

import { useState } from "react";
import { ApiError, Subscription, api } from "../lib/api";
import { relativeTime } from "../lib/format";

/**
 * The plan, the credits, and the way to buy more.
 *
 * `POST /billing/checkout` has existed since billing was built and nothing
 * called it, so the paid tier could not be bought from the product at all --
 * the endpoints were reachable only with curl.
 *
 * The upgrade button is hidden on a paid plan rather than shown and refused,
 * and it says why when there is no Stripe key configured rather than failing
 * when pressed: a button that exists and cannot work is worse than one that
 * explains itself.
 */
export function PlanPanel({ plan }: { plan: Subscription | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upgrade() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.checkout();
      // Off to Stripe's own page. Nothing about the card touches this app.
      window.location.assign(url);
    } catch (err) {
      // A 503 here is the honest one: billing is not configured on this
      // server, which is a fact about the deployment rather than the user.
      setError(err instanceof ApiError ? err.message : "Could not start checkout");
      setBusy(false);
    }
  }

  const paid = plan !== null && plan.tier !== "free";

  return (
    <section className="panel" aria-label="Your plan">
      <h2>Plan</h2>

      {!plan ? (
        <p className="field-note">Loading…</p>
      ) : (
        <>
          <p className="panel-figure">
            <span className="panel-tier">{plan.tier}</span>
            <span className="field-note">
              {plan.jobs_per_day} tutorials a day · {plan.tts} narration
            </span>
          </p>

          <dl className="panel-rows">
            <div>
              <dt>Credits</dt>
              <dd className="figure">{plan.credits}</dd>
            </div>
          </dl>

          {plan.recent.length > 0 && (
            <ul className="panel-ledger">
              {plan.recent.slice(0, 4).map((entry, i) => (
                <li key={i}>
                  <span className="figure">
                    {entry.delta > 0 ? `+${entry.delta}` : entry.delta}
                  </span>
                  <span className="panel-reason">{entry.reason}</span>
                  <span className="panel-when">{relativeTime(entry.at)}</span>
                </li>
              ))}
            </ul>
          )}

          {!paid && (
            <button className="panel-upgrade" onClick={upgrade} disabled={busy}>
              {busy ? "Opening checkout…" : "Upgrade"}
            </button>
          )}

          {error && (
            <p className="notice notice-bad" role="alert">
              <span>{error}</span>
            </p>
          )}
        </>
      )}
    </section>
  );
}
