"use client";

import { FormEvent, useState } from "react";
import { Composer } from "../components/Composer";
import { Library } from "../components/Library";
import { PlanPanel } from "../components/PlanPanel";
import { Achievements, LevelCard, StreakCard } from "../components/Progress";
import {
  ApiError, Job, Mode, Progress, Quota, Settings, SubmitBody, Subscription, api,
} from "../lib/api";
import { labelFor } from "../lib/format";

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
  settings, quota, topic, onTopic, script, onScript, mode, onMode, onSubmitted,
}: {
  settings: Settings | null;
  quota: Quota | null;
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

  return (
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
      />
    </div>
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
