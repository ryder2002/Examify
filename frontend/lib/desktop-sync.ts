"use client";

import {
  apiFetch,
  getAccessToken,
  getDesktopActiveUserId,
  isDesktop,
} from "@/lib/api";

type TeacherClassroom = {
  id: string;
  name: string;
  status?: string;
  can_publish?: boolean;
};

export type DesktopPublicationResult = {
  classroom_id: string;
  status: "synced" | "failed";
  error?: string;
};

export type DesktopExamSyncResult = {
  status: string;
  exam_id: string;
  publications: DesktopPublicationResult[];
};

export type DesktopSyncSummary = {
  synced: Array<{ client_exam_id: string; exam_id: string }>;
  failures: Array<{ client_exam_id: string; error: string }>;
};

let syncPromise: Promise<DesktopSyncSummary> | null = null;

function emitSyncUpdate(summary?: DesktopSyncSummary): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("desktop-sync-updated", { detail: summary }),
    );
  }
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  return payload.detail || fallback;
}

async function desktopAccessToken(): Promise<string> {
  // This authenticated request refreshes the short-lived desktop access token
  // before it is handed to the loopback sidecar.
  const response = await apiFetch("/api/v1/auth/me", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(
      await responseError(
        response,
        "Phiên đăng nhập desktop đã hết hạn. Hãy đăng nhập lại để đồng bộ đề.",
      ),
    );
  }
  const token = getAccessToken();
  if (!token) {
    throw new Error("Không có phiên Teacher hợp lệ để đồng bộ đề lên máy chủ.");
  }
  return token;
}

async function syncOne(
  clientExamId: string,
  token: string,
  classroomIds: string[] = [],
): Promise<DesktopExamSyncResult> {
  const response = await apiFetch(`/api/desktop/exams/${clientExamId}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      access_token: token,
      classroom_ids: classroomIds,
    }),
  });
  if (!response.ok) {
    throw new Error(
      await responseError(response, "Không thể đồng bộ đề lên máy chủ."),
    );
  }
  const payload = (await response.json()) as Partial<DesktopExamSyncResult>;
  if (!payload.exam_id) {
    throw new Error("Máy chủ chưa xác nhận mã đề sau khi đồng bộ.");
  }
  return {
    status: payload.status || "ready",
    exam_id: payload.exam_id,
    publications: payload.publications || [],
  };
}

async function refreshClassroomCache(): Promise<void> {
  const response = await apiFetch("/api/v1/teacher/classrooms", {
    cache: "no-store",
  });
  if (!response.ok) return;
  const payload = (await response.json().catch(() => ({}))) as {
    items?: TeacherClassroom[];
  };
  const items = (payload.items || [])
    .filter((item) => item.status !== "archived")
    .map((item) => ({ ...item, can_publish: item.can_publish !== false }));
  await apiFetch("/api/desktop/classrooms/cache", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
}

async function reconcileDesktopCache(token: string): Promise<void> {
  const response = await apiFetch("/api/desktop/sync/reconcile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: token }),
  });
  if (!response.ok) {
    throw new Error(
      await responseError(response, "Không reconcile được dữ liệu web và Desktop."),
    );
  }
}

export async function queueDesktopPublication(
  clientExamId: string,
  classroomIds: string[],
): Promise<Response> {
  if (!isDesktop()) throw new Error("Chức năng này chỉ có trên desktop");
  return apiFetch(`/api/desktop/exams/${clientExamId}/publications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ classroom_ids: classroomIds }),
  });
}

export async function syncDesktopExam(
  clientExamId: string,
  classroomIds: string[] = [],
): Promise<DesktopExamSyncResult> {
  if (!isDesktop()) throw new Error("Chức năng này chỉ có trên desktop");
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    throw new Error("Đang offline; yêu cầu đã được giữ trên máy và chưa Public lên web.");
  }
  // Avoid two sidecar uploads racing. A direct user action is retried once the
  // coordinator's current pass has finished.
  if (syncPromise) await syncPromise;
  const token = await desktopAccessToken();
  const result = await syncOne(clientExamId, token, classroomIds);
  const summary: DesktopSyncSummary = {
    synced: [{ client_exam_id: clientExamId, exam_id: result.exam_id }],
    failures: result.publications
      .filter((item) => item.status === "failed")
      .map((item) => ({
        client_exam_id: clientExamId,
        error: item.error || `Không Public được tới lớp ${item.classroom_id}`,
      })),
  };
  emitSyncUpdate(summary);
  return result;
}

export async function syncDesktopPending(): Promise<DesktopSyncSummary> {
  const empty: DesktopSyncSummary = { synced: [], failures: [] };
  if (
    !isDesktop() ||
    !getDesktopActiveUserId() ||
    (typeof navigator !== "undefined" && !navigator.onLine)
  ) return empty;
  if (syncPromise) return syncPromise;
  syncPromise = (async () => {
    const summary: DesktopSyncSummary = { synced: [], failures: [] };
    try {
      await refreshClassroomCache().catch(() => undefined);
      const token = await desktopAccessToken();
      // Pull server revisions/deletions before claiming uploads. This ordering
      // prevents a stale local edit from racing and overwriting a newer web
      // revision in the same coordinator pass.
      await reconcileDesktopCache(token);
      const pendingResponse = await apiFetch("/api/desktop/sync/pending", {
        cache: "no-store",
      });
      if (!pendingResponse.ok) {
        throw new Error(
          await responseError(pendingResponse, "Không đọc được hàng đợi đồng bộ local."),
        );
      }
      const payload = (await pendingResponse.json().catch(() => ({}))) as {
        items?: Array<{ client_exam_id: string }>;
      };
      for (const item of payload.items || []) {
        try {
          const result = await syncOne(item.client_exam_id, token);
          summary.synced.push({
            client_exam_id: item.client_exam_id,
            exam_id: result.exam_id,
          });
          for (const publication of result.publications) {
            if (publication.status === "failed") {
              summary.failures.push({
                client_exam_id: item.client_exam_id,
                error:
                  publication.error ||
                  `Không Public được tới lớp ${publication.classroom_id}`,
              });
            }
          }
        } catch (reason) {
          // Continue with independent exams; every failure remains durable in
          // the sidecar queue and is also exposed to foreground callers.
          summary.failures.push({
            client_exam_id: item.client_exam_id,
            error: reason instanceof Error ? reason.message : "Đồng bộ thất bại",
          });
        }
      }
    } catch (reason) {
      summary.failures.push({
        client_exam_id: "",
        error: reason instanceof Error ? reason.message : "Đồng bộ thất bại",
      });
    } finally {
      emitSyncUpdate(summary);
      syncPromise = null;
    }
    return summary;
  })();
  return syncPromise;
}

export function startDesktopSyncCoordinator(): () => void {
  if (!isDesktop() || typeof window === "undefined") return () => undefined;
  let stopped = false;
  let timer: number | null = null;
  const run = () => {
    if (stopped) return;
    void syncDesktopPending().finally(() => {
      if (!stopped) timer = window.setTimeout(run, 30_000);
    });
  };
  const onOnline = () => run();
  const onVisible = () => {
    if (document.visibilityState === "visible") run();
  };
  window.addEventListener("online", onOnline);
  window.addEventListener("smart-exam-desktop-user-changed", run);
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("desktop-sync-requested", run);
  run();
  return () => {
    stopped = true;
    window.removeEventListener("online", onOnline);
    window.removeEventListener("smart-exam-desktop-user-changed", run);
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("desktop-sync-requested", run);
    if (timer !== null) window.clearTimeout(timer);
  };
}
