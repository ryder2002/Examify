"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  BookOpen, ChevronLeft, ChevronRight, Clock3, Search,
} from "lucide-react";

import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import { apiFetch, guideMediaUrl } from "@/lib/api";
import { formatGuideDate, type Guide, type GuideCategory } from "@/lib/guides";

export default function GuidesPage() {
  const [items, setItems] = useState<Guide[]>([]);
  const [categories, setCategories] = useState<GuideCategory[]>([]);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => { setDebounced(query); setPage(1); }, 350);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    void apiFetch("/api/v1/guide-categories").then(async (response) => {
      if (response.ok) setCategories((await response.json()).items || []);
    });
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ q: debounced, category, page: String(page), page_size: "12" });
        const response = await apiFetch(`/api/v1/guides?${params}`, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không tải được hướng dẫn");
        if (active) { setItems(data.items || []); setPages(data.pages || 1); }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Không tải được hướng dẫn");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [debounced, category, page]);

  return (
    <main className="bg-slate-50">
      <Header />
      <section className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-[1380px] px-5 py-12 text-center sm:px-8 sm:py-16">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-[#1f4e79] text-white shadow-lg"><BookOpen className="h-7 w-7" /></span>
          <h1 className="mt-5 text-4xl font-black text-[#1f4e79] sm:text-5xl">Hướng dẫn sử dụng</h1>
          <p className="mx-auto mt-3 max-w-2xl text-slate-600">Tìm câu trả lời nhanh và khám phá đầy đủ các tính năng của Examify.</p>
          <label className="relative mx-auto mt-7 block max-w-2xl text-left"><Search className="absolute left-4 top-3.5 h-5 w-5 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Bạn cần tìm hướng dẫn gì?" className="w-full rounded-2xl border border-slate-300 bg-white py-3.5 pl-12 pr-4 shadow-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10" /></label>
        </div>
      </section>
      <div className="mx-auto max-w-[1380px] px-5 py-9 sm:px-8">
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => { setCategory(""); setPage(1); }} className={`rounded-full border px-4 py-2 text-sm font-bold ${!category ? "border-[#1f4e79] bg-[#1f4e79] text-white" : "border-slate-300 bg-white text-slate-600"}`}>Tất cả</button>
          {categories.map((item) => <button type="button" key={item.id} onClick={() => { setCategory(item.slug); setPage(1); }} className={`rounded-full border px-4 py-2 text-sm font-bold ${category === item.slug ? "border-[#1f4e79] bg-[#1f4e79] text-white" : "border-slate-300 bg-white text-slate-600"}`}>{item.name}</button>)}
        </div>
        {error && <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}
        {loading ? (
          <ExamifyLoader fullScreen={false} message="Đang tải hướng dẫn..." />
        ) : items.length === 0 ? (
          <div className="mt-8 flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white text-center"><Search className="h-10 w-10 text-slate-300" /><h2 className="mt-3 text-lg font-extrabold text-slate-700">Không tìm thấy bài hướng dẫn phù hợp.</h2><p className="mt-1 text-sm text-slate-500">Thử từ khóa hoặc danh mục khác.</p></div>
        ) : (
          <div className="mt-7 grid gap-5 md:grid-cols-2 xl:grid-cols-3">{items.map((guide) => (
            <Link key={guide.id} href={`/guides/read?slug=${encodeURIComponent(guide.slug)}`} className="group overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-1 hover:border-slate-300 hover:shadow-lg">
              {guide.thumbnail_url ? <img src={guideMediaUrl(guide.thumbnail_url)} alt={guide.title} className="aspect-[16/9] w-full object-cover" /> : <div className="flex aspect-[16/9] items-center justify-center bg-gradient-to-br from-slate-100 to-slate-200"><BookOpen className="h-12 w-12 text-slate-400" /></div>}
              <div className="p-5">
                {guide.category && <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-800">{guide.category.name}</span>}
                <h2 className="mt-3 text-xl font-extrabold leading-snug text-[#1f4e79] group-hover:text-blue-700">{guide.title}</h2>
                <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-slate-600">{guide.summary || "Xem nội dung hướng dẫn chi tiết."}</p>
                <p className="mt-4 flex items-center gap-1.5 text-xs font-semibold text-slate-400"><Clock3 className="h-4 w-4" /> Cập nhật {formatGuideDate(guide.updated_at)}</p>
              </div>
            </Link>
          ))}</div>
        )}
        {pages > 1 && <div className="mt-8 flex items-center justify-center gap-3"><button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)} className="ui-btn-secondary px-3 py-2"><ChevronLeft className="h-4 w-4" /> Trước</button><span className="text-sm font-bold text-slate-600">Trang {page}/{pages}</span><button type="button" disabled={page >= pages} onClick={() => setPage((value) => value + 1)} className="ui-btn-secondary px-3 py-2">Sau <ChevronRight className="h-4 w-4" /></button></div>}
      </div>
    </main>
  );
}
