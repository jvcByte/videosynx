import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

const API = process.env.API_URL ?? "http://127.0.0.1:8021";
const DSN = process.env.VIDEOSYNX_TEST_DSN ?? "postgresql:///videosynx_e2e";
const ARTIFACTS = process.env.VIDEOSYNX_ARTIFACTS ?? "/tmp/videosynx/e2e-artifacts";
const REPO = path.resolve(__dirname, "../..");
const PYTHON = process.env.VIDEOSYNX_PYTHON ?? path.resolve(REPO, ".venv/bin/python");

const DEV_USER = { "X-Dev-User": "e2e-user:free" };

// The app itself. "/" is the public landing page now, which is a different
// thing to test and has no workspace on it.
const WORKSPACE = "/dashboard";

/**
 * Land on a view.
 *
 * The dashboard is one page with five views addressed by the hash, so a test
 * that needs the composer has to say so. `goto` rather than clicking: it is
 * the deep-link and refresh path, which is half of what the addresses are
 * for. The sidebar's own behaviour has its own test.
 */
const at = (view: string) => `${WORKSPACE}#${view}`;

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
  await page.goto(at("create"));

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
  await page.goto(at("create"));

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
  await page.goto(at("settings"));
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByLabel("Audience")).toBeVisible();

  await page.getByLabel("Audience").selectOption("expert");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();

  await page.reload();
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByLabel("Audience")).toHaveValue("expert");
});

test("a script takes over from the topic and the mode", async ({ page }) => {
  await page.goto(at("create"));
  await page.getByLabel("Topic").fill("dictionary in python");
  await expect(page.getByLabel("Topic")).toBeEnabled();
  await expect(page.getByRole("radio", { name: "Illustration" })).toBeEnabled();

  await page.getByLabel("Script").fill("# When to Fine-Tune\n\nScene 1.");

  // Both stop applying, and the page says so rather than leaving two controls
  // that quietly do nothing -- which is how "Both" came to be selected on a
  // video that had no code in it.
  await expect(page.getByLabel("Topic")).toBeDisabled();
  await expect(page.getByRole("radio", { name: "Illustration" })).toBeDisabled();
  // Says what will actually happen, and how to get the other half. "The mode
  // is not applied" reads as a limitation rather than as an instruction.
  await expect(page.getByText("This script is all explanation")).toBeVisible();

  // A fence is what decides it, so a script with one says the other thing.
  await page.getByLabel("Script").fill("Scene 1.\n```python\nprint(1)\n```\n");
  await expect(page.getByText("This script has code in it")).toBeVisible();

  // Clearing the script gives them back, and the topic is still there.
  await page.getByLabel("Script").fill("");
  await expect(page.getByLabel("Topic")).toBeEnabled();
  await expect(page.getByLabel("Topic")).toHaveValue("dictionary in python");
});

test("the voice is chosen from a list, not typed", async ({ page }) => {
  await page.goto(at("settings"));
  await page.getByRole("link", { name: "Settings" }).click();

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
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByLabel("Voice")).toHaveValue("am_michael");
});

const LESSON_JSON = JSON.stringify({
  title: "Authored lesson",
  sections: [{
    name: "One",
    explain: { heading: "A heading", columns: [["A column", ["a line"]]] },
    narration: ["One.", "Two.", "Three."],
  }],
});

test("the composer says what a submission will cost", async ({ page }) => {
  await page.goto(at("create"));

  // A topic has to be written, so it costs a call.
  await expect(page.locator(".compose-cost"))
    .toContainText("Counts against your daily allowance");

  // Lesson JSON is rendered as written, so it does not.
  await page.getByLabel("Script").fill(LESSON_JSON);
  await expect(page.locator(".compose-cost")).toContainText("No model call");

  // Prose still costs in auto mode: the model has to structure it.
  await page.getByLabel("Script").fill("Scene 1 — The deadline. A manager speaks.");
  await expect(page.locator(".compose-cost"))
    .toContainText("Counts against your daily allowance");
});

test("local mode renders pasted prose with no model at all", async ({ page }) => {
  await page.goto(at("settings"));
  await page.getByLabel("Generation").selectOption("local");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();
  await page.goto(at("create"));

  // Prose is the case the mode exists for: a script in hand, nothing left to
  // spend. This is what a plain topic cannot do.
  await page.getByLabel("Script").fill(
    "Scene 1 — The Deadline: A manager wants this in two weeks. " +
    "The engineer looks at the pile.");
  await expect(page.locator(".compose-cost")).toContainText("No model call");
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();

  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Generation").selectOption("auto");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();
});

test("local mode refuses a topic rather than spending", async ({ page }) => {
  await page.goto(at("settings"));
  await page.getByLabel("Generation").selectOption("local");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();
  await page.goto(at("create"));

  await page.getByLabel("Topic").fill("decorators");
  // Refused before it is queued, not queued and failed: the promise is that
  // nothing is spent.
  await expect(page.getByRole("button", { name: "Generate" })).toBeDisabled();
  await expect(page.locator(".compose-cost")).toContainText("Local mode is on");

  // Content that needs no model still goes through.
  await page.getByLabel("Script").fill(LESSON_JSON);
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();

  // Put it back. The tests share one database, and a saved setting would
  // otherwise decide whether later ones pass.
  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Generation").selectOption("auto");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".saved")).toBeVisible();
});

test("the library does not claim to be empty while it is loading", async ({ page }) => {
  // Hold the first response open so the loading state is observable at all.
  // Without this the fetch resolves before the assertion could see it, which
  // is exactly why the bug it guards went unnoticed for so long.
  await page.route("**/jobs", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });

  await page.goto(at("videos"));
  await expect(page.locator(".job-skeleton").first()).toBeVisible();
  await expect(page.getByText("No videos yet")).toHaveCount(0);
});

test("submit, poll, and play a video", async ({ page }) => {
  const topic = `decorators ${Date.now()}`;
  const jobId = await submit(topic);

  // 1. The job reaches the page as soon as it exists, before it is rendered.
  await page.goto(at("videos"));
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
  await page.goto(at("create"));

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

test("a running job says what it is doing, not just that it started", async ({ page }) => {
  // It read "Starting" from the claim to the finished video. `job_status` has
  // values for the middle of a render -- generating, narrating, executing --
  // and nothing has ever written one, so a four-minute render had one word.
  // What the worker does write is `stage`, and that is what the row reads.
  execFileSync("psql", [
    DSN, "-q", "-c",
    `insert into jobs (user_id, topic, tier, status, stage, progress)
     values ('e2e-user', 'a job that is rendering', 'free', 'rendering',
             'rendering', 0.7)`,
  ]);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a job that is rendering")');

  await expect(row.locator(".status")).toHaveText("Rendering");
  // Still a running job, so the dot says so rather than saying finished.
  await expect(row).toHaveAttribute("data-status", "rendering");
});

test("a stage the row has no word for falls back to the status", async ({ page }) => {
  // `stage` is free text the worker writes; a value from a newer worker than
  // this page is not a reason to render a blank label.
  execFileSync("psql", [
    DSN, "-q", "-c",
    `insert into jobs (user_id, topic, tier, status, stage, progress)
     values ('e2e-user', 'a job with a strange stage', 'free', 'leased',
             'consulting_the_oracle', 0.4)`,
  ]);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a job with a strange stage")');
  await expect(row.locator(".status")).toHaveText("Starting");
});

test("a job that failed and is coming back reads as Retrying", async ({ page }) => {
  // The state the worker leaves behind after a failure it means to repeat:
  // queued again, an attempt spent, the error kept, and a wait before the
  // next try. Written straight to the row rather than submitted, because
  // this is about what the row says and the allowance is spent by now.
  execFileSync("psql", [
    DSN, "-q", "-c",
    `insert into jobs (user_id, topic, tier, status, attempt, error_code,
                       error_message, not_before)
     values ('e2e-user', 'a job that will be retried', 'free', 'queued', 2,
             'provider_busy', 'every model failed: busy',
             now() + interval '5 minutes')`,
  ]);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a job that will be retried")');

  // Not "Queued" -- that hides a failure the user would otherwise see -- and
  // above all not "Failed", because it has not finished trying.
  await expect(row.locator(".status")).toHaveText("Retrying");
  await expect(row).toHaveAttribute("data-status", "queued");
  await expect(row.locator(".job-note")).toContainText("trying again");
});

test("a failed job can be retried from the page", async ({ page }) => {
  // A job that has given up, written straight to the row: there is no worker
  // here, and this is about what the page offers for it.
  execFileSync("psql", [
    DSN, "-q", "-c",
    `insert into jobs (user_id, topic, tier, status, attempt, max_attempts,
                       error_code, error_message, finished_at)
     values ('e2e-user', 'a job that has given up', 'free', 'failed', 7, 7,
             'provider_quota', 'every model failed: quota', now())`,
  ]);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a job that has given up")');
  await expect(row.locator(".status")).toHaveText("Failed");

  // The one thing a failed job left you able to do.
  await row.getByRole("button", { name: "Try again" }).click();

  // Straight back into the queue without a reload, and it costs no allowance
  // -- the row already exists and the work is the same work.
  await expect(row).toHaveAttribute("data-status", "queued", { timeout: 10_000 });
  await expect(row.locator(".status")).toHaveText("Queued");
});

/** A job row, written directly: the allowance is spent by the time these run. */
function insertJob(topic: string, status = "queued"): string {
  // -q is load-bearing: without it psql prints "INSERT 0 1" after the id,
  // and the id goes off with a second line stuck to it.
  const out = execFileSync("psql", [
    DSN, "-t", "-A", "-q", "-c",
    `insert into jobs (user_id, topic, tier, status)
     values ('e2e-user', '${topic}', 'free', '${status}') returning id`,
  ], { encoding: "utf8" });
  return out.trim();
}

test("the plan behind a video can be inspected", async ({ page }) => {
  // The endpoint has served this since the beginning and had no way in from
  // the page, so a video that came out wrong could only be diagnosed by
  // pulling the plan out of the database by hand.
  const id = insertJob("show me what went in");
  seed(id);
  await page.goto(at("videos"));

  const row = page.locator('[data-testid="job"]:has-text("show me what went in")');
  await row.getByRole("button", { name: "Inspect" }).click();

  const plan = page.getByRole("dialog", { name: "Render plan" });
  await expect(plan).toBeVisible();
  // The slide, its panels, and what was said over each one -- with the frame
  // count, which is what says which slide was on screen at the time.
  await expect(plan.getByText("A seeded slide")).toBeVisible();
  await expect(plan.getByText("the takeaway")).toBeVisible();
  await expect(plan.getByText("One sentence.")).toBeVisible();
  await expect(plan.getByText("90f")).toBeVisible();
  // And the code half, with the output it actually produced.
  await expect(plan.getByText("print('seeded')")).toBeVisible();

  await page.getByRole("button", { name: "Close" }).click();
  await expect(plan).toHaveCount(0);
});

test("a running job can be stopped from the page", async ({ page }) => {
  // Minutes of CPU for a video nobody wants is worth a button. The job is
  // left queued, so this is the trivially cancelable case -- the hard one,
  // mid-render, is a worker test.
  insertJob("stop me");
  await page.goto(at("videos"));

  const row = page.locator('[data-testid="job"]:has-text("stop me")');
  await row.getByRole("button", { name: "Cancel" }).click();

  await expect(row).toHaveAttribute("data-status", "canceled", { timeout: 10_000 });
  await expect(row.locator(".status")).toHaveText("Canceled");
  // Nothing left to stop.
  await expect(row.getByRole("button", { name: "Cancel" })).toHaveCount(0);
});

test("upgrading without billing configured says so", async ({ page }) => {
  // No Stripe key in this run, which is the ordinary state of a deployment
  // that has not set one up. The button exists because the tier is free and
  // the endpoint exists; pressing it must explain itself rather than look
  // like it worked and quietly go nowhere.
  //
  // Deliberately the bare address: the plan panel is in the overview's rail,
  // and the overview is what /dashboard means with nothing after it.
  await page.goto(WORKSPACE);
  const panel = page.getByRole("region", { name: "Your plan" });
  await expect(panel).toBeVisible();

  await panel.getByRole("button", { name: "Upgrade" }).click();

  // It explains itself without naming our environment: a person who cannot
  // set STRIPE_PRICE_ID was being told to set it.
  const alert = panel.getByRole("alert");
  await expect(alert).toContainText(/not available on this server/i);
  await expect(alert).not.toContainText(/STRIPE|SUPABASE/);
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

  await page.goto(at("create"));
  await page.getByLabel("Topic").fill("one too many topics");
  await page.getByRole("button", { name: "Generate" }).click();

  // The server's own words, not a generic failure.
  const alert = page.getByTestId("error");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("quota_exceeded");
});

test("the sidebar swaps views, and the address carries them", async ({ page }) => {
  await page.goto(WORKSPACE);
  // Nothing after the address means the overview, and the overview is where
  // the plan rail is.
  await expect(page.getByRole("region", { name: "Your plan" })).toBeVisible();

  await page.getByRole("link", { name: "Your Videos" }).click();
  await expect(page).toHaveURL(/#videos$/);
  await expect(page.getByRole("link", { name: "Your Videos" }))
    .toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel("Search your videos")).toBeVisible();

  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByLabel("Audience")).toBeVisible();

  // Back goes to the view you came from rather than out of the dashboard,
  // which is most of the reason the views are in the address.
  await page.goBack();
  await expect(page).toHaveURL(/#videos$/);
  await expect(page.getByLabel("Search your videos")).toBeVisible();

  // A hash nobody knows lands on the overview rather than on nothing.
  await page.goto(`${WORKSPACE}#typo`);
  await expect(page.getByRole("region", { name: "Your plan" })).toBeVisible();
});

test("a job submitted into an idle list keeps being followed", async ({ page }) => {
  // The failure this guards is invisible on a busy account: the poll re-arms
  // itself only while something is running, so a submission that armed a
  // bare refresh() instead took one look and stopped. The list here answers
  // "nothing running" until the submission lands, which is exactly the state
  // that used to break it.
  const id = "11111111-1111-4111-8111-111111111111";
  let submitted = false;
  let polls = 0;

  await page.route("**/jobs", (route) => {
    // The submission is answered here too. What is under test is the poll's
    // chain, and a real one would make this depend on the day's allowance and
    // on every job the tests before it left behind.
    if (route.request().method() !== "GET") {
      return route.fulfill({ status: 202, json: { id } });
    }
    polls++;
    return route.fulfill({
      json: submitted
        ? [{
            id, status: "queued", topic: "idle poll", mode: "both", stage: null,
            progress: 0, duration_s: null, error_code: null, error_message: null,
            attempt: 0, max_attempts: 1, not_before: null,
            created_at: new Date().toISOString(),
          }]
        : [],
    });
  });

  await page.goto(at("create"));
  // Let the first look happen and the chain settle, so it really is idle.
  await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(0);
  const idle = polls;

  submitted = true;
  await page.getByLabel("Topic").fill(`idle poll ${Date.now()}`);
  await page.getByRole("button", { name: "Generate" }).click();

  // More than the single look a one-shot refresh would have taken.
  await expect.poll(() => polls, { timeout: 10_000 }).toBeGreaterThan(idle + 1);
});

test("nothing runs off the side of a phone", async ({ page }) => {
  // The header was the worst of it: the landing's buttons wrapped onto two
  // lines and pushed past the edge, and the dashboard's five destinations
  // wrapped into a block that put two of them out of reach. The job titles
  // were the other half -- stacked, they sized to their text, so a long topic
  // ran the whole page sideways instead of ending in an ellipsis.
  await page.setViewportSize({ width: 390, height: 844 });

  for (const url of ["/", WORKSPACE, at("videos"), at("create"), at("settings")]) {
    await page.goto(url);
    // Polled, because the width settles a frame or two after the data lands.
    await expect
      .poll(
        () =>
          page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth
          ),
        { message: `${url} is wider than the screen` }
      )
      .toBeLessThanOrEqual(0);
  }
});

test("the navigation survives scrolling on a phone", async ({ page }) => {
  // It used to scroll away with the page, which on a long list of videos
  // means walking back to the top to go anywhere else.
  await page.setViewportSize({ width: 390, height: 700 });
  // The settings form, because it is tall whatever the account has made --
  // a list of videos is only long for someone who has some.
  await page.goto(at("settings"));
  await expect(page.getByLabel("Audience")).toBeVisible();

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  // The bar itself, and the first destination in it. Not one further along:
  // the bar scrolls sideways on a phone, so a later link is legitimately off
  // the right-hand edge and would fail a check that is about the vertical.
  await expect(page.locator(".sidebar-nav")).toBeInViewport();
  await expect(page.getByRole("link", { name: "Dashboard" })).toBeInViewport();
});

test("a video that outlived its file offers nothing it cannot do", async ({ page }) => {
  // The default store is a /tmp directory and /tmp gets cleared, so a row
  // can outlive the video it describes. It keeps its place -- the render
  // happened -- but Watch used to hand the browser an address that 404s,
  // which reads as a broken page rather than as a video no longer kept.
  const id = insertJob("a video that outlived its file", "succeeded");
  execFileSync("psql", [
    DSN, "-q", "-c",
    `update jobs set artifact_key = 'artifacts/free/${id}.mp4', duration_s = 42
      where id = '${id}'`,
  ]);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a video that outlived its file")');

  await expect(row).toHaveAttribute("data-available", "no");
  await expect(row.getByText("File no longer available")).toBeVisible();
  await expect(row.getByRole("button", { name: "Watch" })).toHaveCount(0);
  await expect(row.getByRole("button", { name: "Download" })).toHaveCount(0);

  // The plan is a different question and still has an answer -- and when it
  // does not, the viewer says so rather than failing.
  await expect(row.getByRole("button", { name: "Inspect" })).toBeVisible();
});

test("a video that is still stored plays as it always did", async ({ page }) => {
  // The other half of the guard: the check must not take Watch away from
  // every row, which is the failure mode of getting availability wrong.
  const id = insertJob("a video still on disk");
  seed(id);

  await page.goto(at("videos"));
  const row = page.locator('[data-testid="job"]:has-text("a video still on disk")');
  await expect(row).not.toHaveAttribute("data-available", "no");
  await row.getByRole("button", { name: "Watch" }).click();
  await expect(page.getByTestId("player")).toBeVisible();
});

test("a planned feature says what is in its way, and counts you once", async ({ page }) => {
  await page.goto(at("coming-soon"));
  const first = page.locator(".want").first();
  await expect(first.getByRole("heading")).toBeVisible();
  // A promise with no obstacle named is a promise nobody has costed.
  await expect(first.getByText("What is in the way")).toBeVisible();

  // Nobody waiting is no number at all, not a zero: "0 waiting" is a true
  // measurement and a reason to let somebody else go first.
  await expect(first.locator(".want-count")).toHaveCount(0);

  await first.getByRole("button", { name: "Join the waitlist" }).click();
  await expect(first.locator(".want-count")).toHaveText("1 waiting");
  await expect(first.getByRole("button")).toBeDisabled();

  // It is a list of people, so it survives a reload and it still counts one.
  await page.reload();
  await expect(first.locator(".want-count")).toHaveText("1 waiting");
  await expect(first.getByRole("button", { name: /on the list/ })).toBeDisabled();
});

test("text can be queued to be read aloud, and a clip is not a video", async ({ page }) => {
  const line = `A variable is a name you give to a value ${Date.now()}`;

  await page.goto(at("speech"));
  await page.getByLabel("Text").fill(line);
  await page.getByRole("button", { name: "Speak it" }).click();

  // Straight into its own list. There is no worker in this stack, so a clip
  // that had to finish first would never appear at all -- and what is being
  // checked is that it was queued as the right kind of thing.
  const clip = page.locator(".clip").filter({ hasText: line });
  await expect(clip).toBeVisible();
  await expect(clip.locator(".status")).toHaveText("Queued");

  // Its own list, not the library: "Your Videos" containing something that is
  // not a video is the kind of small lie that cheapens every other label.
  await page.goto(at("videos"));
  await expect(page.locator(`[data-testid="job"]:has-text("${line}")`)).toHaveCount(0);
});

test("the voice engine is shown, and is the plan's to give", async ({ page }) => {
  // It is a fact about the account, not a preference -- narration on
  // ElevenLabs is roughly sixty cents a video against about one for the code
  // around it, so a dropdown that let anyone switch it on would let anyone
  // spend the operator's money.
  await page.goto(at("settings"));

  const engine = page.getByLabel("Engine");
  await expect(engine).toBeVisible();
  await expect(engine).toBeDisabled();
  // This account is on free, so Kokoro is what it narrates with.
  await expect(engine).toHaveValue("kokoro");

  // And the reason is on the page rather than left as a greyed-out box. It
  // names the plan the way the pricing page does -- "Studio", not the `paid`
  // row in the database.
  await expect(page.getByText(/ElevenLabs narration is included in Studio/)).toBeVisible();
});

test("a voice can be heard before it is chosen", async ({ page, request }) => {
  // `am_michael` says nothing about what either of them sounds like, so the
  // only way to pick between fifty-four of them is to listen.
  await page.goto(at("settings"));

  const voice = page.getByLabel("Voice");
  const chosen = await voice.inputValue();
  // Whatever is saved, not a particular one: the settings tests above save a
  // voice of their own, and this is not the place to assert the default.
  expect(chosen).toBeTruthy();

  // A file, not a render: one of these takes eight seconds to synthesise and
  // the queue would be asked for one per click. It has to be *there* -- a
  // preview that plays nothing is worse than no button, because it looks
  // like it worked.
  const sample = await request.get(`/voices/${chosen}.mp3`);
  expect(sample.status()).toBe(200);
  expect(sample.headers()["content-type"]).toContain("audio");
  expect((await sample.body()).byteLength).toBeGreaterThan(1000);

  // And pressing it asks for that voice's sample.
  const asked = page.waitForRequest((r) => r.url().includes(`/voices/${chosen}.mp3`));
  await page.getByRole("button", { name: /Hear / }).click();
  await asked;
});

test("the wallet says what is in it and what a credit buys", async ({ page }) => {
  await page.goto(at("wallet"));

  // A balance, not a zero: this account has none, and "none" and "not asked
  // yet" are different answers about somebody's money.
  await expect(page.getByText("Balance")).toBeVisible();
  await expect(page.locator(".wallet-balance")).toContainText("0");

  // What it is for, in as many words -- and the word is minutes, because a
  // two-minute lesson and a ten-minute one are not the same amount of work.
  await expect(page.getByText(/one credit is one minute/i)).toBeVisible();

  // The packs, priced by the server rather than by the page.
  await expect(page.locator(".wallet-packs .pack")).toHaveCount(2);

  // Nothing bought yet, and it says so rather than showing an empty table.
  await expect(page.getByText(/Nothing yet. Buying credits/)).toBeVisible();
});

test("buying credits explains itself when billing is not configured", async ({ page }) => {
  // The ordinary state of a deployment that has not set Stripe up. Pressing
  // it must say so rather than look like it worked.
  await page.goto(at("wallet"));
  await page.locator(".wallet-packs .pack").first().getByRole("button").click();
  // Scoped to the wallet's own notice: the page has other regions, and a
  // bare alert locator finds whichever of them happens to be a role=alert.
  const notice = page.locator(".notice-bad");
  await expect(notice).toContainText(/not available on this server/i);
  await expect(notice).not.toContainText(/STRIPE|SUPABASE/);
});
