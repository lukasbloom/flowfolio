import { chromium, FullConfig } from "@playwright/test";
import { resetGoldenDb } from "./tests/e2e/helpers/dbReset";
import { TEST_PASSWORD } from "./tests/e2e/helpers/auth";

const STORAGE_STATE = "tests/e2e/.auth/storageState.json";
const BASE_URL = process.env.PW_BASE_URL ?? "http://localhost:8091";

export default async function globalSetup(_config: FullConfig): Promise<void> {
  // 1. Reset the golden DB so the storageState login hits a known fixture
  await resetGoldenDb();
  // 2. Log in and persist storageState
  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel(/password/i).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/track/);
  await context.storageState({ path: STORAGE_STATE });
  await browser.close();
}
