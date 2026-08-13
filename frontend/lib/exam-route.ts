type ExamRouteIdentity = {
  slug?: string | null;
  title?: string | null;
  exam_id?: string | null;
  id?: string | null;
  client_exam_id?: string | null;
  job_id?: string | null;
};

export function normalizeExamSlug(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[đĐ]/g, "d")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}

export function examSlug(exam: ExamRouteIdentity): string {
  const persisted = normalizeExamSlug(String(exam.slug || ""));
  if (persisted) return persisted;
  const title = normalizeExamSlug(String(exam.title || "de-thi")) || "de-thi";
  const identity = normalizeExamSlug(
    String(exam.exam_id || exam.id || exam.client_exam_id || exam.job_id || "local"),
  );
  return `${title.slice(0, 80)}-${identity || "local"}`;
}

export function quizPath(exam: ExamRouteIdentity): string {
  const slug = encodeURIComponent(examSlug(exam));
  // Tauri serves a finite static export, so arbitrary dynamic path segments do
  // not have a corresponding HTML file. Keep the exam identity in the URL on
  // desktop via the query string; web deployments retain canonical slug paths.
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return `/quiz?slug=${slug}`;
  }
  return `/quiz/${slug}`;
}
