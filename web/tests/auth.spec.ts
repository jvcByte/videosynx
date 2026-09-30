import { expect, test } from "@playwright/test";

// The gate lives with the workspace, not on the public landing page.
const WORKSPACE = "/dashboard";

/**
 * The app with a Supabase project configured, which is what a deployment is.
 *
 * The other 14 tests run against a build with no project, where the app behaves
 * exactly as it always has -- that is the compatibility half of this. This file
 * is the other half: signed out must mean a sign-in form, not a page that
 * fetches anyway and renders the resulting 401 as "your videos could not be
 * loaded".
 *
 * The project URL points at a port nothing answers, on purpose. Nothing here
 * needs Supabase to respond: signed out is decided before any request is made.
 */

test("a signed-out visitor is asked to sign in", async ({ page }) => {
  await page.goto(WORKSPACE);

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  // The workspace is not on the page at all.
  await expect(page.getByRole("button", { name: "Generate" })).toHaveCount(0);
});

test("a signed-out visitor causes no API requests at all", async ({ page }) => {
  // This is the whole point. The page fetches on mount and has no 401 branch,
  // so if the gate came after the fetch the user would be told their videos
  // could not be loaded -- a failure, where the truth is that they are not
  // signed in yet.
  const calls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) calls.push(url.pathname);
  });

  await page.goto(WORKSPACE);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  // Give the mount effects every chance to fire before declaring they did not.
  await page.waitForTimeout(1500);

  expect(calls, `requests made while signed out: ${calls.join(", ")}`).toEqual([]);
});

test("the account form can be switched to", async ({ page }) => {
  await page.goto(WORKSPACE);
  await page.getByRole("button", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();
  // And back, because a one-way toggle is a trap.
  await page.getByRole("button", { name: "I already have one" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
