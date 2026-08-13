"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowLeft, ArrowRight, BookOpen, CalendarDays } from "lucide-react";

import { formatGuideDate, type Guide } from "@/lib/guides";
import { guideHtml, guideMediaUrl, isDesktop } from "@/lib/api";

export default function GuideArticle({
  guide,
  preview = false,
}: {
  guide: Guide;
  preview?: boolean;
}) {
  const html = useMemo(() => guideHtml(guide.rendered_html || ""), [guide.rendered_html]);
  const openLink = async (event: React.MouseEvent<HTMLElement>) => {
    const anchor = (event.target as HTMLElement).closest("a");
    const href = anchor?.getAttribute("href");
    if (!anchor || !href || href.startsWith("#") || !isDesktop()) return;
    event.preventDefault();
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_external_url", { url: href });
  };

  return (
    <div className="mx-auto max-w-[1380px] px-5 py-8 sm:px-8">
      <Link href={preview ? "/admin/guides" : "/guides"} className="inline-flex items-center gap-1.5 text-sm font-bold text-slate-500 hover:text-[#1f4e79]"><ArrowLeft className="h-4 w-4" /> {preview ? "Quản lý hướng dẫn" : "Tất cả hướng dẫn"}</Link>
      {preview && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900">Chế độ xem trước — bài viết này có thể chưa được công khai.</div>}
      <header className="mt-6 overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
        {guide.thumbnail_url && <img src={guideMediaUrl(guide.thumbnail_url)} alt={guide.title} className="max-h-[440px] w-full object-cover" />}
        <div className="p-6 sm:p-10">
          <div className="flex flex-wrap items-center gap-3 text-xs font-bold uppercase tracking-wide text-slate-500">
            {guide.category && <span className="rounded-full bg-blue-50 px-3 py-1.5 text-blue-800">{guide.category.name}</span>}
            <span className="inline-flex items-center gap-1.5"><CalendarDays className="h-4 w-4" /> Cập nhật {formatGuideDate(guide.updated_at)}</span>
          </div>
          <h1 className="mt-4 max-w-5xl text-3xl font-black leading-tight text-[#1f4e79] sm:text-5xl">{guide.title}</h1>
          {guide.summary && <p className="mt-5 max-w-4xl text-lg leading-relaxed text-slate-600">{guide.summary}</p>}
        </div>
      </header>

      <article onClick={(event) => void openLink(event)} className="guide-content mt-6 min-w-0 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10" dangerouslySetInnerHTML={{ __html: html }} />

      {!preview && (guide.previous || guide.next) && (
        <nav className="mt-7 grid gap-4 sm:grid-cols-2">
          {guide.previous ? <Link href={`/guides/read?slug=${encodeURIComponent(guide.previous.slug)}`} className="ui-card group p-5"><span className="inline-flex items-center gap-1 text-xs font-bold uppercase text-slate-400"><ArrowLeft className="h-4 w-4" /> Bài trước</span><p className="mt-2 font-extrabold text-[#1f4e79] group-hover:text-blue-700">{guide.previous.title}</p></Link> : <span />}
          {guide.next && <Link href={`/guides/read?slug=${encodeURIComponent(guide.next.slug)}`} className="ui-card group p-5 text-right"><span className="inline-flex items-center gap-1 text-xs font-bold uppercase text-slate-400">Bài tiếp theo <ArrowRight className="h-4 w-4" /></span><p className="mt-2 font-extrabold text-[#1f4e79] group-hover:text-blue-700">{guide.next.title}</p></Link>}
        </nav>
      )}
      {!html && <div className="mt-6 flex min-h-56 items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white text-slate-500"><BookOpen className="mr-2 h-6 w-6" /> Bài viết chưa có nội dung.</div>}
    </div>
  );
}
