import { JobStatus, isPending } from "../lib/api";

// Sentence case: these are read, not scanned as codes.
const LABELS: Record<string, string> = {
  queued: "Queued",
  leased: "Starting",
  succeeded: "Ready",
  failed: "Failed",
  canceled: "Canceled",
};

/**
 * What a job is doing, from the stage the worker last reported.
 *
 * The status cannot say this. `job_status` has values for the middle of a
 * render -- generating, narrating, executing, rendering, uploading -- and
 * nothing has ever written one: the worker goes straight from `leased` to
 * `succeeded`, so a four-minute render read as "Starting" throughout. What it
 * does write, every time it reaches a boundary, is `stage`; the labels below
 * are that vocabulary.
 *
 * They are coarser than the enum names, and honestly so. Building a lesson is
 * one engine call that writes the code, narrates it and runs it, and the
 * worker cannot see between those without the engine telling it -- so it says
 * "writing the lesson" rather than claiming a step it does not know it is on.
 */
const STAGE_LABELS: Record<string, string> = {
  starting: "Starting",
  building: "Writing the lesson",
  speaking: "Reading it aloud",
  rendering: "Rendering",
  uploading: "Uploading",
  done: "Finishing",
};

/**
 * What a job is doing, as a word plus a dot.
 *
 * The dot is not decoration: it is the only thing that distinguishes "running"
 * from "finished" at a glance down the list, and colour alone would not say
 * which is which.
 */
export function Status({
  status,
  stage,
  attempt = 0,
}: {
  status: JobStatus;
  /** What the worker last said, or null before it has said anything. */
  stage?: string | null;
  /** Attempts already spent. Above zero on a queued job means a retry. */
  attempt?: number;
}) {
  // "Failed" and nothing else. The error *code* belongs in the note under the
  // card, where there is room to say what actually went wrong -- putting
  // "ProviderError" here reads as a status, which it is not.
  //
  // A queued job that has already been tried once is not queued in the sense
  // the word usually means: it failed, it is coming back, and calling that
  // "Queued" hides a failure the user would otherwise have seen. "Retrying"
  // is the honest word and needs no new database status to say.
  const retrying = status === "queued" && attempt > 0;

  // Only while it is running. A finished job keeps the stage it ended on, and
  // "Uploading" is not what a watchable video is doing.
  const doing = !retrying && isPending(status) && stage
    ? STAGE_LABELS[stage]
    : undefined;

  return (
    <span className={`status status-${retrying ? "retrying" : status}`}>
      <span className="status-dot" aria-hidden />
      {retrying ? "Retrying" : doing ?? LABELS[status] ?? status}
    </span>
  );
}

export function isRunning(status: JobStatus): boolean {
  return isPending(status);
}
