// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
const getAccessToken = vi.fn(() => "teacher-access-token");

vi.mock("@/lib/api", () => ({
  apiFetch,
  getAccessToken,
  getDesktopActiveUserId: () => "11111111-1111-4111-8111-111111111111",
  isDesktop: () => true,
}));

describe("desktop exam synchronization", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    getAccessToken.mockReturnValue("teacher-access-token");
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: true,
    });
  });

  it("returns a server receipt only after the exam and publication are synced", async () => {
    apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ role: "teacher" }), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "ready",
            exam_id: "remote-exam-1",
            publications: [{ classroom_id: "class-1", status: "synced" }],
          }),
          { status: 200 },
        ),
      );
    const { syncDesktopExam } = await import("./desktop-sync");

    const result = await syncDesktopExam("local-exam-1", ["class-1"]);

    expect(result.exam_id).toBe("remote-exam-1");
    expect(result.publications[0].status).toBe("synced");
    expect(apiFetch).toHaveBeenNthCalledWith(
      2,
      "/api/desktop/exams/local-exam-1/sync",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          access_token: "teacher-access-token",
          classroom_ids: ["class-1"],
        }),
      }),
    );
  });

  it("surfaces the remote sync error instead of reporting a queued item as Public", async () => {
    apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ role: "teacher" }), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Đã đạt giới hạn đề thi" }), {
          status: 403,
        }),
      );
    const { syncDesktopExam } = await import("./desktop-sync");

    await expect(syncDesktopExam("local-exam-2", ["class-2"]))
      .rejects.toThrow("Đã đạt giới hạn đề thi");
  });

  it("automatically drains the durable queue when the desktop coordinator starts", async () => {
    apiFetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [] }), { status: 200 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ role: "teacher" }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ removed: [], conflicts: [], updated: [] }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [{ client_exam_id: "offline-exam-1" }] }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "ready",
            exam_id: "remote-offline-exam-1",
            publications: [],
          }),
          { status: 200 },
        ),
      );
    const { startDesktopSyncCoordinator } = await import("./desktop-sync");

    const stop = startDesktopSyncCoordinator();
    await vi.waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        "/api/desktop/exams/offline-exam-1/sync",
        expect.objectContaining({ method: "POST" }),
      );
    });
    stop();
  });
});
