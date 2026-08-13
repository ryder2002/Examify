import { afterEach, describe, expect, it, vi } from "vitest";

import { examSlug, normalizeExamSlug, quizPath } from "./exam-route";

describe("exam routes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("normalizes Vietnamese titles and keeps a stable identity", () => {
    expect(normalizeExamSlug("Đề Thi Thử ETS 2022 – Số 8")).toBe(
      "de-thi-thu-ets-2022-so-8",
    );
    expect(
      quizPath({ title: "Đề Thi Thử ETS 2022", exam_id: "abc-123" }),
    ).toBe("/quiz/de-thi-thu-ets-2022-abc-123");
  });

  it("prefers the persisted backend slug", () => {
    expect(examSlug({ slug: "ets-2022-8-cafe", id: "ignored" })).toBe(
      "ets-2022-8-cafe",
    );
  });

  it("uses a static-export-safe quiz URL in the Tauri runtime", () => {
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });

    expect(quizPath({ slug: "de-thi-thu-ets-2022-abc123" })).toBe(
      "/quiz?slug=de-thi-thu-ets-2022-abc123",
    );
  });
});
