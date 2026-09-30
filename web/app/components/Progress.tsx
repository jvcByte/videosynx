"use client";

import { Progress } from "../lib/api";

/**
 * What the account has earned.
 *
 * Every figure comes from `/me/progress`, which computes them from finished
 * jobs -- so an empty account shows an empty dashboard rather than a
 * decorated one. That is the point: a number that appears without the work
 * makes every other number on the page worth nothing.
 */

/** The level, and how far into it. */
export function LevelCard({ progress }: { progress: Progress | null }) {
  if (!progress) {
    return <article className="card rail-card"><span className="skeleton skeleton-line" /></article>;
  }
  const percent = Math.round((progress.xp_into_level / progress.xp_for_level) * 100);

  return (
    <article className="card rail-card">
      <div className="rail-head">
        <span className="medal" aria-hidden>★</span>
        <div>
          <h2>Level {progress.level}</h2>
          <p className="rail-sub">{progress.xp.toLocaleString()} XP earned</p>
        </div>
      </div>

      <div className="meter-line" aria-hidden>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p className="rail-figure figure">
        {progress.xp_into_level} / {progress.xp_for_level} XP
      </p>
      <p className="rail-note">
        Earned by finishing tutorials — {progress.xp_for_level - progress.xp_into_level} XP
        to level {progress.level + 1}.
      </p>
    </article>
  );
}

/** Seven days, and which of them had work in them. */
export function StreakCard({ progress }: { progress: Progress | null }) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  // Which days of the current run are behind us. Derived, not stored: the
  // streak is a length, and the ticks are the last N days of it.
  const run = progress?.streak ?? 0;
  const today = new Date().getDay(); // 0 = Sunday

  return (
    <article className="card rail-card">
      <div className="rail-head">
        <span className="flame" aria-hidden>🔥</span>
        <div>
          <h2>{run} {run === 1 ? "day" : "days"}</h2>
          <p className="rail-sub">Daily streak</p>
        </div>
      </div>

      <ol className="streak">
        {days.map((label, i) => {
          // How many days back this square is, with Monday first.
          const back = (today - (i + 1) + 7) % 7;
          const done = run > back;
          return (
            <li key={label} className={done ? "is-done" : ""}>
              <span aria-hidden>{done ? "✓" : ""}</span>
              {label}
            </li>
          );
        })}
      </ol>
      <p className="rail-note">
        {run === 0
          ? "Render something to start one."
          : "Days with a tutorial submitted, counting back from today."}
      </p>
    </article>
  );
}

/** The badges, earned and not. */
export function Achievements({ progress }: { progress: Progress | null }) {
  if (!progress) return null;

  return (
    <section className="card awards">
      <div className="section-head">
        <h2>Achievements</h2>
        <span className="count figure">
          {progress.achievements.filter((a) => a.earned).length} / {progress.achievements.length}
        </span>
      </div>

      <ul className="awards-row">
        {progress.achievements.map((badge) => (
          <li key={badge.id} className={badge.earned ? "is-earned" : ""}>
            {/* The same medal either way, so the row is a set you are
                collecting rather than a legend of two icons. What tells them
                apart is the metal, which a screen reader cannot see -- hence
                the state in words beside the name. */}
            <span className="award-medal" aria-hidden>★</span>
            <span className="award-name">
              {badge.name}
              <span className="visually-hidden">
                {badge.earned ? " — earned" : " — not earned yet"}
              </span>
            </span>
            {/* What it takes, on every one. A greyed-out badge with no
                explanation is a puzzle, not encouragement. */}
            <span className="award-requires">{badge.requires}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
