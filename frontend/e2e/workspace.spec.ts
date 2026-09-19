import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  const status = await page.request.get("/api/auth/status");
  const setup = (await status.json()).setup_required;
  const response = await page.request.post(setup ? "/api/auth/setup" : "/api/auth/login", {
    data: { email: "admin@example.test", password: "test-workspace-password", ...(setup ? { tenant_id: "acme" } : {}) },
  });
  expect(response.ok()).toBeTruthy();
});

test("upload → retrieve → answer → source drawer → new conversation", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your documents. Only your answers." })).toBeVisible();
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({
    name: "browser-report.md", mimeType: "text/markdown",
    buffer: Buffer.from("# Revenue\n\nAPAC revenue was $82M.\n\n| Region | Revenue |\n| --- | --- |\n| APAC | $82M |"),
  });
  const document = page.getByRole("button", { name: /browser-report.md Ready/ });
  await expect(document).toBeVisible({ timeout: 30000 });
  await document.click();
  await expect(page.getByRole("heading", { name: "Document details" })).toBeVisible();
  await expect(page.getByText("Indexed passages", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Processing times" })).toContainText("Total");
  await page.screenshot({ path: "../.scratch/ingestion-details.png" });
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({
    name: "same-report.md", mimeType: "text/markdown",
    buffer: Buffer.from("# Revenue\n\nAPAC revenue was $82M.\n\n| Region | Revenue |\n| --- | --- |\n| APAC | $82M |"),
  });
  await expect(page.getByText("Already in your library: browser-report.md")).toBeVisible();
  await expect(page.locator(".document-row")).toHaveCount(1);
  await page.getByRole("textbox", { name: "Ask your documents" }).fill("What was APAC revenue?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".markdown")).toContainText("APAC revenue was $82M.");
  await page.locator(".source-card").first().click();
  await expect(page.getByRole("heading", { name: "Source passage" })).toBeVisible();
  await expect(page.locator(".source-excerpt")).toContainText("$82M");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download original" }).click();
  expect((await download).suggestedFilename()).toBe("browser-report.md");
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByRole("heading", { name: "Your documents. Only your answers." })).toBeVisible();
  expect(errors).toEqual([]);
});

test("small screen navigation and setup drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("button", { name: "New conversation" })).toBeVisible();
  await page.getByRole("button", { name: "Connection settings" }).click();
  await expect(page.getByRole("heading", { name: "Connect your knowledge" })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Close sidebar" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.locator(".thread-viewport").evaluate(el => el.scrollTop)).toBe(0);
});

test("server errors are visible and retry is available", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({
    name: "retry-report.txt", mimeType: "text/plain", buffer: Buffer.from("APAC revenue was $82M."),
  });
  await expect(page.getByRole("button", { name: /retry-report.txt Ready/ })).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("textbox", { name: "Ask your documents" })).toBeEnabled();
  await page.route("**/api/chat/stream", route => route.fulfill({
    status: 502, contentType: "application/json", body: JSON.stringify({ detail: "Azure quota exceeded. Try again later." }),
  }));
  await page.getByRole("textbox", { name: "Ask your documents" }).fill("What was revenue?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".assistant-message").getByRole("alert")).toContainText("Azure quota exceeded");
  await expect(page.getByRole("button", { name: "Retry answer" })).toBeVisible();
  await page.unroute("**/api/chat/stream");
  await page.getByRole("button", { name: "Retry answer" }).click();
  await expect(page.locator(".markdown")).toContainText("APAC revenue was $82M.");
});

test("open document details refresh when ingestion finishes", async ({ page }) => {
  const document = {
    document_id: "progress-test", filename: "progress.txt", status: "waiting_embedding",
    created_at: new Date().toISOString(), size: 30, chunk_count: 1, error: null, warnings: [],
    reused: false, timings_ms: { parsing: 25 }, processing: { parser: "native-text" },
    stage_started_at: new Date().toISOString(), completed_at: null as string | null,
  };
  await page.route("**/api/documents", route => route.fulfill({ json: [document] }));
  await page.route("**/api/documents/progress-test", route => route.fulfill({ json: {
    document, chunks: document.status === "ready" ? [{
      chunk_id: "passage-1", document_id: document.document_id, filename: document.filename,
      text: "The processed passage is available.", section: "", page: null,
      page_end: null, content_type: "text", token_count: 7,
    }] : [],
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: /progress.txt Waiting for embeddings/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Waiting for embeddings");
  document.status = "ready";
  document.completed_at = new Date().toISOString();
  await expect(page.getByRole("dialog")).toContainText("The processed passage is available.", {
    timeout: 10000,
  });
});
