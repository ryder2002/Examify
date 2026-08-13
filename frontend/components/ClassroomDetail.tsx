"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  Archive,
  ArrowLeft,
  BarChart3,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  Copy,
  Download,
  FilePlus2,
  FileSpreadsheet,
  GraduationCap,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Settings,
  ShieldAlert,
  Tag,
  UserMinus,
  UserRoundCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

import Header from "@/components/Header";
import ExamLaunchDialog, {
  type ExamLaunchConfiguration,
} from "@/components/ExamLaunchDialog";
import {
  apiFetch,
  isDesktop,
  resolveIdentity,
} from "@/lib/api";
import { syncDesktopPending } from "@/lib/desktop-sync";
import type { ExamSummary, FinalExam } from "@/lib/utils";
import { cacheExamAssets, getExamPack, putExamPack } from "@/lib/offline-db";
import { quizPath } from "@/lib/exam-route";

type Tab = "assignments" | "members" | "monitoring" | "settings";
type Assignment = {
  id: string;
  title: string;
  mode: "exam" | "practice";
  kind: "official_exam" | "study_resource";
  status: "draft" | "published" | "closed";
  opens_at: string | null;
  closes_at: string | null;
  duration_seconds: number;
  attempt_limit: number | null;
  score_release: string;
  answer_release: string;
  anti_cheat_enabled: boolean;
  listening_navigation_locked: boolean;
  attempt_count?: number;
  attempts_remaining?: number | null;
  availability?: "open" | "upcoming" | "closed";
  score_released?: boolean;
  answers_released?: boolean;
  available_part_numbers: number[];
  tag?: string | null;
  latest_attempt?: {
    id: string;
    status: string;
    scores?: { toeic: number; listening: number; reading: number };
  } | null;
  exam: {
    id: string;
    version_id: string;
    title: string;
    exam_type: string;
    question_count: number;
    answer_key_count: number;
    duration_minutes: number;
    category?: string | null;
  };
};
type Classroom = {
  id: string;
  name: string;
  description: string;
  status: "active" | "archived";
  join_code?: string;
  member_count: number;
  assignment_count: number;
  assignments?: Assignment[];
  member?: { id: string; member_ref: string; full_name: string };
};
type Member = {
  id: string;
  member_ref: string;
  full_name: string;
  status: "active" | "removed";
  joined_at: string;
  last_seen_at: string;
};
type MonitorItem = {
  member_id: string;
  member_ref: string;
  full_name: string;
  member_status: string;
  state: string;
  attempt_id: string | null;
  answered_count: number;
  time_left_seconds: number | null;
  score_toeic: number | null;
  listening_score: number | null;
  reading_score: number | null;
  correct_count: number | null;
  graded_count: number | null;
  violation_count: number;
  last_heartbeat_at: string | null;
};
type MonitorHistoryItem = {
  attempt_id: string;
  attempt_number: number;
  assignment_id: string;
  assignment_title: string;
  assignment_mode: "exam" | "practice";
  member_id: string;
  member_ref: string;
  full_name: string;
  state: string;
  status: string;
  answered_count: number;
  score_toeic: number | null;
  listening_score: number | null;
  reading_score: number | null;
  correct_count: number | null;
  graded_count: number;
  violation_count: number;
  started_at: string;
  submitted_at: string | null;
};
type MemberResult = {
  attempt_id: string;
  attempt_number: number;
  title: string;
  mode: "exam" | "practice";
  status: string;
  score_toeic: number | null;
  listening_score: number | null;
  reading_score: number | null;
  correct_count: number | null;
  graded_count: number;
  time_spent_seconds: number | null;
  started_at: string;
  submitted_at: string | null;
  violation_count: number;
};
type MonitorEvent = {
  id: string;
  event_type: string;
  occurred_at: string | null;
  received_at: string;
};

export default function ClassroomDetailPage() {
  const searchParams = useSearchParams();
  const classroomId = searchParams.get("id") || "";
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [offlineLoading, setOfflineLoading] = useState<string | null>(null);
  const [offlineProgress, setOfflineProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [classroom, setClassroom] = useState<Classroom | null>(null);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [monitoring, setMonitoring] = useState<MonitorItem[]>([]);
  const [monitorHistory, setMonitorHistory] = useState<MonitorHistoryItem[]>([]);
  const [monitorSummary, setMonitorSummary] = useState<Record<string, number>>({});
  const [monitorAssignmentId, setMonitorAssignmentId] = useState("");
  const [detailMember, setDetailMember] = useState<{
    member_id: string;
    member_ref: string;
    full_name: string;
  } | null>(null);
  const [memberResults, setMemberResults] = useState<MemberResult[]>([]);
  const [monitorEvents, setMonitorEvents] = useState<MonitorEvent[]>([]);
  const [tab, setTab] = useState<Tab>("assignments");
  const [exams, setExams] = useState<ExamSummary[]>([]);
  const [selectedExamId, setSelectedExamId] = useState("");
  const [assignmentTitle, setAssignmentTitle] = useState("");
  const assignmentMode = "exam" as const;
  const [durationMinutes, setDurationMinutes] = useState(120);
  const [attemptLimit, setAttemptLimit] = useState("1");
  const [opensAt, setOpensAt] = useState("");
  const [closesAt, setClosesAt] = useState("");
  const [scoreRelease, setScoreRelease] = useState("immediate");
  const [answerRelease, setAnswerRelease] = useState("manual");
  const [antiCheat, setAntiCheat] = useState(true);
  const [listeningNavigationLocked, setListeningNavigationLocked] = useState(true);
  const [launchResource, setLaunchResource] = useState<Assignment | null>(null);
  const [studentSearch, setStudentSearch] = useState("");
  const [studentTag, setStudentTag] = useState("all");
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportExamTarget, setExportExamTarget] = useState<string>("all");
  const [exporting, setExporting] = useState(false);

  async function handleExportScores() {
    setExporting(true);
    try {
      const query = new URLSearchParams({ history_limit: "2000" });
      if (exportExamTarget !== "all") {
        query.set("assignment_id", exportExamTarget);
      }
      const res = await apiFetch(
        `/api/v1/teacher/classrooms/${classroomId}/monitoring?${query.toString()}`,
        { cache: "no-store" },
      );
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail || "Không tải được danh sách điểm thi");
      const history: MonitorHistoryItem[] = payload.history || [];

      if (!history.length) {
        setError("Không có dữ liệu điểm thi nào để xuất.");
        return;
      }

      const XLSX = await import("xlsx");
      const wb = XLSX.utils.book_new();

      const createSheet = (items: MonitorHistoryItem[]) => {
        const headers = [
          "STT",
          "Họ và tên",
          "Mã học viên",
          "Bài thi",
          "Lần làm",
          "Chế độ",
          "Trạng thái",
          "Điểm TOEIC",
          "Điểm LC",
          "Điểm RC",
          "Số câu đúng",
          "Cảnh báo gian lận",
          "Thời gian bắt đầu",
          "Thời gian nộp bài",
        ];

        const rows = items.map((item, idx) => [
          idx + 1,
          item.full_name,
          item.member_ref,
          item.assignment_title,
          `#${item.attempt_number}`,
          item.assignment_mode === "exam" ? "Thi" : "Luyện tập",
          item.status === "submitted" ? "Đã nộp" : item.state === "in_progress" ? "Đang làm" : "Mất kết nối",
          item.score_toeic ?? "—",
          item.listening_score ?? "—",
          item.reading_score ?? "—",
          item.correct_count != null ? `${item.correct_count}/${item.graded_count}` : "—",
          item.violation_count || 0,
          item.started_at ? new Date(item.started_at).toLocaleString("vi-VN") : "—",
          item.submitted_at ? new Date(item.submitted_at).toLocaleString("vi-VN") : "—",
        ]);

        const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
        ws["!cols"] = [
          { wch: 6 },
          { wch: 25 },
          { wch: 14 },
          { wch: 25 },
          { wch: 10 },
          { wch: 12 },
          { wch: 14 },
          { wch: 12 },
          { wch: 10 },
          { wch: 10 },
          { wch: 14 },
          { wch: 18 },
          { wch: 22 },
          { wch: 22 },
        ];
        return ws;
      };

      if (exportExamTarget === "all") {
        const grouped = new Map<string, { title: string; items: MonitorHistoryItem[] }>();
        for (const item of history) {
          const key = item.assignment_id || item.assignment_title;
          if (!grouped.has(key)) {
            grouped.set(key, { title: item.assignment_title || "Bài thi", items: [] });
          }
          grouped.get(key)!.items.push(item);
        }

        const usedSheetNames = new Set<string>();
        for (const [, { title, items }] of grouped) {
          let sheetName = title.replace(/[\\/?*:[\]]/g, "").trim().slice(0, 28) || "Bài thi";
          let counter = 1;
          const originalName = sheetName;
          while (usedSheetNames.has(sheetName)) {
            sheetName = `${originalName}_${counter++}`;
          }
          usedSheetNames.add(sheetName);

          const ws = createSheet(items);
          XLSX.utils.book_append_sheet(wb, ws, sheetName);
        }
      } else {
        const selectedAss = officialAssignments.find((a) => a.id === exportExamTarget);
        const title = selectedAss?.title || history[0]?.assignment_title || "Bài thi";
        let sheetName = title.replace(/[\\/?*:[\]]/g, "").trim().slice(0, 30) || "Bài thi";
        const ws = createSheet(history);
        XLSX.utils.book_append_sheet(wb, ws, sheetName);
      }

      const classNameClean = (classroom?.name || "LopHoc")
        .replace(/[^a-zA-Z0-9àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ\s_-]/g, "")
        .trim();
      const fileName = `Diem_Hoc_Vien_${classNameClean}_${new Date().toISOString().slice(0, 10)}.xlsx`;
      XLSX.writeFile(wb, fileName);
      setShowExportModal(false);
    } catch (err: any) {
      setError(err.message || "Xuất file thất bại");
    } finally {
      setExporting(false);
    }
  }

  const loadTeacher = useCallback(async () => {
    if (isDesktop() && navigator.onLine) {
      const summary = await syncDesktopPending();
      if (summary.failures.length > 0) {
        setError(
          `Có đề trên máy chưa đồng bộ được: ${summary.failures[0].error}`,
        );
      }
    }
    const [classResponse, membersResponse, examsResponse] = await Promise.all([
      apiFetch(`/api/v1/teacher/classrooms/${classroomId}`, { cache: "no-store" }),
      apiFetch(`/api/v1/teacher/classrooms/${classroomId}/members?page_size=100`, { cache: "no-store" }),
      apiFetch("/api/v1/exams?page_size=100", { cache: "no-store" }),
    ]);
    const [classPayload, membersPayload, examsPayload] = await Promise.all([
      classResponse.json().catch(() => ({})),
      membersResponse.json().catch(() => ({})),
      examsResponse.json().catch(() => ({})),
    ]);
    if (!classResponse.ok) throw new Error(classPayload.detail || "Không tải được lớp");
    if (!membersResponse.ok) {
      throw new Error(membersPayload.detail || "Không tải được học viên");
    }
    if (!examsResponse.ok) {
      throw new Error(
        examsPayload.detail ||
          "Không tải được kho đề trên máy chủ. Đề local cần đồng bộ trước khi tổ chức thi.",
      );
    }
    let allMembers = membersPayload.items || [];
    const memberPages = Number(membersPayload.pages || 1);
    if (memberPages > 1) {
      const remainingResponses = await Promise.all(
        Array.from({ length: memberPages - 1 }, (_, index) =>
          apiFetch(
            `/api/v1/teacher/classrooms/${classroomId}/members?page_size=100&page=${index + 2}`,
            { cache: "no-store" },
          ),
        ),
      );
      const remainingPayloads = await Promise.all(
        remainingResponses.map((response) => response.json().catch(() => ({}))),
      );
      if (remainingResponses.some((response) => !response.ok)) {
        throw new Error("Không tải đủ danh sách học viên");
      }
      allMembers = [
        ...allMembers,
        ...remainingPayloads.flatMap((payload) => payload.items || []),
      ];
    }
    setClassroom(classPayload);
    setAssignments(classPayload.assignments || []);
    setMembers(allMembers);
    setExams(examsPayload.items || []);
  }, [classroomId]);

  const loadStudent = useCallback(async () => {
    const [classResponse, assignmentsResponse] = await Promise.all([
      apiFetch(`/api/v1/student/classrooms/${classroomId}`, {
        cache: "no-store",
      }),
      apiFetch(`/api/v1/student/classrooms/${classroomId}/assignments`, {
        cache: "no-store",
      }),
    ]);
    const [classPayload, assignmentsPayload] = await Promise.all([
      classResponse.json().catch(() => ({})),
      assignmentsResponse.json().catch(() => ({})),
    ]);
    if (!classResponse.ok) {
      throw new Error(classPayload.detail || "Không tải được lớp học");
    }
    setClassroom(classPayload);
    setAssignments(assignmentsPayload.items || []);
  }, [classroomId]);

  useEffect(() => {
    if (!classroomId) {
      setError("Không tìm thấy lớp học");
      return;
    }
    void resolveIdentity()
      .then(async (nextRole) => {
        setRole(nextRole);
        if (nextRole === "teacher") await loadTeacher();
        else await loadStudent();
      })
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Không tải được lớp học"),
      )
      .finally(() => setReady(true));
  }, [classroomId, loadStudent, loadTeacher, router]);

  const loadMonitoring = useCallback(async () => {
    const query = new URLSearchParams({ history_limit: "0" });
    if (monitorAssignmentId) query.set("assignment_id", monitorAssignmentId);
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/monitoring?${query.toString()}`,
      { cache: "no-store" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Không tải được giám sát");
    setMonitoring(payload.items || []);
    setMonitorSummary(payload.summary || {});
  }, [classroomId, monitorAssignmentId]);

  const loadMonitorHistory = useCallback(async () => {
    const query = new URLSearchParams({ history_limit: "200" });
    if (monitorAssignmentId) query.set("assignment_id", monitorAssignmentId);
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/monitoring?${query.toString()}`,
      { cache: "no-store" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Không tải được lịch sử");
    setMonitorHistory(payload.history || []);
  }, [classroomId, monitorAssignmentId]);

  useEffect(() => {
    if (role !== "teacher" || tab !== "monitoring") return;
    void Promise.all([loadMonitoring(), loadMonitorHistory()]).catch((reason) =>
      setError(reason instanceof Error ? reason.message : "Không tải được giám sát"),
    );
    const timer = window.setInterval(() => {
      void loadMonitoring().catch(() => undefined);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [loadMonitorHistory, loadMonitoring, role, tab]);

  useEffect(() => {
    const exam = exams.find((item) => item.id === selectedExamId);
    if (!exam) return;
    setAssignmentTitle(exam.title);
    setDurationMinutes(120);
  }, [exams, selectedExamId]);

  async function createAssignment(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/v1/teacher/classrooms/${classroomId}/assignments`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            exam_id: selectedExamId,
            title: assignmentTitle.trim() || null,
            mode: assignmentMode,
            duration_seconds: durationMinutes * 60,
            attempt_limit: attemptLimit ? Number(attemptLimit) : 1,
            opens_at: opensAt ? new Date(opensAt).toISOString() : null,
            closes_at: closesAt ? new Date(closesAt).toISOString() : null,
            score_release: scoreRelease,
            answer_release: answerRelease,
            anti_cheat_enabled: antiCheat,
            listening_navigation_locked: listeningNavigationLocked,
            publish: true,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không giao được đề");
      setAssignments((current) => [payload, ...current]);
      setSelectedExamId("");
      setAssignmentTitle("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không giao được đề");
    } finally {
      setLoading(false);
    }
  }

  async function changeMember(member: Member) {
    const nextStatus = member.status === "active" ? "removed" : "active";
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/members/${member.id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      },
    );
    if (response.ok) {
      setMembers((current) =>
        current.map((item) =>
          item.id === member.id ? { ...item, status: nextStatus } : item,
        ),
      );
    }
  }

  async function regenerateCode() {
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/regenerate-code`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => ({}));
    if (response.ok) {
      setClassroom((current) => (current ? { ...current, join_code: payload.join_code } : current));
    } else {
      setError(payload.detail || "Không đổi được mã lớp");
    }
  }

  async function closeAssignment(assignment: Assignment) {
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/assignments/${assignment.id}/close`,
      { method: "POST" },
    );
    if (response.ok) {
      setAssignments((current) =>
        current.map((item) =>
          item.id === assignment.id ? { ...item, status: "closed" } : item,
        ),
      );
    }
  }

  async function reopenAssignment(assignment: Assignment) {
    setError(null);
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/assignments/${assignment.id}/reopen`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ additional_attempts: 1, closes_at: null }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không mở lại được bài");
      return;
    }
    setAssignments((current) =>
      current.map((item) => (item.id === assignment.id ? { ...item, ...payload } : item)),
    );
  }

  async function renameAssignment(assignment: Assignment) {
    const title = window.prompt("Tên hiển thị mới của bài thi", assignment.title)?.trim();
    if (!title || title === assignment.title) return;
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/assignments/${assignment.id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không đổi được tên bài");
      return;
    }
    setAssignments((current) =>
      current.map((item) => (item.id === assignment.id ? { ...item, ...payload } : item)),
    );
  }

  async function changeAttemptLimit(assignment: Assignment) {
    if (assignment.mode === "practice") return;
    const raw = window.prompt(
      "Tổng số lượt mỗi học viên được phép làm",
      String(assignment.attempt_limit || 1),
    );
    if (raw == null) return;
    const attemptLimitValue = Number(raw);
    if (
      !Number.isInteger(attemptLimitValue) ||
      attemptLimitValue < 1 ||
      attemptLimitValue > 100
    ) {
      setError("Số lượt phải là số nguyên từ 1 đến 100");
      return;
    }
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/assignments/${assignment.id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attempt_limit: attemptLimitValue }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không đổi được số lượt");
      return;
    }
    setAssignments((current) =>
      current.map((item) => (item.id === assignment.id ? { ...item, ...payload } : item)),
    );
  }

  async function releaseAssignment(assignment: Assignment, answers: boolean) {
    const response = await apiFetch(
      `/api/v1/teacher/classrooms/${classroomId}/assignments/${assignment.id}/release?answers=${answers}`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không công bố được kết quả");
      return;
    }
    setAssignments((current) =>
      current.map((item) =>
        item.id === assignment.id
          ? {
              ...item,
              score_released: true,
              answers_released: answers || item.answers_released,
            }
          : item,
      ),
    );
  }

  async function openMemberDetail(
    item: { member_id: string; member_ref: string; full_name: string },
    attemptId?: string | null,
  ) {
    setDetailMember(item);
    setMemberResults([]);
    setMonitorEvents([]);
    const requests: Promise<Response>[] = [
      apiFetch(
        `/api/v1/teacher/classrooms/${classroomId}/members/${item.member_id}/results`,
        { cache: "no-store" },
      ),
    ];
    if (attemptId) {
      requests.push(
        apiFetch(
          `/api/v1/teacher/classrooms/${classroomId}/attempts/${attemptId}/events`,
          { cache: "no-store" },
        ),
      );
    }
    const responses = await Promise.all(requests);
    const resultPayload = await responses[0].json().catch(() => ({}));
    if (responses[0].ok) setMemberResults(resultPayload.items || []);
    if (responses[1]) {
      const eventPayload = await responses[1].json().catch(() => ({}));
      if (responses[1].ok) setMonitorEvents(eventPayload.items || []);
    }
  }

  async function saveClassroomSettings(event: FormEvent) {
    event.preventDefault();
    if (!classroom) return;
    const response = await apiFetch(`/api/v1/teacher/classrooms/${classroomId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: classroom.name,
        description: classroom.description,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.ok) setClassroom((current) => (current ? { ...current, ...payload } : current));
    else setError(payload.detail || "Không lưu được cài đặt lớp");
  }

  async function toggleArchive() {
    if (!classroom) return;
    const status = classroom.status === "active" ? "archived" : "active";
    const response = await apiFetch(`/api/v1/teacher/classrooms/${classroomId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (response.ok) setClassroom({ ...classroom, status });
  }

  async function startAssignment(
    assignment: Assignment,
    configuration?: ExamLaunchConfiguration,
  ) {
    setLoading(true);
    setError(null);
    try {
      if (!navigator.onLine) {
        const offline = await getExamPack(`assignment:${assignment.id}`);
        const attemptId = String(offline?.metadata?.attemptId || "");
        if (!offline?.exam || !attemptId) {
          throw new Error("Bộ đề này chưa được tải offline khi còn internet.");
        }
        sessionStorage.setItem("quiz-data", JSON.stringify(offline.exam));
        sessionStorage.setItem(
          "quiz-mode",
          offline.metadata?.launchMode === "mock_exam" || offline.metadata?.launchMode === "official_exam"
            ? "exam"
            : "practice",
        );
        sessionStorage.setItem(
          "quiz-duration",
          String(offline.metadata?.timeLeftSeconds || assignment.duration_seconds),
        );
        sessionStorage.setItem(
          "quiz-time-left",
          String(offline.metadata?.timeLeftSeconds || assignment.duration_seconds),
        );
        sessionStorage.setItem("quiz-attempt-id", attemptId);
        sessionStorage.setItem(
          "quiz-initial-answers",
          JSON.stringify(offline.metadata?.answers || {}),
        );
        sessionStorage.removeItem("quiz-flagged-questions");
        sessionStorage.removeItem("quiz-group-index");
        sessionStorage.removeItem("quiz-question-index");
        sessionStorage.removeItem("quiz-question-number");
        sessionStorage.setItem(
          "quiz-class-session",
          JSON.stringify({
            accountAuth: true,
            classroomId,
            assignmentId: assignment.id,
            antiCheatEnabled: false,
          }),
        );
        const path = quizPath(offline.exam);
        sessionStorage.setItem("quiz-slug", path.split("/").pop() || "");
        sessionStorage.setItem(
          "quiz-resume-pending",
          Object.keys(offline.metadata?.answers || {}).length > 0 ? "1" : "0",
        );
        router.push(path);
        return;
      }
      const response = await apiFetch(
        `/api/v1/student/assignments/${assignment.id}/attempts`,
        {
          method: "POST",
          headers: configuration ? { "Content-Type": "application/json" } : undefined,
          body: configuration
            ? JSON.stringify({
                launch_mode: configuration.launchMode,
                part_numbers: configuration.partNumbers,
                duration_seconds: configuration.durationSeconds,
              })
            : undefined,
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không bắt đầu được bài");
      sessionStorage.setItem("quiz-data", JSON.stringify(payload.exam as FinalExam));
      sessionStorage.setItem(
        "quiz-mode",
        payload.launch_mode === "mock_exam" || payload.launch_mode === "official_exam"
          ? "exam"
          : "practice",
      );
      sessionStorage.setItem("quiz-duration", String(payload.time_left_seconds));
      sessionStorage.setItem("quiz-time-left", String(payload.time_left_seconds));
      sessionStorage.setItem("quiz-attempt-id", payload.attempt_id);
      sessionStorage.setItem(
        "quiz-initial-answers",
        JSON.stringify(payload.answers || {}),
      );
      sessionStorage.setItem("quiz-resume-pending", payload.resumed ? "1" : "0");
      sessionStorage.removeItem("quiz-flagged-questions");
      sessionStorage.removeItem("quiz-group-index");
      sessionStorage.removeItem("quiz-question-index");
      if (payload.current_question_number) {
        sessionStorage.setItem(
          "quiz-question-number",
          String(payload.current_question_number),
        );
      } else if (!payload.resumed) {
        sessionStorage.removeItem("quiz-question-number");
      }
      sessionStorage.setItem(
        "quiz-class-session",
        JSON.stringify({
          accountAuth: true,
          classroomId,
          assignmentId: assignment.id,
          antiCheatEnabled: payload.anti_cheat_enabled,
          listeningNavigationLocked: payload.listening_navigation_locked,
        }),
      );
      sessionStorage.removeItem("quiz-result");
      setLaunchResource(null);
      const path = quizPath(payload.exam as FinalExam);
      sessionStorage.setItem("quiz-slug", path.split("/").pop() || "");
      router.push(path);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không bắt đầu được bài");
    } finally {
      setLoading(false);
    }
  }

  async function downloadOfflinePack(assignment: Assignment) {
    if (!navigator.onLine) {
      setError("Cần online một lần để tải bộ đề offline.");
      return;
    }
    setOfflineLoading(assignment.id);
    setOfflineProgress(0);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/v1/student/assignments/${assignment.id}/offline-pack`,
        { method: "POST" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.exam) {
        throw new Error(payload.detail || "Không tải được bộ đề offline");
      }
      await putExamPack(`assignment:${assignment.id}`, payload.exam as FinalExam, {
        assignmentId: assignment.id,
        attemptId: payload.attempt_id,
        timeLeftSeconds: payload.time_left_seconds,
        launchMode: payload.launch_mode,
        answers: payload.answers || {},
        acceptedRevision: payload.accepted_revision || 0,
        classroomId,
      });
      await cacheExamAssets(payload.exam as FinalExam, (completed, total) => {
        setOfflineProgress(Math.round((completed / Math.max(1, total)) * 100));
      });
      setError("Đã tải bộ đề và tạo lượt làm offline. Bạn có thể mất mạng rồi tiếp tục làm bài.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được bộ đề offline");
    } finally {
      setOfflineLoading(null);
      setOfflineProgress(0);
    }
  }

  const studentTags = useMemo(
    () =>
      Array.from(
        new Set(
          assignments
            .map((assignment) =>
              (assignment.tag || assignment.exam.category || "").trim(),
            )
            .filter(Boolean),
        ),
      ).sort((a, b) => a.localeCompare(b, "vi")),
    [assignments],
  );
  const filteredStudentAssignments = useMemo(() => {
    const query = studentSearch.trim().toLocaleLowerCase("vi");
    return assignments.filter((assignment) => {
      if (assignment.availability === "closed" || assignment.status === "closed") {
        return false;
      }
      const tag = (assignment.tag || assignment.exam.category || "").trim();
      const matchesTag = studentTag === "all" || tag === studentTag;
      const matchesSearch =
        !query ||
        `${assignment.title} ${assignment.exam.title} ${tag}`
          .toLocaleLowerCase("vi")
          .includes(query);
      return matchesTag && matchesSearch;
    });
  }, [assignments, studentSearch, studentTag]);

  if (!ready || !classroom) {
    return (
      <main className="min-h-screen bg-slate-50">
        <Header />
        <div className="flex min-h-[60vh] items-center justify-center text-slate-500">
          {error ? error : <><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Đang tải lớp...</>}
        </div>
      </main>
    );
  }

  const studyResources = assignments.filter(
    (assignment) => assignment.kind === "study_resource",
  );
  const officialAssignments = assignments.filter(
    (assignment) => assignment.kind !== "study_resource",
  );
  const studentStudyResources = filteredStudentAssignments.filter(
    (assignment) => assignment.kind === "study_resource",
  );
  const studentOfficialAssignments = filteredStudentAssignments.filter(
    (assignment) => assignment.kind !== "study_resource",
  );

  if (role !== "teacher") {
    return (
      <main className="min-h-screen bg-slate-50">
        <Header />
        <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
          <button onClick={() => router.push("/classrooms")} className="ui-btn-secondary px-3 py-2 text-xs">
            <ArrowLeft className="h-4 w-4" /> Các lớp của tôi
          </button>
          <div className="mt-6 rounded-2xl border border-[#1f4e79] bg-[#1f4e79] p-7 text-white shadow-lg">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-300">Lớp học</p>
            <h1 className="mt-1 text-3xl font-extrabold">{classroom.name}</h1>
            <p className="mt-2 text-sm text-slate-200">{classroom.description}</p>
            <div className="mt-5 inline-flex rounded-lg border border-white/25 bg-white/10 px-3 py-2 text-sm font-bold">
              <GraduationCap className="mr-2 h-4 w-4" />
              {classroom.member?.full_name} · {classroom.member?.member_ref}
            </div>
          </div>
          {error && <div className="mt-5 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
          <section className="mt-8">
            <h2 className="text-xl font-extrabold text-[#1f4e79]">Nội dung lớp học</h2>
            {assignments.length > 0 && (
              <div className="ui-card mt-4 p-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setStudentTag("all")}
                      className={`rounded-lg border px-3 py-2 text-sm font-bold transition ${
                        studentTag === "all"
                          ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                          : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
                      }`}
                    >
                      Tất cả
                    </button>
                    {studentTags.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => setStudentTag(tag)}
                        className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-bold transition ${
                          studentTag === tag
                            ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                            : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
                        }`}
                      >
                        <Tag className="h-3.5 w-3.5" /> {tag}
                      </button>
                    ))}
                  </div>
                  <label className="relative block w-full lg:w-80">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      value={studentSearch}
                      onChange={(event) => setStudentSearch(event.target.value)}
                      placeholder="Tìm theo tên đề hoặc Tag..."
                      className="w-full rounded-lg border border-slate-300 py-2.5 pl-10 pr-4 text-sm outline-none focus:border-[#1f4e79]"
                    />
                  </label>
                </div>
              </div>
            )}
            {[{ title: "Bộ đề luyện tập", items: studentStudyResources }, { title: "Bài thi", items: studentOfficialAssignments }].map((section) => (
              section.items.length > 0 && <div key={section.title} className="mt-6">
                <h3 className="text-sm font-extrabold uppercase tracking-wider text-slate-500">{section.title}</h3>
                <div className="mt-3 grid gap-4 md:grid-cols-2">
              {section.items.map((assignment) => (
                <article key={assignment.id} className="ui-card p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      {assignment.kind === "official_exam" ? (
                        <span className="inline-flex rounded-full border border-rose-300 bg-rose-600 px-3 py-1 text-[11px] font-extrabold uppercase tracking-wide text-white shadow-sm">
                          Bài thi
                        </span>
                      ) : (
                        <span className="text-xs font-extrabold uppercase tracking-wider text-sky-700">Bộ đề</span>
                      )}
                      <h3 className="mt-3 text-lg font-extrabold text-[#1f4e79]">{assignment.title}</h3>
                      {(assignment.tag || assignment.exam.category) && (
                        <span className="mt-2 inline-flex items-center gap-1 rounded-full border border-[#1f4e79]/20 bg-[#1f4e79]/5 px-2.5 py-1 text-xs font-bold text-[#1f4e79]">
                          <Tag className="h-3 w-3" />
                          {assignment.tag || assignment.exam.category}
                        </span>
                      )}
                    </div>
                    <BookOpenCheck className="h-6 w-6 text-slate-400" />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-slate-600">
                    <span><Clock3 className="mr-1 inline h-3.5 w-3.5" />{assignment.kind === "study_resource" ? "Tự chọn thời gian" : `${Math.round(assignment.duration_seconds / 60)} phút`}</span>
                    <span>{assignment.exam.question_count} câu</span>
                    <span>Đã làm: {assignment.attempt_count || 0}</span>
                    <span>
                      Còn lại: {assignment.attempts_remaining == null ? "∞" : assignment.attempts_remaining}
                    </span>
                  </div>
                  {assignment.latest_attempt?.scores && assignment.score_released && (
                    <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-900">
                      TOEIC {assignment.latest_attempt.scores.toeic} · LC {assignment.latest_attempt.scores.listening} · RC {assignment.latest_attempt.scores.reading}
                    </div>
                  )}
                  <button
                    onClick={() =>
                      assignment.kind === "study_resource"
                        ? setLaunchResource(assignment)
                        : void startAssignment(assignment)
                    }
                    disabled={
                      loading ||
                      assignment.availability !== "open" ||
                      assignment.attempts_remaining === 0
                    }
                    className="ui-btn-primary mt-5 w-full py-2.5 disabled:opacity-50"
                  >
                    <Play className="h-4 w-4" />
                    {assignment.availability === "upcoming"
                      ? "Chưa mở"
                      : assignment.availability === "closed"
                        ? "Đã đóng"
                        : assignment.kind === "study_resource"
                          ? "Chọn cách làm"
                          : "Bắt đầu bài thi"}
                  </button>
                  {assignment.availability === "open" &&
                    assignment.attempts_remaining !== 0 && (
                      <button
                        type="button"
                        onClick={() => void downloadOfflinePack(assignment)}
                        disabled={loading || offlineLoading !== null}
                        className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 hover:border-[#1f4e79] hover:text-[#1f4e79] disabled:opacity-50"
                      >
                        {offlineLoading === assignment.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Download className="h-4 w-4" />
                        )}
                        {offlineLoading === assignment.id
                          ? `Đang tải ${offlineProgress}%`
                          : "Tải để làm offline"}
                      </button>
                    )}
                </article>
              ))}
                </div>
              </div>
            ))}
            {!assignments.length && (
              <div className="ui-card mt-4 p-10 text-center text-sm text-slate-500">
                Giáo viên chưa chia sẻ bộ đề hoặc tạo bài thi nào.
              </div>
            )}
            {assignments.length > 0 && filteredStudentAssignments.length === 0 && (
              <div className="ui-card mt-5 p-10 text-center text-sm text-slate-500">
                Không tìm thấy bộ đề hoặc bài thi phù hợp với Tag và từ khóa đã chọn.
              </div>
            )}
          </section>
        </div>
        {launchResource && (
          <ExamLaunchDialog
            title={launchResource.title}
            questionCount={launchResource.exam.question_count}
            durationMinutes={launchResource.exam.duration_minutes}
            availablePartNumbers={launchResource.available_part_numbers}
            loading={loading}
            onClose={() => setLaunchResource(null)}
            onStart={(configuration) => startAssignment(launchResource, configuration)}
          />
        )}
      </main>
    );
  }

  const tabs: Array<{ id: Tab; label: string; icon: typeof BookOpenCheck }> = [
    { id: "assignments", label: "Bài thi", icon: BookOpenCheck },
    { id: "members", label: "Học viên", icon: Users },
    { id: "monitoring", label: "Giám sát học viên", icon: Activity },
    { id: "settings", label: "Cài đặt", icon: Settings },
  ];
  const monitoringCards: Array<[string, number, LucideIcon]> = [
    ["Học viên", monitorSummary.members || 0, Users],
    ["Đang làm", monitorSummary.in_progress || 0, Activity],
    ["Mất kết nối", monitorSummary.disconnected || 0, ShieldAlert],
    ["Tổng lượt làm", monitorSummary.total_attempts || 0, BookOpenCheck],
    ["Đã hoàn thành", monitorSummary.completed_attempts || 0, CheckCircle2],
    ["Điểm TB", monitorSummary.average_score || 0, BarChart3],
  ];

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
        <button onClick={() => router.push("/classrooms")} className="ui-btn-secondary px-3 py-2 text-xs">
          <ArrowLeft className="h-4 w-4" /> Danh sách lớp
        </button>
        <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">Quản lý lớp học</p>
            <h1 className="mt-1 text-3xl font-extrabold text-[#1f4e79]">{classroom.name}</h1>
            <p className="mt-1 text-sm text-slate-500">{classroom.description || "Chưa có mô tả"}</p>
          </div>
          <div className="flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-3 shadow-sm">
            <span className="text-xs font-bold text-slate-500">MÃ LỚP</span>
            <strong className="font-mono text-xl tracking-[0.18em] text-[#1f4e79]">{classroom.join_code}</strong>
            <button onClick={() => navigator.clipboard.writeText(classroom.join_code || "")} className="rounded-md p-2 hover:bg-slate-100">
              <Copy className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="mt-7 flex flex-wrap gap-2 border-b border-slate-300 pb-3">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-bold ${
                tab === id ? "border-[#1f4e79] bg-[#1f4e79] text-white" : "border-slate-300 bg-white text-slate-600"
              }`}
            >
              <Icon className="h-4 w-4" /> {label}
            </button>
          ))}
        </div>
        {error && <div className="mt-5 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-800">{error}</div>}

        {tab === "assignments" && (
          <div className="mt-6 grid gap-6 xl:grid-cols-[400px_1fr]">
            <form onSubmit={createAssignment} className="ui-card h-fit p-6">
              <h2 className="flex items-center gap-2 text-lg font-extrabold text-[#1f4e79]">
                <FilePlus2 className="h-5 w-5" /> Tổ chức thi
              </h2>
              <label className="mt-5 block text-sm font-bold text-slate-700">
                Đề thi
                <select value={selectedExamId} onChange={(event) => setSelectedExamId(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2.5" required>
                  <option value="">Chọn đề trong kho</option>
                  {exams.map((exam) => <option key={exam.id} value={exam.id}>{exam.title} ({exam.question_count} câu)</option>)}
                </select>
              </label>
              <label className="mt-4 block text-sm font-bold text-slate-700">
                Tên bài hiển thị cho học viên
                <input
                  value={assignmentTitle}
                  onChange={(event) => setAssignmentTitle(event.target.value)}
                  maxLength={255}
                  placeholder="Ví dụ: Thi thử TOEIC tuần 3"
                  className="mt-1 w-full rounded-lg border px-3 py-2.5"
                  required
                />
              </label>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <label className="text-sm font-bold text-slate-700">Chế độ<input value="Thi chính thức" readOnly className="mt-1 w-full rounded-lg border bg-slate-100 px-3 py-2.5 text-slate-600" /></label>
                <label className="text-sm font-bold text-slate-700">Thời lượng (phút)
                  <input type="number" min={1} max={300} value={durationMinutes} onChange={(event) => setDurationMinutes(Number(event.target.value))} className="mt-1 w-full rounded-lg border px-3 py-2.5" />
                </label>
              </div>
              <label className="mt-4 block text-sm font-bold text-slate-700">
                Số lượt làm bài
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={attemptLimit}
                  onChange={(event) => setAttemptLimit(event.target.value)}
                  placeholder="1"
                  className="mt-1 w-full rounded-lg border px-3 py-2.5 disabled:bg-slate-100 disabled:text-slate-500"
                />
              </label>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <label className="text-sm font-bold text-slate-700">Mở lúc
                  <input type="datetime-local" value={opensAt} onChange={(event) => setOpensAt(event.target.value)} className="mt-1 w-full rounded-lg border px-2 py-2.5 text-xs" />
                </label>
                <label className="text-sm font-bold text-slate-700">Đóng lúc
                  <input type="datetime-local" value={closesAt} onChange={(event) => setClosesAt(event.target.value)} className="mt-1 w-full rounded-lg border px-2 py-2.5 text-xs" />
                </label>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <label className="text-sm font-bold text-slate-700">Công bố điểm
                  <select value={scoreRelease} onChange={(event) => setScoreRelease(event.target.value)} className="mt-1 w-full rounded-lg border px-2 py-2.5 text-xs">
                    <option value="immediate">Ngay khi nộp</option><option value="after_close">Sau khi đóng</option><option value="manual">Thủ công</option>
                  </select>
                </label>
                <label className="text-sm font-bold text-slate-700">Công bố đáp án
                  <select value={answerRelease} onChange={(event) => setAnswerRelease(event.target.value)} className="mt-1 w-full rounded-lg border px-2 py-2.5 text-xs">
                    <option value="immediate">Ngay khi nộp</option><option value="after_close">Sau khi đóng</option><option value="manual">Thủ công</option><option value="never">Không công bố</option>
                  </select>
                </label>
              </div>
              <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs font-semibold leading-5 text-amber-800">
                Lưu ý: học viên vẫn có thể xem Giải chi tiết ngay sau khi nộp bài. Nếu đề có lời giải, nội dung này có thể tiết lộ đáp án dù “Công bố đáp án” đang là Thủ công hoặc Không công bố.
              </p>
              <label className="mt-4 flex items-center gap-2 text-sm font-bold text-slate-700">
                <input type="checkbox" checked={antiCheat} onChange={(event) => setAntiCheat(event.target.checked)} />
                Bật giám sát chống gian lận
              </label>
              <label className="mt-2 flex items-center gap-2 text-sm font-bold text-slate-700">
                <input
                  type="checkbox"
                  checked={listeningNavigationLocked}
                  onChange={(event) => setListeningNavigationLocked(event.target.checked)}
                />
                Khóa chuyển/quay lại câu ở phần Listening
              </label>
              <button disabled={loading || !selectedExamId} className="ui-btn-primary mt-5 w-full py-2.5">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" />} Tạo kỳ thi chính thức
              </button>
              <button
                type="button"
                onClick={() => {
                  sessionStorage.setItem(
                    "classroom-exam-return",
                    `/classrooms/detail?id=${encodeURIComponent(classroomId)}`,
                  );
                  router.push("/");
                }}
                className="ui-btn-secondary mt-2 w-full py-2.5"
              >
                Tạo đề mới
              </button>
            </form>
            <div className="space-y-3">
              <h2 className="text-sm font-extrabold uppercase tracking-wider text-slate-500">Bài thi chính thức</h2>
              {officialAssignments.map((assignment) => (
                <article key={assignment.id} className="ui-card p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <span className="inline-flex rounded-full border border-rose-300 bg-rose-600 px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider text-white">
                        Bài thi
                      </span>
                      <span className="ml-2 text-[10px] font-bold uppercase text-slate-400">
                        {assignment.status === "closed" ? "Đã đóng" : "Đang mở"}
                      </span>
                      {assignment.listening_navigation_locked && (
                        <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-800">
                          Khóa chuyển câu Listening
                        </span>
                      )}
                      <h3 className="mt-1 text-lg font-extrabold text-[#1f4e79]">{assignment.title}</h3>
                      <p className="mt-1 text-xs text-slate-500">{assignment.exam.question_count} câu · {Math.round(assignment.duration_seconds / 60)} phút · {assignment.attempt_limit ?? "∞"} lượt</p>
                    </div>
                    <ShieldAlert className={`h-5 w-5 ${assignment.anti_cheat_enabled ? "text-amber-600" : "text-slate-300"}`} />
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
                    {assignment.status !== "closed" && (
                      <button type="button" onClick={() => closeAssignment(assignment)} className="ui-btn-secondary px-3 py-2 text-xs">
                        <Archive className="h-4 w-4" /> Đóng bài
                      </button>
                    )}
                    {(assignment.status === "closed" ||
                      (assignment.closes_at &&
                        new Date(assignment.closes_at).getTime() <= Date.now())) && (
                      <button
                        type="button"
                        onClick={() => void reopenAssignment(assignment)}
                        className="inline-flex items-center gap-2 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800"
                      >
                        <RefreshCw className="h-4 w-4" /> Mở lại & thêm 1 lượt
                      </button>
                    )}
                    <button type="button" onClick={() => void renameAssignment(assignment)} className="ui-btn-secondary px-3 py-2 text-xs">
                      Đổi tên
                    </button>
                    <button type="button" onClick={() => void changeAttemptLimit(assignment)} className="ui-btn-secondary px-3 py-2 text-xs">
                      Đổi số lượt
                    </button>
                    <button type="button" onClick={() => releaseAssignment(assignment, false)} className="ui-btn-secondary px-3 py-2 text-xs">
                      <BarChart3 className="h-4 w-4" /> Công bố điểm
                    </button>
                    <button type="button" onClick={() => releaseAssignment(assignment, true)} className="ui-btn-secondary px-3 py-2 text-xs">
                      <CheckCircle2 className="h-4 w-4" /> Công bố điểm & đáp án
                    </button>
                  </div>
                </article>
              ))}
              {!officialAssignments.length && <div className="ui-card p-8 text-center text-sm text-slate-500">Chưa tạo bài thi chính thức nào.</div>}
              {studyResources.length > 0 && (
                <div className="pt-4">
                  <h2 className="mb-3 text-sm font-extrabold uppercase tracking-wider text-slate-500">Bộ đề đã Public</h2>
                  <div className="space-y-3">
                    {studyResources.map((assignment) => (
                      <article key={assignment.id} className="ui-card p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <span className="text-[10px] font-extrabold uppercase tracking-wider text-sky-700">Bộ đề</span>
                            <h3 className="mt-1 font-extrabold text-[#1f4e79]">{assignment.title}</h3>
                            <p className="mt-1 text-xs text-slate-500">{assignment.exam.question_count} câu · Học viên tự cấu hình cách làm</p>
                          </div>
                          {assignment.status !== "closed" && (
                            <button type="button" onClick={() => void closeAssignment(assignment)} className="ui-btn-secondary px-3 py-2 text-xs">
                              <Archive className="h-4 w-4" /> Gỡ Public
                            </button>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "members" && (
          <div className="ui-card mt-6 overflow-x-auto p-5">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="text-xs uppercase text-slate-500"><tr><th className="p-3">Học viên</th><th className="p-3">Mã</th><th className="p-3">Tham gia</th><th className="p-3">Hoạt động cuối</th><th className="p-3">Trạng thái</th><th className="p-3"></th></tr></thead>
              <tbody>{members.map((member) => (
                <tr key={member.id} className="border-t">
                  <td className="p-3 font-bold">{member.full_name}</td><td className="p-3 font-mono">{member.member_ref}</td>
                  <td className="p-3 text-slate-500">{new Date(member.joined_at).toLocaleString("vi-VN")}</td>
                  <td className="p-3 text-slate-500">{new Date(member.last_seen_at).toLocaleString("vi-VN")}</td>
                  <td className="p-3">{member.status === "active" ? <span className="text-emerald-700">Đang học</span> : <span className="text-red-700">Đã loại</span>}</td>
                  <td className="p-3 text-right"><button onClick={() => changeMember(member)} className="ui-btn-secondary px-3 py-2 text-xs">{member.status === "active" ? <UserMinus className="h-4 w-4" /> : <UserRoundCheck className="h-4 w-4" />}{member.status === "active" ? "Loại" : "Khôi phục"}</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        {tab === "monitoring" && (
          <div className="mt-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-extrabold text-[#1f4e79]">Theo dõi gần thời gian thực</h2>
                <p className="text-xs text-slate-500">Dữ liệu live tự làm mới mỗi 10 giây.</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowExportModal(true)}
                  className="inline-flex items-center gap-2 rounded-lg border border-emerald-600 bg-emerald-600 px-3.5 py-2 text-sm font-bold text-white shadow-sm transition hover:bg-emerald-700"
                >
                  <FileSpreadsheet className="h-4 w-4" /> Xuất điểm học viên
                </button>
                <select value={monitorAssignmentId} onChange={(event) => setMonitorAssignmentId(event.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-bold text-slate-700">
                  <option value="">Tất cả bài thi</option>
                  {officialAssignments.map((assignment) => <option key={assignment.id} value={assignment.id}>{assignment.title}</option>)}
                </select>
              </div>
            </div>
            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              {monitoringCards.map(([label, value, Icon]) => (
                <div key={String(label)} className="ui-card p-4"><Icon className="h-5 w-5 text-[#1f4e79]" /><p className="mt-2 text-xs font-bold text-slate-500">{String(label)}</p><p className="text-2xl font-extrabold text-[#1f4e79]">{String(value)}</p></div>
              ))}
            </section>
            <div className="ui-card mt-4 overflow-x-auto p-4">
              <table className="w-full min-w-[900px] text-left text-sm">
                <thead className="text-xs uppercase text-slate-500"><tr><th className="p-3">Học viên</th><th className="p-3">Trạng thái</th><th className="p-3">Tiến độ</th><th className="p-3">Còn lại</th><th className="p-3">Điểm</th><th className="p-3">Cảnh báo</th><th className="p-3">Heartbeat</th></tr></thead>
                <tbody>{monitoring.map((item) => (
                  <tr key={item.member_id} onClick={() => void openMemberDetail(item, item.attempt_id)} className="cursor-pointer border-t hover:bg-slate-50">
                    <td className="p-3"><strong>{item.full_name}</strong><span className="ml-2 font-mono text-xs text-slate-400">{item.member_ref}</span></td>
                    <td className="p-3 font-bold">{item.state === "in_progress" ? <span className="text-blue-700">Đang làm</span> : item.state === "submitted" ? <span className="text-emerald-700">Đã nộp</span> : item.state === "disconnected" ? <span className="text-red-700">Mất kết nối</span> : <span className="text-slate-500">Chưa làm</span>}</td>
                    <td className="p-3">{item.answered_count} câu</td><td className="p-3">{item.time_left_seconds == null ? "—" : `${Math.ceil(item.time_left_seconds / 60)} phút`}</td>
                    <td className="p-3 font-extrabold">{item.score_toeic ?? "—"}</td><td className="p-3"><span className={item.violation_count ? "font-bold text-amber-700" : "text-slate-400"}>{item.violation_count}</span></td>
                    <td className="p-3 text-xs text-slate-500">{item.last_heartbeat_at ? new Date(item.last_heartbeat_at).toLocaleTimeString("vi-VN") : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="ui-card mt-5 overflow-x-auto p-4">
              <div className="mb-3">
                <h3 className="font-extrabold text-[#1f4e79]">Toàn bộ lịch sử làm bài</h3>
                <p className="text-xs text-slate-500">
                  Mỗi lần làm là một dòng riêng và được lưu vĩnh viễn trong lớp.
                </p>
              </div>
              <table className="w-full min-w-[1100px] text-left text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr>
                    <th className="p-3">Học viên</th>
                    <th className="p-3">Bài thi</th>
                    <th className="p-3">Lần</th>
                    <th className="p-3">Chế độ</th>
                    <th className="p-3">Trạng thái</th>
                    <th className="p-3">TOEIC</th>
                    <th className="p-3">LC / RC</th>
                    <th className="p-3">Đúng</th>
                    <th className="p-3">Cảnh báo</th>
                    <th className="p-3">Bắt đầu</th>
                    <th className="p-3">Nộp bài</th>
                  </tr>
                </thead>
                <tbody>
                  {monitorHistory.map((item) => (
                    <tr
                      key={item.attempt_id}
                      onClick={() => void openMemberDetail(item, item.attempt_id)}
                      className="cursor-pointer border-t hover:bg-slate-50"
                    >
                      <td className="p-3">
                        <strong>{item.full_name}</strong>
                        <span className="ml-2 font-mono text-xs text-slate-400">{item.member_ref}</span>
                      </td>
                      <td className="p-3 font-bold">{item.assignment_title}</td>
                      <td className="p-3">#{item.attempt_number}</td>
                      <td className="p-3">
                        <span className={item.assignment_mode === "exam" ? "font-bold text-rose-700" : "font-bold text-sky-700"}>
                          {item.assignment_mode === "exam" ? "Thi" : "Luyện tập"}
                        </span>
                      </td>
                      <td className="p-3">{item.status === "submitted" ? "Đã nộp" : item.state === "in_progress" ? "Đang làm" : "Mất kết nối"}</td>
                      <td className="p-3 font-extrabold">{item.score_toeic ?? "—"}</td>
                      <td className="p-3">{item.listening_score ?? "—"} / {item.reading_score ?? "—"}</td>
                      <td className="p-3">{item.correct_count == null ? "—" : `${item.correct_count}/${item.graded_count}`}</td>
                      <td className="p-3 text-amber-700">{item.violation_count}</td>
                      <td className="p-3 text-xs text-slate-500">{new Date(item.started_at).toLocaleString("vi-VN")}</td>
                      <td className="p-3 text-xs text-slate-500">{item.submitted_at ? new Date(item.submitted_at).toLocaleString("vi-VN") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!monitorHistory.length && (
                <p className="p-6 text-center text-sm text-slate-500">Chưa có lượt làm bài nào.</p>
              )}
            </div>
          </div>
        )}

        {tab === "settings" && (
          <form onSubmit={saveClassroomSettings} className="ui-card mt-6 max-w-2xl p-6">
            <h2 className="text-lg font-extrabold text-[#1f4e79]">Cài đặt lớp</h2>
            <label className="mt-5 block text-sm font-bold text-slate-700">
              Tên lớp
              <input value={classroom.name} onChange={(event) => setClassroom({ ...classroom, name: event.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5" required />
            </label>
            <label className="mt-4 block text-sm font-bold text-slate-700">
              Mô tả
              <textarea value={classroom.description} onChange={(event) => setClassroom({ ...classroom, description: event.target.value })} rows={4} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5" />
            </label>
            <button className="ui-btn-primary mt-4 px-4 py-2.5">Lưu thông tin lớp</button>
            <div className="mt-5 rounded-xl border border-slate-300 bg-slate-50 p-4">
              <p className="text-xs font-bold uppercase text-slate-500">Mã tham gia hiện tại</p>
              <div className="mt-2 flex items-center justify-between gap-3"><strong className="font-mono text-2xl tracking-[0.2em] text-[#1f4e79]">{classroom.join_code}</strong><button onClick={regenerateCode} className="ui-btn-secondary px-3 py-2 text-xs"><RefreshCw className="h-4 w-4" />Tạo mã mới</button></div>
              <p className="mt-2 text-xs text-slate-500">Mã cũ hết hiệu lực ngay; học viên đã tham gia không bị ảnh hưởng.</p>
            </div>
            <button type="button" onClick={toggleArchive} className="mt-5 inline-flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-bold text-amber-900">
              <Archive className="h-4 w-4" /> {classroom.status === "active" ? "Lưu trữ lớp học" : "Mở lại lớp học"}
            </button>
          </form>
        )}
      </div>
      {detailMember && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4" onClick={() => setDetailMember(null)}>
          <section className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl border bg-white p-6 shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div><p className="text-xs font-bold uppercase tracking-wider text-slate-500">Chi tiết học viên</p><h2 className="mt-1 text-2xl font-extrabold text-[#1f4e79]">{detailMember.full_name}</h2><p className="text-xs font-mono text-slate-500">{detailMember.member_ref}</p></div>
              <button onClick={() => setDetailMember(null)} className="ui-btn-secondary px-3 py-2 text-xs">Đóng</button>
            </div>
            <h3 className="mt-6 font-extrabold text-[#1f4e79]">Toàn bộ lịch sử qua các bài test</h3>
            <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="text-xs uppercase text-slate-500"><tr><th className="p-2">Bài thi</th><th className="p-2">Lần</th><th className="p-2">Trạng thái</th><th className="p-2">TOEIC</th><th className="p-2">LC</th><th className="p-2">RC</th><th className="p-2">Đúng</th><th className="p-2">Cảnh báo</th><th className="p-2">Thời gian</th></tr></thead><tbody>{memberResults.map((result) => <tr key={result.attempt_id} className="border-t"><td className="p-2 font-bold">{result.title}<span className={`ml-2 text-[10px] uppercase ${result.mode === "exam" ? "text-rose-700" : "text-sky-700"}`}>{result.mode === "exam" ? "Thi" : "Luyện tập"}</span></td><td className="p-2">#{result.attempt_number}</td><td className="p-2">{result.status === "submitted" ? "Đã nộp" : "Đang làm"}</td><td className="p-2 font-extrabold">{result.score_toeic ?? "—"}</td><td className="p-2">{result.listening_score ?? "—"}</td><td className="p-2">{result.reading_score ?? "—"}</td><td className="p-2">{result.correct_count == null ? "—" : `${result.correct_count}/${result.graded_count}`}</td><td className="p-2 text-amber-700">{result.violation_count}</td><td className="p-2 text-xs text-slate-500">{new Date(result.submitted_at || result.started_at).toLocaleString("vi-VN")}</td></tr>)}</tbody></table>{!memberResults.length && <p className="p-5 text-center text-sm text-slate-500">Chưa có lượt làm bài.</p>}</div>
            <h3 className="mt-6 font-extrabold text-[#1f4e79]">Sự kiện giám sát của lượt đang chọn</h3>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">{monitorEvents.map((event) => <div key={event.id} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm"><strong className="text-amber-900">{event.event_type}</strong><span className="float-right text-xs text-amber-700">{new Date(event.occurred_at || event.received_at).toLocaleTimeString("vi-VN")}</span></div>)}{!monitorEvents.length && <p className="text-sm text-slate-500">Không có sự kiện cảnh báo.</p>}</div>
          </section>
        </div>
      )}
      {showExportModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
          onClick={() => !exporting && setShowExportModal(false)}
        >
          <div
            className="w-full max-w-md rounded-2xl border bg-white p-6 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-extrabold text-[#1f4e79] flex items-center gap-2">
                <FileSpreadsheet className="h-5 w-5 text-emerald-600" />
                Xuất thông tin điểm thi (Excel)
              </h3>
              <button
                type="button"
                onClick={() => !exporting && setShowExportModal(false)}
                className="text-slate-400 hover:text-slate-600 font-bold text-lg"
              >
                ✕
              </button>
            </div>

            <div className="mt-4 space-y-3">
              <p className="text-sm font-semibold text-slate-700">
                Chọn phạm vi dữ liệu điểm thi muốn xuất:
              </p>

              <label className="flex items-center gap-3 rounded-xl border p-3 cursor-pointer hover:bg-slate-50 font-bold text-sm text-slate-800">
                <input
                  type="radio"
                  name="exportTarget"
                  value="all"
                  checked={exportExamTarget === "all"}
                  onChange={() => setExportExamTarget("all")}
                  className="h-4 w-4 text-[#1f4e79]"
                />
                <div>
                  <div>Xuất tất cả bài thi</div>
                  <div className="text-xs font-normal text-slate-500">
                    Tạo 1 file Excel duy nhất chứa nhiều Sheet (mỗi bài thi là 1 Sheet)
                  </div>
                </div>
              </label>

              <label className="flex items-center gap-3 rounded-xl border p-3 cursor-pointer hover:bg-slate-50 font-bold text-sm text-slate-800">
                <input
                  type="radio"
                  name="exportTarget"
                  value="specific"
                  checked={exportExamTarget !== "all"}
                  onChange={() => setExportExamTarget(officialAssignments[0]?.id || "all")}
                  className="h-4 w-4 text-[#1f4e79]"
                />
                <div>
                  <div>Xuất riêng 1 bài thi</div>
                  <div className="text-xs font-normal text-slate-500">
                    Chỉ xuất dữ liệu điểm thi của đề đã chọn
                  </div>
                </div>
              </label>

              {exportExamTarget !== "all" && (
                <div className="pl-7 pt-1">
                  <select
                    value={exportExamTarget}
                    onChange={(e) => setExportExamTarget(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-sm font-bold text-slate-800"
                  >
                    {officialAssignments.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.title} ({a.exam.question_count} câu)
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            <div className="mt-6 flex justify-end gap-3 border-t pt-4">
              <button
                type="button"
                onClick={() => setShowExportModal(false)}
                disabled={exporting}
                className="ui-btn-secondary px-4 py-2 text-xs font-bold"
              >
                Hủy
              </button>
              <button
                type="button"
                onClick={() => void handleExportScores()}
                disabled={exporting}
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {exporting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Đang xuất...
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" /> Tải file Excel
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
