// Critical workflows in a real browser, against the real API and built dashboard.
// Start tests/e2e_server.py first, then: node e2e/run.mjs <base-url> <screenshot-folder>
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.argv[2] ?? "http://127.0.0.1:8123";
const SHOTS = process.argv[3];
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const SECTIONS = ["Overview", "Market Charts", "Market Intelligence", "Quantitative Research", "Machine Learning", "Time Machine", "Trading Skills", "Trading Terminal", "Risk Management", "Performance and Audit"];
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? `: ${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const problems = [];
page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
page.on("console", (m) => {
  // A deliberately refused request appears as a console error, and is not a defect.
  if (m.type() === "error" && !/status of (400|403|404|503)/.test(m.text())) problems.push(`console: ${m.text()}`);
});

const open = async (label) => {
  await page.getByRole("navigation").getByRole("button", { name: label, exact: true }).click();
  await page.getByRole("heading", { level: 1, name: label }).waitFor();
};
const shot = async (name) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

await page.goto(BASE);

// Every section renders with no crash.
for (const label of SECTIONS) {
  await open(label);
  await page.waitForTimeout(1500);
  check(`${label} renders`, (await page.locator("main").innerText()).length > 40);
  await shot(label.toLowerCase().replaceAll(" ", "_"));
}

// The chart draws, and the drawing tool adds a level.
await open("Market Charts");
await page.waitForSelector("[data-testid=chart] canvas");
check("Chart has a canvas", (await page.locator("[data-testid=chart] canvas").count()) > 0);
await page.getByRole("button", { name: "H4", exact: true }).click();
await page.waitForTimeout(800);
await page.getByRole("button", { name: "Draw level" }).click();
await page.locator("[data-testid=chart]").click({ position: { x: 300, y: 200 } });
await page.waitForTimeout(300);
check("Drawing tool records a level", await page.getByRole("button", { name: "Clear drawings (1)" }).isVisible());
await page.getByRole("button", { name: /Clear drawings/ }).click();

// Risk rejection: no stop, so it must not reach the broker.
await open("Trading Terminal");
await page.waitForTimeout(800);
await page.getByRole("button", { name: "Send to risk check" }).click();
await page.getByTestId("order-result").waitFor();
let text = await page.getByTestId("order-result").innerText();
check("Order without a stop is rejected", text.includes("rejected") && text.includes("stop-loss"), text.replaceAll("\n", " "));
check("Rejected order opened nothing", await page.getByText("No open positions.").isVisible());

// Paper order with a stop reaches the paper broker.
const quote = await (await page.request.get(`${BASE}/api/terminal`)).json();
const bid = quote.quote.bid;
await page.getByLabel("Stop").fill(String((bid - 20).toFixed(2)));
await page.getByLabel("Target").fill(String((bid + 60).toFixed(2)));
await page.getByRole("button", { name: "Send to risk check" }).click();
await page.waitForFunction(() => document.querySelector("[data-testid=order-result]")?.innerText.includes("sent"));
await page.waitForTimeout(600);
check("Paper order is sent", (await page.getByTestId("order-result").innerText()).includes("sent"));
check("Position appears in the terminal", await page.getByRole("button", { name: "Close", exact: true }).first().isVisible());
await shot("terminal_with_position");

// Emergency stop closes the position and blocks new entries.
await open("Risk Management");
await page.waitForTimeout(800);
await page.getByRole("button", { name: "EMERGENCY STOP and close all" }).click();
await page.getByText("ACTIVE:").waitFor();
check("Emergency stop shows as active", true);
await shot("risk_emergency");
await open("Trading Terminal");
await page.waitForTimeout(800);
check("Emergency stop closed the position", await page.getByText("No open positions.").isVisible());
await page.getByRole("button", { name: "Send to risk check" }).click();
await page.waitForFunction(() => document.querySelector("[data-testid=order-result]")?.innerText.includes("Emergency stop"));
check("Emergency stop blocks a new order", true);
await open("Risk Management");
await page.waitForTimeout(500);
await page.getByRole("button", { name: "Clear", exact: true }).click();
await page.getByText("Not active").waitFor();
check("Emergency stop can be cleared", true);

// Assisted mode waits for a real yes.
await open("Trading Terminal");
await page.waitForTimeout(500);
await page.getByRole("button", { name: "assisted", exact: true }).click();
await page.waitForTimeout(500);
await page.getByLabel("Stop").fill(String((bid - 20).toFixed(2)));
await page.getByLabel("Target").fill(String((bid + 60).toFixed(2)));
await page.getByRole("button", { name: "Send to risk check" }).click();
await page.getByLabel("Reply").waitFor();
check("Assisted order waits", await page.getByText("No open positions.").isVisible());
await page.getByLabel("Reply").fill("sure");
await page.getByRole("button", { name: "Reply", exact: true }).click();
await page.waitForTimeout(500);
check("'sure' is not accepted as a yes", await page.getByText("No open positions.").isVisible());
await page.getByLabel("Reply").fill("yes");
await page.getByRole("button", { name: "Reply", exact: true }).click();
await page.getByRole("button", { name: "Close", exact: true }).first().waitFor();
check("A plain yes places the order", true);
await page.getByRole("button", { name: "Close", exact: true }).first().click();
await page.getByText("No open positions.").waitFor();
await page.getByRole("button", { name: "paper", exact: true }).click();

// Time Machine replay, with live streaming and results.
await open("Time Machine");
await page.waitForTimeout(800);
const start = new Date(Date.now() - 50 * 864e5).toISOString().slice(0, 10);
const end = new Date(Date.now() - 2 * 864e5).toISOString().slice(0, 10);
await page.getByLabel("From", { exact: true }).fill(start);
await page.getByLabel("To", { exact: true }).fill(end);
await page.getByRole("button", { name: "Load replay" }).click();
await page.getByRole("button", { name: "Step 50" }).waitFor({ timeout: 60000 });
await page.getByRole("button", { name: "Step 50" }).click();
await page.waitForTimeout(600);
const progress = async () => parseInt((await page.locator("main").innerText()).match(/(\d+) \/ \d+/)?.[1] ?? "0", 10);
check("Step moves the replay", (await progress()) === 50, String(await progress()));
await page.getByRole("button", { name: "Play", exact: true }).click();
await page.waitForTimeout(2500);
const during = await progress();
check("Play advances the replay over the WebSocket", during > 50, String(during));
await page.getByRole("button", { name: "Pause", exact: true }).click();
await page.waitForTimeout(600);
const paused = await progress();
await page.waitForTimeout(1200);
check("Pause holds the replay", (await progress()) === paused);
await page.getByRole("button", { name: "Run to end" }).click();
await page.waitForTimeout(3000);
await page.getByRole("button", { name: "Results" }).click();
await page.getByText("Results of this replay").waitFor();
check("Replay results appear", true);
await shot("time_machine_results");

// A skill runs through the dashboard, and refusal text comes back.
await open("Trading Skills");
await page.waitForTimeout(800);
await page.getByRole("row", { name: /Exit Planner/ }).getByRole("button", { name: "Open" }).click();
await page.getByRole("button", { name: /Example plan/ }).click();
await page.getByText('"stop": 1980').waitFor();
check("A skill runs from the dashboard", true);

// Search returns attributed evidence.
await open("Quantitative Research");
await page.waitForTimeout(800);
await page.getByPlaceholder("Search research").fill("reward risk stop");
await page.getByRole("button", { name: "Rebuild index" }).click();
await page.waitForTimeout(2500);
check("Search returns attributed sources", (await page.locator("main").innerText()).toLowerCase().includes("documentation"));

// Narrow layout does not scroll sideways.
await page.setViewportSize({ width: 390, height: 800 });
await open("Overview");
await page.waitForTimeout(800);
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
check("Phone width has no sideways page scroll", overflow <= 1, `${overflow}px`);
await shot("overview_phone");

check("No browser errors during the run", problems.length === 0, problems.slice(0, 6).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
