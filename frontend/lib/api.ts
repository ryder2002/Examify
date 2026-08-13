"use client";

import { getDeviceKey } from "@/lib/device";

type DesktopRuntime = {
  localApi: string;
  remoteApi: string;
  secret: string;
};

export type Role = "admin" | "teacher" | "student" | "user";
export const DESKTOP_APP_VERSION = "0.1.6";

export type AuthState = {
  state: "activation_required" | "registration_required" | "authenticated";
  authenticated: boolean;
  role: Role | null;
  user?: { id: string; display_name: string; email: string };
  active_class_count: number;
  offline?: boolean;
  data_epoch?: string | null;
};

export function roleLanding(role: string | null): string {
  if (role === "admin") return "/admin";
  if (role === "teacher") return "/exam-bank";
  if (role === "student") return "/exam-bank";
  return "/";
}

let runtimePromise: Promise<DesktopRuntime | null> | null = null;
let accessToken: string | null = null;
let authPromise: Promise<boolean> | null = null;
let identityPromise: Promise<string | null> | null = null;
let identityResolved = false;
let identityRole: string | null = null;
let identityStartupCheckStarted = false;
const DESKTOP_QUOTA_KEY = "smart-exam-desktop-exam-quota";
const DESKTOP_OFFLINE_AUTH_KEY = "smart-exam-desktop-offline-auth-v1";
const DESKTOP_ACTIVE_USER_KEY = "toeic-doc-desktop-active-user-v1";
const DATA_EPOCH_KEY = "toeic-doc-data-epoch-v1";

async function applyDataEpoch(dataEpoch: string | null | undefined): Promise<void> {
  if (!dataEpoch || typeof window === "undefined") return;
  const previous = localStorage.getItem(DATA_EPOCH_KEY);
  if (isDesktop()) {
    const response = await apiFetch("/api/desktop/data-epoch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data_epoch: dataEpoch }),
    });
    if (!response.ok) {
      throw new Error("Không thể cô lập dữ liệu Desktop thuộc phiên hệ thống cũ.");
    }
  }
  if (previous && previous !== dataEpoch) {
    const { clearOfflineBusinessData } = await import("@/lib/offline-db");
    await clearOfflineBusinessData();
    const exactKeys = new Set([
      "attempt-saved",
      "classroom-exam-return",
      "editing-exam",
      "extraction-job",
      "pending-listening-exam",
    ]);
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (key && (key.startsWith("quiz-") || exactKeys.has(key))) {
        sessionStorage.removeItem(key);
      }
    }
  }
  localStorage.setItem(DATA_EPOCH_KEY, dataEpoch);
}

type DesktopOfflineAuth = {
  role: Role;
  user?: AuthState["user"];
  active_class_count: number;
  cached_at: number;
};

function readDesktopOfflineAuth(): DesktopOfflineAuth | null {
  if (!isDesktop() || typeof window === "undefined") return null;
  try {
    const value = JSON.parse(
      localStorage.getItem(DESKTOP_OFFLINE_AUTH_KEY) || "null",
    ) as Partial<DesktopOfflineAuth> | null;
    if (
      !value ||
      !["admin", "teacher", "student", "user"].includes(value.role || "") ||
      !Number.isFinite(value.cached_at)
    ) {
      return null;
    }
    return {
      role: value.role as Role,
      user: value.user,
      active_class_count: Math.max(0, value.active_class_count || 0),
      cached_at: Number(value.cached_at),
    };
  } catch {
    return null;
  }
}

function writeDesktopOfflineAuth(state: Pick<AuthState, "role" | "user" | "active_class_count">): void {
  if (!isDesktop() || typeof window === "undefined" || !state.role) return;
  localStorage.setItem(
    DESKTOP_OFFLINE_AUTH_KEY,
    JSON.stringify({
      role: state.role,
      user: state.user,
      active_class_count: state.active_class_count,
      cached_at: Date.now(),
    } satisfies DesktopOfflineAuth),
  );
}

function clearDesktopOfflineAuth(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(DESKTOP_OFFLINE_AUTH_KEY);
  }
}

function clearDesktopAccountSession(): void {
  if (typeof window === "undefined") return;
  const exactKeys = new Set([
    "attempt-saved",
    "classroom-exam-return",
    "editing-exam",
    "extraction-job",
    "pending-listening-exam",
  ]);
  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index);
    if (key && (key.startsWith("quiz-") || exactKeys.has(key))) {
      sessionStorage.removeItem(key);
    }
  }
}

export function getDesktopActiveUserId(): string | null {
  if (!isDesktop() || typeof window === "undefined") return null;
  return localStorage.getItem(DESKTOP_ACTIVE_USER_KEY);
}

export function bindDesktopUser(userId: string | null | undefined): void {
  if (!isDesktop() || typeof window === "undefined") return;
  const normalized = String(userId || "").trim();
  const previous = localStorage.getItem(DESKTOP_ACTIVE_USER_KEY);
  if (previous && previous !== normalized) clearDesktopAccountSession();
  if (normalized) localStorage.setItem(DESKTOP_ACTIVE_USER_KEY, normalized);
  else localStorage.removeItem(DESKTOP_ACTIVE_USER_KEY);
  if (previous !== normalized) {
    window.dispatchEvent(
      new CustomEvent("smart-exam-desktop-user-changed", {
        detail: { userId: normalized || null },
      }),
    );
  }
}

type DesktopExamQuota = {
  userId: string;
  limit: number | null;
  used: number;
};

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function updateDesktopExamQuota(
  userId: string | null | undefined,
  limit: number | null | undefined,
  serverUsed: number | null | undefined,
): void {
  if (!isDesktop() || typeof window === "undefined" || !userId) return;
  const current = getDesktopExamQuota();
  const used =
    current?.userId === userId
      ? Math.max(current.used, Math.max(0, serverUsed || 0))
      : Math.max(0, serverUsed || 0);
  localStorage.setItem(
    DESKTOP_QUOTA_KEY,
    JSON.stringify({
      userId,
      limit: limit ?? null,
      used,
    } satisfies DesktopExamQuota),
  );
}

export function getDesktopExamQuota(): DesktopExamQuota | null {
  if (!isDesktop() || typeof window === "undefined") return null;
  try {
    const value = JSON.parse(
      localStorage.getItem(DESKTOP_QUOTA_KEY) || "null",
    ) as DesktopExamQuota | null;
    return value && Number.isFinite(value.used) ? value : null;
  } catch {
    return null;
  }
}

export function consumeDesktopExamQuota(): void {
  const quota = getDesktopExamQuota();
  if (!quota) return;
  localStorage.setItem(
    DESKTOP_QUOTA_KEY,
    JSON.stringify({ ...quota, used: quota.used + 1 } satisfies DesktopExamQuota),
  );
}

export async function desktopRuntime(): Promise<DesktopRuntime | null> {
  if (!isDesktop()) return null;
  if (!runtimePromise) {
    runtimePromise = import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<DesktopRuntime>("desktop_runtime"))
      .then((runtime) => {
        sessionStorage.setItem("smart-exam-local-api", runtime.localApi);
        sessionStorage.setItem("smart-exam-remote-api", runtime.remoteApi);
        sessionStorage.setItem("smart-exam-desktop-secret", runtime.secret);
        return runtime;
      });
  }
  return runtimePromise;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

function isPublicRemoteRequest(input: string, init: RequestInit): boolean {
  const method = (init.method || "GET").toUpperCase();
  return (
    input === "/api/v1/desktop/activate" ||
    input === "/api/v1/desktop/auth/register" ||
    input === "/api/v1/desktop/auth/login" ||
    input === "/api/v1/desktop/auth/logout" ||
    input === "/api/v1/desktop/auth/refresh" ||
    input === "/api/v1/auth/register" ||
    input === "/api/v1/auth/login" ||
    input === "/api/v1/auth/device-status" ||
    input === "/api/v1/auth/refresh" ||
    input.startsWith("/api/v1/class-session/") ||
    (method === "GET" &&
      (input === "/api/v1/policies/terms" ||
        input === "/api/v1/policies/privacy"))
  );
}

const CLASS_BROWSER_KEY = "smart-exam-class-browser-key";
const CLASS_SESSIONS_KEY = "smart-exam-class-sessions";

export type StoredClassSession = {
  token: string;
  classroomId: string;
  classroomName: string;
  fullName: string;
};

export function classBrowserKey(): string {
  if (typeof window === "undefined") return "";
  let value = localStorage.getItem(CLASS_BROWSER_KEY);
  if (!value) {
    const random = new Uint8Array(24);
    crypto.getRandomValues(random);
    value = Array.from(random, (item) => item.toString(16).padStart(2, "0")).join("");
    localStorage.setItem(CLASS_BROWSER_KEY, value);
  }
  return value;
}

export function storedClassSessions(): StoredClassSession[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(localStorage.getItem(CLASS_SESSIONS_KEY) || "{}") as Record<
      string,
      StoredClassSession
    >;
    return Object.values(value).filter((item) => Boolean(item.token && item.classroomId));
  } catch {
    return [];
  }
}

export function storedClassSession(classroomId: string): StoredClassSession | null {
  return storedClassSessions().find((item) => item.classroomId === classroomId) || null;
}

export function saveClassSession(value: StoredClassSession): void {
  if (typeof window === "undefined") return;
  const current = Object.fromEntries(
    storedClassSessions().map((item) => [item.classroomId, item]),
  );
  current[value.classroomId] = value;
  localStorage.setItem(CLASS_SESSIONS_KEY, JSON.stringify(current));
}

export function removeClassSession(classroomId: string): void {
  if (typeof window === "undefined") return;
  const current = Object.fromEntries(
    storedClassSessions()
      .filter((item) => item.classroomId !== classroomId)
      .map((item) => [item.classroomId, item]),
  );
  localStorage.setItem(CLASS_SESSIONS_KEY, JSON.stringify(current));
}

export async function classroomFetch(
  input: string,
  token: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Classroom-Session", token);
  return apiFetch(input, { ...init, headers });
}

function emitAuthChange(authenticated: boolean): void {
  if (typeof window !== "undefined") {
    if (!authenticated) {
      identityResolved = true;
      identityRole = null;
      sessionStorage.setItem("smart-exam-auth-known", "1");
      sessionStorage.removeItem("smart-exam-auth-role");
    }
    window.dispatchEvent(
      new CustomEvent("smart-exam-auth-changed", { detail: { authenticated } }),
    );
  }
}

function cacheIdentityRole(role: string | null): void {
  identityResolved = true;
  identityRole = role;
  if (typeof window === "undefined") return;
  sessionStorage.setItem("smart-exam-auth-known", "1");
  if (role) sessionStorage.setItem("smart-exam-auth-role", role);
  else sessionStorage.removeItem("smart-exam-auth-role");
}

export function acceptIdentity(
  role: string | null,
  userId?: string | null,
): void {
  cacheIdentityRole(role);
  bindDesktopUser(userId);
  if (role && ["admin", "teacher", "student", "user"].includes(role)) {
    writeDesktopOfflineAuth({
      role: role as Role,
      user: userId
        ? { id: userId, display_name: "", email: "" }
        : undefined,
      active_class_count: 0,
    });
  } else if (isDesktop()) {
    clearDesktopOfflineAuth();
  }
  emitAuthChange(Boolean(role));
}

export async function resolveAuthState(): Promise<AuthState> {
  const offlineState = (): AuthState | null => {
    const cached = readDesktopOfflineAuth();
    if (!cached) return null;
    bindDesktopUser(cached.user?.id);
    cacheIdentityRole(cached.role);
    return {
      state: "authenticated",
      authenticated: true,
      role: cached.role,
      user: cached.user,
      active_class_count: cached.active_class_count,
      offline: true,
    };
  };

  if (isDesktop() && typeof navigator !== "undefined" && !navigator.onLine) {
    const cached = offlineState();
    if (cached) return cached;
  }

  let response: Response;
  try {
    response = await apiFetch("/api/v1/auth/state", { cache: "no-store" });
  } catch (reason) {
    const cached = offlineState();
    if (cached) return cached;
    throw reason;
  }
  const payload = (await response.json().catch(() => ({}))) as Partial<AuthState> & {
    detail?: string;
  };
  if (!response.ok) {
    if (response.status >= 500) {
      const cached = offlineState();
      if (cached) return cached;
    }
    throw new Error(payload.detail || "Không kiểm tra được phiên đăng nhập");
  }
  if (payload.state === "authenticated") {
    bindDesktopUser(payload.user?.id);
    await applyDataEpoch(payload.data_epoch);
    cacheIdentityRole(payload.role || null);
    writeDesktopOfflineAuth({
      role: payload.role || null,
      user: payload.user,
      active_class_count: payload.active_class_count || 0,
    });
  } else if (isDesktop()) {
    // A successful authoritative response is allowed to revoke the local
    // offline capability. Transport failures never enter this branch.
    clearDesktopOfflineAuth();
    bindDesktopUser(null);
  }
  return {
    state: payload.state || "activation_required",
    authenticated: Boolean(payload.authenticated),
    role: payload.role || null,
    user: payload.user,
    active_class_count: payload.active_class_count || 0,
    data_epoch: payload.data_epoch,
  };
}

export function watchIdentityRole(
  onRole: (role: string | null) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  let stopped = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;

  const scheduleReconnect = () => {
    if (stopped || reconnectTimer) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, 3000);
  };

  const connect = async () => {
    if (stopped) return;
    const role = await resolveIdentity(true);
    if (!role) {
      // Before activation there is no identity socket to maintain. Wait for
      // the explicit auth-change event instead of polling /auth/me and
      // consuming the activation rate-limit budget.
      return;
    }
    const desktop = isDesktop();
    const runtime = desktop ? await desktopRuntime() : null;
    if (desktop && runtime && !accessToken) {
      await refreshDesktopAccess(runtime);
    }
    if (desktop && !accessToken) {
      scheduleReconnect();
      return;
    }
    const base = runtime?.remoteApi || window.location.origin;
    const websocketUrl = `${base.replace(/^http/, "ws")}/api/v1/ws/identity`;
    socket = new WebSocket(websocketUrl);
    socket.onopen = () => {
      socket?.send(
        JSON.stringify({
          access_token: desktop ? accessToken : undefined,
          device_key: desktop ? undefined : getDeviceKey(),
        }),
      );
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as {
          type?: string;
          role?: string | null;
        };
        if (payload.type !== "identity") return;
        const nextRole = payload.role || null;
        cacheIdentityRole(nextRole);
        onRole(nextRole);
      } catch {
        // Ignore malformed control frames; reconnect handles transport errors.
      }
    };
    socket.onclose = () => {
      socket = null;
      scheduleReconnect();
    };
    socket.onerror = () => socket?.close();
  };

  const reconnectForAuthChange = () => {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    socket?.close();
    socket = null;
    void connect();
  };
  window.addEventListener("smart-exam-auth-changed", reconnectForAuthChange);
  void connect();
  return () => {
    stopped = true;
    window.removeEventListener("smart-exam-auth-changed", reconnectForAuthChange);
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}

export async function resolveIdentity(force = false): Promise<string | null> {
  if (!force && identityResolved) return identityRole;
  if (!force && typeof window !== "undefined") {
    const known = sessionStorage.getItem("smart-exam-auth-known") === "1";
    if (known) {
      identityResolved = true;
      identityRole = sessionStorage.getItem("smart-exam-auth-role");
      return identityRole;
    }
  }
  if (!identityPromise) {
    identityPromise = apiFetch("/api/v1/auth/me", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return null;
        const payload = (await response.json()) as { role?: string };
        return payload.role || null;
      })
      .catch(() => null)
      .then((role) => {
        cacheIdentityRole(role);
        return role;
      })
      .finally(() => {
        identityPromise = null;
      });
  }
  return identityPromise;
}

export function cachedIdentity(): { ready: boolean; role: string | null } {
  if (identityResolved) return { ready: true, role: identityRole };
  if (typeof window === "undefined") return { ready: false, role: null };
  const ready = sessionStorage.getItem("smart-exam-auth-known") === "1";
  return {
    ready,
    role: ready ? sessionStorage.getItem("smart-exam-auth-role") : null,
  };
}

export function resolveIdentityAtStartup(): Promise<string | null> {
  if (!identityStartupCheckStarted) {
    identityStartupCheckStarted = true;
    return resolveIdentity(true);
  }
  return resolveIdentity();
}

export async function clearRefreshToken(): Promise<void> {
  cachedRefreshTokenInMemory = null;
  if (!isDesktop()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("clear_refresh_token").catch(() => undefined);
}

export async function abandonPendingListeningSession(): Promise<void> {
  if (typeof window === "undefined") return;
  const raw = sessionStorage.getItem("pending-listening-exam");
  if (!raw) return;
  let pending: { exam_id?: string | null; client_exam_id?: string | null } = {};
  try {
    pending = JSON.parse(raw) as typeof pending;
  } catch {
    sessionStorage.removeItem("pending-listening-exam");
    return;
  }

  const endpoint = isDesktop()
    ? pending.client_exam_id
      ? `/api/desktop/exams/${encodeURIComponent(pending.client_exam_id)}`
      : null
    : pending.exam_id
      ? `/api/v1/full-test-components/${encodeURIComponent(pending.exam_id)}`
      : null;
  if (endpoint) {
    const response = await apiFetch(endpoint, { method: "DELETE" });
    if (!response.ok && response.status !== 404) {
      const payload = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      throw new Error(payload.detail || "Không thể hủy phiên Full Test tạm");
    }
  }
  sessionStorage.removeItem("pending-listening-exam");
}

export async function logoutSession(): Promise<void> {
  await abandonPendingListeningSession().catch(() => undefined);
  try {
    if (isDesktop()) {
      const refresh = await loadRefreshToken();
      if (refresh) {
        await apiFetch("/api/v1/desktop/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
      }
    } else {
      await apiFetch("/api/v1/auth/logout", { method: "POST" });
    }
  } catch {
    // Local credential cleanup below is authoritative for packaged desktop.
  }
  accessToken = null;
  clearDesktopOfflineAuth();
  bindDesktopUser(null);
  if (isDesktop()) await clearRefreshToken().catch(() => undefined);
  identityResolved = true;
  identityRole = null;
  if (typeof window !== "undefined") {
    sessionStorage.removeItem("pending-listening-exam");
    sessionStorage.removeItem("smart-exam-auth-role");
    sessionStorage.setItem("smart-exam-auth-known", "1");
    window.dispatchEvent(new Event("smart-exam-auth-changed"));
  }
}

async function clearDesktopAuthentication(): Promise<void> {
  const wasAuthenticated = accessToken !== null;
  accessToken = null;
  clearDesktopOfflineAuth();
  bindDesktopUser(null);
  try {
    await clearRefreshToken();
  } catch {
    // A missing Windows credential is already the desired state.
  }
  if (wasAuthenticated) emitAuthChange(false);
}

async function refreshDesktopAccess(runtime: DesktopRuntime): Promise<boolean> {
  if (!authPromise) {
    authPromise = (async () => {
      const refresh = await loadRefreshToken();
      if (!refresh) return false;
      const response = await fetch(
        `${runtime.remoteApi}/api/v1/desktop/auth/refresh`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Examify-Desktop-Version": DESKTOP_APP_VERSION,
          },
          body: JSON.stringify({ refresh_token: refresh }),
        },
      );
      if (!response.ok) return false;
      const payload = (await response.json()) as {
        access_token?: string;
        user_id?: string;
        exam_limit?: number | null;
        exam_created_count?: number;
      };
      if (!payload.access_token) return false;
      accessToken = payload.access_token;
      bindDesktopUser(payload.user_id);
      updateDesktopExamQuota(
        payload.user_id,
        payload.exam_limit,
        payload.exam_created_count,
      );
      emitAuthChange(true);
      return true;
    })().finally(() => {
      authPromise = null;
    });
  }
  return authPromise;
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  if (
    !isDesktop() &&
    typeof input === "string" &&
    input.startsWith("/api/extractions")
  ) {
    console.info(
      `[OCR_ROUTE] location=REMOTE_SERVER transport=https endpoint=${input}`,
    );
  }
  if (
    !isDesktop() ||
    typeof input !== "string" ||
    (!input.startsWith("/api/") && input !== "/health/ready")
  ) {
    if (
      !isDesktop() &&
      typeof input === "string" &&
      input.startsWith("/api/")
    ) {
      const headers = new Headers(init.headers);
      headers.set("X-Examify-Device-Key", getDeviceKey());
      return fetch(input, { ...init, credentials: "include", headers });
    }
    return fetch(input, init);
  }
  const runtime = await desktopRuntime();
  if (!runtime) return fetch(input, init);
  const local =
    input.startsWith("/api/extractions") ||
    input.startsWith("/api/desktop") ||
    input === "/health/ready";
  const publicRemote = !local && isPublicRemoteRequest(input, init);
  const url = `${local ? runtime.localApi : runtime.remoteApi}${input}`;
  if (input.startsWith("/api/extractions")) {
    console.info(
      `[OCR_ROUTE] location=${local ? "LOCAL_EDGE" : "REMOTE_SERVER"} transport=${
        local ? "tauri-loopback" : "https"
      } endpoint=${input}`,
    );
  }

  const request = async (): Promise<Response> => {
    const headers = new Headers(init.headers);
    if (local) {
      headers.set("X-Desktop-Secret", runtime.secret);
      const userId = getDesktopActiveUserId();
      if (userId) headers.set("X-TOEICDOC-User-ID", userId);
    } else {
      headers.set("X-Examify-Desktop-Version", DESKTOP_APP_VERSION);
      if (!publicRemote && accessToken) {
        headers.set("Authorization", `Bearer ${accessToken}`);
      }
    }
    return fetch(url, {
      ...init,
      credentials: local ? "omit" : "include",
      headers,
    });
  };

  if (local) {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        return await request();
      } catch (error) {
        if (attempt === 2) throw error;
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      }
    }
    throw new Error("Không thể kết nối sidecar desktop");
  }

  if (!publicRemote && !accessToken) {
    await refreshDesktopAccess(runtime);
  }
  let response = await request();
  if (response.status === 401 && !publicRemote) {
    const refreshed = await refreshDesktopAccess(runtime);
    if (refreshed) response = await request();
    if (response.status === 401) await clearDesktopAuthentication();
  }
  return response;
}

export function assetUrl(url: string): string {
  if (!url) return "";
  if (!isDesktop()) {
    if (url.startsWith("/api/desktop/exams/")) {
      return url.replace("/api/desktop/exams/", "/api/v1/exams/");
    }
    // Older quiz drafts may still contain an absolute production URL. When a
    // learner opens that draft through a LAN IP/local gateway, keep the signed
    // path and query but send it through the current same-origin reverse proxy.
    if (typeof window !== "undefined" && /^https?:\/\//i.test(url)) {
      try {
        const parsed = new URL(url);
        const current = new URL(window.location.href);
        if (
          (parsed.hostname === "exam.congnhat.online" ||
            parsed.hostname === "www.exam.congnhat.online") &&
          parsed.origin !== current.origin
        ) {
          return `${current.origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
        }
      } catch {
        // Keep the original URL if it is malformed; the media component will
        // report the normal load error instead of breaking the quiz render.
      }
    }
    return url;
  }
  if (
    !url.startsWith("/api/extractions") && !url.startsWith("/api/desktop")
  )
    return url;
  const base =
    typeof window === "undefined"
      ? ""
      : sessionStorage.getItem("smart-exam-local-api") || "";
  const secret =
    typeof window === "undefined"
      ? ""
      : sessionStorage.getItem("smart-exam-desktop-secret") || "";
  const userId = getDesktopActiveUserId() || "";
  if (!secret) return `${base}${url}`;
  const params = new URLSearchParams();
  params.set("desktop_secret", secret);
  if (userId) params.set("desktop_user", userId);
  const separator = url.includes("?") ? "&" : "?";
  return `${base}${url}${separator}${params.toString()}`;
}

export function guideMediaUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (!isDesktop() || !url.startsWith("/api/v1/guide-media/")) return url;
  const remote =
    typeof window === "undefined"
      ? ""
      : sessionStorage.getItem("smart-exam-remote-api") || "";
  return `${remote}${url}`;
}

export function publicWebUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (typeof window === "undefined") return normalizedPath;
  if (!isDesktop()) return `${window.location.origin}${normalizedPath}`;
  const remote =
    sessionStorage.getItem("smart-exam-remote-api") || "https://exam.congnhat.online";
  return `${remote.replace(/\/$/, "")}${normalizedPath}`;
}

export function guideHtml(html: string): string {
  if (!isDesktop() || typeof window === "undefined") return html;
  const remote = sessionStorage.getItem("smart-exam-remote-api") || "";
  if (!remote) return html;
  return html.replace(
    /(["'])\/api\/v1\/guide-media\//g,
    `$1${remote}/api/v1/guide-media/`,
  );
}

let cachedRefreshTokenInMemory: string | null | undefined = undefined;

export async function saveRefreshToken(token: string): Promise<void> {
  if (!isDesktop()) return;
  cachedRefreshTokenInMemory = token;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("store_refresh_token", { token });
  identityResolved = false;
  identityRole = null;
  sessionStorage.removeItem("smart-exam-auth-known");
  sessionStorage.removeItem("smart-exam-auth-role");
  emitAuthChange(true);
}

export async function loadRefreshToken(): Promise<string | null> {
  if (!isDesktop()) return null;
  if (cachedRefreshTokenInMemory !== undefined) {
    return cachedRefreshTokenInMemory;
  }
  const { invoke } = await import("@tauri-apps/api/core");
  const token = await invoke<string | null>("load_refresh_token").catch(() => null);
  cachedRefreshTokenInMemory = token;
  return token;
}
