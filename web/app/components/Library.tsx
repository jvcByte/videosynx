"use client";

import { Job, isPending } from "../lib/api";
import { duration, modeLabel, relativeTime } from "../lib/format";
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
  onPlay,
  onDownload,
  onRetry,
}: {
  jobs: Job[];
  loaded: boolean;
  failed: boolean;
  onPlay: (job: Job) => void;
  onDownload: (job: Job) => void;
  onRetry: () => void;
}) {
  return (
    <section className="library">
      <div className="section-head">
        <h2>Your videos</h2>
        {loaded && jobs.length > 0 && (
          <span className="count figure">{jobs.length}</span>
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
            <JobCard key={job.id} job={job} onPlay={onPlay} onDownload={onDownload} />
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
}: {
  job: Job;
  onPlay: (job: Job) => void;
  onDownload: (job: Job) => void;
}) {
  return (
    <li className="job" data-testid="job" data-status={job.status}>
      <div className="job-main">
        <p className="job-title">{job.topic}</p>
        <Status status={job.status} />
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

      {job.status === "failed" && (
        <>
          <p className="job-note">{job.error_message ?? "Render failed"}</p>
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

      {job.status === "succeeded" && (
        <div className="job-actions">
          <button className="ghost" onClick={() => onPlay(job)}>Watch</button>
          {/* Downloading from the card, not only from the player: the row is
              where you look for a video you made earlier. */}
          <button className="ghost" onClick={() => onDownload(job)}>Download</button>
        </div>
      )}
    </li>
  );
}
