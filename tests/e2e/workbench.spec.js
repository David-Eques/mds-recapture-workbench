import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

let artifacts;

test.beforeAll(() => {
  artifacts = mkdtempSync(path.join(tmpdir(), "mds-workbench-e2e-"));
  execFileSync("uv", ["run", "python", "-m", "tests.public.generate_e2e_case", artifacts], {
    stdio: "inherit",
  });
});

test.afterAll(() => {
  rmSync(artifacts, { recursive: true, force: true });
});

test("fresh scanned PDF is OCRed into an uncounted review candidate", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /upload the assessment/i })).toBeVisible();

  await page.locator("#assessment").setInputFiles(path.join(artifacts, "assessment.json"));
  await page.locator("#documents").setInputFiles(path.join(artifacts, "unique-scanned-note.pdf"));
  await page.getByLabel("Document date for unique-scanned-note.pdf").fill("2026-05-20");
  await page.getByLabel("Document type for unique-scanned-note.pdf").selectOption("dietary");
  await page.getByRole("button", { name: "Analyze uploaded case" }).click();

  await expect(page.getByText("Text review candidate", { exact: true })).toBeVisible();
  await expect(page.getByText("KAXE1 → KBXE1 HIPPS")).toBeVisible();
  await expect(page.getByText(/\$30\.54\/day potential Scenario-A movement; not counted/)).toBeVisible();
  await expect(page.getByText(/OCR \d+%/)).toBeVisible();
  await expect(page.getByText("Not counted", { exact: true })).toBeVisible();

  if (process.env.GENERATE_PUBLIC_SCREENSHOT === "1" && test.info().project.name === "desktop") {
    await page.screenshot({ path: "docs/workbench.png", fullPage: true });
  }

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("structured-only signal is supported and counted with a matched baseline", async ({ page }) => {
  await page.goto("/");
  await page.locator("#assessment").setInputFiles(path.join(artifacts, "assessment.json"));
  await page.locator("#signals").setInputFiles(path.join(artifacts, "signals.json"));
  await page.getByRole("button", { name: "Analyze uploaded case" }).click();

  await expect(page.getByText("Structured supported").first()).toBeVisible();
  await expect(page.getByText("$30.54/day", { exact: true }).last()).toBeVisible();
  await expect(page.getByText(/\$30\.54\/day potential and counted Scenario-A movement/)).toBeVisible();
});
