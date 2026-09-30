import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

const DSN = process.env.VIDEOSYNX_TEST_DSN ?? "postgresql:///videosynx_e2e";
const ARTIFACTS = process.env.VIDEOSYNX_ARTIFACTS ?? "/tmp/videosynx/e2e-artifacts";

/**
 * The one video this instance has published, for the landing page.
 *
 * The page renders its examples on the server now, so a browser test cannot
 * intercept them into existence -- the request is made before the browser
 * has one. This puts a real job and a real mp4 in place instead, which
 * exercises the whole path: the API finds it, ffmpeg takes a still from it,
 * and the page serves the still through /api.
 */
const PUBLISHED = "11111111-1111-4111-8111-111111111111";

/**
 * Start from an empty queue.
 *
 * The tests share one database, and one of them deliberately exhausts the
 * free allowance. Without this, run order would decide whether they pass.
 */
export default function globalSetup(): void {
  // Every table a test writes. A saved setting leaking into the next test is
  // the same failure as a leaked job: run order deciding the result.
  execFileSync(
    "psql",
    [DSN, "-q", "-c",
     "truncate jobs, subscriptions, credit_ledger, user_settings, feature_interest"],
    { stdio: "inherit" },
  );
  execFileSync("rm", ["-rf", ARTIFACTS]);

  // A real mp4, not the seeded stub: the still is taken with ffmpeg, and a
  // file it cannot open leaves the card with no picture.
  const key = path.join(ARTIFACTS, "artifacts", "free", `${PUBLISHED}.mp4`);
  mkdirSync(path.dirname(key), { recursive: true });
  execFileSync("ffmpeg", [
    "-loglevel", "error", "-f", "lavfi",
    // Longer than the running time the row claims: the still is taken from
    // 45% of the way in, and seeking past the end of a shorter file yields no
    // frame at all, which leaves the card with no picture.
    "-i", "color=c=black:s=320x180:r=10:d=130", "-pix_fmt", "yuv420p", "-y", key,
  ]);
  execFileSync("psql", [
    DSN, "-q", "-c",
    // Owned by nobody who takes tests: /stats counts every job regardless of
    // whose it is, but the daily allowance is per account -- and a published
    // example that spends one of e2e-user's three leaves the tests that
    // submit with nothing to submit against.
    `insert into jobs (id, user_id, topic, tier, status, options, artifact_key,
                       duration_s, progress, finished_at)
     values ('${PUBLISHED}', 'demo', 'A published tutorial', 'free',
             'succeeded', '{"mode": "both"}',
             'artifacts/free/${PUBLISHED}.mp4', 272, 1, now())`,
  ]);
}
