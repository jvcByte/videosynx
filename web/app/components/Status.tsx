import { JobStatus, isPending } from "../lib/api";

// Sentence case: these are read, not scanned as codes.
const LABELS: Record<string, string> = {
  queued: "Queued",
  leased: "Starting",
  generating: "Writing code",
  narrating: "Writing narration",
  executing: "Running the code",
  rendering: "Rendering",
  uploading: "Uploading",
  succeeded: "Ready",
  failed: "Failed",
  canceled: "Canceled",
};

/**
 * What a job is doing, as a word plus a dot.
 *
 * The dot is not decoration: it is the only thing that distinguishes "running"
 * from "finished" at a glance down the list, and colour alone would not say
 * which is which.
 */
export function Status({ status }: { status: JobStatus }) {
  // "Failed" and nothing else. The error *code* belongs in the note under the
  // card, where there is room to say what actually went wrong -- putting
  // "ProviderError" here reads as a status, which it is not.
  return (
    <span className={`status status-${status}`}>
      <span className="status-dot" aria-hidden />
      {LABELS[status] ?? status}
    </span>
  );
}

export function isRunning(status: JobStatus): boolean {
  return isPending(status);
}
