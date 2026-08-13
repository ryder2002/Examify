"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import GuideArticle from "@/components/GuideArticle";
import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import { apiFetch, resolveIdentity } from "@/lib/api";
import type { Guide } from "@/lib/guides";

function PreviewContent() {
  const router = useRouter();
  const id = useSearchParams().get("id");
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void resolveIdentity().then(async (role) => {
      if (role !== "admin") return router.replace(role ? "/my-exams" : "/admin");
      if (!id) return setError("Thiếu mã bài hướng dẫn.");
      const response = await apiFetch(`/api/v1/admin/guides/${id}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) return setError(data.detail || "Không tải được bản xem trước");
      setGuide(data);
    });
  }, [id, router]);
  return (
    <main className="bg-slate-50"><Header />{error ? <div className="mx-auto max-w-2xl p-10 text-center font-bold text-red-700">{error}</div> : guide ? <GuideArticle guide={guide} preview /> : <ExamifyLoader fullScreen={false} message="Đang tạo bản xem trước..." />}</main>
  );
}

export default function GuidePreviewPage() {
  return <Suspense fallback={null}><PreviewContent /></Suspense>;
}
