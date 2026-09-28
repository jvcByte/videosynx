"use client";

import { FormEvent } from "react";
import { Mode } from "../lib/api";

const MODES: { value: Mode; label: string; blurb: string }[] = [
  {
    value: "both",
    label: "Both",
    blurb: "Drawn slides explain the idea first, then the code is written and run.",
  },
  {
    value: "illustration",
    label: "Illustration",
    blurb: "Drawn slides only. No code appears on screen.",
  },
  {
    value: "coding",
    label: "Code",
    blurb: "Code only, with the explanation slides left out.",
  },
];

/**
 * The one thing this page is for.
 *
 * Everything else on the page is a result of pressing Generate, so this is
 * where the weight goes: the only filled button, the only place a topic is
 * asked for.
 */
export function Composer({
  topic, onTopic, script, onScript, mode, onMode,
  onSubmit, submitting, error, onDismissError,
}: {
  topic: string;
  onTopic: (value: string) => void;
  script: string;
  onScript: (value: string) => void;
  mode: Mode;
  onMode: (value: Mode) => void;
  onSubmit: (event: FormEvent) => void;
  submitting: boolean;
  error: string | null;
  onDismissError: () => void;
}) {
  const ready = Boolean(topic.trim() || script.trim());
  const chosen = MODES.find((m) => m.value === mode) ?? MODES[0];

  return (
    <form className="card compose" onSubmit={onSubmit}>
      <h2>New video</h2>

      <div className="field">
        <label htmlFor="topic">Topic</label>
        <input
          id="topic"
          value={topic}
          onChange={(event) => onTopic(event.target.value)}
          placeholder="Python list comprehensions"
          autoComplete="off"
        />
      </div>

      <div className="field">
        <span className="field-label" id="mode-label">Mode</span>
        <div className="segmented" role="radiogroup" aria-labelledby="mode-label">
          {MODES.map((option) => (
            <label key={option.value}>
              <input
                type="radio"
                name="mode"
                value={option.value}
                checked={mode === option.value}
                onChange={() => onMode(option.value)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        {/* Only what the chosen one means, not all three at once. */}
        <p className="field-note">{chosen.blurb}</p>
      </div>

      <div className="field">
        <label htmlFor="script">
          Script <span className="optional">optional</span>
        </label>
        <textarea
          id="script"
          value={script}
          onChange={(event) => onScript(event.target.value)}
          rows={3}
          placeholder="Paste a script, a scene breakdown, or a lesson JSON."
        />
      </div>

      {/* Contained here rather than at the top of the page: a rejected
          submission is this form's problem, and taking over the whole view
          for it would hide the thing being corrected. */}
      {error && (
        <p className="notice notice-bad" role="alert" data-testid="error">
          <span>{error}</span>
          <button type="button" className="ghost" onClick={onDismissError}>
            Dismiss
          </button>
        </p>
      )}

      <div className="compose-actions">
        <button type="submit" disabled={submitting || !ready}>
          {submitting ? "Submitting…" : "Generate"}
        </button>
        {!ready && (
          <span className="compose-hint">Enter a topic, or paste a script</span>
        )}
      </div>
    </form>
  );
}
