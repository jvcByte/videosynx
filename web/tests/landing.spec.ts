import { expect, test } from "@playwright/test";

import { tierLabel } from "../app/lib/format";

/**
 * The public page, which is what someone sees before they have an account.
 *
 * It runs against the same server as the workspace tests -- no Supabase, so
 * the landing page is simply reachable and the dashboard is too. The point of
 * these is that "/" and "/dashboard" are now different things and neither one
 * has swallowed the other.
 */

test("the landing page introduces the product", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1 })).toContainText("coding");
  // The two things it makes, and the first thing on the page: this used to
  // read "AI-powered coding tutorials", which describes how it is built
  // rather than what lands at the end of it.
  await expect(page.getByText("Video generation · Text to speech")).toBeVisible();
  // The workspace is not on it: a composer here would be a second place to
  // submit from, with its own copy of everything.
  await expect(page.getByRole("button", { name: "Generate" })).toHaveCount(0);
});

/** The only API routes a signed-out visitor is allowed to cause. */
const PUBLIC_ROUTES = ["/api/stats", "/api/examples", "/api/artifacts"];

test("the landing page makes no authenticated requests", async ({ page }) => {
  // It is public. Anything it asks the API for has to work without a login,
  // or the page is broken for exactly the visitor it exists for.
  const calls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) calls.push(url.pathname);
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForTimeout(1200);

  for (const path of calls) {
    // Prefix rather than equality: a still's path is /api/artifacts/<key>,
    // and the key is the job's.
    const allowed = PUBLIC_ROUTES.some((route) => path.startsWith(route));
    expect(allowed, `unexpected call from a public page: ${path}`).toBe(true);
  }
});

test("the landing page leads to the composer", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Get started" }).click();

  // The create view, not the bare workspace: someone who pressed "Get
  // started" has said what they came to do, and the address carries that
  // through a sign-in rather than dropping them on an overview afterwards.
  await expect(page).toHaveURL(/\/dashboard#create$/);
  await expect(page.getByRole("button", { name: "Generate" })).toBeVisible();
});

// The two states this section has -- published cards, and the topic
// placeholders it falls back to -- cannot both be reached in one run, because
// which is published is the server's business now. This stack publishes one,
// so that is what is asserted; the fallback is covered in api/tests, where
// the list of published ids can be set per test.
test("the examples section shows what is published", async ({ page }) => {
  await page.goto("/");
  const section = page.locator("#examples");

  await expect(section.getByRole("heading", { name: "See it in action" })).toBeVisible();
  await expect(section.getByRole("link", { name: /View more examples/ })).toBeVisible();
  // One card, and it is the published one rather than a topic placeholder --
  // the difference being that this one has a running time and a still.
  await expect(section.getByRole("article")).toHaveCount(1);
  await expect(section.locator(".landing-card-time")).toHaveCount(1);
});

test("a published video shows its still and its running time", async ({ page }) => {
  // Published for real rather than intercepted. The page renders its examples
  // on the server -- which is the point of that, and the reason a browser
  // route cannot stand in for one any more. global-setup puts a real job and
  // a real mp4 in the database this stack runs against.
  await page.goto("/");
  const section = page.locator("#examples");

  const card = section.getByRole("article").filter({ hasText: "A published tutorial" });
  await expect(card).toBeVisible();
  // 272 seconds, which is also what the row says.
  await expect(card.locator(".landing-card-time")).toHaveText("4:32");
  // And it is not one of the topic placeholders.
  await expect(section.getByRole("article")).toHaveCount(1);
});

test("a published still is fetched from the API, not from the page's own origin", async ({ page }) => {
  // The API answers artifacts on /artifacts/..., but the browser only reaches
  // it through the /api rewrite -- so a path used raw is a request to the Next
  // server, which has never served one. Every example card showed a broken
  // image, and nothing noticed because no example had ever been published.
  await page.goto("/");
  const section = page.locator("#examples");

  // The URL shape, and then the thing itself: a src that is right and an
  // image that never loads is the bug this was written for.
  await expect(section.locator(".landing-card-poster").first())
    .toHaveAttribute("src", /^\/api\/artifacts\//);
  await expect(section.getByRole("link", { name: /Play A published tutorial/ }))
    .toHaveAttribute("href", /^\/api\/artifacts\//);

  // Polled, because a src that is right and an image still in flight look
  // identical for a moment -- and the moment is what an evaluate lands on.
  await expect
    .poll(
      () => section.locator(".landing-card-poster").first()
        .evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0),
      { message: "the still was fetched but is not an image" }
    )
    .toBe(true);
});

test("the figures shown are the ones the API reports", async ({ page, request }) => {
  // It used to assert the empty case, which this stack can no longer reach:
  // it publishes a tutorial, so the service is never empty. Three zeroes is a
  // true measurement and a bad sentence -- that half lives in api/tests,
  // against a database that can be emptied per test. What is checked here is
  // that the number on the page is the number the API counted.
  const stats = await (await request.get("http://127.0.0.1:8021/stats")).json();

  await page.goto("/");
  const section = page.locator(".landing-stats");
  await expect(section).toBeVisible();
  await expect(section.getByText("Platform activity")).toBeVisible();

  // By its label rather than by the number, because with one tutorial
  // rendered the same "1" is also the topic count.
  const figures = section.locator("dl > div");
  await expect(figures.nth(0).locator("dt")).toHaveText("Tutorials created");
  await expect(figures.nth(1).locator("dt")).toHaveText("Video minutes");

  // A real count rather than an em dash or a zero -- which is the property
  // this band exists for. The exact figure is not compared: /stats is cached
  // for a minute server-side, so it is allowed to lag the API by one, and an
  // assertion that forbade that would fail on a page that is working.
  expect(stats.tutorials_rendered).toBeGreaterThan(0);
  await expect(figures.nth(0).locator("dd")).not.toHaveText("—");
  await expect(figures.nth(0).locator("dd")).toHaveText(/^\d+$/);
});

test("the features section says what the service does", async ({ page }) => {
  // It had no heading at all -- six columns of loose paragraphs with nothing
  // saying what they were a list of. The nav links to it, so it needs
  // something to land on.
  await page.goto("/");
  const section = page.locator("#features");
  await expect(section.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(section.getByRole("article")).toHaveCount(4);
  // Each card is a name and the instruction that produces the thing named.
  // The instruction is the half a reader can act on, so it has to be text
  // rather than a picture of text.
  await expect(section.getByText("Explain Python list comprehensions")).toBeVisible();
  await expect(section.getByText("Read this aloud, calmly")).toBeVisible();
});

test("the prices are in the page, not fetched after it", async ({ page, request }) => {
  // Rendered on the server, which is the point: a price that only exists
  // after JavaScript has run is a price a crawler does not see, and price is
  // the one fact on a marketing page somebody came for.
  // Comments stripped, because React separates adjacent text nodes with one:
  // the markup for a price is `$<!-- -->7`, and a substring check for "$7"
  // fails on a page that is perfectly correct.
  const html = (await (await request.get("/")).text()).replace(/<!--.*?-->/g, "");
  expect(html).toContain("landing-pricing");
  expect(html).toContain("Start free. Pay when it earns it.");

  const plans = await (await request.get("http://127.0.0.1:8021/plans")).json();
  for (const plan of plans.plans) {
    // The API sends the tier's database name, so the label is the page's --
    // which means this is checking the page applied one at all, and it is the
    // same function the page uses.
    expect(html, `${plan.id} is missing from the HTML`).toContain(tierLabel(plan.id));
    // The number a visitor reads is the number the API enforces.
    expect(html).toContain(`$${plan.price_usd}`);
  }
  for (const pack of plans.credit_packs) {
    expect(html).toContain(String(pack.credits));
  }

  // And on screen, in the same shape.
  await page.goto("/");
  const section = page.locator("#pricing");
  await expect(section).toBeVisible();
  await expect(section.getByRole("listitem").filter({ hasText: "Free" })).toBeVisible();
});

test("the prices sell the video, not the parts it is made from", async ({ page }) => {
  // The plans were called Free, Byok and Paid, and the features named the
  // voice engine and the model. None of that is what a visitor is buying --
  // and naming a supplier dates the page the day it is swapped out.
  await page.goto("/");
  const section = page.locator("#pricing");

  // The plan names, exactly -- `byok` and `paid` are database values that
  // happen to be readable, which is not the same as being names.
  expect(await section.locator(".plan h3").allTextContents())
    .toEqual(["Free", "Pro", "Studio"]);

  for (const jargon of ["Byok", "Kokoro", "ElevenLabs", "model keys"]) {
    await expect(section, `${jargon} is on the price list`).not.toContainText(jargon);
  }
});
