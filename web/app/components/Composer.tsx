"use client";

import { FormEvent } from "react";
import { Mode } from "../lib/api";
import { isStructuredLesson } from "../lib/format";

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
  onSubmit, submitting, error, onDismissError, generation,
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
  generation: "auto" | "local";
}) {
  const ready = Boolean(topic.trim() || script.trim());
  const chosen = MODES.find((m) => m.value === mode) ?? MODES[0];
  // A script carries its own content, so the topic and the mode both stop
  // applying. Saying so beats leaving two controls that quietly do nothing --
  // which is how "both" came to be selected on a video that had no code in it.
  const scriptActive = Boolean(script.trim());

  // What this submission will cost. Rendering is always local and free --
  // Pillow, ffmpeg and Kokoro -- so the only thing that costs anything is
  // *writing* the content, and lesson JSON does not need writing.
  const structured = isStructuredLesson(script);
  // In local mode nothing is sent at all -- it structures prose on this
  // machine. In auto mode the model does the writing, and lesson JSON is the
  // one thing it does not have to write.
  const writtenForYou = generation === "local"
    ? false
    : scriptActive ? !structured : true;
  // Only a bare topic is left with nothing local mode can work from.
  const refused = generation === "local" && !scriptActive;

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
          disabled={scriptActive}
        />
        {scriptActive && (
          <p className="field-note">Not used while a script is present.</p>
        )}
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
                disabled={scriptActive}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        {/* Only what the chosen one means, not all three at once. */}
        <p className="field-note">
          {scriptActive
            ? "A script decides its own content, so no mode is applied. Put code in the script if you want code on screen."
            : chosen.blurb}
        </p>
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
        {scriptActive && (
          <p className="field-note">
            This script replaces the topic. It can be prose, or lesson JSON to
            render as written.
          </p>
        )}
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
        <button type="submit" disabled={submitting || !ready || refused}>
          {submitting ? "Submitting…" : "Generate"}
        </button>
        {!ready && (
          <span className="compose-hint">Enter a topic, or paste a script</span>
        )}
      </div>

      {/* Said before submitting, not after failing. The allowance is per
          model, per day, and running out is a normal thing that happens. */}
      <p className={`compose-cost${refused ? " is-blocked" : ""}`}>
        {refused
          ? "Local mode is on, and a topic is the one thing it cannot work from. Paste a script, or switch generation back to auto."
          : writtenForYou
            ? "A model writes the code and narration. Counts against your daily allowance."
            : "Structured on this machine, at no cost. No model call."}
      </p>
    </form>
  );
}
