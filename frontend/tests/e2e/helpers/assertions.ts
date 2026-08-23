import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Asserts no "failed"/"error" text is visible anywhere on the page. The
 * sonner toast is this repo's error surface, so an absent match means the
 * page survived the preceding action without surfacing an error.
 */
export async function expectNoErrorToast(page: Page): Promise<void> {
  await expect(page.getByText(/failed|error/i)).toHaveCount(0);
}
