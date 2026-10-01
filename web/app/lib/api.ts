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
  /**
   * What this job makes: "video" or "speech".
   *
   * A clip and a tutorial are the same row in the same queue -- they lease,
   * heartbeat and get cancelled the same way -- so the list has to be able to
   * tell them apart without unpacking the options blob.
   */
  kind: string;
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
  /**
   * Whether a finished video is still there to play.
   *
   * Null for anything that has not finished — there is no file to be
   * missing. False means the row keeps its place but stops offering Watch
   * and Download, because the store is a temporary directory by default and
   * a bucket can be emptied.
   */
  available?: boolean | null;
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
  /** How fast the narration reads: about 154, 178 or 192 words a minute. */
  pace: "calm" | "normal" | "fast";
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

/** The plan, the limits it grants, what this account has made, and the ledger. */
export interface Subscription {
  tier: string;
  jobs_per_day: number;
  tts: string;
  credits: number;
  /** Counted server-side: the job list is capped, so counting it is wrong. */
  tutorials_created: number;
  tutorials_completed: number;
  minutes_of_video: number;
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
  if (url.startsWith("http")) return url;
  // Already through the rewrite. Without this a path that has been prefixed
  // once becomes /api/api/... -- the API returns /artifacts/... today, so
  // this is a trap rather than a live fault, and the cheapest kind to close.
  if (url === API_BASE || url.startsWith(`${API_BASE}/`)) return url;
  return `${API_BASE}${url}`;
}

/**
 * Something not built yet, and who is waiting for it.
 *
 * `waiting` is null rather than 0 when nobody is: "0 waiting" is a true
 * measurement and a reason to let someone else go first, which is not the
 * sentence to put under a feature you want people to ask for.
 */
export interface Feature {
  id: string;
  title: string;
  blurb: string;
  /** What stands in the way. A promise with no obstacle named is uncosted. */
  blocker: string;
  waiting: number | null;
  joined: boolean;
}

/**
 * One plan, as the landing page advertises it and the API enforces it.
 *
 * `id` is the tier's database name, not a label — `tierLabel` turns it into
 * the one a visitor reads.
 */
export interface Plan {
  id: string;
  price_usd: number;
  tagline: string;
  features: string[];
  jobs_per_day: number;
  resolution: number[];
  watermark: boolean;
  tts: string;
}

/** A pack of credits, sold on top of a plan. */
export interface CreditPack {
  credits: number;
  usd: number;
  note: string;
}

export interface Plans {
  currency: string;
  plans: Plan[];
  credit_packs: CreditPack[];
  /** What a minute of video costs once the day's allowance is spent. */
  credits_per_minute: number;
}

/** What the service has actually done. Public — no account needed. */
export interface Stats {
  tutorials_rendered: number;
  minutes_of_video: number;
  topics_covered: number;
}

/**
 * What the account has earned, all of it computed from jobs that finished.
 *
 * There is no counter behind this that anything can increment: XP is the work
 * multiplied out, the streak is days that actually had a submission, and a
 * badge is a fact about the history.
 */
export interface Progress {
  xp: number;
  level: number;
  xp_into_level: number;
  xp_for_level: number;
  streak: number;
  achievements: Achievement[];
}

export interface Achievement {
  id: string;
  name: string;
  requires: string;
  earned: boolean;
  earned_on: string | null;
}

/** A tutorial the operator has published. Public, like the page it is on. */
export interface PublishedExample {
  id: string;
  title: string;
  duration_s: number | null;
  mode: string;
  poster_url: string | null;
  video_url: string;
}

/**
 * The API, from the server.
 *
 * A relative URL cannot be fetched from a server component -- there is no
 * origin to resolve it against -- so this reaches the API where the rewrite
 * would have. Public endpoints only: nothing here carries a token, and the
 * landing page is the only thing that uses it.
 */
export async function serverGet<T>(
  path: string,
  { cache = "revalidate" }: { cache?: "revalidate" | "fresh" } = {}
): Promise<T | null> {
  const base = process.env.VIDEOSYNX_API_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${base}${path}`, {
      // A response carrying signed URLs must never be cached. The signature
      // is time-limited, so a cached one is a dead link waiting to be served
      // -- and it is minted with a per-process secret, so an API restart
      // invalidates every one of them at once. That is a 403 on a still, for
      // as long as the cache lives, which is exactly what it looked like.
      ...(cache === "fresh"
        ? { cache: "no-store" as const }
        : { next: { revalidate: 60 } }),
    });
    return response.ok ? ((await response.json()) as T) : null;
  } catch {
    // A landing page that cannot reach its own API still shows the product.
    return null;
  }
}

export const api = {
  stats: () => request<Stats>("/stats"),
  examples: () => request<PublishedExample[]>("/examples"),
  progress: () => request<Progress>("/me/progress"),
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
  /** What is planned, what is in the way of each, and who is waiting. */
  features: () => request<Feature[]>("/features"),
  /** Join the waitlist for one. Joining twice is still one person. */
  joinFeature: (id: string) =>
    request<Feature>(`/features/${id}/interest`, { method: "POST" }),
  /** What it costs. Public — the landing page renders it. */
  plans: () => request<Plans>("/plans"),
  /** Buy a pack of credits. Returns a Stripe URL to open. */
  buyCredits: (credits: number) =>
    request<{ url: string; id: string }>("/billing/credits", {
      method: "POST",
      body: JSON.stringify({ credits }),
    }),
  /** Queue text to be read aloud. Comes back as a job, like a video. */
  speak: (text: string, voice?: string) =>
    request<{ id: string }>("/speech", {
      method: "POST",
      body: JSON.stringify(voice ? { text, voice } : { text }),
    }),
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
