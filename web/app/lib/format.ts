// Presentation helpers. Kept out of the components so the same value reads the
// same way wherever it appears.

const MODE_LABELS: Record<string, string> = {
  both: "Both",
  illustration: "Illustration",
  coding: "Code",
};

/** A mode's short name, for a badge. Unknown values are shown as they came. */
export function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode;
}

/**
 * Whether a script is already lesson JSON, which renders as written.
 *
 * Mirrors `is_structured_lesson` on the API, which is the authority: it is
 * what decides whether a submission is refused in local mode.
 */
export function isStructuredLesson(text: string): boolean {
  const stripped = text.trim();
  if (!stripped.startsWith("{")) return false;
  try {
    const data = JSON.parse(stripped) as { sections?: unknown };
    return typeof data === "object" && data !== null && Boolean(data.sections);
  } catch {
    return false;
  }
}

/**
 * A filename for a downloaded video, derived from its title.
 *
 * Everything that is not a letter or a digit becomes a dash, so a title with
 * a slash, a colon or a question mark in it cannot produce a path or a name
 * the filesystem refuses.
 */
export function videoFilename(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${slug || "pycoding"}.mp4`;
}

/** `4:07` for a video length. Minutes are worth showing, seconds alone are not. */
export function duration(seconds: number): string {
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes ? `${minutes}:${String(rest).padStart(2, "0")}` : `${rest}s`;
}

/**
 * When something happened, in the roughest unit that is still true.
 *
 * An absolute date is right for anything older than a week; "just now" is
 * right for a job that finished while you were looking at the page.
 */
/**
 * How long until something, in the roughest unit that is still true.
 *
 * The mirror of `relativeTime` and deliberately as vague: a retry due in four
 * minutes is "in 4m", and one due tomorrow is "in 18h". Nobody needs the
 * seconds, and a countdown ticking to the second would be a lie anyway — the
 * next poll is what will actually notice.
 */
export function timeUntil(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const seconds = Math.round((then - Date.now()) / 1000);
  if (seconds <= 45) return "any moment";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
