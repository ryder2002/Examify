"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  BookOpen,
  Edit,
  FileQuestion,
  FolderOpen,
  Headphones,
  History as HistoryIcon,
  Loader2,
  Megaphone,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  Trash2,
  Check,
  X,
  Link as LinkIcon,
  ClipboardList,
  Copy,
  Users,
} from "lucide-react";

import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import ExamLaunchDialog, {
  type ExamLaunchConfiguration,
} from "@/components/ExamLaunchDialog";
import { apiFetch, isDesktop, publicWebUrl, resolveIdentity } from "@/lib/api";
import {
  queueDesktopPublication,
  syncDesktopExam,
  syncDesktopPending,
} from "@/lib/desktop-sync";
import type { ExamSummary, FinalExam } from "@/lib/utils";
import { quizPath } from "@/lib/exam-route";

const UNCATEGORIZED = "__uncategorized__";

function isMiniTestTag(tag?: string | null): boolean {
  if (!tag) return false;
  const normalized = tag.toLowerCase().replace(/[\s_-]+/g, "");
  return normalized.includes("minitest");
}

type TeacherClassroom = {
  id: string;
  name: string;
  status: "active" | "archived";
};

type BankTag = { id: string; name: string };

export default function MyExamsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const [items, setItems] = useState<ExamSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [kindFilter, setKindFilter] = useState<"all" | "listening" | "reading" | "combined">("all");
  const [bankTags, setBankTags] = useState<BankTag[]>([]);
  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [tagMenu, setTagMenu] = useState<string | null>(null);

  const [selectedExam, setSelectedExam] = useState<ExamSummary | null>(null);
  const [launching, setLaunching] = useState(false);
  const [isTeacher, setIsTeacher] = useState(false);
  const [role, setRole] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [publicExam, setPublicExam] = useState<ExamSummary | null>(null);
  const [publicTag, setPublicTag] = useState<string | null>(null);
  const [teacherClasses, setTeacherClasses] = useState<TeacherClassroom[]>([]);
  const [selectedClassIds, setSelectedClassIds] = useState<string[]>([]);
  const [publishedClassIds, setPublishedClassIds] = useState<string[]>([]);
  const [tagClassProgress, setTagClassProgress] = useState<
    Record<string, { published: number; total: number }>
  >({});
  const [publicLoading, setPublicLoading] = useState(false);
  const [publicMessage, setPublicMessage] = useState<string | null>(null);

  // Public Share & Mini Test Results state
  const [publicLinkModalExam, setPublicLinkModalExam] = useState<ExamSummary | null>(null);
  const [publicShareCode, setPublicShareCode] = useState<string | null>(null);
  const [copiedShareLink, setCopiedShareLink] = useState(false);

  const [resultsModalExam, setResultsModalExam] = useState<ExamSummary | null>(null);
  const [publicSubmissions, setPublicSubmissions] = useState<any[]>([]);
  const [loadingSubmissions, setLoadingSubmissions] = useState(false);
  const [submissionSearch, setSubmissionSearch] = useState("");
  const [selectedSubmissionDetail, setSelectedSubmissionDetail] = useState<any | null>(null);

  async function handleCopyPublicLink(exam: ExamSummary) {
    try {
      const targetId = exam.remote_exam_id || exam.id;
      const res = await apiFetch(`/api/v1/exams/${targetId}/public-share`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Không thể tạo link public");
        return;
      }
      const data = await res.json();
      const fullUrl = publicWebUrl(`/public-test/${data.share_code}`);
      await navigator.clipboard.writeText(fullUrl);
      setPublicShareCode(data.share_code);
      setPublicLinkModalExam(exam);
      setCopiedShareLink(true);
      setTimeout(() => setCopiedShareLink(false), 3000);
    } catch (err: any) {
      alert(err.message || "Lỗi tạo link public");
    }
  }

  async function handleOpenSubmissionsModal(exam: ExamSummary) {
    setResultsModalExam(exam);
    setLoadingSubmissions(true);
    try {
      const targetId = exam.remote_exam_id || exam.id;
      const res = await apiFetch(`/api/v1/exams/${targetId}/public-submissions`);
      if (res.ok) {
        const data = await res.json();
        setPublicSubmissions(data.items || []);
      } else {
        setPublicSubmissions([]);
      }
    } catch {
      setPublicSubmissions([]);
    } finally {
      setLoadingSubmissions(false);
    }
  }

  async function handleDeleteSubmission(submissionId: string) {
    if (!confirm("Bạn có chắc chắn muốn xóa kết quả của học viên này?")) return;
    try {
      const res = await apiFetch(`/api/v1/public-submissions/${submissionId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setPublicSubmissions((prev) => prev.filter((s) => s.id !== submissionId));
      }
    } catch (err: any) {
      alert(err.message || "Không thể xóa kết quả");
    }
  }

  useEffect(() => {
    void resolveIdentity().then((resolvedRole) => {
      setRole(resolvedRole);
      setIsTeacher(resolvedRole === "teacher" || resolvedRole === "admin");
      if (
        pathname === "/my-exams" &&
        (resolvedRole === "teacher" || resolvedRole === "student")
      ) {
        router.replace("/exam-bank");
      }
    });
  }, [pathname, router]);

  useEffect(() => {
    if (role !== "teacher" && role !== "student" && role !== "admin") return;
    void apiFetch("/api/v1/exam-bank/tags", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return;
        const payload = await response.json();
        setBankTags(payload.items || []);
      })
      .catch(() => undefined);
  }, [role]);

  useEffect(() => {
    if (!isDesktop()) return;
    const reload = () => void load();
    window.addEventListener("desktop-sync-updated", reload);
    return () => window.removeEventListener("desktop-sync-updated", reload);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load() {
    if (role === null) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("search", search.trim());
      params.set("page", String(page));
      params.set("page_size", "20");
      if (isTeacher) params.set("include_archived", "true");
      const sharedBank = role === "teacher" || role === "student" || role === "admin";
      if (sharedBank && kindFilter !== "all") params.set("kind", kindFilter);
      if (sharedBank && filter !== "all" && filter !== UNCATEGORIZED) {
        const selectedTag = bankTags.find((tag) => tag.name === filter);
        if (selectedTag) params.set("tag_id", selectedTag.id);
      }
      let localItems: ExamSummary[] = [];
      if (isDesktop()) {
        const localResponse = await apiFetch("/api/desktop/exams", {
          cache: "no-store",
        });
        if (localResponse.ok) {
          const localPayload = await localResponse.json();
          localItems = (localPayload.items || [])
            .filter(
              (item: { title: string; category?: string }) =>
                !search.trim() ||
                `${item.title} ${item.category || ""}`
                  .toLowerCase()
                  .includes(search.trim().toLowerCase()),
            )
            .filter(
              (item: { category?: string; exam_type: string }) =>
                (kindFilter === "all" || item.exam_type === kindFilter) &&
                (filter === "all" ||
                  (filter === UNCATEGORIZED
                    ? !item.category?.trim()
                    : item.category?.trim() === filter)),
            )
            .map(
              (item: {
                client_exam_id: string;
                title: string;
                category: string;
                exam_type: ExamSummary["exam_type"];
                sync_status: string;
                sync_error?: string | null;
                remote_exam_id?: string | null;
                attempt_count?: number;
                last_attempt_at?: number | null;
                created_at: number;
                updated_at: number;
                payload: FinalExam;
              }) => ({
                id: item.client_exam_id,
                title: item.title,
                category: item.category,
                exam_type: item.exam_type,
                status: "ready",
                question_count: item.payload.questions.length,
                answer_key_count: item.payload.questions.filter((q) => q.correct).length,
                duration_minutes: item.exam_type === "listening" ? 45 : 75,
                created_at: new Date(item.created_at * 1000).toISOString(),
                updated_at: new Date(item.updated_at * 1000).toISOString(),
                attempt_count: item.attempt_count || 0,
                last_attempt_at: item.last_attempt_at
                  ? new Date(item.last_attempt_at * 1000).toISOString()
                  : null,
                local_payload: item.payload,
                job_id: item.payload.job_id,
                sync_status: item.sync_status,
                sync_error: item.sync_error || null,
                remote_exam_id: item.remote_exam_id || null,
              }),
            );
        }
      }
      let response: Response;
      try {
        response = await apiFetch(
          `${sharedBank ? "/api/v1/exam-bank" : "/api/v1/exams"}?${params}`,
          { cache: "no-store" },
        );
      } catch (reason) {
        // Desktop remains useful before activation and while offline: local
        // exams are still available, and an empty library is not an error.
        if (isDesktop()) {
          setItems(localItems);
          return;
        }
        throw reason;
      }
      if (response.status === 401) {
        if (isDesktop()) {
          setItems(localItems);
          return;
        }
        router.replace("/activate");
        return;
      }
      const responseText = await response.text();
      let payload: { detail?: string; items?: Array<ExamSummary & { tag?: { name: string } | null }>; pages?: number; total?: number } = {};
      try {
        payload = responseText ? JSON.parse(responseText) : {};
      } catch {
        payload = {};
      }
      if (!response.ok) {
        if (isDesktop()) {
          setItems(localItems);
          return;
        }
        throw new Error(payload.detail || "Không tải được đề thi");
      }
      const remote = (payload.items || []).map((item) => ({
        ...item,
        category: item.category || item.tag?.name || "",
      }));
      setPages(Math.max(1, payload.pages || 1));
      const remoteIds = new Set(remote.map((item: ExamSummary) => item.id));
      const syncedClientIds = new Set(
        remote
          .map((item: ExamSummary) => item.client_exam_id)
          .filter((id: string | null | undefined): id is string => Boolean(id)),
      );
      setItems([
        ...remote,
        ...localItems.filter(
          (item) =>
            item.sync_status === "conflict" ||
            (!remoteIds.has(item.id) && !syncedClientIds.has(item.id)),
        ),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(load, 200);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, page, role, isTeacher, filter, kindFilter, bankTags]);

  const totals = useMemo(
    () => ({
      all: items.length,
      ready: items.filter((item) => item.status === "ready").length,
      attempts: items.reduce((sum, item) => sum + item.attempt_count, 0),
    }),
    [items],
  );
  const tags = useMemo(
    () => role === "teacher" || role === "student" || role === "admin"
      ? bankTags.map((tag) => tag.name)
      :
      Array.from(
        new Set(
          items
            .map((item) => item.category?.trim())
            .filter((tag): tag is string => Boolean(tag)),
        ),
      ).sort((a, b) => a.localeCompare(b, "vi")),
    [bankTags, items, role],
  );
  const visibleItems = useMemo(
    () =>
      role === "teacher" || role === "student" || role === "admin"
        ? items
        : filter === "all"
        ? items
        : filter === UNCATEGORIZED
          ? items.filter((item) => !item.category?.trim())
          : items.filter((item) => item.category?.trim() === filter),
    [filter, items, role],
  );

  async function startConfiguredExam(configuration: ExamLaunchConfiguration) {
    if (!selectedExam) return;
    setLaunching(true);
    setError(null);
    try {
      let payloadExam: FinalExam | null = selectedExam.local_payload || null;

      if (!payloadExam) {
        const sharedBank = role === "teacher" || role === "student" || role === "admin";
        const response = await apiFetch(`${sharedBank ? "/api/v1/exam-bank" : "/api/v1/exams"}/${selectedExam.id}/attempts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            duration_seconds: configuration.durationSeconds,
            ...(sharedBank
              ? {
                  launch_mode: configuration.launchMode,
                  part_numbers: configuration.partNumbers,
                }
              : {}),
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Không bắt đầu được bài thi");
        payloadExam = payload.exam as FinalExam;
        sessionStorage.setItem("quiz-attempt-id", payload.attempt_id);
        sessionStorage.setItem(
          "quiz-attempt-source",
          sharedBank ? "bank" : "personal",
        );
        sessionStorage.setItem(
          "quiz-initial-answers",
          JSON.stringify(payload.answers || {}),
        );
        sessionStorage.setItem("quiz-resume-pending", payload.resumed ? "1" : "0");
        if (payload.current_question_number) {
          sessionStorage.setItem(
            "quiz-question-number",
            String(payload.current_question_number),
          );
        }
      } else {
        sessionStorage.removeItem("quiz-attempt-id");
        sessionStorage.removeItem("quiz-attempt-source");
        sessionStorage.removeItem("quiz-initial-answers");
        sessionStorage.setItem("quiz-resume-pending", "0");
      }

      if (!payloadExam) throw new Error("Không có dữ liệu bài thi");

      let finalQuestions = payloadExam.questions;
      if (configuration.launchMode === "practice") {
        const selected = new Set(configuration.partNumbers);
        const partForQuestion = (number: number) =>
          number <= 6 ? 1 : number <= 31 ? 2 : number <= 70 ? 3 :
            number <= 100 ? 4 : number <= 130 ? 5 : number <= 146 ? 6 : 7;
        finalQuestions = payloadExam.questions.filter((question) =>
          selected.has(partForQuestion(question.number)),
        );
      }

      const filteredExam: FinalExam = {
        ...payloadExam,
        questions: finalQuestions,
        returned_count: finalQuestions.length,
      };

      sessionStorage.setItem("quiz-data", JSON.stringify(filteredExam));
      sessionStorage.setItem(
        "quiz-mode",
        configuration.launchMode === "mock_exam" ? "exam" : "practice",
      );
      sessionStorage.setItem("quiz-duration", String(configuration.durationSeconds));
      sessionStorage.setItem("quiz-time-left", String(configuration.durationSeconds));
      sessionStorage.removeItem("quiz-flagged-questions");
      sessionStorage.removeItem("quiz-group-index");
      sessionStorage.removeItem("quiz-question-index");
      if (sessionStorage.getItem("quiz-resume-pending") !== "1") {
        sessionStorage.removeItem("quiz-question-number");
      }
      sessionStorage.setItem(
        "quiz-selected-parts",
        JSON.stringify(configuration.partNumbers.map((part) => `P${part}`)),
      );
      sessionStorage.removeItem("quiz-result");
      sessionStorage.removeItem("quiz-class-session");
      sessionStorage.removeItem("quiz-class-return");
      const path = quizPath(filteredExam);
      sessionStorage.setItem("quiz-slug", path.split("/").pop() || "");
      router.push(path);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    } finally {
      setLaunching(false);
    }
  }

  async function reopenForEditing(
    exam: ExamSummary,
    initialTab: "content" | "solutions" = "content",
  ) {
    const jobId = exam.local_payload?.job_id || exam.job_id;
    setMenu(null);
    setError(null);
    if (isTeacher && !exam.local_payload) {
      try {
        const response = await apiFetch(
          `/api/v1/exam-bank/${exam.id}/edit-sessions`,
          { method: "POST" },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(
            typeof payload.detail === "string"
              ? payload.detail
              : payload.detail?.message || "Không mở được edit session",
          );
        }
        const listeningJob = payload.job_ids?.listening;
        const readingJob = payload.job_ids?.reading;
        const firstJob = listeningJob || readingJob;
        if (!firstJob) throw new Error("Edit session không có draft job");
        sessionStorage.setItem(
          "editing-exam",
          JSON.stringify({
            id: exam.id,
            exam_id: exam.id,
            title: exam.title,
            category: exam.category || "",
            edit_session_id: payload.id,
            base_revision: payload.base_revision,
            full_test: Boolean(listeningJob && readingJob),
            listening_job_id: listeningJob || null,
            reading_job_id: readingJob || null,
          }),
        );
        sessionStorage.setItem("extraction-job", firstJob);
        sessionStorage.removeItem("pending-listening-exam");
        router.push(
          `/review?job=${encodeURIComponent(firstJob)}&edit=1${listeningJob && readingJob ? "&step=listening" : ""}${initialTab === "solutions" ? "&tab=solutions" : ""}`,
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Không mở được edit session");
      }
      return;
    }
    if (exam.exam_type === "combined") {
      try {
        const desktop = isDesktop();
        const response = await apiFetch(
          desktop
            ? `/api/desktop/exams/${exam.id}/edit`
            : `/api/v1/exams/${exam.id}/edit`,
          {
          method: "POST",
          },
        );
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Không mở được Full Test");
        }
        const listeningJob = payload.component_job_ids?.listening;
        const readingJob = payload.component_job_ids?.reading;
        if (!listeningJob || !readingJob) throw new Error("Thiếu dữ liệu hai phần thi");
        sessionStorage.setItem(
          "editing-exam",
          JSON.stringify({
            id: exam.id,
            title: exam.title,
            category: exam.category || "",
            client_exam_id: desktop ? exam.id : null,
            exam_id: desktop ? null : exam.id,
            full_test: true,
            listening_job_id: listeningJob,
            reading_job_id: readingJob,
          }),
        );
        sessionStorage.setItem("extraction-job", listeningJob);
        sessionStorage.removeItem("pending-listening-exam");
        router.push(`/review?job=${encodeURIComponent(listeningJob)}&edit=1&step=listening${initialTab === "solutions" ? "&tab=solutions" : ""}`);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Không mở được Full Test");
      }
      return;
    }
    if (!jobId) {
      setError("Không tìm thấy dữ liệu OCR để chỉnh sửa đề.");
      return;
    }
    sessionStorage.setItem(
      "editing-exam",
      JSON.stringify({
        id: exam.id,
        title: exam.title,
        category: exam.category || "",
        client_exam_id: exam.local_payload?.client_exam_id || (isDesktop() ? exam.id : null),
      }),
    );
    sessionStorage.setItem("extraction-job", jobId);
    router.push(`/review?job=${encodeURIComponent(jobId)}&edit=1${initialTab === "solutions" ? "&tab=solutions" : ""}`);
  }

  async function deleteExam(exam: ExamSummary) {
    if (!window.confirm("Bạn có chắc chắn muốn xóa đề thi này?")) return;
    const sharedBank = role === "teacher" || role === "student" || role === "admin";
    const localOnly = isDesktop() && Boolean(exam.local_payload);
    const response = await apiFetch(localOnly
      ? `/api/desktop/exams/${exam.id}`
      : `${sharedBank ? "/api/v1/exam-bank" : "/api/v1/exams"}/${exam.id}`, {
      method: "DELETE",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không cập nhật được đề thi");
      return;
    }
    setItems((current) => current.filter((item) => item.id !== exam.id));
    setMenu(null);
    if (isDesktop() && !localOnly && navigator.onLine) {
      // Apply the server tombstone to the local cache immediately instead of
      // waiting for the next 30-second coordinator interval.
      await syncDesktopPending().catch(() => undefined);
    }
  }

  async function renameTag(tag: BankTag) {
    const nextName = window.prompt("Tên Tag mới", tag.name);
    if (nextName === null) return;
    const name = nextName.trim();
    if (!name || name === tag.name) return;
    const response = await apiFetch(
      `/api/v1/exam-bank/tags/${encodeURIComponent(tag.id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không đổi được tên Tag");
      return;
    }
    const updatedName = payload.name || name;
    setBankTags((current) =>
      current.map((item) => (item.id === tag.id ? { ...item, name: updatedName } : item)),
    );
    if (filter === tag.name) {
      setFilter(updatedName);
      setPage(1);
    }
    setTagMenu(null);
    await load();
  }

  async function deleteTag(tag: BankTag) {
    if (
      !window.confirm(
        `Xóa Tag "${tag.name}"? Các đề đang dùng Tag sẽ chuyển thành Chưa phân loại.`,
      )
    ) {
      return;
    }
    const response = await apiFetch(
      `/api/v1/exam-bank/tags/${encodeURIComponent(tag.id)}`,
      { method: "DELETE" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không xóa được Tag");
      return;
    }
    setBankTags((current) => current.filter((item) => item.id !== tag.id));
    if (filter === tag.name) {
      setFilter("all");
      setPage(1);
    }
    setTagMenu(null);
    await load();
  }

  async function toggleArchive(exam: ExamSummary) {
    const response = await apiFetch(`/api/v1/exam-bank/${exam.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_revision: exam.revision || 1,
        archived: exam.status !== "archived",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail;
      setError(typeof detail === "string" ? detail : detail?.message || "Không cập nhật được trạng thái đề");
      return;
    }
    setItems((current) => current.map((item) =>
      item.id === exam.id
        ? { ...item, status: exam.status === "archived" ? "ready" : "archived", revision: payload.revision }
        : item,
    ));
    setMenu(null);
  }

  async function openPublicDialog(exam: ExamSummary) {
    setPublicTag(null);
    setPublicExam(exam);
    setPublicLoading(true);
    setPublicMessage(null);
    setSelectedClassIds([]);
    setTagClassProgress({});
    try {
      if (exam.local_payload && isDesktop()) {
        let syncMessage: string | null = null;
        if (navigator.onLine) {
          const summary = await syncDesktopPending();
          const failure = summary.failures.find(
            (item) => !item.client_exam_id || item.client_exam_id === exam.id,
          );
          syncMessage = failure
            ? `Đề chưa đồng bộ được: ${failure.error}`
            : null;
        }
        const [classResponse, publicationResponse] = await Promise.all([
          apiFetch("/api/desktop/classrooms/cache", { cache: "no-store" }),
          apiFetch(`/api/desktop/exams/${exam.id}/publications`, { cache: "no-store" }),
        ]);
        const classesPayload = await classResponse.json().catch(() => ({}));
        const publicationPayload = await publicationResponse.json().catch(() => ({}));
        setTeacherClasses(
          (classesPayload.items || []).filter(
            (item: TeacherClassroom & { can_publish?: boolean }) => item.can_publish !== false,
          ),
        );
        setPublishedClassIds(
          (publicationPayload.items || [])
            .filter((item: { status: string }) => item.status === "synced")
            .map((item: { classroom_id: string }) => item.classroom_id),
        );
        if (!classesPayload.items?.length) {
          setPublicMessage("Chưa có danh sách lớp được cache. Hãy online một lần để tải danh sách lớp.");
        } else if (syncMessage) {
          setPublicMessage(syncMessage);
        }
        return;
      }
      const [classResponse, publicationResponse] = await Promise.all([
        apiFetch("/api/v1/teacher/classrooms", { cache: "no-store" }),
        apiFetch(`/api/v1/teacher/exams/${exam.id}/class-publications`, {
          cache: "no-store",
        }),
      ]);
      const classesPayload = await classResponse.json().catch(() => ({}));
      const publicationPayload = await publicationResponse.json().catch(() => ({}));
      if (!classResponse.ok) {
        throw new Error(classesPayload.detail || "Không tải được danh sách lớp");
      }
      if (!publicationResponse.ok) {
        throw new Error(publicationPayload.detail || "Không tải được trạng thái Public");
      }
      setTeacherClasses(
        (classesPayload.items || []).filter(
          (item: TeacherClassroom) => item.status === "active",
        ),
      );
      if (isDesktop()) {
        await apiFetch("/api/desktop/classrooms/cache", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: classesPayload.items || [] }),
        }).catch(() => undefined);
      }
      const activePublications = (publicationPayload.items || [])
        .filter((item: { status: string }) => item.status !== "closed")
        .map((item: { classroom_id: string }) => item.classroom_id);
      setPublishedClassIds(Array.from(new Set<string>(activePublications)));
    } catch (reason) {
      setPublicMessage(
        reason instanceof Error ? reason.message : "Không tải được dữ liệu Public",
      );
    } finally {
      setPublicLoading(false);
    }
  }

  async function openTagPublicDialog(tag: string) {
    setTagMenu(null);
    setPublicExam(null);
    setPublicTag(tag);
    setPublicLoading(true);
    setPublicMessage(null);
    setSelectedClassIds([]);
    setPublishedClassIds([]);
    setTagClassProgress({});
    try {
      const params = new URLSearchParams({ tag });
      const [classResponse, publicationResponse] = await Promise.all([
        apiFetch("/api/v1/teacher/classrooms", { cache: "no-store" }),
        apiFetch(`/api/v1/teacher/exam-tags/class-publications?${params}`, {
          cache: "no-store",
        }),
      ]);
      const classesPayload = await classResponse.json().catch(() => ({}));
      const publicationPayload = await publicationResponse.json().catch(() => ({}));
      if (!classResponse.ok) {
        throw new Error(classesPayload.detail || "Không tải được danh sách lớp");
      }
      if (!publicationResponse.ok) {
        throw new Error(publicationPayload.detail || "Không tải được trạng thái Tag");
      }
      setTeacherClasses(
        (classesPayload.items || []).filter(
          (item: TeacherClassroom) => item.status === "active",
        ),
      );
      const progress: Record<string, { published: number; total: number }> = {};
      const fullyPublished: string[] = [];
      for (const item of publicationPayload.items || []) {
        progress[item.classroom_id] = {
          published: item.published_exam_count || 0,
          total: item.exam_count || publicationPayload.exam_count || 0,
        };
        if (item.fully_published) fullyPublished.push(item.classroom_id);
      }
      setTagClassProgress(progress);
      setPublishedClassIds(fullyPublished);
    } catch (reason) {
      setPublicMessage(
        reason instanceof Error ? reason.message : "Không tải được dữ liệu Public",
      );
    } finally {
      setPublicLoading(false);
    }
  }

  async function publishPractice() {
    if ((!publicExam && !publicTag) || selectedClassIds.length === 0) return;
    setPublicLoading(true);
    setPublicMessage(null);
    try {
      if (publicExam?.local_payload && isDesktop()) {
        const requestedClassIds = [...selectedClassIds];
        if (!navigator.onLine) {
          const response = await queueDesktopPublication(publicExam.id, requestedClassIds);
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.detail || "Không lưu được yêu cầu Public offline");
          }
          setSelectedClassIds([]);
          setPublicMessage(
            "Đã lưu yêu cầu trên máy, nhưng CHƯA Public lên web. Hệ thống sẽ thử lại khi có internet.",
          );
          window.dispatchEvent(new Event("desktop-sync-requested"));
          return;
        }

        const result = await syncDesktopExam(publicExam.id, requestedClassIds);
        const statusResponse = await apiFetch(
          `/api/desktop/exams/${publicExam.id}/publications`,
          { cache: "no-store" },
        );
        const statusPayload = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok) {
          throw new Error(statusPayload.detail || "Không xác minh được trạng thái Public");
        }
        const statuses = new Map<string, { status: string; last_error?: string }>(
          (statusPayload.items || []).map(
            (item: { classroom_id: string; status: string; last_error?: string }) => [
              item.classroom_id,
              item,
            ],
          ),
        );
        const confirmed = requestedClassIds.filter(
          (classroomId) => statuses.get(classroomId)?.status === "synced",
        );
        const unconfirmed = requestedClassIds.filter(
          (classroomId) => !confirmed.includes(classroomId),
        );
        if (unconfirmed.length > 0) {
          const detail =
            unconfirmed
              .map((classroomId) => statuses.get(classroomId)?.last_error)
              .find(Boolean) ||
            result.publications.find((item) => item.status === "failed")?.error;
          throw new Error(
            detail ||
              `Máy chủ chưa xác nhận Public cho ${unconfirmed.length} lớp; yêu cầu vẫn được giữ để thử lại.`,
          );
        }
        setPublishedClassIds((current) =>
          Array.from(new Set([...current, ...confirmed])),
        );
        setSelectedClassIds([]);
        setPublicMessage(
          `Đã đồng bộ đề vào CSDL và Public thành công tới ${confirmed.length} lớp. Học viên web đã có thể nhìn thấy.`,
        );
        void load();
        return;
      }
      const response = await apiFetch(
        publicTag
          ? "/api/v1/teacher/exam-tags/class-publications"
          : `/api/v1/teacher/exams/${publicExam!.id}/class-publications`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            classroom_ids: selectedClassIds,
            ...(publicTag ? { tag: publicTag } : {}),
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không Public được đề");
      const affected = publicTag
        ? [...selectedClassIds]
        : [
            ...(payload.created || []),
            ...(payload.reopened || []),
            ...(payload.already_published || []),
          ];
      setPublishedClassIds((current) => Array.from(new Set([...current, ...affected])));
      if (publicTag) {
        setTagClassProgress((current) => {
          const next = { ...current };
          for (const classroomId of affected) {
            next[classroomId] = {
              published: payload.exam_count || 0,
              total: payload.exam_count || 0,
            };
          }
          return next;
        });
      }
      setSelectedClassIds([]);
      setPublicMessage(
        publicTag
          ? `Đã Public ${payload.exam_count || 0} đề thuộc Tag “${publicTag}” tới ${affected.length} lớp.`
          : `Đã Public bộ đề gồm ${payload.question_count || 0} câu tới ${affected.length} lớp.`,
      );
    } catch (reason) {
      setPublicMessage(reason instanceof Error ? reason.message : "Không Public được đề");
    } finally {
      setPublicLoading(false);
    }
  }

  const typeLabel = {
    listening: "Listening",
    reading: "Reading",
    combined: "Full Test",
  } as const;

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">
              {role === "user"
                ? "Thư viện cá nhân"
                : role === "student"
                  ? "Kho đề của giáo viên lớp đã tham gia"
                  : "Thư viện dùng chung"}
            </p>
            <h1 className="mt-1 text-3xl font-extrabold text-[#1f4e79]">
              {role === "user" ? "My Exams" : "Kho đề thi"}
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              {role === "student"
                ? "Kho chỉ hiện đề của các giáo viên đang sở hữu lớp bạn đã tham gia."
                : "Mở lại đề đã tạo và luyện tập theo từng Part hoặc Thi thử 120 phút."}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button onClick={() => router.push("/history")} className="ui-btn-secondary">
              <HistoryIcon className="h-4 w-4 text-[#1f4e79]" /> Lịch sử làm bài
            </button>
            {(isTeacher || role === "user") && (
              <button onClick={() => router.push("/")} className="ui-btn-primary">
                <Plus className="h-5 w-5" /> Tạo đề mới
              </button>
            )}
          </div>
        </div>

        <section className="mt-7 grid gap-4 sm:grid-cols-3">
          {[
            { label: "Tổng số đề", value: totals.all, icon: FileQuestion },
            { label: "Sẵn sàng", value: totals.ready, icon: BookOpen },
            { label: "Lượt đã làm", value: totals.attempts, icon: Play },
          ].map(({ label, value, icon: Icon }) => (
            <div key={label} className="ui-card flex items-center gap-4 p-5">
              <span className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-[#1f4e79]">
                <Icon className="h-6 w-6" />
              </span>
              <div>
                <p className="text-sm font-semibold text-slate-500">{label}</p>
                <p className="text-2xl font-extrabold text-[#1f4e79]">{value}</p>
              </div>
            </div>
          ))}
        </section>

        <section className="ui-card mt-6 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-2">
              {(role === "teacher" || role === "student" || role === "admin") && (
                <div className="mr-2 inline-flex rounded-lg border border-slate-300 bg-slate-50 p-1">
                  {[
                    ["all", "Mọi loại"],
                    ["listening", "Listening"],
                    ["reading", "Reading"],
                    ["combined", "Full Test"],
                  ].map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => {
                        setKindFilter(value as typeof kindFilter);
                        setPage(1);
                      }}
                      className={`rounded-md px-3 py-1.5 text-xs font-bold ${
                        kindFilter === value
                          ? "bg-[#1f4e79] text-white"
                          : "text-slate-600 hover:bg-white"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              <button
                type="button"
                onClick={() => {
                  setFilter("all");
                  setPage(1);
                }}
                className={`rounded-lg border px-4 py-2 text-sm font-bold shadow-sm transition ${
                  filter === "all"
                    ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                    : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
                }`}
              >
                Tất cả
              </button>
              {tags.map((tag) => {
                const remoteExamCount = items.filter(
                  (item) => item.category?.trim() === tag && !item.local_payload,
                ).length;
                const bankTag = bankTags.find((item) => item.name === tag);
                return (
                  <div key={tag} className="relative inline-flex">
                    <button
                      type="button"
                      onClick={() => {
                        setFilter(tag);
                        setPage(1);
                      }}
                      className={`rounded-l-lg border border-r-0 px-4 py-2 text-sm font-bold shadow-sm transition ${
                        filter === tag
                          ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                          : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
                      }`}
                    >
                      {tag}
                    </button>
                    {(role === "teacher" || role === "admin") && bankTag && (
                      <button
                        type="button"
                        aria-label={`Thao tác với Tag ${tag}`}
                        aria-expanded={tagMenu === tag}
                        onClick={() => setTagMenu(tagMenu === tag ? null : tag)}
                        className={`rounded-r-lg border px-2.5 shadow-sm transition ${
                          filter === tag
                            ? "border-[#1f4e79] bg-[#1f4e79] text-white hover:bg-[#1d4c72]"
                            : "border-slate-300 bg-white text-slate-500 hover:border-[#1f4e79]"
                        }`}
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                    )}
                    {tagMenu === tag && bankTag && (
                      <div className="absolute left-0 top-12 z-30 w-64 rounded-xl border border-slate-300 bg-white p-1.5 shadow-xl">
                        <button
                          type="button"
                          onClick={() => void renameTag(bankTag)}
                          className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-semibold text-slate-700 hover:bg-slate-50"
                        >
                          <Edit className="h-4 w-4 text-slate-500" />
                          Đổi tên Tag
                        </button>
                        <button
                          type="button"
                          onClick={() => void deleteTag(bankTag)}
                          className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-semibold text-red-700 hover:bg-red-50"
                        >
                          <Trash2 className="h-4 w-4" />
                          Xóa Tag
                        </button>
                        <button
                          type="button"
                          disabled={remoteExamCount === 0}
                          onClick={() => void openTagPublicDialog(tag)}
                          className="flex w-full items-start gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <Megaphone className="mt-0.5 h-4 w-4 shrink-0 text-[#1f4e79]" />
                          <span>
                            Public toàn bộ Tag
                            <small className="mt-0.5 block font-normal text-slate-500">
                              {remoteExamCount} đề đã đồng bộ
                            </small>
                          </span>
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              {role === "user" && items.some((item) => !item.category?.trim()) && (
                <button
                  type="button"
                  onClick={() => setFilter(UNCATEGORIZED)}
                  className={`rounded-lg border px-4 py-2 text-sm font-bold shadow-sm transition ${
                    filter === UNCATEGORIZED
                      ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                      : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
                  }`}
                >
                  Chưa phân loại
                </button>
              )}
            </div>
            <label className="relative block w-full lg:w-80">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
                placeholder="Tìm theo tên đề..."
                className="w-full rounded-lg border border-slate-300 py-2.5 pl-10 pr-4 text-sm outline-none focus:border-[#1f4e79]"
              />
            </label>
          </div>
        </section>

        {error && (
          <div className="mt-5 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {loading ? (
          <ExamifyLoader fullScreen={false} message="Đang tải kho đề thi..." />
        ) : visibleItems.length === 0 ? (
          <section className="ui-card mt-6 flex min-h-80 flex-col items-center justify-center p-8 text-center">
            <FileQuestion className="h-12 w-12 text-slate-300" />
            <h2 className="mt-4 text-xl font-extrabold text-[#1f4e79]">
              Chưa có đề thi
            </h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
              Tạo đề từ PDF scan. Sau khi review và finalize, đề sẽ tự động xuất hiện tại đây.
            </p>
            {(isTeacher || role === "user") && (
              <button onClick={() => router.push("/")} className="ui-btn-primary mt-5">
                <Plus className="h-5 w-5" /> Tạo đề đầu tiên
              </button>
            )}
          </section>
        ) : (
          <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {visibleItems.map((exam) => {
              const Icon = exam.exam_type === "listening" ? Headphones : BookOpen;
              return (
                <article key={exam.id} className="ui-card relative flex flex-col justify-between p-4">
                  <div>
                    <div className="flex items-start justify-between gap-4">
                      <span className="rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-[#1f4e79]">
                        <Icon className="h-5 w-5" />
                      </span>
                      {(isTeacher || role === "user") && <div className="relative">
                        <button
                          onClick={() => setMenu(menu === exam.id ? null : exam.id)}
                          className="rounded-lg border border-slate-300 bg-white p-2 text-slate-500 shadow-sm hover:border-[#1f4e79]"
                        >
                          <MoreHorizontal className="h-5 w-5" />
                        </button>
                        {menu === exam.id && (
                          <div className="absolute right-0 top-11 z-20 w-52 rounded-xl border border-slate-300 bg-white p-1 shadow-xl">
                            <button
                              onClick={() => {
                                setMenu(null);
                                void reopenForEditing(exam);
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 font-medium"
                            >
                              <Edit className="h-4 w-4 text-slate-500" /> Chỉnh sửa đề
                            </button>
                            <button
                              onClick={() => {
                                setMenu(null);
                                void reopenForEditing(exam, "solutions");
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-semibold text-[#173a5c] hover:bg-brand-50"
                            >
                              <BookOpen className="h-4 w-4" /> Nhập giải chi tiết
                            </button>
                            {isTeacher && (
                              <button
                                onClick={() => void toggleArchive(exam)}
                                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 font-medium"
                              >
                                <FolderOpen className="h-4 w-4 text-slate-500" />
                                {exam.status === "archived" ? "Bỏ lưu trữ" : "Lưu trữ"}
                              </button>
                            )}

                            {isTeacher && isMiniTestTag(exam.category) && (
                              <>
                                <button
                                  onClick={() => {
                                    setMenu(null);
                                    void handleCopyPublicLink(exam);
                                  }}
                                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-semibold text-brand-700 hover:bg-brand-50"
                                >
                                  <LinkIcon className="h-4 w-4 text-brand-600" /> Tạo link public
                                </button>
                                <button
                                  onClick={() => {
                                    setMenu(null);
                                    void handleOpenSubmissionsModal(exam);
                                  }}
                                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-semibold text-blue-700 hover:bg-blue-50"
                                >
                                  <ClipboardList className="h-4 w-4 text-blue-600" /> Danh sách kết quả
                                </button>
                              </>
                            )}

                            <button
                              onClick={() => {
                                setMenu(null);
                                void deleteExam(exam);
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-red-700 hover:bg-red-50 font-medium"
                            >
                              <Trash2 className="h-4 w-4" /> Xóa
                            </button>
                          </div>
                        )}
                      </div>}
                    </div>
                    <div className="mt-4">
                      <span className="rounded-full border border-slate-300 bg-slate-50 px-3 py-1 text-xs font-bold text-[#1f4e79]">
                        {typeLabel[exam.exam_type]}
                      </span>
                      <span className="ml-2 rounded-full border border-[#1f4e79]/20 bg-[#1f4e79]/5 px-3 py-1 text-xs font-bold text-[#1f4e79]">
                        {exam.category?.trim() || "Chưa phân loại"}
                      </span>
                      <div className="mt-3 flex items-start gap-2">
                        <h2 className="min-w-0 flex-1 line-clamp-2 text-base font-extrabold text-slate-900">
                          {exam.title}
                        </h2>
                        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-600">
                          <Play className="h-3 w-3" />
                          {exam.attempt_count} lượt
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-semibold text-slate-600">
                        <span>{exam.question_count} câu</span>
                        <span>{exam.answer_key_count} đáp án</span>
                        <span>
                          {exam.solution_question_count || 0}/{exam.question_count} câu có giải
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Tạo ngày {new Date(exam.created_at).toLocaleDateString("vi-VN")}
                      </p>
                    </div>
                  </div>

                  <div>
                    {isTeacher && (
                      <div className="mt-4 space-y-2">
                        <button
                          type="button"
                          onClick={() => void reopenForEditing(exam, "solutions")}
                          className="ui-btn-secondary w-full"
                        >
                          <BookOpen className="h-4 w-4" /> Import / sửa giải chi tiết
                        </button>
                        {isMiniTestTag(exam.category) && (
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => void handleCopyPublicLink(exam)}
                              className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-brand-300 bg-brand-50 px-3 py-2 text-xs font-bold text-brand-700 hover:bg-brand-100 transition-all shadow-sm"
                            >
                              <LinkIcon className="h-4 w-4" /> Tạo link public
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleOpenSubmissionsModal(exam)}
                              className="flex items-center justify-center gap-1.5 rounded-xl border border-blue-300 bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700 hover:bg-blue-100 transition-all shadow-sm"
                              title="Xem danh sách kết quả học viên"
                            >
                              <ClipboardList className="h-4 w-4" /> Kết quả
                            </button>
                          </div>
                        )}
                        <button
                          type="button"
                          onClick={() => void openPublicDialog(exam)}
                          className="ui-btn-secondary w-full"
                        >
                          {exam.local_payload ? (
                            <><Megaphone className="h-4 w-4" /> Giao bài cho lớp {exam.sync_status === "synced" ? "(đã đồng bộ)" : exam.sync_status === "failed" ? "(lỗi đồng bộ)" : exam.sync_status === "conflict" ? "(đang xung đột)" : "(chờ đồng bộ)"}</>
                          ) : (
                            <><Megaphone className="h-4 w-4" /> Giao bài cho lớp</>
                          )}
                        </button>
                      </div>
                    )}
                    {exam.local_payload && ["failed", "conflict"].includes(exam.sync_status || "") && (
                      <p className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700">
                        {exam.sync_status === "conflict"
                          ? `${exam.sync_error || "Bản web đã thay đổi."} Đây là bản local được giữ an toàn; xóa card này nếu muốn dùng bản web.`
                          : exam.sync_error || "Đề chưa đồng bộ được lên máy chủ. Mở Public để thử lại và xem lỗi chi tiết."}
                      </p>
                    )}
                    <button
                      onClick={() => {
                        setSelectedExam(exam);
                      }}
                      className={`ui-btn-primary w-full ${isTeacher ? "mt-2" : "mt-4"}`}
                    >
                      <Play className="h-5 w-5" /> Làm bài
                    </button>
                  </div>
                </article>
              );
            })}
          </section>
        )}
        {pages > 1 && (
          <div className="mt-7 flex items-center justify-center gap-3">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              className="ui-btn-secondary disabled:opacity-50"
            >
              Trang trước
            </button>
            <span className="text-sm font-bold text-slate-600">{page}/{pages}</span>
            <button
              type="button"
              disabled={page >= pages}
              onClick={() => setPage((current) => Math.min(pages, current + 1))}
              className="ui-btn-secondary disabled:opacity-50"
            >
              Trang sau
            </button>
          </div>
        )}
      </div>

      {selectedExam && (
        <ExamLaunchDialog
          title={selectedExam.title}
          questionCount={selectedExam.question_count}
          durationMinutes={Math.max(1, selectedExam.duration_minutes || 60)}
          availablePartNumbers={
            selectedExam.exam_type === "listening"
              ? [1, 2, 3, 4]
              : selectedExam.exam_type === "reading"
                ? [5, 6, 7]
                : [1, 2, 3, 4, 5, 6, 7]
          }
          loading={launching}
          onClose={() => setSelectedExam(null)}
          onStart={startConfiguredExam}
        />
      )}

      {(publicExam || publicTag) && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-0 sm:items-center sm:p-4">
          <section className="flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border border-slate-300 bg-white shadow-2xl sm:rounded-2xl">
            <div className="flex items-start justify-between bg-[#1f4e79] px-5 py-4 text-white">
              <div>
                <p className="text-xs font-bold uppercase tracking-widest text-slate-300">
                  {publicTag ? "Giao bài theo Tag" : "Giao bài cho lớp học"}
                </p>
                <h2 className="mt-1 text-xl font-extrabold">
                  {publicTag ? `Tag: ${publicTag}` : publicExam?.title}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => {
                  setPublicExam(null);
                  setPublicTag(null);
                }}
                className="rounded-lg p-2 hover:bg-white/10"
                aria-label="Đóng"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-4 overflow-y-auto p-5">
              <p className="text-sm leading-6 text-slate-600">
                {publicTag
                  ? "Kho đề tự hiện theo chủ lớp. Chọn lớp ở đây chỉ để giao đồng thời toàn bộ đề thuộc Tag này như tài nguyên học tập."
                  : "Kho đề tự hiện theo chủ lớp. Chọn lớp ở đây chỉ để giao bộ đề này như tài nguyên học tập; học viên vẫn tự chọn Luyện tập hoặc Thi thử, Part và thời gian."}
              </p>
              {publicLoading && teacherClasses.length === 0 ? (
                <div className="flex min-h-32 items-center justify-center text-sm text-slate-500">
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Đang tải lớp học...
                </div>
              ) : teacherClasses.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 p-5 text-center text-sm text-slate-500">
                  Bạn chưa có lớp đang hoạt động để Public đề.
                </div>
              ) : (
                <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                  {teacherClasses.map((classroom) => {
                    const alreadyPublished = publishedClassIds.includes(classroom.id);
                    const checked = selectedClassIds.includes(classroom.id);
                    const progress = tagClassProgress[classroom.id];
                    return (
                      <label
                        key={classroom.id}
                        className={`flex cursor-pointer items-center justify-between rounded-xl border p-4 ${
                          checked ? "border-[#1f4e79] bg-slate-50" : "border-slate-200"
                        }`}
                      >
                        <span className="font-bold text-slate-800">{classroom.name}</span>
                        <span className="flex items-center gap-3">
                          {alreadyPublished && (
                            <span className="text-xs font-bold text-emerald-700">
                              {publicTag ? "Đã Public đủ" : "Đã có bài Public"}
                            </span>
                          )}
                          {publicTag && progress && !alreadyPublished && progress.published > 0 && (
                            <span className="text-xs font-bold text-amber-700">
                              Đã có {progress.published}/{progress.total} đề
                            </span>
                          )}
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setSelectedClassIds((current) =>
                                current.includes(classroom.id)
                                  ? current.filter((id) => id !== classroom.id)
                                  : [...current, classroom.id],
                              )
                            }
                            className="h-5 w-5 accent-[#1f4e79]"
                          />
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
              {publicMessage && (
                <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-700">
                  {publicMessage}
                </p>
              )}
              <div className="flex justify-end gap-3 border-t border-slate-200 pt-5">
                <button
                  type="button"
                  onClick={() => {
                    setPublicExam(null);
                    setPublicTag(null);
                  }}
                  className="ui-btn-secondary"
                >
                  Đóng
                </button>
                <button
                  type="button"
                  disabled={
                    publicLoading ||
                    selectedClassIds.length === 0
                  }
                  onClick={() => void publishPractice()}
                  className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {publicLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Megaphone className="h-4 w-4" />}
                  {publicTag ? "Public toàn bộ Tag" : "Public bộ đề"}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {/* PUBLIC SHARE LINK MODAL */}
      {publicLinkModalExam && publicShareCode && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl animate-in fade-in zoom-in duration-150 border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-brand-50 text-[#1f4e79] font-bold">
                  <LinkIcon className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-lg font-extrabold text-slate-900">Link Làm Bài Public Mini Test</h3>
                  <p className="text-xs text-slate-500 line-clamp-1">{publicLinkModalExam.title}</p>
                </div>
              </div>
              <button
                onClick={() => {
                  setPublicLinkModalExam(null);
                  setPublicShareCode(null);
                }}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="mt-5 space-y-4">
              <p className="text-xs font-semibold text-slate-600 leading-relaxed">
                Gửi đường link công khai này cho học viên hoặc thí sinh tuyển sinh. Học viên có thể vào làm bài trực tiếp trên mọi trình duyệt mà không cần nhập mã token hay đăng nhập tài khoản.
              </p>

              <div className="rounded-2xl border border-brand-200 bg-brand-50/60 p-3.5">
                <label className="block text-xs font-extrabold text-brand-800 mb-1.5">
                  Đường link làm bài:
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    readOnly
                    value={publicWebUrl(`/public-test/${publicShareCode}`)}
                    className="w-full rounded-xl border border-brand-300 bg-white px-3 py-2 text-xs font-mono text-slate-800 select-all focus:outline-none"
                  />
                  <button
                    onClick={async () => {
                      const url = publicWebUrl(`/public-test/${publicShareCode}`);
                      await navigator.clipboard.writeText(url);
                      setCopiedShareLink(true);
                      setTimeout(() => setCopiedShareLink(false), 3000);
                    }}
                    className="flex shrink-0 items-center gap-1.5 rounded-xl bg-[#1f4e79] px-4 py-2 text-xs font-bold text-white shadow-sm hover:bg-[#1e3a5f] active:scale-95 transition-all"
                  >
                    {copiedShareLink ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    {copiedShareLink ? "Đã chép!" : "Sao chép"}
                  </button>
                </div>
              </div>

              <div className="flex items-start gap-2.5 rounded-xl bg-amber-50 p-3 text-xs font-semibold text-amber-800 border border-amber-200">
                <span className="shrink-0 text-base">💡</span>
                <span>Trước khi làm bài, thí sinh sẽ điền Họ tên & SĐT. Hệ thống sẽ tự động lưu và tổng hợp danh sách kết quả gửi về cho bạn.</span>
              </div>
            </div>

            <div className="mt-6 flex justify-end">
              <button
                onClick={() => {
                  setPublicLinkModalExam(null);
                  setPublicShareCode(null);
                }}
                className="rounded-xl bg-slate-100 px-5 py-2.5 text-xs font-bold text-slate-700 hover:bg-slate-200"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MINI TEST RESULTS MODAL */}
      {resultsModalExam && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="my-8 w-full max-w-4xl rounded-3xl bg-white p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in duration-150">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 font-bold">
                  <ClipboardList className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-lg font-extrabold text-slate-900">Danh Sách Kết Quả Mini Test</h3>
                  <p className="text-xs text-slate-500 line-clamp-1">{resultsModalExam.title}</p>
                </div>
              </div>
              <button
                onClick={() => {
                  setResultsModalExam(null);
                  setSelectedSubmissionDetail(null);
                }}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Metrics Bar */}
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3.5">
                <span className="text-xs font-bold text-slate-500">Tổng bài nộp</span>
                <p className="text-2xl font-black text-slate-900">{publicSubmissions.length} lượt</p>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3.5">
                <span className="text-xs font-bold text-slate-500">Số câu đúng trung bình</span>
                <p className="text-2xl font-black text-[#1f4e79]">
                  {publicSubmissions.length > 0
                    ? (publicSubmissions.reduce((a, b) => a + (b.total_correct || 0), 0) / publicSubmissions.length).toFixed(1)
                    : 0}{" "}
                  <span className="text-xs font-bold text-slate-500">câu</span>
                </p>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3.5">
                <span className="text-xs font-bold text-slate-500">Số câu đúng cao nhất</span>
                <p className="text-2xl font-black text-blue-600">
                  {publicSubmissions.length > 0
                    ? Math.max(...publicSubmissions.map((s) => s.total_correct || 0))
                    : 0}{" "}
                  <span className="text-xs font-bold text-slate-500">câu</span>
                </p>
              </div>
            </div>

            {/* Search Input */}
            <div className="mt-4 flex items-center gap-3">
              <div className="relative flex-1">
                <Search className="absolute left-3.5 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  type="text"
                  placeholder="Tìm theo tên học viên hoặc SĐT..."
                  value={submissionSearch}
                  onChange={(e) => setSubmissionSearch(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 pl-10 pr-4 py-2 text-xs font-semibold focus:border-[#1f4e79] focus:outline-none"
                />
              </div>
            </div>

            {/* Table */}
            <div className="mt-4 max-h-[400px] overflow-y-auto rounded-2xl border border-slate-200">
              {loadingSubmissions ? (
                <div className="flex h-36 items-center justify-center gap-2 text-slate-500 text-xs font-bold">
                  <Loader2 className="h-5 w-5 animate-spin text-[#1f4e79]" /> Đang tải danh sách kết quả...
                </div>
              ) : publicSubmissions.length === 0 ? (
                <div className="flex h-36 flex-col items-center justify-center text-slate-400 text-xs font-bold">
                  <Users className="h-8 w-8 text-slate-300 mb-1" />
                  Chưa có học viên nào làm bài mini test này.
                </div>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-100 font-extrabold text-slate-700 border-b border-slate-200">
                    <tr>
                      <th className="px-4 py-3">Học viên / SĐT</th>
                      <th className="px-4 py-3">Thời gian nộp</th>
                      <th className="px-4 py-3">Kết quả</th>
                      <th className="px-4 py-3">Phân tích từng Part</th>
                      <th className="px-4 py-3 text-right">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 font-semibold text-slate-700">
                    {publicSubmissions
                      .filter((sub) => {
                        if (!submissionSearch.trim()) return true;
                        const q = submissionSearch.toLowerCase();
                        return (
                          sub.student_name.toLowerCase().includes(q) ||
                          (sub.phone && sub.phone.includes(q))
                        );
                      })
                      .map((sub) => (
                        <tr key={sub.id} className="hover:bg-slate-50">
                          <td className="px-4 py-3">
                            <div className="font-extrabold text-slate-900">{sub.student_name}</div>
                            <div className="text-[11px] text-slate-500 font-mono">{sub.phone || "Không có SĐT"}</div>
                          </td>
                          <td className="px-4 py-3 text-slate-600">
                            {sub.submitted_at
                              ? new Date(sub.submitted_at).toLocaleString("vi-VN")
                              : "Đang làm..."}
                          </td>
                          <td className="px-4 py-3">
                            <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-black text-brand-700 border border-brand-200">
                              {sub.total_correct}/{sub.question_count} câu
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-1 max-w-xs">
                              {Object.entries(sub.part_breakdown || {}).map(([partName, stat]: any) => (
                                <span
                                  key={partName}
                                  className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-600 border border-slate-200"
                                >
                                  {partName}: <span className="text-brand-700 font-black">{stat.correct}</span>/{stat.total}
                                </span>
                              ))}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <button
                                onClick={() => setSelectedSubmissionDetail(sub)}
                                className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs font-bold text-blue-700 hover:bg-blue-100 transition-colors"
                              >
                                Xem chi tiết
                              </button>
                              <button
                                onClick={() => handleDeleteSubmission(sub.id)}
                                className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 transition-colors"
                                title="Xóa kết quả"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="mt-6 flex justify-end">
              <button
                onClick={() => {
                  setResultsModalExam(null);
                  setSelectedSubmissionDetail(null);
                }}
                className="rounded-xl bg-slate-100 px-5 py-2.5 text-xs font-bold text-slate-700 hover:bg-slate-200"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SELECTED SUBMISSION DETAIL MODAL */}
      {selectedSubmissionDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="my-8 w-full max-w-2xl rounded-3xl bg-white p-6 shadow-2xl border border-slate-200 animate-in fade-in zoom-in duration-150">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4">
              <div>
                <h3 className="text-lg font-black text-slate-900">Chi Tiết Bài Làm - {selectedSubmissionDetail.student_name}</h3>
                <p className="text-xs text-slate-500 font-bold">SĐT: {selectedSubmissionDetail.phone || "N/A"} • Nộp lúc: {selectedSubmissionDetail.submitted_at ? new Date(selectedSubmissionDetail.submitted_at).toLocaleString("vi-VN") : "N/A"}</p>
              </div>
              <button
                onClick={() => setSelectedSubmissionDetail(null)}
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="mt-4 flex items-center justify-between rounded-2xl bg-brand-50 p-4 border border-brand-200">
              <div>
                <span className="text-xs font-bold text-brand-800">Tổng số câu đúng:</span>
                <p className="text-2xl font-black text-brand-700">{selectedSubmissionDetail.total_correct} / {selectedSubmissionDetail.question_count} câu</p>
              </div>
              <div className="text-right">
                <span className="text-xs font-bold text-brand-800">Tỷ lệ chính xác:</span>
                <p className="text-2xl font-black text-brand-700">
                  {Math.round((selectedSubmissionDetail.total_correct / (selectedSubmissionDetail.question_count || 1)) * 100)}%
                </p>
              </div>
            </div>

            <div className="mt-4 max-h-[350px] overflow-y-auto rounded-2xl border border-slate-200 p-4">
              <h4 className="text-xs font-extrabold text-slate-700 uppercase tracking-wider mb-3">Danh sách câu trả lời</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {Object.entries(selectedSubmissionDetail.answers || {}).map(([qNum, ansInfo]: any) => (
                  <div
                    key={qNum}
                    className={`flex items-center justify-between rounded-xl p-2.5 text-xs font-extrabold border ${
                      ansInfo.is_correct
                        ? "bg-emerald-50 border-emerald-300 text-emerald-800"
                        : "bg-red-50 border-red-300 text-red-800"
                    }`}
                  >
                    <span>Câu {qNum}:</span>
                    <span>
                      {ansInfo.selected || "Chưa chọn"} {ansInfo.is_correct ? "✓" : `(ĐƯ: ${ansInfo.correct})`}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-6 flex justify-end">
              <button
                onClick={() => setSelectedSubmissionDetail(null)}
                className="rounded-xl bg-slate-100 px-5 py-2.5 text-xs font-bold text-slate-700 hover:bg-slate-200"
              >
                Quay lại danh sách
              </button>
            </div>
          </div>
        </div>
      )}

    </main>
  );
}
