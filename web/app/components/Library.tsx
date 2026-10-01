"use client";

import { Job, isPending } from "../lib/api";
import { duration, modeLabel, relativeTime, timeUntil } from "../lib/format";
import { Status } from "./Status";

/**
 * Everything rendered so far.
 *
 * Three states that must not be confused with each other: not loaded yet, no
 * videos, and failed to load. Showing "nothing here" before the first response
 * arrives tells someone with twenty videos that they have none.
 */
export function Library({
  jobs,
  loaded,
  failed,
  title = "Your videos",
  count,
  onPlay,
  onDownload,
  onRetry,
  onRerun,
  onInspect,
  onCancel,
}: {
  jobs: Job[];
  loaded: boolean;
  failed: boolean;
  /** The overview shows five of them, so it does not call them all yours. */
  title?: string;
  /**
   * What the corner says, or `null` for nothing at all.
   *
   * Left out, it is how many are listed. The overview passes null: it shows
   * five of them, so "5" would be a false total -- and the real total there
   * would read as five recent ones, which is worse.
   */
  count?: number | null;
  onPlay: (job: Job) => void;
  onDownload: (job: Job) => void;
  /** Reload the library, which is what the failed-to-load state offers. */
  onRetry: () => void;
  /** Put a job that has given up back in the queue. */
  onRerun: (job: Job) => void;
  /** Show the plan the renderer produced, for when the video is not right. */
  onInspect: (job: Job) => void;
  /** Stop a job that has not finished. */
  onCancel: (job: Job) => void;
}) {
  return (
    <section className="library">
      <div className="section-head">
        <h2>{title}</h2>
        {loaded && jobs.length > 0 && count !== null && (
          <span className="count figure">{count ?? jobs.length}</span>
        )}
      </div>

      {!loaded ? (
        <ul className="jobs" aria-busy="true" aria-label="Loading your videos">
          {[0, 1, 2].map((row) => (
            <li key={row} className="job job-skeleton">
              <span className="skeleton skeleton-line" />
              <span className="skeleton skeleton-line short" />
            </li>
          ))}
        </ul>
      ) : failed ? (
        <div className="empty">
          <p className="empty-title">Your videos could not be loaded</p>
          <p className="empty-body">
            The library is unavailable. Anything already rendering is unaffected.
          </p>
          <button className="ghost" onClick={onRetry}>Try again</button>
        </div>
      ) : jobs.length === 0 ? (
        <div className="empty">
          <p className="empty-title">No videos yet</p>
          <p className="empty-body">
            Give it a topic, or paste a script. It will appear here while it
            renders, and stay once it is done.
          </p>
        </div>
      ) : (
        <ul className="jobs">
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} onPlay={onPlay}
                     onDownload={onDownload} onRerun={onRerun}
                     onInspect={onInspect} onCancel={onCancel} />
          ))}
        </ul>
      )}
    </section>
  );
}

function JobCard({
  job,
  onPlay,
  onDownload,
  onRerun,
  onInspect,
  onCancel,
}: {
  job: Job;
  onPlay: (job: Job) => void;
  onDownload: (job: Job) => void;
  onRerun: (job: Job) => void;
  onInspect: (job: Job) => void;
  onCancel: (job: Job) => void;
}) {
  // Finished, but the file it finished with is not there any more. Its own
  // state rather than a kind of failure: nothing went wrong with the render.
  const gone = job.status === "succeeded" && job.available === false;

  return (
    <li className={`job${gone ? " job-unavailable" : ""}`}
        data-testid="job" data-status={job.status}
        data-available={job.available === false ? "no" : undefined}>
      <div className="job-main">
        <p className="job-title">{job.topic}</p>
        <Status status={job.status} stage={job.stage} attempt={job.attempt} />
      </div>

      {/* What it is, how long, and how long ago. The three things you would
          ask about a video you did not remember making. */}
      <p className="job-meta">
        <span className="badge">{modeLabel(job.mode)}</span>
        {job.duration_s != null && (
          <span className="figure">{duration(job.duration_s)}</span>
        )}
        {job.created_at && (
          <>
            <span className="dot" aria-hidden>·</span>
            <span>{relativeTime(job.created_at)}</span>
          </>
        )}
      </p>

      {isPending(job.status) && (
        <div className="bar" aria-hidden>
          <span style={{ width: `${Math.round(job.progress * 100)}%` }} />
        </div>
      )}

      {/* Shown while it is still being retried, not only once it has given up.
          A script that will never parse is worth knowing about immediately,
          and the alternative is hiding it behind a day of retries. */}
      {job.status === "queued" && job.attempt > 0 && (
        <>
          <p className="job-note">
            {job.error_message ?? "The last attempt failed"} — trying again{" "}
            {timeUntil(job.not_before)}.
          </p>
          {/* A spent allowance is a normal thing that happens, not a fault,
              so it gets the way forward rather than only the bad news. */}
          {job.error_code === "provider_quota" && (
            <p className="job-hint">
              Paste lesson JSON and it renders as written, on this machine, at
              no cost — or switch generation to local only in Settings.
            </p>
          )}
        </>
      )}

      {job.status === "failed" && (
        <>
          <p className="job-note">{job.error_message ?? "Render failed"}</p>
          {/* Only once it has stopped trying. Saying "attempt 4 of 7" here
              would be reporting a limit rather than a result. */}
          {job.attempt >= job.max_attempts && (
            <p className="job-hint">
              Tried {job.attempt} times without success.
            </p>
          )}
          {job.error_code === "provider_quota" && (
            <p className="job-hint">
              Paste lesson JSON and it renders as written, on this machine, at
              no cost — or switch generation to local only in Settings.
            </p>
          )}
          {/* The one thing a failed job left you able to do. It costs no
              allowance: the row already exists and the work is the same. */}
          <div className="job-actions">
            <button className="ghost" onClick={() => onRerun(job)}>
              Try again
            </button>
          </div>
        </>
      )}

      {gone && (
        <p className="job-note job-note-quiet">
          File no longer available. The render is on record, but the video
          itself is not stored any more.
        </p>
      )}

      {job.status === "succeeded" && (
        <div className="job-actions">
          {/* Offered only while there is a file to offer. The store is a
              `/tmp` directory by default and `/tmp` is cleared, so a render
              outlives its video -- and a Watch button that answers 404 is
              worse than no button. */}
          {job.available !== false && (
            <>
              <button className="ghost" onClick={() => onPlay(job)}>Watch</button>
              {/* Downloading from the card, not only from the player: the row
                  is where you look for a video you made earlier. */}
              <button className="ghost" onClick={() => onDownload(job)}>Download</button>
            </>
          )}
          {/* The other two give you the video; this gives you what went into
              it, which is the only way to find out why one came out wrong.
              Kept when the file is gone, because the plan may still be there
              and, when it is not, it says so rather than failing. */}
          <button className="ghost" onClick={() => onInspect(job)}>Inspect</button>
        </div>
      )}

      {/* A running job can still be stopped. Minutes of CPU for something
          nobody wants is worth a button. */}
      {isPending(job.status) && (
        <div className="job-actions">
          <button className="ghost" onClick={() => onCancel(job)}>Cancel</button>
        </div>
      )}
    </li>
  );
}
