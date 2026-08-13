export type GuideCategory = {
  id: string;
  name: string;
  slug: string;
  sort_order: number;
};

export type GuideMedia = {
  id: string;
  file_name: string;
  original_name: string;
  object_key: string;
  bucket: string;
  url: string;
  mime_type: string;
  media_type: "image" | "video";
  size: number;
  width: number | null;
  height: number | null;
  created_at: string;
};

export type Guide = {
  id: string;
  title: string;
  slug: string;
  summary: string;
  thumbnail_url: string | null;
  thumbnail_object_key: string | null;
  category_id: string | null;
  category: GuideCategory | null;
  content?: Record<string, unknown>;
  rendered_html?: string;
  content_format?: string;
  status: "DRAFT" | "PUBLISHED" | "HIDDEN";
  sort_order: number;
  keywords: string[];
  created_by: string | null;
  creator_name: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
  previous?: Guide | null;
  next?: Guide | null;
};

export const EMPTY_GUIDE_CONTENT = {
  type: "doc",
  content: [{ type: "paragraph" }],
};

export function formatGuideDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

export function statusLabel(status: Guide["status"]): string {
  return {
    DRAFT: "Bản nháp",
    PUBLISHED: "Đã đăng",
    HIDDEN: "Đã ẩn",
  }[status];
}
