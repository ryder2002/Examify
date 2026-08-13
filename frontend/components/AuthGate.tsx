"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Image from "next/image";
import { KeyRound, Loader2, LogIn } from "lucide-react";

import { apiFetch, DESKTOP_APP_VERSION, isDesktop, resolveAuthState, roleLanding, type Role } from "@/lib/api";
import {
  getDeviceIdentity,
  isDeviceActivated,
  markDeviceActivated,
} from "@/lib/device";
import ExamifyLoader from "@/components/ExamifyLoader";

const PUBLIC_PAGES = new Set(["/login", "/register", "/terms", "/privacy"]);

export function normalizeAppPathname(pathname: string): string {
  if (!pathname || pathname === "/") return "/";
  return pathname.replace(/\/+$/, "") || "/";
}

function routeAllowed(role: Role, pathname: string): boolean {
  if (pathname === "/public-test" || pathname.startsWith("/public-test/")) return true;
  if (role === "admin") return pathname.startsWith("/admin");
  if (role === "student") {
    return ["/exam-bank", "/classrooms", "/history", "/quiz", "/result", "/solutions"].some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
  }
  if (role === "teacher") {
    return ["/", "/exam-bank", "/my-exams", "/classrooms", "/history", "/review", "/quiz", "/result", "/solutions"].some(
      (prefix) => prefix === "/" ? pathname === "/" : pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
  }
  return ["/", "/my-exams", "/history", "/review", "/quiz", "/result", "/solutions"].some(
    (prefix) => prefix === "/" ? pathname === "/" : pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function ActivationDialog({ onRetry }: { onRetry: () => void }) {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canLogin, setCanLogin] = useState(false);

  useEffect(() => {
    setCanLogin(isDeviceActivated());
    void getDeviceIdentity()
      .then((deviceKey) =>
        apiFetch("/api/v1/auth/device-status", {
          cache: "no-store",
          headers: { "X-Examify-Device-Key": deviceKey },
        }),
      )
      .then(async (response) => {
        if (!response.ok) return;
        const payload = await response.json().catch(() => ({}));
        if (payload.activated === true) {
          markDeviceActivated();
          setCanLogin(true);
        }
      })
      .catch(() => undefined);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const desktop = isDesktop();
      const deviceKey = await getDeviceIdentity();
      const response = await apiFetch(
        desktop ? "/api/v1/desktop/activate" : "/api/v1/activations/redeem",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code,
            device_key: deviceKey,
            device_name: desktop ? "Examify Desktop" : "Web Browser",
            platform: navigator.platform,
            app_version: desktop ? DESKTOP_APP_VERSION : "web-0.1.2",
            client_kind: desktop ? "desktop" : "web",
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không thể kích hoạt ứng dụng");
      markDeviceActivated();
      setCanLogin(true);
      if (payload.setup_token) {
        sessionStorage.setItem("smart-exam-onboarding-token", payload.setup_token);
      }
      router.replace(payload.next === "register" ? "/register" : "/login");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể kích hoạt ứng dụng");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-6 py-12">
      <div role="dialog" aria-modal="true" className="grid w-full max-w-6xl min-h-[580px] overflow-hidden rounded-3xl border border-slate-300 bg-white shadow-2xl lg:grid-cols-[1.1fr_460px]">
        <section className="flex flex-col justify-between bg-[#1f4e79] p-10 text-white sm:p-14 lg:p-16">
          <div>
            <div className="flex items-center justify-start w-full shrink-0">
              <Image
                src="/logo.png"
                alt="Examify Logo"
                width={512}
                height={512}
                priority
                unoptimized
                className="h-16 sm:h-20 md:h-24 lg:h-28 w-auto object-contain shrink-0 self-start drop-shadow-sm"
              />
            </div>
            <h1 className="mt-8 text-3xl sm:text-4xl font-black tracking-tight">Kích hoạt Examify</h1>
            <p className="mt-4 max-w-xl text-base sm:text-lg leading-8 text-slate-100 font-medium">
              Ứng dụng cần được kích hoạt trước khi đăng ký tài khoản và sử dụng các chức năng bên trong.
            </p>
          </div>
          <div className="mt-10 rounded-2xl border border-white/25 bg-white/10 p-5 text-sm sm:text-base text-slate-100 backdrop-blur-sm">
            Mỗi Key được cấu hình tối đa 1–2 thiết bị. Không chia sẻ Key hoặc thông tin đăng nhập.
          </div>
        </section>
        <section className="flex flex-col justify-center p-9 sm:p-12 lg:p-14">
          <h2 className="text-2xl sm:text-3xl font-extrabold text-[#1f4e79]">Nhập Key kích hoạt</h2>
          <form onSubmit={submit} className="mt-8 space-y-5">
            <input
              value={code}
              onChange={(event) => setCode(event.target.value.toUpperCase())}
              placeholder="EXAMIFY-XXXX-XXXX-XXXX-XXXX"
              autoFocus
              className="w-full rounded-2xl border border-slate-300 px-5 py-4 text-lg font-mono uppercase tracking-wider outline-none focus:border-[#1f4e79] focus:ring-4 focus:ring-[#1f4e79]/15 shadow-inner"
            />
            {error && <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm font-medium text-red-800">{error}</div>}
            <button disabled={loading || !code.trim()} className="ui-btn-primary w-full py-4 text-base font-bold rounded-2xl shadow-lg">
              {loading ? <Loader2 className="h-6 w-6 animate-spin" /> : <KeyRound className="h-6 w-6" />}
              Kích hoạt ngay
            </button>
          </form>
          {canLogin && (
            <>
              <div className="my-6 flex items-center gap-3 text-xs font-bold text-slate-400"><span className="h-px flex-1 bg-slate-200" />HOẶC<span className="h-px flex-1 bg-slate-200" /></div>
              <button onClick={() => router.push("/login")} className="ui-btn-secondary w-full py-4 text-base font-bold rounded-2xl">
                <LogIn className="h-5 w-5" /> Đã có tài khoản? Đăng nhập
              </button>
            </>
          )}
          {error && <button onClick={onRetry} className="mt-4 w-full text-xs font-bold text-slate-500 hover:text-[#1f4e79]">Kiểm tra lại kết nối</button>}
        </section>
      </div>
    </main>
  );
}

export default function AuthGate({ children }: { children: ReactNode }) {
  const pathname = normalizeAppPathname(usePathname());
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [showActivation, setShowActivation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checkGeneration = useRef(0);

  const check = useCallback(async () => {
    const generation = ++checkGeneration.current;
    const isCurrent = () => generation === checkGeneration.current;
    if (PUBLIC_PAGES.has(pathname) || pathname === "/public-test" || pathname.startsWith("/public-test/")) {
      setChecking(false);
      setShowActivation(false);
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const state = await resolveAuthState();
      if (!isCurrent()) return;
      if (state.state === "registration_required") {
        router.replace("/register");
        return;
      }
      if (!state.authenticated || !state.role) {
        setShowActivation(true);
        setChecking(false);
        return;
      }
      if (
        state.role === "student" &&
        (pathname === "/quiz" || pathname.startsWith("/quiz/"))
      ) {
        const source = sessionStorage.getItem("quiz-attempt-source");
        const rawContext = sessionStorage.getItem("quiz-class-session");
        const attemptId = sessionStorage.getItem("quiz-attempt-id");
        if (source === "bank" && attemptId) {
          setShowActivation(false);
          setChecking(false);
          return;
        }
        try {
          const context = rawContext ? JSON.parse(rawContext) : null;
          if (!attemptId || context?.accountAuth !== true || !context?.classroomId) {
            router.replace("/exam-bank");
            return;
          }
        } catch {
          router.replace("/exam-bank");
          return;
        }
      }
      if (!routeAllowed(state.role, pathname)) {
        router.replace(roleLanding(state.role));
        return;
      }
      setShowActivation(false);
      setChecking(false);
    } catch (reason) {
      if (!isCurrent()) return;
      setError(reason instanceof Error ? reason.message : "Không kết nối được máy chủ");
      // A container rebuild briefly makes the API unavailable. This is not an
      // authentication failure: keep the durable refresh cookie and retry.
      setShowActivation(false);
      setChecking(false);
    }
  }, [pathname, router]);

  useEffect(() => {
    if (pathname === "/activate") {
      router.replace("/");
      return;
    }
    void check();
    return () => {
      checkGeneration.current += 1;
    };
  }, [check, pathname, router]);

  useEffect(() => {
    if (!error || showActivation) return;
    const timer = window.setTimeout(() => void check(), 2000);
    return () => window.clearTimeout(timer);
  }, [check, error, showActivation]);

  if (checking) {
    return <ExamifyLoader message="Đang kiểm tra phiên..." />;
  }
  if (showActivation) return <ActivationDialog onRetry={() => void check()} />;
  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 p-8">
        <div className="text-center text-[#1f4e79]">
          <Loader2 className="mx-auto h-8 w-8 animate-spin" />
          <p className="mt-3 font-bold">Máy chủ đang khởi động lại…</p>
          <p className="mt-1 text-sm text-slate-500">Phiên đăng nhập vẫn được giữ. Hệ thống đang tự kết nối lại.</p>
          <button type="button" onClick={() => void check()} className="ui-btn-secondary mt-4 px-4 py-2 text-sm">
            Thử lại ngay
          </button>
        </div>
      </main>
    );
  }
  return <>{children}</>;
}
