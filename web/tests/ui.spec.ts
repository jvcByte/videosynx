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

/** The job's stored options, read straight from the row. */
function storedOptions(jobId: string): Record<string, unknown> {
  const out = execFileSync(
    "psql",
    [DSN, "-t", "-A", "-c", `select options from jobs where id = '${jobId}'`],
    { encoding: "utf8" },
  );
  return JSON.parse(out.trim());
}

async function jobIdFor(topic: string): Promise<string> {
  const response = await fetch(`${API}/jobs`, { headers: DEV_USER });
  const jobs = (await response.json()) as { id: string; topic: string }[];
  const job = jobs.find((j) => j.topic === topic);
  expect(job, `no job was created for ${topic}`).toBeTruthy();
  return job!.id;
}

test("a script can be pasted instead of a topic", async ({ page }) => {
  const heading = `Queues ${Date.now()}`;
  await page.goto("/");

  // Nothing typed in Topic: the script alone is enough to submit.
  await page.getByLabel("Script").fill(`# ${heading}\n\nScene 1. A request arrives.`);
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
  await page.getByRole("button", { name: "Generate" }).click();

  // Named from the script, by the same rule the API uses.
  const row = page.locator(`[data-testid="job"]:has-text("${heading}")`);
  await expect(row).toBeVisible();

  // And the text itself travelled with the job, rather than being a file path
  // the worker would have to be able to read.
  const options = storedOptions(await jobIdFor(heading));
  expect(options.script).toContain("A request arrives");
});

test("the mode chosen for one job is stored on it", async ({ page }) => {
  const topic = `modes ${Date.now()}`;
  await page.goto("/");

  await page.getByLabel("Topic").fill(topic);
  await page.getByRole("radio", { name: "Illustration" }).check();
  await page.getByRole("button", { name: "Generate" }).click();

  const row = page.locator(`[data-testid="job"]:has-text("${topic}")`);
  await expect(row).toBeVisible();
  // The library says what kind of video it is, not just what it is about.
  await expect(row.locator(".badge")).toHaveText("Illustration");
  expect(storedOptions(await jobIdFor(topic)).mode).toBe("illustration");
});

test("settings are saved and come back after a reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByLabel("Audience")).toBeVisible();

  await page.getByLabel("Audience").selectOption("expert");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByLabel("Audience")).toHaveValue("expert");
});

test("a script takes over from the topic and the mode", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Topic").fill("dictionary in python");
  await expect(page.getByLabel("Topic")).toBeEnabled();
  await expect(page.getByRole("radio", { name: "Illustration" })).toBeEnabled();

  await page.getByLabel("Script").fill("# When to Fine-Tune\n\nScene 1.");

  // Both stop applying, and the page says so rather than leaving two controls
  // that quietly do nothing -- which is how "Both" came to be selected on a
  // video that had no code in it.
  await expect(page.getByLabel("Topic")).toBeDisabled();
  await expect(page.getByRole("radio", { name: "Illustration" })).toBeDisabled();
  await expect(page.getByText("A script decides its own content")).toBeVisible();

  // Clearing the script gives them back, and the topic is still there.
  await page.getByLabel("Script").fill("");
  await expect(page.getByLabel("Topic")).toBeEnabled();
  await expect(page.getByLabel("Topic")).toHaveValue("dictionary in python");
});

test("the voice is chosen from a list, not typed", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();

  const voice = page.getByLabel("Voice");
  await expect(voice).toBeVisible();

  // A real catalogue, grouped by language.
  const options = await voice.locator("option").count();
  expect(options).toBeGreaterThan(20);
  await expect(voice.locator('optgroup[label="English (US)"]')).toHaveCount(1);

  await voice.selectOption("am_michael");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();

  // And it survives the round trip, which is the point of the dropdown.
  await page.reload();
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByLabel("Voice")).toHaveValue("am_michael");
});

test("the library does not claim to be empty while it is loading", async ({ page }) => {
  // Hold the first response open so the loading state is observable at all.
  // Without this the fetch resolves before the assertion could see it, which
  // is exactly why the bug it guards went unnoticed for so long.
  await page.route("**/jobs", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });

  await page.goto("/");
  await expect(page.locator(".job-skeleton").first()).toBeVisible();
  await expect(page.getByText("No videos yet")).toHaveCount(0);
});

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
  await expect(queued.locator(".status")).toHaveText("Ready");

  // 3. A finished video can be saved without opening it first.
  await expect(queued.getByRole("button", { name: "Download" })).toBeVisible();

  // 4. Playback: clicking through must produce a video element with a real
  //    source, which means the API issued a URL the browser can fetch.
  await queued.getByRole("button", { name: "Watch" }).click();
  const player = page.getByTestId("player");
  await expect(player).toBeVisible();

  const src = await player.getAttribute("src");
  expect(src, "the player has no source").toBeTruthy();

  // Resolved against the page, because the player is handed a same-origin path
  // now rather than an absolute URL -- a <video> element does that for itself,
  // but Node's fetch will not.
  const url = new URL(src!, page.url());

  // Still fetched with no headers, because that is all a <video> element can
  // send. A URL needing an Authorization header would be unplayable here,
  // which is the bug this caught the first time.
  const head = await fetch(url);
  expect(head.status).toBe(200);
  expect(head.headers.get("content-type")).toBe("video/mp4");

  // 5. And the player offers the same file as a download, named after the
  //    video rather than after its id.
  const download = page.getByRole("link", { name: "Download" });
  await expect(download).toBeVisible();
  await expect(download).toHaveAttribute("download", /\.mp4$/);
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
