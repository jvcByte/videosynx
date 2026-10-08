"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Composer } from "../components/Composer";
import { Library } from "../components/Library";
import { PlanPanel } from "../components/PlanPanel";
import { Achievements, LevelCard, StreakCard } from "../components/Progress";
import { Player } from "../components/Player";
import { Status } from "../components/Status";
import {
  ApiError, Feature, Job, Mode, Plans, Progress, Quota, Settings, SubmitBody,
  Subscription, Voice, absolute, api, isPending,
} from "../lib/api";
import {
  byLanguage, duration, labelFor, relativeTime, videoFilename,
} from "../lib/format";

const POLL_MS = 2000;

/**
 * The five things the dashboard can be showing.
 *
 * Each view is handed what it needs and owns only the state that nobody else
 * has an opinion about -- what is half-typed in a box, which sentence of an
 * error is on screen. Everything the server answers for stays in the page
 * above them, because a view unmounts when you leave it and a render in
 * flight must not.
 */

/** How many videos the overview shows before it hands over to Your Videos. */
const RECENT = 5;

/**
 * How many the create view shows beside the composer.
 *
 * Fewer, because these sit in the 320px rail rather than across the page, so
 * three cards is already most of a screen. The link underneath says how many
 * there really are -- the same arrangement the overview uses, for the same
 * reason: a slice cannot state a total without lying about it.
 */
const RAIL_RECENT = 3;

/**
 * Everything a list of videos needs, passed as one piece.
 *
 * The overview and Your Videos both draw the same rows with the same
 * handlers, and those handlers have to live above both of them -- retrying a
 * job rewrites the shared list, and watching one opens an overlay that
 * outlives whichever view opened it.
 */
export type VideoList = {
  jobs: Job[];
  loaded: boolean;
  failed: boolean;
  error: string | null;
  onDismissError: () => void;
  onPlay: (job: Job) => void;
  onDownload: (job: Job) => void;
  onRetry: () => void;
  onRerun: (job: Job) => void;
  onInspect: (job: Job) => void;
  onCancel: (job: Job) => void;
};

/** The error strip a video action leaves behind, shown where the rows are. */
function RowError({ list }: { list: VideoList }) {
  if (!list.error) return null;
  return (
    <p className="notice notice-bad" role="alert">
      <span>{list.error}</span>
      <button className="ghost" onClick={list.onDismissError}>Dismiss</button>
    </p>
  );
}

/** One figure, and what it is. */
function Tile({ label, value, icon, tone }: {
  label: string;
  value?: number;
  icon: string;
  // "accent" for what the account has earned, "ok" for what is finished.
  // Two roles rather than four hues: a colour per tile would distinguish
  // nothing, since the tiles are already distinguished by their labels.
  tone: "accent" | "ok";
}) {
  return (
    <div className="tile">
      <span className={`tile-icon tone-${tone}`} aria-hidden>{icon}</span>
      <div className="tile-body">
        {/* An em dash, not a zero: "none" and "not asked yet" are different
            answers and must not look the same. */}
        <span className="tile-value figure">{value ?? "—"}</span>
        <span className="tile-label">{label}</span>
      </div>
    </div>
  );
}

/** Where you are: what you have made, and how far along you are. */
export function OverviewView({ list, plan, progress }: {
  list: VideoList;
  plan: Subscription | null;
  progress: Progress | null;
}) {
  const recent = list.jobs.slice(0, RECENT);

  return (
    <>
      <section className="tiles" aria-label="What you have made">
        <Tile icon="▶" tone="accent" label="Tutorials created"
              value={plan?.tutorials_created} />
        <Tile icon="✓" tone="ok" label="Completed"
              value={plan?.tutorials_completed} />
        <Tile icon="★" tone="accent" label="Total XP" value={progress?.xp} />
        <Tile icon="🏆" tone="accent" label="Achievements"
              value={progress?.achievements.filter((a) => a.earned).length} />
      </section>

      <RowError list={list} />

      <div className="dash-grid">
        <div className="dash-main">
          <Library
            jobs={recent}
            loaded={list.loaded}
            failed={list.failed}
            title="Recent videos"
            // A slice has no honest count, and the total would read as five
            // recent ones. The link below carries it instead.
            count={null}
            onPlay={list.onPlay}
            onDownload={list.onDownload}
            onRetry={list.onRetry}
            onRerun={list.onRerun}
            onInspect={list.onInspect}
            onCancel={list.onCancel}
          />
          {list.jobs.length > RECENT && (
            <a className="see-all" href="#videos" onClick={() => window.scrollTo(0, 0)}>
              See all {list.jobs.length} videos →
            </a>
          )}
        </div>

        <aside className="dash-rail">
          <LevelCard progress={progress} />
          <StreakCard progress={progress} />
          <PlanPanel plan={plan} />
        </aside>
      </div>
    </>
  );
}

/** Everything you have made, and a box to search it. */
export function VideosView({ list }: { list: VideoList }) {
  const [query, setQuery] = useState("");

  // Filtered in place, and only on the title: a search box that quietly
  // matched on hidden fields would be a worse lie than no search box.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? list.jobs.filter((job) => job.topic.toLowerCase().includes(needle))
    : list.jobs;

  return (
    <>
      <div className="view-head">
        <label className="search">
          <span className="search-icon" aria-hidden>⌕</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search your videos"
            aria-label="Search your videos"
          />
        </label>
      </div>

      <RowError list={list} />

      <Library
        jobs={shown}
        loaded={list.loaded}
        failed={list.failed}
        onPlay={list.onPlay}
        onDownload={list.onDownload}
        onRetry={list.onRetry}
        onRerun={list.onRerun}
        onInspect={list.onInspect}
        onCancel={list.onCancel}
      />
    </>
  );
}

/**
 * Ask for a new one.
 *
 * The draft -- topic, script, mode -- is held a level up, because this view
 * unmounts when you go and look at something else, and a script someone
 * pasted has to survive the trip. Only what is meaningless a second later
 * stays here: whether a submission is in flight, and what it said if it
 * failed.
 */
export function CreateView({
  list, settings, quota, credits, topic, onTopic, script, onScript, mode,
  onMode, onSubmitted,
}: {
  list: VideoList;
  settings: Settings | null;
  quota: Quota | null;
  credits: number | null;
  topic: string;
  onTopic: (topic: string) => void;
  script: string;
  onScript: (script: string) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  onSubmitted: (job: Job) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
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
    setError(null);
    try {
      const { id } = await api.submit(body);
      onTopic("");
      onScript("");
      onSubmitted({
        id, status: "queued", topic: pasted ? labelFor(pasted) : value, mode,
        kind: "video",
        stage: null, progress: 0, duration_s: null, error_code: null,
        error_message: null, attempt: 0, max_attempts: 1, not_before: null,
        created_at: new Date().toISOString(),
      });
    } catch (err) {
      // A quota rejection is the common case and deserves the server's own
      // words rather than a generic failure.
      setError(err instanceof ApiError ? err.message : "Could not submit");
    } finally {
      setSubmitting(false);
    }
  }

  // What you have already made, beside the box you make the next one in.
  //
  // Asking for a video and then navigating away to find out whether it worked
  // is two places for one thought. The list was always there; it was just on
  // another view. It is the same component the overview and Your Videos use,
  // so the three cannot disagree about what a job looks like.
  const recent = list.jobs.slice(0, RAIL_RECENT);

  return (
    <>
      <RowError list={list} />

      <div className="dash-grid">
        <div className="dash-main">
          <div className="view-narrow">
            <Composer
              topic={topic}
              onTopic={onTopic}
              script={script}
              onScript={onScript}
              mode={mode}
              onMode={onMode}
              onSubmit={onSubmit}
              submitting={submitting}
              error={error}
              onDismissError={() => setError(null)}
              generation={settings?.generation ?? "auto"}
              quota={quota}
              credits={credits}
            />
          </div>
        </div>

        <aside className="dash-rail">
          <Library
            jobs={recent}
            loaded={list.loaded}
            failed={list.failed}
            title="What you have made"
            // A slice has no honest count, and the real total would read as
            // three recent ones. The link below carries it instead.
            count={null}
            onPlay={list.onPlay}
            onDownload={list.onDownload}
            onRetry={list.onRetry}
            onRerun={list.onRerun}
            onInspect={list.onInspect}
            onCancel={list.onCancel}
          />
          {list.jobs.length > RAIL_RECENT && (
            <a className="see-all" href="#videos" onClick={() => window.scrollTo(0, 0)}>
              See all {list.jobs.length} videos →
            </a>
          )}
        </aside>
      </div>
    </>
  );
}

/** The badges, and the two figures they are earned against. */
export function AchievementsView({ progress }: { progress: Progress | null }) {
  return (
    <>
      <div className="dash-grid">
        <div className="dash-main">
          <Achievements progress={progress} />
        </div>
        <aside className="dash-rail">
          <LevelCard progress={progress} />
          <StreakCard progress={progress} />
        </aside>
      </div>
    </>
  );
}


/**
 * What is not built yet, and who is waiting for it.
 *
 * A page of promises is a page that can lie, so this one is built to be
 * checkable: every entry names what stands in the way of it, and the only
 * number on it is one the visitor can change by their own click.
 */
export function ComingSoonView() {
  const [features, setFeatures] = useState<Feature[] | null>(null);
  const [joined, setJoined] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // No polling: nothing here changes except by this page's own button.
    api.features().then(setFeatures).catch(() => setFailed(true));
  }, []);

  async function join(id: string) {
    setBusy(id);
    try {
      const updated = await api.joinFeature(id);
      setFeatures((current) =>
        current?.map((f) => (f.id === id ? updated : f)) ?? current
      );
      setJoined((current) => new Set(current).add(id));
    } catch {
      // Local, and beside the row it belongs to rather than over the page.
      setJoined((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      setFailed(true);
    } finally {
      setBusy(null);
    }
  }

  if (failed && !features) {
    return (
      <div className="card rail-card">
        <p className="empty-title">This list could not be loaded</p>
        <p className="empty-body">Nothing else on the page is affected.</p>
      </div>
    );
  }

  if (!features) {
    return (
      <div className="card rail-card">
        <span className="skeleton skeleton-line" />
      </div>
    );
  }

  return (
    <div className="coming-soon">
      <header className="landing-section-head coming-head">
        <h2>Coming soon</h2>
        <p className="rail-sub">
          Not built yet. Join the list and it counts you once — the numbers
          here are people, not votes.
        </p>
      </header>

      <ul className="wants">
        {features.map((feature) => (
          <li className="card want" key={feature.id}>
            <div className="want-body">
              <h3>{feature.title}</h3>
              <p className="want-blurb">{feature.blurb}</p>
              {/* What is in the way, said plainly. A promise with no obstacle
                  named is a promise nobody has costed. */}
              <p className="want-blocker">
                <span className="want-blocker-label">What is in the way</span>
                {feature.blocker}
              </p>
            </div>

            <div className="want-foot">
              {feature.waiting !== null && (
                <span className="want-count figure">
                  {feature.waiting} waiting
                </span>
              )}
              <button
                className="ghost"
                disabled={feature.joined || busy === feature.id}
                onClick={() => join(feature.id)}
              >
                {feature.joined ? "You're on the list" : "Join the waitlist"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}


/**
 * Text in, speech out.
 *
 * The narration half of the pipeline without the video around it. It is a job
 * like any other -- five minutes of speech is four minutes of CPU, so it
 * queues rather than holding a request open -- which is why this page has a
 * list under it rather than handing back a download.
 */
export function SpeechView() {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);

  const refresh = useCallback(
    () => api.jobs().then(setJobs).catch(() => setJobs([])),
    []
  );

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
    // The allowance is the local renders, and it is what actually stops a
    // long clip being queued -- so the number belongs on the page that spends
    // it, not only on the one you submit videos from.
    api.quota().then(setQuota).catch(() => setQuota(null));
    refresh();
  }, [refresh]);

  // Only the clips. A tutorial is a different thing that happens to share a
  // queue, and this list is about what you said.
  const clips = (jobs ?? []).filter((job) => job.kind === "speech");
  const pending = clips.some((job) => isPending(job.status));

  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [pending, refresh]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || sending) return;
    setSending(true);
    setError(null);
    try {
      await api.speak(value, voice || undefined);
      setText("");
      await refresh();
      api.quota().then(setQuota).catch(() => {});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not queue it");
    } finally {
      setSending(false);
    }
  }

  async function onPlay(job: Job) {
    setError(null);
    try {
      const { url } = await api.videoUrl(job.id);
      setPlaying(absolute(url));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the clip");
    }
  }

  async function onDownload(job: Job) {
    setError(null);
    try {
      const { url } = await api.videoUrl(job.id);
      // An mp3, so the name says so -- a file called .mp4 that is audio is a
      // file nothing on the desktop will open.
      const link = document.createElement("a");
      link.href = absolute(url);
      link.download = videoFilename(job.topic, "mp3");
      document.body.append(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not download it");
    }
  }

  return (
    <div className="view-narrow">
      <form className="card speech-form" onSubmit={onSubmit}>
        <h2>Text to speech</h2>
        <p className="field-note">
          Read aloud with the voices the narration uses. No model is called, so
          it costs none of your daily tutorials — it draws on the local
          renders, which are the ones that use this machine and nothing else.
        </p>

        <label className="field">
          <span className="field-label">Text</span>
          <textarea
            aria-label="Text"
            rows={8}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Paste anything you want read aloud."
          />
        </label>

        <label className="field">
          <span className="field-label">Voice</span>
          <select
            aria-label="Voice"
            value={voice}
            onChange={(event) => setVoice(event.target.value)}
          >
            <option value="">The default for your plan</option>
            {byLanguage(voices).map(([language, group]) => (
              <optgroup key={language} label={language}>
                {group.map((v) => (
                  <option key={v.id} value={v.id}>{v.id} — {v.gender}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>

        {error && <p className="notice notice-bad" role="alert">{error}</p>}

        <div className="speech-foot">
          <button type="submit" disabled={!text.trim() || sending}>
            {sending ? "Queueing…" : "Speak it"}
          </button>
          {quota && (
            <span className="field-note figure">
              {quota.local_remaining} of {quota.local_jobs_per_day} local renders left today
            </span>
          )}
        </div>
      </form>

      <h3 className="clips-head">Your audio</h3>
      {jobs === null ? (
        <div className="card"><span className="skeleton skeleton-line" /></div>
      ) : clips.length === 0 ? (
        <p className="clips-empty">
          Nothing yet. Anything you have read aloud appears here.
        </p>
      ) : (
        <ul className="clips">
          {clips.map((job) => (
            <li className="card clip" key={job.id}>
              <div className="clip-body">
                <p className="clip-title">{job.topic}</p>
                <Status status={job.status} stage={job.stage} attempt={job.attempt} />
              </div>
              {job.status === "succeeded" && (
                <div className="clip-actions">
                  {job.duration_s != null && (
                    <span className="figure clip-time">{duration(job.duration_s)}</span>
                  )}
                  <button className="ghost" onClick={() => onPlay(job)}>Play</button>
                  <button className="ghost" onClick={() => onDownload(job)}>Download</button>
                </div>
              )}
              {/* A clip whose file has gone says so rather than offering to
                  play nothing. Same rule as the library. */}
              {job.status === "succeeded" && job.available === false && (
                <p className="clip-note">File no longer available.</p>
              )}
            </li>
          ))}
        </ul>
      )}

      {playing && (
        <Player url={playing} title="Your audio" audio
                onClose={() => setPlaying(null)} />
      )}
    </div>
  );
}


/**
 * The wallet: what is in it, what it buys, and where it went.
 *
 * A balance is only money if something spends it. Credits are what a job
 * costs once the day's allowance is gone, and the ledger below is the
 * record -- every grant and every charge, append-only, added up rather than
 * stored as a running total that could disagree with its own history.
 */
export function WalletView() {
  const [plan, setPlan] = useState<Subscription | null>(null);
  const [packs, setPacks] = useState<Plans | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.subscription().then(setPlan).catch(() => setPlan(null));
    api.plans().then(setPacks).catch(() => setPacks(null));
  }, []);

  async function buy(credits: number) {
    setBusy(credits);
    setError(null);
    try {
      const { url } = await api.buyCredits(credits);
      window.location.assign(url);
    } catch (err) {
      // Billing not being configured is the ordinary state of a deployment
      // that has not set it up, and the server says so in its own words.
      setError(err instanceof ApiError ? err.message : "Could not start the purchase");
      setBusy(null);
    }
  }

  // No separate failure branch: a balance that did not load is an em dash and
  // a history that did not load is a skeleton, which is what the rest of the
  // app does and what the states above already render.
  const balance = plan?.credits;

  return (
    <div className="view-narrow">
      <section className="card wallet-card">
        <span className="wallet-label">Balance</span>
        {/* An em dash, not a zero: "none" and "not asked yet" are different
            answers, and only one of them is about your money. */}
        <p className="wallet-balance figure">
          {balance ?? "—"}
          <span>{balance === 1 ? "credit" : "credits"}</span>
        </p>
        {/* Minutes, and charged at the end rather than the start. Both halves
            matter to a reader deciding whether to spend one: a credit is a
            minute of finished video, and it is taken when there is a video to
            take it for. */}
        <p className="field-note">
          {packs
            ? `One credit is one minute of finished video, once the day's ${
                plan?.jobs_per_day ?? ""
              } are used up. Nothing else spends them.`
            : "One credit is one minute of finished video, past the day's allowance."}
        </p>
      </section>

      <h3 className="clips-head">Buy more</h3>
      <ul className="packs-row wallet-packs">
        {(packs?.credit_packs ?? []).map((pack) => (
          <li className="pack" key={pack.credits}>
            <span className="pack-credits figure">{pack.credits}</span>
            <span className="pack-unit">credits</span>
            <span className="pack-price figure">${pack.usd}</span>
            <span className="pack-note">{pack.note}</span>
            <button className="ghost" disabled={busy !== null} onClick={() => buy(pack.credits)}>
              {busy === pack.credits ? "Opening…" : "Buy"}
            </button>
          </li>
        ))}
      </ul>

      {error && <p className="notice notice-bad" role="alert">{error}</p>}

      <h3 className="clips-head">History</h3>
      {!plan ? (
        <div className="card"><span className="skeleton skeleton-line" /></div>
      ) : plan.recent.length === 0 ? (
        <p className="clips-empty">
          Nothing yet. Buying credits, and spending them, both appear here.
        </p>
      ) : (
        <ul className="ledger">
          {plan.recent.map((entry, index) => (
            <li className="ledger-row" key={`${entry.at}-${index}`}>
              <span className={`ledger-delta figure ${entry.delta > 0 ? "is-in" : ""}`}>
                {entry.delta > 0 ? `+${entry.delta}` : entry.delta}
              </span>
              <span className="ledger-reason">{entry.reason}</span>
              <span className="ledger-at">{relativeTime(entry.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
