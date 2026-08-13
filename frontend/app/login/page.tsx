"use client";

import Link from "next/link";
import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { KeyRound, Loader2, LogIn, ShieldCheck } from "lucide-react";

import {
  acceptIdentity,
  apiFetch,
  bindDesktopUser,
  isDesktop,
  resolveAuthState,
  roleLanding,
  saveRefreshToken,
  setAccessToken,
  updateDesktopExamQuota,
} from "@/lib/api";
import { getDeviceIdentity, markDeviceActivated } from "@/lib/device";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState(searchParams.get("email") || "");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void resolveAuthState().then((state) => {
      if (state.authenticated && state.role) router.replace(roleLanding(state.role));
    }).catch(() => undefined);
  }, [router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const desktop = isDesktop();
      const deviceKey = await getDeviceIdentity();
      const response = await apiFetch(
        desktop ? "/api/v1/desktop/auth/login" : "/api/v1/auth/login",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email,
            password,
            device_key: deviceKey,
            device_name: desktop ? "Examify Desktop" : "Web Browser",
            platform: navigator.platform,
            client_kind: desktop ? "desktop" : "web",
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không thể đăng nhập");
      if (desktop) {
        setAccessToken(payload.access_token);
        await saveRefreshToken(payload.refresh_token);
        bindDesktopUser(payload.user_id);
        updateDesktopExamQuota(payload.user_id, payload.exam_limit, payload.exam_created_count);
      }
      markDeviceActivated();
      acceptIdentity(payload.role || null, payload.user_id || null);
      router.replace(roleLanding(payload.role || null));
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể đăng nhập");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-6 py-12">
      <section className="ui-card w-full max-w-xl p-9 sm:p-12 shadow-2xl rounded-3xl">
        <div className="mb-8 flex flex-col items-center text-center shrink-0">
          <div className="relative flex w-full justify-center shrink-0">
            <Image
              src="/logo.png"
              alt="Examify Logo"
              width={512}
              height={512}
              priority
              unoptimized
              className="h-20 sm:h-24 md:h-28 w-auto object-contain shrink-0 self-center"
            />
          </div>
        </div>
        <div className="flex items-center gap-3 text-[#1f4e79]"><LogIn className="h-8 w-8" /><h1 className="text-3xl font-extrabold tracking-tight">Đăng nhập Examify</h1></div>
        <div className="mt-5 flex gap-3.5 rounded-2xl border border-blue-200 bg-blue-50/80 p-4 text-sm sm:text-base leading-7 text-blue-950">
          <ShieldCheck className="mt-0.5 h-6 w-6 shrink-0 text-[#1f4e79]" />
          <p>Chỉ tài khoản đã kích hoạt bằng Key và hoàn tất đăng ký mới có thể đăng nhập.</p>
        </div>
        {searchParams.get("registered") === "1" && <div className="mt-5 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-medium text-emerald-900">Đăng ký thành công. Vui lòng đăng nhập để tiếp tục.</div>}
        <form onSubmit={submit} className="mt-8 space-y-5">
          <label className="block text-sm sm:text-base font-bold text-slate-700">Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" className="mt-1.5 w-full rounded-2xl border border-slate-300 px-4 py-3.5 text-base font-normal outline-none focus:border-[#1f4e79] focus:ring-4 focus:ring-[#1f4e79]/15 shadow-inner" required /></label>
          <label className="block text-sm sm:text-base font-bold text-slate-700">Mật khẩu<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} maxLength={128} autoComplete="current-password" className="mt-1.5 w-full rounded-2xl border border-slate-300 px-4 py-3.5 text-base font-normal outline-none focus:border-[#1f4e79] focus:ring-4 focus:ring-[#1f4e79]/15 shadow-inner" required /></label>
          {error && <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm font-medium text-red-800">{error}</div>}
          <button disabled={loading} className="ui-btn-primary w-full py-4 text-base font-bold rounded-2xl shadow-lg">{loading ? <Loader2 className="h-6 w-6 animate-spin" /> : <LogIn className="h-6 w-6" />} Đăng nhập ngay</button>
        </form>
        <button onClick={() => router.push("/")} className="ui-btn-secondary mt-4 w-full py-4 text-base font-bold rounded-2xl"><KeyRound className="h-5 w-5" /> Chưa kích hoạt? Nhập Key</button>
        <p className="mt-8 text-center text-sm text-slate-500"><Link href="/terms" className="hover:underline font-medium">Điều khoản</Link> · <Link href="/privacy" className="hover:underline font-medium">Chính sách riêng tư</Link></p>
      </section>
    </main>
  );
}
