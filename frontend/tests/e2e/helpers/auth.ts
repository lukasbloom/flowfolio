import type { APIRequestContext } from "@playwright/test";

export const TEST_PASSWORD = "test-password-e2e"; // compose.test.yml

export async function loginViaApi(api: APIRequestContext): Promise<void> {
  const resp = await api.post("/api/auth/login", { data: { password: TEST_PASSWORD } });
  if (!resp.ok()) {
    throw new Error(`Login failed: ${resp.status()} ${await resp.text()}`);
  }
}
