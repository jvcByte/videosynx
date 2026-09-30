import { execFileSync } from "node:child_process";

const DSN = process.env.VIDEOSYNX_TEST_DSN ?? "postgresql:///videosynx_e2e";

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
  execFileSync("rm", ["-rf", process.env.VIDEOSYNX_ARTIFACTS ?? "/tmp/videosynx/e2e-artifacts"]);
}
