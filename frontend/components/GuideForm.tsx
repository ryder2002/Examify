"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft, CheckCircle2, Eye, ImagePlus, Loader2, Plus,
  Save, Send, TriangleAlert, X,
} from "lucide-react";

import GuideEditor from "@/components/GuideEditor";
import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import { apiFetch, guideMediaUrl, resolveIdentity } from "@/lib/api";
import {
  EMPTY_GUIDE_CONTENT,
  type Guide,
  type GuideCategory,
  type GuideMedia,
} from "@/lib/guides";

type SaveState = "idle" | "saving" | "saved" | "error";

function slugify(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/đ/g, "d")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export default function GuideForm({ mode }: { mode: "new" | "edit" }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialId = mode === "edit" ? searchParams.get("id") : null;
  const [recordId, setRecordId] = useState(initialId);
  const [categories, setCategories] = useState<GuideCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [slugEdited, setSlugEdited] = useState(false);
  const [thumbnailUploading, setThumbnailUploading] = useState(false);
  const initialized = useRef(false);
  const saving = useRef(false);
  const lastSavedStatus = useRef<Guide["status"]>("DRAFT");
  const [form, setForm] = useState({
    title: "",
    slug: "",
    summary: "",
    thumbnail_url: "",
    thumbnail_object_key: "",
    category_id: "",
    content: EMPTY_GUIDE_CONTENT as Record<string, unknown>,
    rendered_html: "",
    sort_order: 0,
    status: "DRAFT" as Guide["status"],
    keywords: "",
  });

  const storageKey = `examify-guide-draft-${recordId || "new"}`;

  useEffect(() => {
    let active = true;
    async function load() {
      const role = await resolveIdentity();
      if (!active) return;
      if (role !== "admin") {
        router.replace(role ? "/my-exams" : "/admin");
        return;
      }
      try {
        const categoryResponse = await apiFetch("/api/v1/guide-categories");
        if (categoryResponse.ok) {
          setCategories((await categoryResponse.json()).items || []);
        }
        if (initialId) {
          const response = await apiFetch(`/api/v1/admin/guides/${initialId}`, { cache: "no-store" });
          const guide = (await response.json()) as Guide & { detail?: string };
          if (!response.ok) throw new Error(guide.detail || "Không tải được bài hướng dẫn");
          setForm({
            title: guide.title,
            slug: guide.slug,
            summary: guide.summary || "",
            thumbnail_url: guide.thumbnail_url || "",
            thumbnail_object_key: guide.thumbnail_object_key || "",
            category_id: guide.category_id || "",
            content: guide.content || EMPTY_GUIDE_CONTENT,
            rendered_html: guide.rendered_html || "",
            sort_order: guide.sort_order,
            status: guide.status,
            keywords: (guide.keywords || []).join(", "),
          });
          setSlugEdited(true);
          lastSavedStatus.current = guide.status;
        } else {
          const local = localStorage.getItem("examify-guide-draft-new");
          if (local && window.confirm("Khôi phục bản nháp chưa đồng bộ trên máy này?")) {
            setForm(JSON.parse(local));
          }
        }
      } catch (reason) {
        setMessage(reason instanceof Error ? reason.message : "Không tải được dữ liệu");
      } finally {
        initialized.current = true;
        setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [initialId, router]);

  useEffect(() => {
    if (!initialized.current || !dirty) return;
    localStorage.setItem(storageKey, JSON.stringify(form));
  }, [dirty, form, storageKey]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const payload = useCallback((status: Guide["status"]) => ({
    title: form.title.trim(),
    slug: form.slug.trim(),
    summary: form.summary.trim(),
    thumbnail_url: form.thumbnail_url || null,
    thumbnail_object_key: form.thumbnail_object_key || null,
    category_id: form.category_id || null,
    content: form.content,
    rendered_html: form.rendered_html,
    sort_order: Number(form.sort_order) || 0,
    status,
    keywords: form.keywords.split(",").map((value) => value.trim()).filter(Boolean),
  }), [form]);

  const save = useCallback(async (status: Guide["status"], quiet = false): Promise<string | null> => {
    if (saving.current || !form.title.trim()) {
      if (!quiet && !form.title.trim()) setMessage("Vui lòng nhập tiêu đề bài viết.");
      return null;
    }
    saving.current = true;
    setSaveState("saving");
    if (!quiet) setMessage(null);
    try {
      const response = await apiFetch(
        recordId ? `/api/v1/admin/guides/${recordId}` : "/api/v1/admin/guides",
        {
          method: recordId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload(status)),
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không lưu được bài hướng dẫn");
      setRecordId(data.id);
      setForm((current) => ({ ...current, slug: data.slug, status: data.status }));
      lastSavedStatus.current = data.status;
      setSlugEdited(true);
      setDirty(false);
      setSaveState("saved");
      localStorage.removeItem(storageKey);
      if (!quiet) setMessage(status === "PUBLISHED" ? "Đã đăng bài hướng dẫn." : "Đã lưu bài hướng dẫn.");
      return data.id as string;
    } catch (reason) {
      setSaveState("error");
      setMessage(reason instanceof Error ? reason.message : "Không lưu được bài hướng dẫn");
      return null;
    } finally {
      saving.current = false;
    }
  }, [form.title, payload, recordId, storageKey]);

  useEffect(() => {
    if (!dirty || !form.title.trim() || loading) return;
    const timer = window.setTimeout(() => void save(lastSavedStatus.current, true), 3500);
    return () => window.clearTimeout(timer);
  }, [dirty, form.title, form.content, form.summary, form.thumbnail_url, form.category_id, form.sort_order, form.keywords, loading, save, saveState]);

  const update = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setSaveState("idle");
  };

  async function uploadThumbnail(file: File) {
    setThumbnailUploading(true);
    setMessage(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await apiFetch("/api/v1/admin/guide-media/upload", { method: "POST", body });
      const media = (await response.json()) as GuideMedia & { detail?: string };
      if (!response.ok) throw new Error(media.detail || "Không upload được ảnh");
      if (media.media_type !== "image") throw new Error("Ảnh đại diện phải là file ảnh.");
      setForm((current) => ({
        ...current,
        thumbnail_url: media.url,
        thumbnail_object_key: media.object_key,
      }));
      setDirty(true);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Không upload được ảnh");
    } finally {
      setThumbnailUploading(false);
    }
  }

  async function addCategory() {
    const name = window.prompt("Tên danh mục mới:");
    if (!name?.trim()) return;
    const response = await apiFetch("/api/v1/admin/guide-categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), slug: slugify(name), sort_order: categories.length }),
    });
    const data = await response.json();
    if (!response.ok) return setMessage(data.detail || "Không tạo được danh mục");
    setCategories((current) => [...current, data]);
    update("category_id", data.id);
  }

  async function preview() {
    const id = await save(form.status, true);
    if (id) window.open(`/admin/guides/preview?id=${encodeURIComponent(id)}`, "_blank", "noopener");
  }

  if (loading) {
    return <main className="bg-slate-50"><Header /><ExamifyLoader fullScreen={false} message="Đang tải trình soạn thảo..." /></main>;
  }

  return (
    <main className="bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-4 py-7 sm:px-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <button type="button" onClick={() => (!dirty || window.confirm("Bạn có nội dung chưa lưu. Rời trang?")) && router.push("/admin/guides")} className="mb-2 inline-flex items-center gap-1 text-sm font-bold text-slate-500 hover:text-[#1f4e79]"><ArrowLeft className="h-4 w-4" /> Quản lý hướng dẫn</button>
            <h1 className="text-3xl font-extrabold text-[#1f4e79]">{recordId ? "Chỉnh sửa hướng dẫn" : "Tạo hướng dẫn mới"}</h1>
            <div className="mt-1 flex items-center gap-2 text-xs font-semibold text-slate-500">
              {saveState === "saving" && <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Đang tự động lưu...</>}
              {saveState === "saved" && <><CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> Đã lưu</>}
              {saveState === "error" && <><TriangleAlert className="h-3.5 w-3.5 text-red-600" /> Lưu thất bại — bản nháp vẫn còn trên máy</>}
              {saveState === "idle" && dirty && "Có thay đổi chưa lưu"}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void preview()} className="ui-btn-secondary"><Eye className="h-4 w-4" /> Xem trước</button>
            <button type="button" onClick={() => void save("DRAFT")} disabled={saveState === "saving"} className="ui-btn-secondary"><Save className="h-4 w-4" /> Lưu bản nháp</button>
            <button type="button" onClick={() => void save("PUBLISHED")} disabled={saveState === "saving"} className="ui-btn-primary"><Send className="h-4 w-4" /> Đăng bài</button>
          </div>
        </div>

        {message && <div className={`mt-5 flex items-center justify-between rounded-xl border px-4 py-3 text-sm font-semibold ${saveState === "error" ? "border-red-200 bg-red-50 text-red-700" : "border-blue-200 bg-blue-50 text-blue-800"}`}>{message}<button type="button" onClick={() => setMessage(null)}><X className="h-4 w-4" /></button></div>}

        <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_320px]">
          <section className="space-y-5">
            <div className="ui-card grid gap-4 p-5 md:grid-cols-2">
              <label className="md:col-span-2"><span className="text-sm font-bold text-slate-700">Tiêu đề *</span><input value={form.title} onChange={(event) => { update("title", event.target.value); if (!slugEdited) setForm((current) => ({ ...current, slug: slugify(event.target.value) })); }} maxLength={255} className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2.5 text-lg font-bold outline-none focus:border-[#1f4e79]" /></label>
              <label><span className="text-sm font-bold text-slate-700">Slug</span><input value={form.slug} onChange={(event) => { setSlugEdited(true); update("slug", slugify(event.target.value)); }} maxLength={280} className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2.5 outline-none focus:border-[#1f4e79]" /></label>
              <label><span className="text-sm font-bold text-slate-700">Từ khóa (phân cách bằng dấu phẩy)</span><input value={form.keywords} onChange={(event) => update("keywords", event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2.5 outline-none focus:border-[#1f4e79]" /></label>
              <label className="md:col-span-2"><span className="text-sm font-bold text-slate-700">Mô tả ngắn</span><textarea value={form.summary} onChange={(event) => update("summary", event.target.value)} maxLength={2000} rows={3} className="mt-1 w-full resize-y rounded-lg border border-slate-300 px-4 py-3 outline-none focus:border-[#1f4e79]" /></label>
            </div>
            <div>
              <h2 className="mb-2 text-sm font-extrabold uppercase tracking-wide text-slate-600">Nội dung hướng dẫn</h2>
              <GuideEditor value={form.content} onChange={(content, html) => { setForm((current) => ({ ...current, content, rendered_html: html })); setDirty(true); setSaveState("idle"); }} />
            </div>
          </section>

          <aside className="space-y-5">
            <section className="ui-card p-5">
              <h2 className="font-extrabold text-[#1f4e79]">Xuất bản</h2>
              <label className="mt-4 block"><span className="text-sm font-bold text-slate-700">Trạng thái</span><select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as Guide["status"] }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5"><option value="DRAFT">Bản nháp</option><option value="PUBLISHED">Đã đăng</option><option value="HIDDEN">Đã ẩn</option></select></label>
              <label className="mt-4 block"><span className="text-sm font-bold text-slate-700">Thứ tự hiển thị</span><input type="number" value={form.sort_order} onChange={(event) => update("sort_order", Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5" /></label>
              <div className="mt-4 flex gap-2"><button type="button" onClick={() => void save(form.status)} className="ui-btn-primary flex-1"><Save className="h-4 w-4" /> Cập nhật</button></div>
            </section>
            <section className="ui-card p-5">
              <div className="flex items-center justify-between"><h2 className="font-extrabold text-[#1f4e79]">Danh mục</h2><button type="button" onClick={() => void addCategory()} title="Thêm danh mục" className="rounded-lg border border-slate-300 p-1.5 hover:border-[#1f4e79]"><Plus className="h-4 w-4" /></button></div>
              <select value={form.category_id} onChange={(event) => update("category_id", event.target.value)} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2.5"><option value="">Không có danh mục</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select>
            </section>
            <section className="ui-card p-5">
              <h2 className="font-extrabold text-[#1f4e79]">Ảnh đại diện</h2>
              {form.thumbnail_url ? (
                <div className="relative mt-3 overflow-hidden rounded-xl border border-slate-200">
                  <img src={guideMediaUrl(form.thumbnail_url)} alt="" className="aspect-video w-full object-cover" />
                  <button type="button" onClick={() => { update("thumbnail_url", ""); update("thumbnail_object_key", ""); }} className="absolute right-2 top-2 rounded-full bg-white p-1.5 text-red-600 shadow"><X className="h-4 w-4" /></button>
                </div>
              ) : <div className="mt-3 flex aspect-video items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 text-slate-400"><ImagePlus className="h-8 w-8" /></div>}
              <label className="ui-btn-secondary mt-3 w-full cursor-pointer">{thumbnailUploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImagePlus className="h-4 w-4" />} {thumbnailUploading ? "Đang upload..." : "Chọn ảnh"}<input type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden onChange={(event) => event.target.files?.[0] && void uploadThumbnail(event.target.files[0])} /></label>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
