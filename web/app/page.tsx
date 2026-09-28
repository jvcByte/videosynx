"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { Library } from "./components/Library";
import { Player } from "./components/Player";
import { QuotaMeter } from "./components/QuotaMeter";
import { SettingsSheet } from "./components/SettingsSheet";
import {
  ApiError, Job, Mode, Quota, Settings, SubmitBody, absolute, api, isPending,
} from "./lib/api";

const POLL_MS = 2000;

export default function Home() {
  const [topic, setTopic] = useState("");
  const [script, setScript] = useState("");
  const [mode, setMode] = useState<Mode>("both");

  // Preferences, which belong to the account rather than this browser.
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listFailed, setListFailed] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [quotaFailed, setQuotaFailed] = useState(false);

  // Errors are kept apart by where they happened, so one failing thing does
  // not take over a page whose other half is working.
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [playError, setPlayError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [playing, setPlaying] = useState<{ url: string; title: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    // Settled, not all: a quota that failed to load must not blank the list,
    // and a list that failed must not hide a working allowance.
    const [jobsResult, quotaResult] = await Promise.allSettled([api.jobs(), api.quota()]);

    if (jobsResult.status === "fulfilled") {
      setJobs(jobsResult.value);
      setListFailed(false);
    } else {
      setListFailed(true);
    }
    if (quotaResult.status === "fulfilled") {
      setQuota(quotaResult.value);
      setQuotaFailed(false);
    } else {
      setQuotaFailed(true);
    }
    setLoaded(true);

    return jobsResult.status === "fulfilled" ? jobsResult.value : [];
  }, []);

  // Fetched once, not per submission. A failure leaves the defaults working --
  // losing a saved preference is not a reason to stop someone submitting.
  useEffect(() => {
    api
      .settings()
      .then((loadedSettings) => {
        setSettings(loadedSettings);
        setMode(loadedSettings.mode);
      })
      .catch(() => setSettingsFailed(true));
  }, []);

  // Poll only while something is actually running: a list that is all finished
  // has nothing to wait for.
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      const next = await refresh();
      if (cancelled) return;
      if (next.some((job) => isPending(job.status))) {
        timer.current = setTimeout(tick, POLL_MS);
      }
    };
    tick();

    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const value = topic.trim();
    const pasted = script.trim();
    if ((!value && !pasted) || submitting) return;

    const body: SubmitBody = { mode };
    if (value) body.topic = value;
    if (pasted) body.script = pasted;

    setSubmitting(true);
    setSubmitError(null);
    try {
      const { id } = await api.submit(body);
      setTopic("");
      setScript("");
      setJobs((current) => [
        {
          id, status: "queued", topic: value || labelFor(pasted), mode,
          stage: null, progress: 0, duration_s: null, error_code: null,
          error_message: null, attempt: 0, created_at: new Date().toISOString(),
        },
        ...current,
      ]);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => refresh(), POLL_MS);
    } catch (err) {
      // A quota rejection is the common case and deserves the server's own
      // words rather than a generic failure.
      setSubmitError(err instanceof ApiError ? err.message : "Could not submit");
    } finally {
      setSubmitting(false);
    }
  }

  async function onPlay(job: Job) {
    setPlayError(null);
    try {
      const { url } = await api.videoUrl(job.id);
      setPlaying({ url: absolute(url), title: job.topic });
    } catch (err) {
      setPlayError(err instanceof ApiError ? err.message : "Could not load the video");
    }
  }

  function editSettings(patch: Partial<Settings>) {
    setSettings((current) => (current ? { ...current, ...patch } : current));
    setSaved(false);
  }

  async function onSaveSettings(event: React.FormEvent) {
    event.preventDefault();
    if (!settings || saving) return;
    setSaving(true);
    setSaved(false);
    setSettingsError(null);
    try {
      setSettings(await api.saveSettings(settings));
      setSaved(true);
    } catch (err) {
      // Reported in the sheet, not in the composer: it is the sheet's problem,
      // and the composer may not even be on screen behind it.
      setSettingsError(err instanceof ApiError ? err.message : "Could not save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <h1>pycoding</h1>
          <div className="topbar-right">
            <QuotaMeter quota={quota} failed={quotaFailed} />
            <button className="ghost" onClick={() => setSettingsOpen(true)}>
              Settings
            </button>
          </div>
        </div>
      </header>

      <main className="layout">
        <Composer
          topic={topic}
          onTopic={setTopic}
          script={script}
          onScript={setScript}
          mode={mode}
          onMode={setMode}
          onSubmit={onSubmit}
          submitting={submitting}
          error={submitError}
          onDismissError={() => setSubmitError(null)}
        />

        <div className="column">
          {playError && (
            <p className="notice notice-bad" role="alert">
              <span>{playError}</span>
              <button className="ghost" onClick={() => setPlayError(null)}>Dismiss</button>
            </p>
          )}
          <Library
            jobs={jobs}
            loaded={loaded}
            failed={listFailed}
            onPlay={onPlay}
            onRetry={refresh}
          />
        </div>
      </main>

      {playing && (
        <Player
          url={playing.url}
          title={playing.title}
          onClose={() => setPlaying(null)}
        />
      )}

      <SettingsSheet
        open={settingsOpen}
        settings={settings}
        failed={settingsFailed}
        saving={saving}
        saved={saved}
        error={settingsError}
        onEdit={editSettings}
        onSave={onSaveSettings}
        onClose={() => setSettingsOpen(false)}
      />
    </>
  );
}

/**
 * What to call a script-only job until the server's own label arrives.
 *
 * Deliberately the same rule as `job_label` on the API: the row should not
 * rename itself a second after it appears.
 */
function labelFor(script: string): string {
  const line = script
    .split("\n")
    .map((l) => l.trim().replace(/^#+/, "").trim())
    .find(Boolean);
  return line ? line.slice(0, 120) : "untitled";
}
