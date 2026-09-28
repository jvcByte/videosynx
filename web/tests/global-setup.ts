import { execFileSync } from "node:child_process";

const DSN = process.env.PYCODING_TEST_DSN ?? "postgresql:///pycoding_e2e";

/**
 * Start from an empty queue.
 *
 * The tests share one database, and one of them deliberately exhausts the
 * free allowance. Without this, run order would decide whether they pass.
 */
export default function globalSetup(): void {
  execFileSync("psql", [DSN, "-q", "-c", "truncate jobs, subscriptions, credit_ledger"], {
    stdio: "inherit",
  });
  execFileSync("rm", ["-rf", process.env.PYCODING_ARTIFACTS ?? "/tmp/pycoding/e2e-artifacts"]);
}
