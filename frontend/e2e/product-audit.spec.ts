import { test, expect } from "@playwright/test";

test("capture current trust and access journey", async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const status = await page.request.get("/api/auth/status");
  const setup = (await status.json()).setup_required;
  await page.goto("/");
  if (setup) await page.getByLabel("Workspace ID").fill("acme");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page.getByLabel("Password", { exact: true }).fill("test-workspace-password");
  await page.getByRole("button", { name: setup ? "Create workspace" : "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your documents. Only your answers." })).toBeVisible();
  await page.screenshot({ path: "../.scratch/product-audit-20260917/01-ask.png", fullPage: true });
  await page.getByRole("button", { name: "People & access", exact: true }).click();
  await expect(page.getByRole("heading", { name: /People & access/ })).toBeVisible();
  await page.screenshot({ path: "../.scratch/product-audit-20260917/02-people.png", fullPage: true });
  await page.getByRole("button", { name: "Ask your documents", exact: true }).click();
  await page.locator(".upload-button").click();
  await expect(page.getByRole("heading", { name: "Upload with permissions" })).toBeVisible();
  await page.screenshot({ path: "../.scratch/product-audit-20260917/03-upload-acl.png", fullPage: true });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Architecture/ }).click();
  await expect(page.getByRole("heading", { name: "Trust is a boundary. Not a prompt." })).toBeVisible();
  await page.screenshot({ path: "../.scratch/product-audit-20260917/04-architecture.png", fullPage: true });
  expect(errors).toEqual([]);
});
