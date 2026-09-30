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
    expect(["/api/stats"], `unexpected call from a public page: ${path}`)
      .toContain(path);
  }
});

test("the landing page leads to the workspace", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Get started" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("button", { name: "Generate" })).toBeVisible();
});

test("an empty service shows no numbers rather than zeroes", async ({ page }) => {
  // Three zeroes is a true measurement and a bad sentence. Nothing to report
  // and reporting nothing are different, and this is the difference.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Tutorials generated")).toHaveCount(0);
});
