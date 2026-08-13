"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen } from "lucide-react";

import GuideArticle from "@/components/GuideArticle";
import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import { apiFetch } from "@/lib/api";
import type { Guide } from "@/lib/guides";

function GuideReader() {
  const slug = useSearchParams().get("slug");
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!slug) return setError("Không tìm thấy bài hướng dẫn.");
    let active = true;
    void apiFetch(`/api/v1/guides/${encodeURIComponent(slug)}`, { cache: "no-store" }).then(async (response) => {
      const data = await response.json();
      if (!active) return;
      if (!response.ok) setError(data.detail || "Không tìm thấy bài hướng dẫn.");
      else setGuide(data);
    }).catch(() => active && setError("Không thể kết nối máy chủ."));
    return () => { active = false; };
  }, [slug]);
  return (
    <main className="bg-slate-50"><Header />{error ? <div className="mx-auto flex min-h-[55vh] max-w-xl flex-col items-center justify-center px-5 text-center"><BookOpen className="h-12 w-12 text-slate-300" /><h1 className="mt-4 text-xl font-extrabold text-slate-700">{error}</h1></div> : guide ? <GuideArticle guide={guide} /> : <ExamifyLoader fullScreen={false} message="Đang tải hướng dẫn..." />}</main>
  );
}

export default function GuideReadPage() {
  return <Suspense fallback={null}><GuideReader /></Suspense>;
}
