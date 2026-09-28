import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

const API = process.env.API_URL ?? "http://127.0.0.1:8021";
const DSN = process.env.PYCODING_TEST_DSN ?? "postgresql:///pycoding_e2e";
const ARTIFACTS = process.env.PYCODING_ARTIFACTS ?? "/tmp/pycoding/e2e-artifacts";
const REPO = path.resolve(__dirname, "../..");
const PYTHON = process.env.PYCODING_PYTHON ?? path.resolve(REPO, ".venv/bin/python");

const DEV_USER = { "X-Dev-User": "e2e-user:free" };

/** Stand in for the worker's last step: upload, then mark the row. */
function seed(jobId: string): void {
  execFileSync(PYTHON, [path.resolve(REPO, "web/tests/helpers/seed.py"), DSN, ARTIFACTS, jobId], {
    cwd: REPO,
    env: { ...process.env, PYTHONPATH: `${REPO}/engine/src:${REPO}/worker/src` },
  });
}

async function submit(topic: string): Promise<string> {
  const response = await fetch(`${API}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...DEV_USER },
    body: JSON.stringify({ topic }),
  });
  // Read the body once: consuming it for the assertion message leaves
  // nothing for the parse.
  const body = await response.text();
  expect(response.status, body).toBe(202);
  return JSON.parse(body).id;
}

test("submit, poll, and play a video", async ({ page }) => {
  const topic = `decorators ${Date.now()}`;
  const jobId = await submit(topic);

  // 1. The job reaches the page as soon as it exists, before it is rendered.
  await page.goto("/");
  const queued = page.locator(`[data-testid="job"]:has-text("${topic}")`);
  await expect(queued).toBeVisible();

  // 2. The worker finishes; the page must find that out on its own. Nothing
  //    here reloads -- the list polls while a job is pending.
  seed(jobId);
  await expect(queued).toHaveAttribute("data-status", "succeeded", {
    timeout: 15_000,
  });
  await expect(queued.locator(".status")).toHaveText("ready");

  // 3. Playback: clicking through must produce a video element with a real
  //    source, which means the API issued a URL the browser can fetch.
  await queued.getByRole("button", { name: "Watch" }).click();
  const player = page.getByTestId("player");
  await expect(player).toBeVisible();

  const src = await player.getAttribute("src");
  expect(src, "the player has no source").toBeTruthy();

  // Fetched with no headers, because that is all a <video> element can send.
  // A URL that needs an Authorization header would be unplayable here, which
  // is exactly the bug this caught.
  const head = await fetch(src!);
  expect(head.status).toBe(200);
  expect(head.headers.get("content-type")).toBe("video/mp4");
});

test("the quota is shown and a bad topic is refused", async ({ page }) => {
  await page.goto("/");

  // The allowance is visible before spending anything.
  await expect(page.locator(".quota")).toContainText("left today");

  // The button stays disabled until there is something to submit, so the
  // obvious mistake cannot be made.
  await expect(page.getByRole("button", { name: "Generate" })).toBeDisabled();

  await page.getByLabel("Topic").fill("a");
  // Too short for the API's own validation, and the browser should not let it
  // through either.
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
});

test("a rejected submission explains itself", async ({ page }) => {
  // Exhaust the free allowance so the next submit is refused.
  const headers = { "Content-Type": "application/json", ...DEV_USER };
  const quota = await (await fetch(`${API}/me/quota`, { headers: DEV_USER })).json();
  for (let i = quota.used_today; i < quota.jobs_per_day; i++) {
    await fetch(`${API}/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ topic: `filler number ${i}` }),
    });
  }

  await page.goto("/");
  await page.getByLabel("Topic").fill("one too many topics");
  await page.getByRole("button", { name: "Generate" }).click();

  // The server's own words, not a generic failure.
  const alert = page.getByTestId("error");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("quota_exceeded");
});
