import { test, expect } from "@playwright/test";

test("access map previews role policy without switching users", async ({ page }) => {
  test.setTimeout(120000);
  const status = await page.request.get("/api/auth/status");
  const setup = (await status.json()).setup_required;
  await page.goto("/");
  if (setup) await page.getByLabel("Workspace ID").fill("acme");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page.getByLabel("Password", { exact: true }).fill("test-workspace-password");
  await page.getByRole("button", { name: setup ? "Create workspace" : "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your documents. Only your answers." })).toBeVisible();

  await page.locator(".upload-button").click();
  await expect(page.getByRole("heading", { name: "Upload with permissions" })).toBeVisible();
  await page.getByLabel("finance", { exact: true }).check();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose files & upload" }).click();
  await (await chooser).setFiles({ name: "finance-policy.md", mimeType: "text/markdown", buffer: Buffer.from("# Finance\n\nAPAC revenue was $82M.") });
  await expect(page.getByRole("button", { name: /finance-policy.md Ready/ })).toBeVisible({ timeout: 30000 });
  await page.getByRole("textbox", { name: "Ask your documents" }).fill("What is APAC revenue?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".assistant-message")).toContainText("APAC revenue", { timeout: 30000 });

  await expect(page.getByRole("heading", { name: "Trust summary" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Me", exact: true })).toHaveClass(/selected/);
  await page.getByRole("button", { name: "HR", exact: true }).click();
  await expect(page.locator(".access-map-row").filter({ hasText: "finance-policy.md" })).toBeVisible();
  await expect(page.getByText("Filtered", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Compare access", exact: true }).click();
  await expect(page.getByText("Previewing hr access")).toBeVisible();
  await expect(page.getByText("never signs in as this role", { exact: false })).toBeVisible();
  await page.screenshot({ path: "../.scratch/access-map-latest.png", fullPage: true });
});
