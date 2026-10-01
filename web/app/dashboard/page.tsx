"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Player } from "../components/Player";
import { QuotaMeter } from "../components/QuotaMeter";
import { SettingsPanel } from "../components/SettingsPanel";
import { ThemeToggle } from "../components/ThemeToggle";
import { PlanViewer } from "../components/PlanViewer";
import { Sidebar } from "../components/Sidebar";
import { SignIn } from "../components/SignIn";
import {
  AchievementsView, ComingSoonView, CreateView, OverviewView, SpeechView,
  VideoList, VideosView, WalletView,
} from "./views";
import {
  ApiError, Job, Mode, Progress, Quota, Settings, Subscription, Voice,
  absolute, api, isPending, setAccessToken,
} from "../lib/api";
import { videoFilename } from "../lib/format";
import { authEnabled, supabase } from "../lib/supabase";
import { View, goToView, useView } from "../lib/view";

const POLL_MS = 2000;

/** What each view is called, in the topbar. */
const TITLES: Record<View, string> = {
  overview: "Dashboard",
  videos: "Your Videos",
  create: "Create Video",
  speech: "Text to speech",
  wallet: "Wallet",
  achievements: "Achievements",
  "coming-soon": "Coming soon",
  settings: "Settings",
};

export default function Home() {
  // Which view is on screen. It comes from the address, so the back button
  // and a refresh both land where they should.
  const view = useView();

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

  // What is half-written in the composer: the topic, the script, and the
  // account's default mode.
  //
  // These sit here rather than in the Create view, and not for tidiness. A
  // view unmounts when you leave it, so state kept inside one is thrown away
  // -- paste a script, go and look at Your Videos, come back, and the script
  // is gone. `mode` has a second reason: it is seeded by a settings fetch
  // this view may not be mounted for.
  const [topic, setTopic] = useState("");
  const [script, setScript] = useState("");
  const [mode, setMode] = useState<Mode>("both");

  // Preferences, which belong to the account rather than this browser.
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);
  const [voices, setVoices] = useState<Voice[]>([]);
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

  // Everything the views draw rows from, and the poll that keeps it current.
  // It lives here, above the views, because a view unmounts when you leave it
  // and a render in flight has to keep being watched from wherever you are.
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listFailed, setListFailed] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [quotaFailed, setQuotaFailed] = useState(false);

  // Errors are kept apart by where they happened, so one failing thing does
  // not take over a page whose other half is working.
  const [playError, setPlayError] = useState<string | null>(null);

  const [playing, setPlaying] = useState<{ url: string; title: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Which poll is the live one. A look already in flight when the next one
  // starts would otherwise schedule behind it, and two loops would run.
  const chain = useRef(0);
  // How many jobs were still running at the last look, so the poll can tell
  // when one has finished. -1 before the first, because the first look is not
  // something having just finished.
  const running = useRef(-1);

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

  // The figures a finished render moves. A failure keeps the last good ones
  // rather than blanking the tiles, which would be a claim about the account.
  const reloadProgress = useCallback(() => {
    api.subscription().then(setPlan).catch(() => {});
    api.progress().then(setProgress).catch(() => {});
  }, []);

  // Load the jobs now and again every couple of seconds while any of them is
  // still running. The loop names itself so that a submission can arm this
  // same one: arming a bare `refresh()` instead is what let a job submitted
  // from an idle list stop being followed after a single tick.
  const armPoll = useCallback(
    (delay = 0) => {
      const mine = ++chain.current;
      if (timer.current) clearTimeout(timer.current);

      const run = async () => {
        const next = await refresh();
        // Started over while this one was waiting: its answer is stale, and
        // re-arming it would leave a second loop running.
        if (mine !== chain.current) return;

        const pending = next.filter((job) => isPending(job.status)).length;

        // A render just ended, so the level, streak and totals have moved.
        // They are not in the poll payload, and asking for them twice a
        // second would be the wrong price for numbers that only change when
        // a render finishes.
        if (running.current >= 0 && pending < running.current) reloadProgress();
        running.current = pending;

        if (pending > 0) timer.current = setTimeout(run, POLL_MS);
      };

      timer.current = setTimeout(run, delay);
    },
    [refresh, reloadProgress]
  );

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

  // Fetched once, not per view. A failure leaves the defaults working --
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

  useEffect(() => {
    if (!signedIn) return;
    armPoll();
    return () => {
      // Signed out or gone: nothing may re-arm.
      chain.current++;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [armPoll, signedIn]);

  // A submission is written straight in rather than waiting for the next
  // poll, so the row stops saying Queued the moment it stops being true.
  const onSubmitted = useCallback(
    (job: Job) => {
      setJobs((current) => [job, ...current]);
      armPoll(POLL_MS);
      // You have just asked for a video, and watching it appear is the next
      // thing you want -- the composer has already cleared itself.
      goToView("videos");
    },
    [armPoll]
  );

  async function onRerun(job: Job) {
    setPlayError(null);
    try {
      const fresh = await api.retry(job.id);
      setJobs((current) =>
        current.map((row) => (row.id === fresh.id ? fresh : row))
      );
      armPoll(POLL_MS);
    } catch (err) {
      setPlayError(err instanceof ApiError ? err.message : "Could not retry the job");
    }
  }

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
      // Reported beside the form, not in the composer: it is this form's
      // problem, and the composer is on another view entirely.
      setSettingsError(err instanceof ApiError ? err.message : "Could not save settings");
    } finally {
      setSaving(false);
    }
  }

  // Both views that list videos draw the same rows with the same handlers,
  // and every one of those handlers has to live up here: retrying rewrites
  // the shared list, and watching opens an overlay that outlives the view
  // that opened it.
  const list: VideoList = {
    jobs,
    loaded,
    failed: listFailed,
    error: playError,
    onDismissError: () => setPlayError(null),
    onPlay,
    onDownload,
    // Not `refresh` alone: the poll stops on its own once nothing is running,
    // so a list that just failed to load has no chain left to re-arm.
    onRetry: () => armPoll(0),
    onRerun,
    onInspect: setInspecting,
    onCancel,
  };

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
        view={view}
        onSignOut={authEnabled ? () => { setAccessToken(null); supabase?.auth.signOut(); } : undefined}
      />

      <div className="shell-main">
        <header className="topbar">
          <div className="topbar-inner">
            <h1>{TITLES[view]}</h1>
            <div className="topbar-right">
              <QuotaMeter quota={quota} failed={quotaFailed} />
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main className="dash">
          {view === "overview" && (
            <OverviewView list={list} plan={plan} progress={progress} />
          )}
          {view === "videos" && <VideosView list={list} />}
          {view === "create" && (
            <CreateView
              settings={settings}
              quota={quota}
              credits={plan?.credits ?? null}
              topic={topic}
              onTopic={setTopic}
              script={script}
              onScript={setScript}
              mode={mode}
              onMode={setMode}
              onSubmitted={onSubmitted}
            />
          )}
          {view === "speech" && <SpeechView />}
          {view === "wallet" && <WalletView />}
          {view === "achievements" && <AchievementsView progress={progress} />}
          {view === "coming-soon" && <ComingSoonView />}
          {view === "settings" && (
            <div className="view-narrow">
              <SettingsPanel
                settings={settings}
                voices={voices}
                engine={plan?.tts ?? null}
                failed={settingsFailed}
                saving={saving}
                saved={saved}
                error={settingsError}
                onEdit={editSettings}
                onSave={onSaveSettings}
              />
            </div>
          )}
        </main>
      </div>

      {/* Overlays sit above whichever view is underneath, because the job
          they belong to does not stop existing when you leave its view. */}
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
    </div>
  );
}
