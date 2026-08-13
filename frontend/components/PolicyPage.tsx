"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import Header from "@/components/Header";
import { apiFetch, isDesktop } from "@/lib/api";

type Policy = {
  title?: string;
  content?: string;
  rendered_html?: string;
};

export default function PolicyPage({
  policyKey,
  eyebrow,
  icon,
  fallbackTitle,
}: {
  policyKey: "terms" | "privacy";
  eyebrow: string;
  icon: ReactNode;
  fallbackTitle: string;
}) {
  const [policy, setPolicy] = useState<Policy>({ title: fallbackTitle });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const response = await apiFetch(`/api/v1/policies/${policyKey}`);
        if (response.ok) {
          setPolicy(await response.json());
          return;
        }
        throw new Error("remote policy unavailable");
      } catch {
        if (isDesktop()) {
          const fallback = await apiFetch(`/api/desktop/policies/${policyKey}`).catch(() => null);
          if (fallback?.ok) setPolicy(await fallback.json());
        }
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [policyKey]);

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
        <div className="rounded-2xl border border-slate-300 bg-white p-6 shadow-md sm:p-10">
          <div className="flex items-center gap-3 border-b border-slate-200 pb-6">
            <span className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-[#1f4e79]">{icon}</span>
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-slate-500">{eyebrow}</p>
              <h1 className="text-2xl font-extrabold text-[#1f4e79] sm:text-3xl">{policy.title || fallbackTitle}</h1>
            </div>
          </div>
          {loading ? (
            <div className="flex min-h-64 items-center justify-center text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Đang tải...</div>
          ) : policy.rendered_html ? (
            <article className="policy-content mt-8 text-slate-700" dangerouslySetInnerHTML={{ __html: policy.rendered_html }} />
          ) : (
            <article className="mt-8 whitespace-pre-wrap leading-relaxed text-slate-700">{policy.content || "Đang cập nhật nội dung..."}</article>
          )}
        </div>
      </div>
    </main>
  );
}
