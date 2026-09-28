"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Job, Quota, absolute, api, isPending } from "./lib/api";

const POLL_MS = 2000;

export default function Home() {
  const [topic, setTopic] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [playing, setPlaying] = useState<{ id: string; url: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextJobs, nextQuota] = await Promise.all([api.jobs(), api.quota()]);
      setJobs(nextJobs);
      setQuota(nextQuota);
      return nextJobs;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API");
      return [];
    }
  }, []);

  // Poll only while something is actually running: a job list that is all
  // finished has nothing to wait for.
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
    if (!value || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const { id } = await api.submit(value);
      setTopic("");
      setJobs((current) => [
        {
          id, status: "queued", topic: value, stage: null, progress: 0,
          duration_s: null, error_code: null, error_message: null,
          attempt: 0, created_at: new Date().toISOString(),
        },
        ...current,
      ]);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => refresh(), POLL_MS);
    } catch (err) {
      // A quota rejection is the common case and deserves the server's own
      // words rather than a generic failure.
      setError(err instanceof ApiError ? err.message : "Could not submit");
    } finally {
      setSubmitting(false);
    }
  }

  async function onPlay(id: string) {
    try {
      const { url } = await api.videoUrl(id);
      setPlaying({ id, url: absolute(url) });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the video");
    }
  }

  return (
    <main>
      <header className="masthead">
        <h1>pycoding</h1>
        <p className="tagline">
          Give it a topic. Get back a narrated video of the code being written,
          run, and explained line by line.
        </p>
      </header>

      <form className="submit" onSubmit={onSubmit}>
        <label htmlFor="topic" className="label">Topic</label>
        <div className="row">
          <input
            id="topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Python list comprehensions"
            autoComplete="off"
          />
          <button type="submit" disabled={submitting || !topic.trim()}>
            {submitting ? "Submitting…" : "Generate"}
          </button>
        </div>
        {quota && (
          <p className="quota">
            <span className="tier">{quota.tier}</span>
            <span className="sep">·</span>
            <span className="figure">{quota.remaining}</span> of{" "}
            <span className="figure">{quota.jobs_per_day}</span> left today
            <span className="sep">·</span>
            narration: {quota.tts}
          </p>
        )}
      </form>

      {error && (
        <p className="error" role="alert" data-testid="error">
          {error}
          <button className="dismiss" onClick={() => setError(null)}>dismiss</button>
        </p>
      )}

      {playing && (
        <section className="player">
          <video
            key={playing.url}
            src={playing.url}
            controls
            autoPlay
            data-testid="player"
          />
          <button className="dismiss" onClick={() => setPlaying(null)}>close</button>
        </section>
      )}

      <section className="jobs">
        <h2>Your videos</h2>
        {jobs.length === 0 ? (
          <p className="empty">Nothing yet. Submit a topic above.</p>
        ) : (
          <ul>
            {jobs.map((job) => (
              <li key={job.id} data-testid="job" data-status={job.status}>
                <div className="job-head">
                  <span className="job-topic">{job.topic}</span>
                  <Status status={job.status} error={job.error_code} />
                </div>
                {isPending(job.status) && (
                  <div className="bar" aria-hidden>
                    <span style={{ width: `${Math.round(job.progress * 100)}%` }} />
                  </div>
                )}
                {job.status === "failed" && (
                  <p className="job-note">{job.error_message ?? "render failed"}</p>
                )}
                {job.status === "succeeded" && (
                  <div className="job-actions">
                    <button onClick={() => onPlay(job.id)}>Watch</button>
                    {job.duration_s != null && (
                      <span className="figure">
                        {Math.floor(job.duration_s / 60)}m{" "}
                        {Math.round(job.duration_s % 60)}s
                      </span>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function Status({ status, error }: { status: string; error: string | null }) {
  const label: Record<string, string> = {
    queued: "queued", leased: "starting", generating: "writing code",
    narrating: "writing narration", executing: "running the code",
    rendering: "rendering", uploading: "uploading",
    succeeded: "ready", failed: error ?? "failed", canceled: "canceled",
  };
  return (
    <span className={`status status-${status}`}>
      {label[status] ?? status}
    </span>
  );
}
