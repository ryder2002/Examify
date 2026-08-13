"use client";

import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, UserPlus } from "lucide-react";

import { apiFetch, isDesktop, resolveAuthState } from "@/lib/api";
import ExamifyLoader from "@/components/ExamifyLoader";

export default function RegisterPage() {
  const router = useRouter();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void resolveAuthState()
      .then((state) => {
        const desktopSetup = sessionStorage.getItem("smart-exam-onboarding-token");
        if (state.state !== "registration_required" && !desktopSetup) router.replace("/");
        else setReady(true);
      })
      .catch(() => {
        if (sessionStorage.getItem("smart-exam-onboarding-token")) setReady(true);
        else router.replace("/");
      });
  }, [router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("Xác nhận mật khẩu không khớp");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const desktop = isDesktop();
      const response = await apiFetch(
        desktop ? "/api/v1/desktop/auth/register" : "/api/v1/auth/register",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: displayName,
            email,
            password,
            password_confirmation: confirmation,
            setup_token: desktop ? sessionStorage.getItem("smart-exam-onboarding-token") : undefined,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không thể đăng ký");
      sessionStorage.removeItem("smart-exam-onboarding-token");
      router.replace(`/login?registered=1&email=${encodeURIComponent(payload.email || email)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể đăng ký");
    } finally {
      setLoading(false);
    }
  }

  if (!ready) return <ExamifyLoader message="Đang kiểm tra trạng thái đăng ký..." />;

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-5 py-10">
      <section className="ui-card w-full max-w-lg p-7 sm:p-9">
        <div className="mb-6 flex flex-col items-center text-center">
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
        <div className="flex items-center gap-3 text-[#1f4e79]"><UserPlus className="h-7 w-7" /><h1 className="text-2xl font-extrabold">Đăng ký tài khoản</h1></div>
        <p className="mt-2 text-sm text-slate-500">Vai trò của tài khoản đã được xác định bởi Key kích hoạt.</p>
        <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block text-sm font-bold text-slate-700">Họ và tên<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={2} maxLength={160} autoComplete="name" className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-3 font-normal outline-none focus:border-[#1f4e79]" required /></label>
          <label className="block text-sm font-bold text-slate-700">Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={320} autoComplete="email" className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-3 font-normal outline-none focus:border-[#1f4e79]" required /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-bold text-slate-700">Mật khẩu<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} maxLength={128} autoComplete="new-password" className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-3 font-normal outline-none focus:border-[#1f4e79]" required /></label>
            <label className="block text-sm font-bold text-slate-700">Xác nhận mật khẩu<input type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} minLength={8} maxLength={128} autoComplete="new-password" className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-3 font-normal outline-none focus:border-[#1f4e79]" required /></label>
          </div>
          {error && <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
          <button disabled={loading} className="ui-btn-primary w-full py-3">{loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <UserPlus className="h-5 w-5" />} Tạo tài khoản</button>
        </form>
      </section>
    </main>
  );
}
