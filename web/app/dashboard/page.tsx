"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Composer } from "../components/Composer";
import { Library } from "../components/Library";
import { Player } from "../components/Player";
import { QuotaMeter } from "../components/QuotaMeter";
import { SettingsSheet } from "../components/SettingsSheet";
import { PlanPanel } from "../components/PlanPanel";
import { Achievements, LevelCard, StreakCard } from "../components/Progress";
import { ThemeToggle } from "../components/ThemeToggle";
import { PlanViewer } from "../components/PlanViewer";
import { Sidebar } from "../components/Sidebar";
import { SignIn } from "../components/SignIn";
import {
  ApiError, Job, Mode, Progress, Quota, Settings, SubmitBody, Subscription,
  Voice, absolute, api, isPending, setAccessToken,
} from "../lib/api";
import { videoFilename } from "../lib/format";
import { authEnabled, supabase } from "../lib/supabase";

const POLL_MS = 2000;

/**
 * One figure, and what it is.
 *
 * Undefined renders as an em dash rather than a zero: the tiles are counts of
 * what has been made, and "none" and "we have not asked yet" are different
 * answers that must not look the same.
 */
function Tile({ label, value, text, icon, tone }: {
  label: string;
  value?: number;
  text?: string;
  icon?: string;
  // "accent" for what the account has earned, "ok" for what is finished.
  // Two roles rather than four hues: a colour per tile would distinguish
  // nothing, since the tiles are already distinguished by their labels.
  tone?: "accent" | "ok";
}) {
  return (
    <div className="tile">
      {icon && <span className={`tile-icon tone-${tone ?? "accent"}`} aria-hidden>{icon}</span>}
      <div className="tile-body">
        <span className="tile-value figure">
          {text ?? (value === undefined ? "—" : value)}
        </span>
        <span className="tile-label">{label}</span>
      </div>
    </div>
  );
}

export default function Home() {
  // Who is signed in. Null with auth configured means signed out, and the
  // page renders the sign-in form instead of itself -- the gate has to come
  // before the first fetch, because the API answers 401 and a 401 arriving as
  // "your videos could not be loaded" is not a sign-in prompt.
  //
  // With no Supabase project this is "ready" from the start and nothing below
  // changes: same dev header, no sign-in screen, same code the browser tests
  // have always run against.
  //
  // Two flags, not one. They were one to begin with, and resolving the lookup
  // with nobody signed in still counted as ready -- so every visitor got the
  // workspace and the sign-in form was unreachable.
  const [resolved, setResolved] = useState(!authEnabled);
  const [signedIn, setSignedIn] = useState(!authEnabled);
  const [email, setEmail] = useState<string | null>(null);

  const [topic, setTopic] = useState("");
  const [script, setScript] = useState("");
  const [mode, setMode] = useState<Mode>("both");

  // Preferences, which belong to the account rather than this browser.
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  // The account's own totals and plan. Counted by the server, because the job
  // list below is capped and counting its rows would quietly say "20" to
  // anyone with more than twenty.
  const [plan, setPlan] = useState<Subscription | null>(null);
  const [inspecting, setInspecting] = useState<Job | null>(null);
  // XP, level, streak and badges -- all computed by the server from jobs that
  // finished, so there is nothing here to keep in step by hand.
  const [progress, setProgress] = useState<Progress | null>(null);

  const [query, setQuery] = useState("");
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

  // Resolve the session before anything is fetched. `onAuthStateChange` fires
  // on sign-in, sign-out and every token refresh, so the module-level token the
  // API client sends is never stale.
  useEffect(() => {
    if (!supabase) return;
    let cancelled = false;

    supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setAccessToken(data.session?.access_token ?? null);
      setEmail(data.session?.user.email ?? null);
      setSignedIn(Boolean(data.session));
      setResolved(true);
    });

    const { data } = supabase.auth.onAuthStateChange((_event, next) => {
      setAccessToken(next?.access_token ?? null);
      setEmail(next?.user.email ?? null);
      setSignedIn(Boolean(next));
    });
    return () => {
      cancelled = true;
      data.subscription.unsubscribe();
    };
  }, []);

  // Fetched once, not per submission. A failure leaves the defaults working --
  // losing a saved preference is not a reason to stop someone submitting.
  useEffect(() => {
    if (!signedIn) return;
    api
      .settings()
      .then((loadedSettings) => {
        setSettings(loadedSettings);
        setMode(loadedSettings.mode);
      })
      .catch(() => setSettingsFailed(true));

    // The catalogue of voices. A failure leaves the saved one selectable on
    // its own, which is enough to save the rest of the form.
    api.voices().then(setVoices).catch(() => setVoices([]));
    // A failure leaves the tiles blank rather than showing zeroes, which
    // would be a claim about how much has been made.
    api.subscription().then(setPlan).catch(() => setPlan(null));
    api.progress().then(setProgress).catch(() => setProgress(null));
  }, [signedIn]);

  // Poll only while something is actually running: a list that is all finished
  // has nothing to wait for.
  useEffect(() => {
    if (!signedIn) return;
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
  }, [refresh, signedIn]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const value = topic.trim();
    const pasted = script.trim();
    if ((!value && !pasted) || submitting) return;

    // A script carries its own content, so when one is present the topic and
    // the mode are not sent at all -- the API names the job from the script
    // for the same reason.
    const body: SubmitBody = {};
    if (pasted) {
      body.script = pasted;
    } else {
      body.topic = value;
      body.mode = mode;
    }

    setSubmitting(true);
    setSubmitError(null);
    try {
      const { id } = await api.submit(body);
      setTopic("");
      setScript("");
      setJobs((current) => [
        {
          id, status: "queued", topic: pasted ? labelFor(pasted) : value, mode,
          stage: null, progress: 0, duration_s: null, error_code: null,
          error_message: null, attempt: 0, max_attempts: 1, not_before: null,
          created_at: new Date().toISOString(),
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

  async function onRerun(job: Job) {
    setPlayError(null);
    try {
      const fresh = await api.retry(job.id);
      // Written straight in rather than waiting for the next poll, so the row
      // stops saying Failed the moment it stops being true.
      setJobs((current) =>
        current.map((row) => (row.id === fresh.id ? fresh : row))
      );
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => refresh(), POLL_MS);
    } catch (err) {
      setPlayError(err instanceof ApiError ? err.message : "Could not retry the job");
    }
  }

  // Filtered in place, and only on the title: a search box that quietly
  // matched on hidden fields would be a worse lie than no search box.
  const shown = query.trim()
    ? jobs.filter((job) => job.topic.toLowerCase().includes(query.trim().toLowerCase()))
    : jobs;

  async function onCancel(job: Job) {
    setPlayError(null);
    try {
      const stopped = await api.cancel(job.id);
      setJobs((current) =>
        current.map((row) => (row.id === stopped.id ? stopped : row))
      );
    } catch (err) {
      setPlayError(err instanceof ApiError ? err.message : "Could not cancel the job");
    }
  }

  async function onDownload(job: Job) {
    setPlayError(null);
    try {
      const { url } = await api.videoUrl(job.id);
      // A real link, clicked once. Going through the DOM rather than opening
      // the URL keeps the browser's own save behaviour, including streaming a
      // large file instead of holding it in memory.
      const link = document.createElement("a");
      link.href = absolute(url);
      link.download = videoFilename(job.topic);
      document.body.append(link);
      link.click();
      link.remove();
    } catch (err) {
      setPlayError(err instanceof ApiError ? err.message : "Could not download the video");
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

  // Signed out, with a project configured: the sign-in form is the whole page.
  // Everything below fetches on mount, so rendering it first would fire
  // requests with no token and show their 401s as load failures.
  if (!resolved) {
    return (
      <main className="layout layout-single">
        <div className="card"><span className="skeleton skeleton-line" /></div>
      </main>
    );
  }
  if (!signedIn) {
    return (
      <main className="layout layout-single">
        <SignIn />
      </main>
    );
  }

  return (
    <div className="shell" id="top">
      <Sidebar
        email={email}
        level={progress?.level ?? null}
        percent={
          progress
            ? Math.round((progress.xp_into_level / progress.xp_for_level) * 100)
            : null
        }
        onSettings={() => setSettingsOpen(true)}
        onSignOut={authEnabled ? () => { setAccessToken(null); supabase?.auth.signOut(); } : undefined}
      />

      <div className="shell-main">
      <header className="topbar">
        <div className="topbar-inner">
          {/* Search is here because the design has it, and it filters the
              tutorial list below rather than pretending to search a
              catalogue that does not exist. */}
          <label className="search">
            <span className="search-icon" aria-hidden>⌕</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search your tutorials"
              aria-label="Search your tutorials"
            />
          </label>
          <div className="topbar-right">
            <QuotaMeter quota={quota} failed={quotaFailed} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="dash">
        <section className="tiles" aria-label="What you have made">
          <Tile icon="▶" tone="accent" label="Tutorials created"
                value={plan?.tutorials_created} />
          <Tile icon="✓" tone="ok" label="Completed"
                value={plan?.tutorials_completed} />
          <Tile icon="★" tone="accent" label="Total XP" value={progress?.xp} />
          <Tile icon="🏆" tone="accent" label="Achievements"
                value={progress?.achievements.filter((a) => a.earned).length} />
        </section>

        <div className="dash-grid">
        <div className="dash-main">

        <div className="layout layout-dash">
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
          generation={settings?.generation ?? "auto"}
          quota={quota}
        />

        <div className="column">
          {playError && (
            <p className="notice notice-bad" role="alert">
              <span>{playError}</span>
              <button className="ghost" onClick={() => setPlayError(null)}>Dismiss</button>
            </p>
          )}
          <Library
            jobs={shown}
            loaded={loaded}
            failed={listFailed}
            onPlay={onPlay}
            onDownload={onDownload}
            onRetry={refresh}
            onRerun={onRerun}
            onInspect={setInspecting}
            onCancel={onCancel}
          />
        </div>
        </div>

        <Achievements progress={progress} />
        </div>

        <aside className="dash-rail">
          <LevelCard progress={progress} />
          <StreakCard progress={progress} />
          <PlanPanel plan={plan} />
        </aside>
        </div>
      </main>
      </div>

      {inspecting && (
        <PlanViewer job={inspecting} onClose={() => setInspecting(null)} />
      )}

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
        voices={voices}
        failed={settingsFailed}
        saving={saving}
        saved={saved}
        error={settingsError}
        onEdit={editSettings}
        onSave={onSaveSettings}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
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
