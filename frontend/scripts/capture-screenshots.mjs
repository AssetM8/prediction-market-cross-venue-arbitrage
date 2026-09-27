// Capture README screenshots from a running dashboard (default http://127.0.0.1:5173).
// Usage: node scripts/capture-screenshots.mjs [baseUrl] [outputDir]
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:5173";
const out = process.argv[3] ?? "../docs/screenshots";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
await page.goto(base);
await page.getByTestId("opportunity-table").waitFor();
await page.getByTestId("opportunity-table").locator("tr", { hasText: "validated" }).first().click();
await page.getByTestId("opportunity-detail").waitFor();
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/opportunities.png`, fullPage: true });

await page.getByRole("tab", { name: "Market pairs" }).click();
await page.getByTestId("pair-table").locator("tr", { hasText: "B_IMPLIES_A" }).first().click();
await page.getByTestId("pair-detail").waitFor();
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/market-pairs.png`, fullPage: true });

await page.getByRole("tab", { name: "Paper trading" }).click();
await page.getByTestId("execution-table").waitFor();
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/paper-trading.png`, fullPage: true });

await page.getByRole("tab", { name: "Relationships & analytics" }).click();
await page.getByTestId("relationship-edges").waitFor();
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/relationships.png`, fullPage: true });

await browser.close();
console.log(`screenshots written to ${out}`);
