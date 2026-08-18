import path from "node:path";

import { expect, test } from "@playwright/test";

type HarnessManifest = {
  error?: string;
  exam_type?: string;
  questions?: Array<{ number: number; option_letters: string[]; issues: string[] }>;
  issues?: Array<{ code: string }>;
  metadata?: Record<string, unknown>;
};

const fixtures = [
  { type: "listening", file: "TEST 1 LC.pdf", start: 1, end: 100 },
  { type: "reading", file: "TEST 1 RC .pdf", start: 101, end: 200 },
] as const;

for (const fixture of fixtures) {
  test(`${fixture.type} fixture keeps all question/option anchors`, async ({ page }) => {
    const forbiddenOcrRequests: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (/\/api\/extractions(?:\/|$)|answer-key-image|\/pages\/\d+/.test(url)) {
        forbiddenOcrRequests.push(url);
      }
    });
    await page.goto("/ocr-harness");
    await page.getByLabel("Exam type").selectOption(fixture.type);
    await page.getByLabel("PDF fixture").setInputFiles(
      path.resolve(process.cwd(), "..", fixture.file),
    );
    await page.getByRole("button", { name: "Run local OCR" }).click();
    await expect(page.getByTestId("ocr-status")).toHaveText(/complete|failed/, {
      timeout: 6 * 60 * 1000,
    });
    const manifest = await page.evaluate(
      () => window.__OCR_HARNESS_RESULT__ as HarnessManifest,
    );
    expect(manifest.error).toBeUndefined();
    expect(manifest.exam_type).toBe(fixture.type);
    expect(manifest.questions).toHaveLength(100);
    expect(manifest.questions?.map((question) => question.number)).toEqual(
      Array.from({ length: 100 }, (_, index) => fixture.start + index),
    );
    expect(
      manifest.questions?.filter((question) =>
        question.issues.some((issue) => issue === "question_missing" || issue === "options_missing"),
      ),
    ).toEqual([]);
    expect(forbiddenOcrRequests).toEqual([]);
  });
}
