import { expect, test } from "@playwright/test";

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
  await expect(page.getByText("AI-powered coding tutorials")).toBeVisible();
  // The workspace is not on it: a composer here would be a second place to
  // submit from, with its own copy of everything.
  await expect(page.getByRole("button", { name: "Generate" })).toHaveCount(0);
});

/** The only API routes a signed-out visitor is allowed to cause. */
const PUBLIC_ROUTES = ["/api/stats", "/api/examples"];

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
    expect(PUBLIC_ROUTES, `unexpected call from a public page: ${path}`)
      .toContain(path);
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

test("the examples section stands on its own with nothing published", async ({ page }) => {
  // What it looks like before an operator publishes anything, which is the
  // state this build is in. The cards carry topics the service can make --
  // and no running times, because a duration for a video nobody rendered is
  // the same kind of claim as a user count nobody earned.
  await page.goto("/");
  const section = page.locator("#examples");

  await expect(section.getByRole("heading", { name: "See it in action" })).toBeVisible();
  await expect(section.getByRole("link", { name: /View more examples/ })).toBeVisible();
  await expect(section.getByRole("article")).toHaveCount(3);
  await expect(section.locator(".landing-card-time")).toHaveCount(0);
});

test("a published video shows its still and its running time", async ({ page }) => {
  // Intercepted rather than published for real: this is about what the page
  // does with an example, not about which ones this instance has.
  await page.route("**/api/examples", (route) =>
    route.fulfill({
      json: [{
        id: "e1", title: "A published tutorial", duration_s: 272,
        // What the API actually returns: the prefix is added by the page.
        mode: "both", poster_url: null, video_url: "/artifacts/x.mp4",
      }],
    })
  );

  await page.goto("/");
  const section = page.locator("#examples");

  await expect(section.getByText("A published tutorial")).toBeVisible();
  await expect(section.locator(".landing-card-time")).toHaveText("4:32");
  // It has somewhere to go, and it is not one of the topic placeholders.
  await expect(section.getByRole("link", { name: /Play A published tutorial/ }))
    .toHaveAttribute("href", "/api/artifacts/x.mp4");
  await expect(section.getByRole("article")).toHaveCount(1);
});

test("a published still is fetched from the API, not from the page's own origin", async ({ page }) => {
  // The API answers artifacts on /artifacts/..., but the browser only reaches
  // it through the /api rewrite -- so a path used raw is a request to the Next
  // server, which has never served one. Every example card showed a broken
  // image, and nothing noticed because no example had ever been published.
  await page.route("**/api/examples", (route) =>
    route.fulfill({
      json: [{
        id: "e1", title: "A published tutorial", duration_s: 272, mode: "both",
        poster_url: "/artifacts/artifacts/free/e1.poster.jpg?expires=1&sig=x",
        video_url: "/artifacts/artifacts/free/e1.mp4?expires=1&sig=x",
      }],
    })
  );

  await page.goto("/");
  const section = page.locator("#examples");

  await expect(section.locator(".landing-card-poster"))
    .toHaveAttribute("src", /^\/api\/artifacts\//);
  await expect(section.getByRole("link", { name: /Play A published tutorial/ }))
    .toHaveAttribute("href", /^\/api\/artifacts\//);
});

test("an empty service shows no numbers rather than zeroes", async ({ page }) => {
  // Three zeroes is a true measurement and a bad sentence. Nothing to report
  // and reporting nothing are different, and this is the difference.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Tutorials created")).toHaveCount(0);
  await expect(page.getByText("Platform activity")).toHaveCount(0);
});

test("the features section says what the service does", async ({ page }) => {
  // It had no heading at all -- six columns of loose paragraphs with nothing
  // saying what they were a list of. The nav links to it, so it needs
  // something to land on.
  await page.goto("/");
  const section = page.locator("#features");
  await expect(section.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(section.getByRole("article")).toHaveCount(6);
});
