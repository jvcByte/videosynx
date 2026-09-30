// The API client.
//
// Two ways to say who is calling. With Supabase configured the real session
// token is sent as a bearer token, and the API verifies it. Without it -- local
// development, and the browser tests -- an X-Dev-User header is sent instead,
// which the API honours only while no credential is configured on it either.
// The two must be configured consistently: a server with a credential set
// ignores the dev header, so a client sending one is simply unauthenticated.
//
// The token is held in a module variable rather than read from Supabase on
// each call. `authHeader` is spread into every request and has to stay
// synchronous; keeping a variable current is what `onAuthStateChange` in
// page.tsx is for.

// Same origin, forwarded to the API by a rewrite in next.config.ts.
//
// Deliberately not a NEXT_PUBLIC_* value: those are inlined into a client
// chunk at build time, which makes the API's address something a browser can
// cache and go stale on -- and there is nothing on the page that can clear it.
const API_BASE = "/api";

export type JobStatus =
  | "queued" | "leased" | "generating" | "narrating"
  | "executing" | "rendering" | "uploading"
  | "succeeded" | "failed" | "canceled";

export interface Job {
  id: string;
  status: JobStatus;
  topic: string;
  /** Which kind of video this is: coding, illustration, or both. */
  mode: string;
  stage: string | null;
  progress: number;
  duration_s: number | null;
  error_code: string | null;
  error_message: string | null;
  /** Attempts already spent. Above zero on a queued job means a retry. */
  attempt: number;
  max_attempts: number;
  /** When a requeued job becomes claimable again. */
  not_before: string | null;
  created_at: string | null;
}

/**
 * Two allowances, counted apart.
 *
 * `jobs_per_day` covers the jobs that call a model and cost money. Local
 * mode spends none, so it is not counted against that — it is what you reach
 * for once it is gone. It still renders on the server, so it has its own
 * limit rather than none.
 */
export interface Quota {
  tier: string;
  jobs_per_day: number;
  used_today: number;
  remaining: number;
  local_jobs_per_day: number;
  local_used_today: number;
  local_remaining: number;
  narration: boolean;
  tts: string;
  max_snippets: number;
}

/** Which halves of a tutorial to produce. The API owns these names. */
export type Mode = "coding" | "illustration" | "both";

/**
 * What a user chose, saved server-side so it follows them between machines.
 *
 * Deliberately does not include voice engine, resolution, watermark or the
 * snippet cap: those belong to the tier, and the server merges them over
 * anything sent from here.
 */
export interface Settings {
  mode: Mode;
  /** "local" promises never to call a model. */
  generation: "auto" | "local";
  style: "sketch" | "clean";
  audience: "beginner" | "intermediate" | "expert";
  focus: string;
  voice: string;
  captions: boolean;
  length: "short" | "medium" | "long";
}

/**
 * What the renderer produced, for when a video is not what you expected.
 *
 * The plan is made afresh each run and exists nowhere else, so this is the
 * only way to see which slide carried the wrong words. Older jobs predate it
 * and come back with a `note` instead of sections, which is a state to render
 * rather than an error.
 */
export interface PlanSection {
  kind: "explain" | "code";
  heading?: string;
  columns?: [string, string[]][];
  diagram?: { layout: string; nodes: { id: string; label: string }[] } | null;
  note?: string;
  code?: string;
  output?: string[];
  narration: { text: string; frames: number }[];
}

export interface RenderPlan {
  topic?: string;
  duration_s?: number;
  total_frames?: number;
  sections?: PlanSection[];
  note?: string;
}

/** The plan, the limits it grants, and the credit ledger. */
export interface Subscription {
  tier: string;
  jobs_per_day: number;
  tts: string;
  credits: number;
  recent: { delta: number; reason: string; at: string | null }[];
}

/** One narration voice the service offers. */
export interface Voice {
  id: string;
  language: string;
  gender: string;
}

export interface SubmitBody {
  /** What to teach. Optional when a script is given instead. */
  topic?: string;
  /** A written script to render, as text. */
  script?: string;
  /** One job can differ from the saved default without changing it. */
  mode?: Mode;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

let accessToken: string | null = null;

/** Called by the page whenever the session changes. */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

function authHeader(): Record<string, string> {
  if (accessToken) return { Authorization: `Bearer ${accessToken}` };
  const devUser = process.env.NEXT_PUBLIC_DEV_USER ?? "web-user:free";
  return { "X-Dev-User": devUser };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail: unknown;
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail;
      // FastAPI puts a string or an object in `detail`; both carry meaning.
      // A structured detail may carry a sentence written for a person as well
      // as a tag for the client to branch on. Show the sentence: a reader can
      // do nothing with "local_mode_needs_content".
      message =
        typeof body.detail === "string"
          ? body.detail
          : body.detail?.message ?? body.detail?.error ?? JSON.stringify(body.detail);
    } catch {
      /* a non-JSON error body is not worth failing over */
    }
    throw new ApiError(response.status, message, detail);
  }
  return response.json() as Promise<T>;
}

/** Absolute URL for a video, which is relative when the store is local. */
export function absolute(url: string): string {
  return url.startsWith("http") ? url : `${API_BASE}${url}`;
}

/** What the service has actually done. Public — no account needed. */
export interface Stats {
  tutorials_rendered: number;
  minutes_of_video: number;
  topics_covered: number;
}

export const api = {
  stats: () => request<Stats>("/stats"),
  quota: () => request<Quota>("/me/quota"),
  jobs: () => request<Job[]>("/jobs"),
  settings: () => request<Settings>("/me/settings"),
  voices: () => request<Voice[]>("/voices"),
  saveSettings: (settings: Settings) =>
    request<Settings>("/me/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  submit: (body: SubmitBody) =>
    request<{ id: string }>("/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  videoUrl: (id: string) => request<{ url: string; duration_s: number | null }>(
    `/jobs/${id}/video`
  ),
  /** Put a job that has given up back in the queue. Costs no allowance. */
  retry: (id: string) =>
    request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  /** Stop a job that has not finished. */
  cancel: (id: string) =>
    request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  /** The plan the renderer actually produced, or a note saying there isn't one. */
  plan: (id: string) => request<RenderPlan>(`/jobs/${id}/script`),
  subscription: () => request<Subscription>("/me/subscription"),
  /** A Stripe Checkout URL to open. 503 when billing is not configured. */
  checkout: () => request<{ url: string; id: string }>("/billing/checkout",
    { method: "POST" }),
};

export const PENDING: JobStatus[] = [
  "queued", "leased", "generating", "narrating", "executing", "rendering", "uploading",
];

export function isPending(status: JobStatus): boolean {
  return PENDING.includes(status);
}
