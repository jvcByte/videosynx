"use client";

import { FormEvent } from "react";
import { Mode, Settings, Voice } from "../lib/api";
import { byLanguage } from "../lib/format";

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "both", label: "Explain, then code" },
  { value: "illustration", label: "Illustration only" },
  { value: "coding", label: "Coding only" },
];

/**
 * Saved preferences.
 *
 * These were a sheet over the page, on the grounds that they are set once and
 * revisited rarely. They are a view now: the sidebar is how you move around,
 * and a preference you cannot link to or come back to with the back button is
 * harder to find than one with an address.
 *
 * The controls carry explicit `aria-label`s: a wrapping label takes its
 * accessible name from all its text, which here would include every <option>.
 */
/** What each engine is, in the order the picker lists them. */
const ENGINES: { id: string; label: string; note: string; paid: boolean }[] = [
  { id: "kokoro", label: "Kokoro", note: "Included", paid: false },
  // "auto" is what the byok tier carries: the worker picks the best engine
  // the machine can run, which today means Kokoro.
  { id: "auto", label: "Kokoro", note: "Included", paid: false },
  { id: "elevenlabs", label: "ElevenLabs", note: "Paid", paid: true },
];

export function SettingsPanel({
  settings, voices, engine, failed, saving, saved, error, onEdit, onSave,
}: {
  settings: Settings | null;
  voices: Voice[];
  /**
   * The engine this account narrates with -- a property of the plan, not a
   * preference, so it is shown rather than chosen.
   *
   * Narration is the cost driver: on ElevenLabs it is roughly sixty cents a
   * video against about one for the code around it. Letting anyone switch it
   * on would let anyone spend the operator's money by opening a dropdown,
   * which is why the tier owns it and the worker reads it from the job.
   */
  engine?: string | null;
  failed: boolean;
  saving: boolean;
  saved: boolean;
  error: string | null;
  onEdit: (patch: Partial<Settings>) => void;
  onSave: (event: FormEvent) => void;
}) {
  return (
    <section className="card settings-view" aria-label="Settings">
      {/* Three states kept apart: still loading is not a failure, and a
          failure still leaves the defaults working. */}
      {!settings ? (
        failed ? (
          <p className="sheet-note">
            Your settings could not be loaded. The defaults are in use, and
            saving will replace them.
          </p>
        ) : (
          <p className="sheet-note" aria-busy="true">Loading your settings…</p>
        )
      ) : (
        <form className="settings-body" onSubmit={onSave}>
          <fieldset className="group">
            <legend>Content</legend>

            <label className="field">
              <span className="field-label">Generation</span>
              <select
                aria-label="Generation"
                value={settings.generation}
                onChange={(e) =>
                  onEdit({ generation: e.target.value as Settings["generation"] })
                }
              >
                <option value="auto">Auto — a model writes the content</option>
                <option value="local">Local only — never call a model</option>
              </select>
            </label>
            <p className="field-note">
              Rendering is always local and free. This decides only whether a
              model writes the content, which is the one thing that costs
              anything. Local mode cannot invent a tutorial from a topic, so
              it renders lesson JSON you supply.
            </p>

            <label className="field">
              <span className="field-label">Default mode</span>
              <select
                aria-label="Default mode"
                value={settings.mode}
                onChange={(e) => onEdit({ mode: e.target.value as Mode })}
              >
                {MODE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span className="field-label">Audience</span>
              <select
                aria-label="Audience"
                value={settings.audience}
                onChange={(e) =>
                  onEdit({ audience: e.target.value as Settings["audience"] })
                }
              >
                <option value="beginner">Beginner — assume no background</option>
                <option value="intermediate">Intermediate — knows the basics</option>
                <option value="expert">Expert — skip the groundwork</option>
              </select>
            </label>

            <label className="field">
              <span className="field-label">Length</span>
              <select
                aria-label="Length"
                value={settings.length}
                onChange={(e) =>
                  onEdit({ length: e.target.value as Settings["length"] })
                }
              >
                <option value="short">Short</option>
                <option value="medium">Medium</option>
                <option value="long">Long</option>
              </select>
            </label>

            <label className="field">
              <span className="field-label">Focus</span>
              <input
                aria-label="Focus"
                value={settings.focus}
                onChange={(e) => onEdit({ focus: e.target.value })}
                placeholder="keep it practical; emphasise the failure case"
                autoComplete="off"
              />
            </label>
          </fieldset>

          <fieldset className="group">
            <legend>Presentation</legend>

            <label className="field">
              <span className="field-label">Illustration style</span>
              <select
                aria-label="Illustration style"
                value={settings.style}
                onChange={(e) =>
                  onEdit({ style: e.target.value as Settings["style"] })
                }
              >
                <option value="sketch">Sketch — pencil on paper</option>
                <option value="clean">Clean — flat and dark</option>
              </select>
            </label>

            <label className="check">
              <input
                aria-label="Burn captions into the video"
                type="checkbox"
                checked={settings.captions}
                onChange={(e) => onEdit({ captions: e.target.checked })}
              />
              <span>Burn captions into the video</span>
            </label>
          </fieldset>

          <fieldset className="group">
            <legend>Narration</legend>

            <label className="field">
              <span className="field-label">Engine</span>
              <select aria-label="Engine" value={engine ?? "kokoro"} disabled>
                {ENGINES.map((option) => (
                  <option key={option.id} value={option.id} disabled={option.paid}>
                    {option.label} — {option.note}
                  </option>
                ))}
              </select>
            </label>
            {/* Said plainly rather than left as a greyed-out control: a
                disabled box with no explanation is a puzzle. */}
            {engine !== "elevenlabs" && (
              <p className="field-note">
                ElevenLabs narration is included in Studio.{" "}
                <a className="field-link" href="#overview">See plans →</a>
              </p>
            )}

            <label className="field">
              <span className="field-label">Pace</span>
              <select
                aria-label="Pace"
                value={settings.pace}
                onChange={(e) => onEdit({ pace: e.target.value as Settings["pace"] })}
              >
                <option value="calm">Calm — unhurried, about 140 words a minute</option>
                <option value="normal">Normal</option>
                <option value="fast">Fast — the engine's own rate</option>
              </select>
            </label>
            <p className="field-note">
              Slower is not only slower: a voice that hurries sounds like it
              wants the video over with.
            </p>

            <label className="field">
              <span className="field-label">Voice</span>
              <select
                aria-label="Voice"
                value={settings.voice}
                onChange={(e) => onEdit({ voice: e.target.value })}
              >
                {/* Until the catalogue arrives, the saved voice is still
                    selectable -- a picker that silently drops the current
                    value would look like the setting had been lost. */}
                {voices.length === 0 && (
                  <option value={settings.voice}>{settings.voice}</option>
                )}
                {byLanguage(voices).map(([language, group]) => (
                  <optgroup key={language} label={language}>
                    {group.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.id} — {voice.gender}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <p className="field-note">
              Which Kokoro voice. The engine above decides whether it is
              used at all, so a plan narrating with ElevenLabs ignores it.
            </p>
          </fieldset>

          {error && <p className="notice notice-bad" role="alert">{error}</p>}

          <footer className="settings-foot">
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save settings"}
            </button>
            {saved && <span className="saved">Saved</span>}
          </footer>
        </form>
      )}
    </section>
  );
}
