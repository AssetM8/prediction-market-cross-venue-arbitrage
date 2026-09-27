import { expect, test } from "@playwright/test";

test("dashboard loads fixture data and paper-executes a validated opportunity", async ({ page }) => {
  await page.goto("/");

  const banner = page.getByTestId("paper-banner");
  await expect(banner).toContainText("Paper trading only");
  await expect(page.getByTestId("data-mode")).toContainText("FIXTURE DATA");
  await expect(page.getByTestId("health-panel")).toContainText("status: ok");

  const table = page.getByTestId("opportunity-table");
  const validated = table.locator("tr", { hasText: "validated" }).first();
  await expect(validated).toBeVisible();
  await validated.click();

  const detail = page.getByTestId("opportunity-detail");
  await expect(detail).toContainText("Expected net profit");
  await expect(detail.getByRole("img", { name: "Cumulative net profit by quantity" })).toBeVisible();

  await detail.getByLabel("Quantity per leg").fill("25");
  await detail.getByRole("button", { name: "Paper execute" }).click();
  await expect(page.getByTestId("execution-result")).toContainText("hedged");

  await page.getByRole("tab", { name: "Paper trading" }).click();
  await expect(page.getByTestId("execution-table")).toContainText("hedged");
  await expect(page.getByTestId("orders-table")).toContainText("simulated");

  await page.getByRole("tab", { name: "Market pairs" }).click();
  await page.getByTestId("pair-table").locator("tr", { hasText: "UNRELATED" }).first().click();
  await expect(page.getByTestId("blocking-mismatches")).toBeVisible();
});
