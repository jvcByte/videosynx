"use client";

import { FormEvent, useEffect } from "react";
import { Mode, Settings, Voice } from "../lib/api";

/** Voices grouped by language, in the order the API returned them. */
function byLanguage(voices: Voice[]): [string, Voice[]][] {
  const groups = new Map<string, Voice[]>();
  for (const voice of voices) {
    const list = groups.get(voice.language) ?? [];
    list.push(voice);
    groups.set(voice.language, list);
  }
  return [...groups.entries()];
}

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "both", label: "Explain, then code" },
  { value: "illustration", label: "Illustration only" },
  { value: "coding", label: "Coding only" },
];

/**
 * Saved preferences, over the page.
 *
 * A sheet rather than a panel in the flow: these are set once and revisited
 * rarely, so they are kept out of the way of the thing done every time. The
 * second and last place depth is spent, for the same reason as the player --
 * it genuinely sits above the page.
 *
 * The controls carry explicit `aria-label`s: a wrapping label takes its
 * accessible name from all its text, which here would include every <option>.
 */
export function SettingsSheet({
  open, settings, voices, failed, saving, saved, error, onEdit, onSave, onClose,
}: {
  open: boolean;
  settings: Settings | null;
  voices: Voice[];
  failed: boolean;
  saving: boolean;
  saved: boolean;
  error: string | null;
  onEdit: (patch: Partial<Settings>) => void;
  onSave: (event: FormEvent) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Settings">
      <button className="overlay-backdrop" aria-label="Dismiss" onClick={onClose} />
      <aside className="sheet">
        <header className="sheet-head">
          <h2>Settings</h2>
          <button className="ghost" onClick={onClose}>Close</button>
        </header>

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
          <form className="sheet-body" onSubmit={onSave}>
            <fieldset className="group">
              <legend>Content</legend>

              <label className="field">
                <span>Generation</span>
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
                <span>Default mode</span>
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
                <span>Audience</span>
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
                <span>Length</span>
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
                <span>Focus</span>
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
                <span>Illustration style</span>
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
                <span>Voice</span>
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
                Used when your tier narrates with Kokoro. The voice is picked
                inside whichever engine the tier allows, so it is ignored by a
                tier that narrates with ElevenLabs.
              </p>
            </fieldset>

            {error && <p className="notice notice-bad" role="alert">{error}</p>}

            <footer className="sheet-foot">
              <button type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save settings"}
              </button>
              {saved && <span className="saved">Saved</span>}
            </footer>
          </form>
        )}
      </aside>
    </div>
  );
}
