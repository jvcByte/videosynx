// The API client.
//
// In development the API accepts an X-Dev-User header, but only while no JWT
// secret is configured on the server. A deployment verifies Supabase tokens
// instead, at which point this header is ignored and `authHeader` should
// return a real bearer token.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type JobStatus =
  | "queued" | "leased" | "generating" | "narrating"
  | "executing" | "rendering" | "uploading"
  | "succeeded" | "failed" | "canceled";

export interface Job {
  id: string;
  status: JobStatus;
  topic: string;
  stage: string | null;
  progress: number;
  duration_s: number | null;
  error_code: string | null;
  error_message: string | null;
  attempt: number;
  created_at: string | null;
}

export interface Quota {
  tier: string;
  jobs_per_day: number;
  used_today: number;
  remaining: number;
  narration: boolean;
  tts: string;
  max_snippets: number;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

function authHeader(): Record<string, string> {
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
      message =
        typeof body.detail === "string"
          ? body.detail
          : body.detail?.error ?? JSON.stringify(body.detail);
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

export const api = {
  quota: () => request<Quota>("/me/quota"),
  jobs: () => request<Job[]>("/jobs"),
  submit: (topic: string) =>
    request<{ id: string }>("/jobs", {
      method: "POST",
      body: JSON.stringify({ topic }),
    }),
  videoUrl: (id: string) => request<{ url: string; duration_s: number | null }>(
    `/jobs/${id}/video`
  ),
};

export const PENDING: JobStatus[] = [
  "queued", "leased", "generating", "narrating", "executing", "rendering", "uploading",
];

export function isPending(status: JobStatus): boolean {
  return PENDING.includes(status);
}
