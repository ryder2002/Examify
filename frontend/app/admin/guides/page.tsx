"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BookOpen, ChevronLeft, ChevronRight, Edit3, Eye, EyeOff,
  Loader2, Plus, Search, Send, Trash2,
} from "lucide-react";

import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import { apiFetch, guideMediaUrl, resolveIdentity } from "@/lib/api";
import {
  formatGuideDate,
  statusLabel,
  type Guide,
  type GuideCategory,
} from "@/lib/guides";

const statusClass = {
  DRAFT: "border-amber-200 bg-amber-50 text-amber-800",
  PUBLISHED: "border-emerald-200 bg-emerald-50 text-emerald-800",
  HIDDEN: "border-slate-300 bg-slate-100 text-slate-600",
};

export default function AdminGuidesPage() {
  const router = useRouter();
  const [items, setItems] = useState<Guide[]>([]);
  const [categories, setCategories] = useState<GuideCategory[]>([]);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [status, setStatus] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [sort, setSort] = useState("order_asc");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [authorized, setAuthorized] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        q: debouncedQuery,
        status,
        category_id: categoryId,
        sort,
        page: String(page),
        page_size: "20",
      });
      const response = await apiFetch(`/api/v1/admin/guides?${params}`, { cache: "no-store" });
      const data = await response.json();
      if (response.status === 403) return router.replace("/my-exams");
      if (response.status === 401) return router.replace("/admin");
      if (!response.ok) throw new Error(data.detail || "Không tải được danh sách");
      setItems(data.items || []);
      setPages(data.pages || 1);
      setTotal(data.total || 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được danh sách");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    void resolveIdentity().then(async (role) => {
      if (!active) return;
      if (role !== "admin") {
        router.replace(role ? "/my-exams" : "/admin");
        return;
      }
      setAuthorized(true);
      const response = await apiFetch("/api/v1/guide-categories");
      if (response.ok && active) setCategories((await response.json()).items || []);
    });
    return () => { active = false; };
  }, [router]);

  useEffect(() => {
    if (authorized) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorized, debouncedQuery, status, categoryId, sort, page]);

  async function changeVisibility(guide: Guide) {
    const action = guide.status === "PUBLISHED" ? "unpublish" : "publish";
    const response = await apiFetch(`/api/v1/admin/guides/${guide.id}/${action}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) return setError(data.detail || "Không cập nhật được trạng thái");
    setItems((current) => current.map((item) => item.id === guide.id ? data : item));
  }

  async function remove(guide: Guide) {
    if (!window.confirm(`Xóa bài hướng dẫn “${guide.title}”? Media đã upload sẽ được giữ lại.`)) return;
    const response = await apiFetch(`/api/v1/admin/guides/${guide.id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return setError(data.detail || "Không xóa được bài hướng dẫn");
    setItems((current) => current.filter((item) => item.id !== guide.id));
    setTotal((current) => Math.max(0, current - 1));
  }

  return (
    <main className="bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">Quản trị nội dung</p>
            <h1 className="mt-1 flex items-center gap-3 text-3xl font-extrabold text-[#1f4e79]"><BookOpen className="h-8 w-8" /> Quản lý hướng dẫn sử dụng</h1>
            <p className="mt-1 text-sm text-slate-500">{total} bài hướng dẫn</p>
          </div>
          <Link href="/admin/guides/new" className="ui-btn-primary"><Plus className="h-5 w-5" /> Tạo hướng dẫn mới</Link>
        </div>

        <section className="ui-card mt-6 p-4">
          <div className="grid gap-3 md:grid-cols-[minmax(240px,1fr)_180px_220px_190px]">
            <label className="relative"><Search className="absolute left-3 top-2.5 h-5 w-5 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo tiêu đề..." className="w-full rounded-lg border border-slate-300 py-2.5 pl-10 pr-3 outline-none focus:border-[#1f4e79]" /></label>
            <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }} className="rounded-lg border border-slate-300 px-3 py-2.5"><option value="">Mọi trạng thái</option><option value="DRAFT">Bản nháp</option><option value="PUBLISHED">Đã đăng</option><option value="HIDDEN">Đã ẩn</option></select>
            <select value={categoryId} onChange={(event) => { setCategoryId(event.target.value); setPage(1); }} className="rounded-lg border border-slate-300 px-3 py-2.5"><option value="">Mọi danh mục</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select>
            <select value={sort} onChange={(event) => { setSort(event.target.value); setPage(1); }} className="rounded-lg border border-slate-300 px-3 py-2.5"><option value="order_asc">Thứ tự tăng dần</option><option value="order_desc">Thứ tự giảm dần</option><option value="created_desc">Mới tạo trước</option><option value="created_asc">Cũ tạo trước</option></select>
          </div>
        </section>

        {error && <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">{error}</div>}
        <section className="ui-card mt-5 overflow-hidden">
          {loading ? (
            <ExamifyLoader fullScreen={false} message="Đang tải bài hướng dẫn..." />
          ) : items.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center px-5 text-center"><BookOpen className="h-12 w-12 text-slate-300" /><h2 className="mt-3 text-lg font-extrabold text-slate-700">Chưa có bài hướng dẫn</h2><p className="mt-1 text-sm text-slate-500">Thay đổi bộ lọc hoặc tạo bài hướng dẫn đầu tiên.</p></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-500"><tr><th className="px-5 py-3">Bài viết</th><th className="px-4 py-3">Danh mục</th><th className="px-4 py-3">Trạng thái</th><th className="px-4 py-3">Thứ tự</th><th className="px-4 py-3">Ngày tạo / cập nhật</th><th className="px-4 py-3">Người tạo</th><th className="px-5 py-3 text-right">Thao tác</th></tr></thead>
                <tbody>{items.map((guide) => (
                  <tr key={guide.id} className="border-b border-slate-100 align-middle hover:bg-slate-50/70">
                    <td className="px-5 py-4"><div className="flex items-center gap-3">{guide.thumbnail_url ? <img src={guideMediaUrl(guide.thumbnail_url)} alt="" className="h-14 w-24 rounded-lg border border-slate-200 object-cover" /> : <div className="flex h-14 w-24 items-center justify-center rounded-lg bg-slate-100"><BookOpen className="h-5 w-5 text-slate-400" /></div>}<div><p className="max-w-sm font-extrabold text-[#1f4e79]">{guide.title}</p><p className="mt-0.5 max-w-sm truncate text-xs text-slate-500">/{guide.slug}</p></div></div></td>
                    <td className="px-4 py-4">{guide.category?.name || "—"}</td>
                    <td className="px-4 py-4"><span className={`rounded-full border px-2.5 py-1 text-xs font-bold ${statusClass[guide.status]}`}>{statusLabel(guide.status)}</span></td>
                    <td className="px-4 py-4 font-bold">{guide.sort_order}</td>
                    <td className="px-4 py-4 text-xs text-slate-600"><p>{formatGuideDate(guide.created_at)}</p><p className="mt-1">Sửa: {formatGuideDate(guide.updated_at)}</p></td>
                    <td className="px-4 py-4">{guide.creator_name || "—"}</td>
                    <td className="px-5 py-4"><div className="flex justify-end gap-1.5">
                      <Link href={`/admin/guides/edit?id=${guide.id}`} title="Chỉnh sửa" className="rounded-lg border border-slate-300 p-2 hover:border-[#1f4e79]"><Edit3 className="h-4 w-4" /></Link>
                      <Link href={`/admin/guides/preview?id=${guide.id}`} target="_blank" title="Xem trước" className="rounded-lg border border-slate-300 p-2 hover:border-[#1f4e79]"><Eye className="h-4 w-4" /></Link>
                      <button type="button" onClick={() => void changeVisibility(guide)} title={guide.status === "PUBLISHED" ? "Ẩn bài" : "Đăng bài"} className="rounded-lg border border-slate-300 p-2 hover:border-[#1f4e79]">{guide.status === "PUBLISHED" ? <EyeOff className="h-4 w-4" /> : <Send className="h-4 w-4" />}</button>
                      <button type="button" onClick={() => void remove(guide)} title="Xóa" className="rounded-lg border border-red-200 p-2 text-red-600 hover:bg-red-50"><Trash2 className="h-4 w-4" /></button>
                    </div></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>

        <div className="mt-5 flex items-center justify-between text-sm text-slate-600">
          <span>Trang {page}/{pages}</span>
          <div className="flex gap-2"><button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)} className="ui-btn-secondary px-3 py-2"><ChevronLeft className="h-4 w-4" /> Trước</button><button type="button" disabled={page >= pages || loading} onClick={() => setPage((value) => value + 1)} className="ui-btn-secondary px-3 py-2">Sau <ChevronRight className="h-4 w-4" /></button></div>
        </div>
      </div>
    </main>
  );
}
