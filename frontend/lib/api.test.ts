// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("desktop API authentication", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    invoke.mockImplementation((command: string) => {
      if (command === "desktop_runtime") {
        return Promise.resolve({ localApi: "http://127.0.0.1:18765", remoteApi: "https://example.test", secret: "local-secret" });
      }
      if (command === "load_refresh_token") return Promise.resolve("refresh-token");
      return Promise.resolve();
    });
  });

  it("refreshes an expired access token and retries once", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("unauthorized", { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "fresh-token" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ role: "user" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");
    api.setAccessToken("expired-token");

    const response = await api.apiFetch("/api/v1/auth/me");

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe("https://example.test/api/v1/desktop/auth/refresh");
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Authorization")).toBe("Bearer fresh-token");
  });

  it("clears credentials and emits an auth event after revoke", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("unauthorized", { status: 401 }))
      .mockResolvedValueOnce(new Response("revoked", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const listener = vi.fn();
    window.addEventListener("smart-exam-auth-changed", listener);
    const api = await import("./api");
    api.setAccessToken("revoked-access-token");

    const response = await api.apiFetch("/api/v1/auth/me");

    expect(response.status).toBe(401);
    expect(invoke).toHaveBeenCalledWith("clear_refresh_token");
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("smart-exam-auth-changed", listener);
  });

  it("routes authenticated dictionary and pronunciation requests to the remote API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Blob(["audio"]), {
        status: 200,
        headers: { "Content-Type": "audio/mpeg" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");
    api.setAccessToken("dictionary-access-token");

    const response = await api.apiFetch(
      "/api/v1/dictionary/pronunciation?q=example&variant=0",
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://example.test/api/v1/dictionary/pronunciation?q=example&variant=0",
    );
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer dictionary-access-token");
    expect(headers.get("X-Desktop-Secret")).toBeNull();
  });

  it("builds authenticated local asset URLs for Tauri audio and images", async () => {
    const api = await import("./api");
    await api.desktopRuntime();
    api.bindDesktopUser("11111111-1111-4111-8111-111111111111");

    expect(
      api.assetUrl("/api/desktop/exams/exam-1/assets/listening.mp3"),
    ).toBe(
      "http://127.0.0.1:18765/api/desktop/exams/exam-1/assets/listening.mp3?desktop_secret=local-secret&desktop_user=11111111-1111-4111-8111-111111111111",
    );
    expect(api.publicWebUrl("/public-test/SHARE123")).toBe(
      "https://example.test/public-test/SHARE123",
    );
  });

  it("rewrites legacy production media URLs to the current LAN origin", async () => {
    delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
    window.history.replaceState({}, "", "/quiz");
    const api = await import("./api");

    expect(
      api.assetUrl(
        "https://exam.congnhat.online/api/v1/class-assets/asset-1?token=signed",
      ),
    ).toBe("http://localhost:3000/api/v1/class-assets/asset-1?token=signed");
  });

  it("scopes every local sidecar request to the active account", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");
    api.bindDesktopUser("22222222-2222-4222-8222-222222222222");

    await api.apiFetch("/api/desktop/exams", { cache: "no-store" });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("X-Desktop-Secret")).toBe("local-secret");
    expect(headers.get("X-TOEICDOC-User-ID")).toBe(
      "22222222-2222-4222-8222-222222222222",
    );
  });

  it("clears account session state when the desktop user changes", async () => {
    const api = await import("./api");
    api.bindDesktopUser("33333333-3333-4333-8333-333333333333");
    sessionStorage.setItem("quiz-data", "old-account-exam");
    sessionStorage.setItem("quiz-attempt-id", "old-attempt");

    api.bindDesktopUser("44444444-4444-4444-8444-444444444444");

    expect(sessionStorage.getItem("quiz-data")).toBeNull();
    expect(sessionStorage.getItem("quiz-attempt-id")).toBeNull();
  });

  it("abandons the pending web Full Test component before clearing local state", async () => {
    delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    sessionStorage.setItem(
      "pending-listening-exam",
      JSON.stringify({ exam_id: "pending-listening-id" }),
    );
    const api = await import("./api");

    await api.abandonPendingListeningSession();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/full-test-components/pending-listening-id",
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
    expect(sessionStorage.getItem("pending-listening-exam")).toBeNull();
  });

  it("routes anonymous classroom sessions remotely without activation refresh", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");

    const response = await api.classroomFetch(
      "/api/v1/class-session/assignments",
      "class-session-token",
      { cache: "no-store" },
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://example.test/api/v1/class-session/assignments",
    );
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("X-Classroom-Session")).toBe("class-session-token");
    expect(headers.get("Authorization")).toBeNull();
  });

  it("persists browser-scoped classroom sessions independently", async () => {
    const api = await import("./api");
    const browserKey = api.classBrowserKey();
    expect(browserKey).toHaveLength(48);
    expect(api.classBrowserKey()).toBe(browserKey);

    api.saveClassSession({
      token: "session-one",
      classroomId: "class-one",
      classroomName: "Lớp Một",
      fullName: "Nguyễn Văn A",
    });
    api.saveClassSession({
      token: "session-two",
      classroomId: "class-two",
      classroomName: "Lớp Hai",
      fullName: "Nguyễn Văn A",
    });

    expect(api.storedClassSessions()).toHaveLength(2);
    expect(api.storedClassSession("class-one")?.token).toBe("session-one");
    api.removeClassSession("class-one");
    expect(api.storedClassSession("class-one")).toBeNull();
  });

  it("restores the last verified desktop identity after an offline app restart", async () => {
    const onlineFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          state: "authenticated",
          authenticated: true,
          role: "teacher",
          user: {
            id: "55555555-5555-4555-8555-555555555555",
            display_name: "Offline Teacher",
            email: "teacher@example.test",
          },
          active_class_count: 2,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", onlineFetch);
    let api = await import("./api");
    api.setAccessToken("verified-access-token");
    expect((await api.resolveAuthState()).role).toBe("teacher");

    vi.resetModules();
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: false,
    });
    const offlineFetch = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", offlineFetch);
    api = await import("./api");

    const restored = await api.resolveAuthState();

    expect(restored).toMatchObject({
      state: "authenticated",
      authenticated: true,
      role: "teacher",
      offline: true,
      active_class_count: 2,
    });
    expect(offlineFetch).not.toHaveBeenCalled();
  });

  it("removes offline access after an authoritative signed-out response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            state: "authenticated",
            authenticated: true,
            role: "teacher",
            active_class_count: 0,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            state: "activation_required",
            authenticated: false,
            role: null,
            active_class_count: 0,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");
    api.setAccessToken("verified-access-token");

    await api.resolveAuthState();
    const signedOut = await api.resolveAuthState();
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      value: false,
    });

    expect(signedOut.authenticated).toBe(false);
    await expect(api.resolveAuthState()).rejects.toThrow();
  });
});
