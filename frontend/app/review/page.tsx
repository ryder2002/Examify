"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Crop,
  FileCheck2,
  Files,
  Loader2,
  Plus,
  Save,
  Trash2,
} from "lucide-react";

import AnswerKeyImport from "@/components/AnswerKeyImport";
import {
  AuthenticatedAudio,
  AuthenticatedImage,
} from "@/components/AuthenticatedMedia";
import CropEditor, { type CropSelection } from "@/components/CropEditor";
import ExamifyLoader from "@/components/ExamifyLoader";
import SolutionEditor from "@/components/SolutionEditor";
import {
  apiFetch,
  consumeDesktopExamQuota,
  getAccessToken,
  isDesktop,
  resolveIdentity,
} from "@/lib/api";
import type {
  AssetRef,
  ExamDraft,
  FinalExam,
  Question,
  Stimulus,
} from "@/lib/utils";

function partFor(examType: ExamDraft["exam_type"], number: number) {
  if (examType === "listening") {
    if (number <= 6) return "Part 1 - Phần 1";
    if (number <= 31) return "Part 2 - Phần 2";
    if (number <= 70) return "Part 3 - Phần 3";
    return "Part 4 - Phần 4";
  }
  if (number <= 130) return "Part 5 - Phần 5";
  if (number <= 146) return "Part 6 - Phần 6";
  return "Part 7 - Phần 7";
}

function questionRangeForDraft(draft: Pick<ExamDraft, "exam_type" | "questions" | "metadata">): [number, number] {
  const lower = draft.exam_type === "listening" ? 1 : 101;
  const upper = draft.exam_type === "listening" ? 100 : 200;
  const raw = draft.metadata?.detected_question_range;
  if (Array.isArray(raw) && raw.length === 2) {
    const start = Number(raw[0]);
    const end = Number(raw[1]);
    if (Number.isInteger(start) && Number.isInteger(end) && lower <= start && start <= end && end <= upper) {
      return [start, end];
    }
  }
  const numbers = draft.questions
    .map((question) => question.number)
    .filter((number) => lower <= number && number <= upper)
    .sort((a, b) => a - b);
  if (numbers.length) return [numbers[0], numbers[numbers.length - 1]];
  // OCR may be unable to identify a marker on a custom/partial upload. Do
  // not turn that uncertainty into 100 fabricated review slots.
  return [lower, lower - 1];
}

function expectedQuestionNumbers(draft: Pick<ExamDraft, "exam_type" | "questions" | "metadata">) {
  const [start, end] = questionRangeForDraft(draft);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
}

function optionLettersFor(examType: ExamDraft["exam_type"], number: number) {
  return examType === "listening" && number >= 7 && number <= 31
    ? ["A", "B", "C"]
    : ["A", "B", "C", "D"];
}

function requiresQuestionText(examType: ExamDraft["exam_type"], number: number) {
  if (examType === "listening" && number <= 31) return false;
  return !(examType === "reading" && number >= 131 && number <= 146);
}

function requiresOptions(examType: ExamDraft["exam_type"], number: number) {
  return !(examType === "listening" && number <= 31);
}

function hasIncompleteQuestionContent(
  question: Question,
  examType: ExamDraft["exam_type"],
) {
  const missingText =
    requiresQuestionText(examType, question.number) && !question.text.trim();
  const missingOptions =
    requiresOptions(examType, question.number) &&
    question.option_letters.some((letter) => !question.options[letter]?.trim());
  return missingText || missingOptions;
}

function needsManualEntry(
  question: Question,
  examType: ExamDraft["exam_type"],
) {
  return (
    hasIncompleteQuestionContent(question, examType) ||
    question.issues.some((issue) =>
      ["question_missing", "options_missing"].includes(issue),
    )
  );
}

function missingQuestionPlaceholder(
  examType: ExamDraft["exam_type"],
  number: number,
): Question {
  const optionLetters = optionLettersFor(examType, number);
  return {
    number,
    part: partFor(examType, number),
    text: "",
    options: Object.fromEntries(optionLetters.map((letter) => [letter, ""])),
    option_letters: optionLetters,
    correct: null,
    group_id: `q-${number}`,
    stimulus_id: null,
    confidence: 0,
    issues: ["question_missing", "options_missing"],
  };
}

function addMissingQuestionSlots(draft: ExamDraft) {
  const existing = new Set(draft.questions.map((question) => question.number));
  const missing = expectedQuestionNumbers(draft).filter(
    (number) => !existing.has(number),
  );
  if (!missing.length) return draft;
  return {
    ...draft,
    questions: [
      ...draft.questions,
      ...missing.map((number) => missingQuestionPlaceholder(draft.exam_type, number)),
    ].sort((a, b) => a.number - b.number),
  };
}

function cleanQuestionIssues(question: Question, examType: ExamDraft["exam_type"]) {
  if (!hasIncompleteQuestionContent(question, examType)) {
    return question.issues.filter(
      (issue) =>
        !["question_missing", "options_missing", "low_confidence"].includes(issue),
    );
  }
  return question.issues;
}

export default function ReviewPage() {
  const router = useRouter();
  const [draft, setDraft] = useState<ExamDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reviewTab, setReviewTab] = useState<"content" | "solutions">("content");
  const savedSolutionsRef = useRef<string>("[]");
  const [error, setError] = useState<string | null>(null);
  const [answerText, setAnswerText] = useState("");
  const [cropTarget, setCropTarget] = useState<{
    stimulusId: string;
    asset: AssetRef;
    assetIndex: number;
    assetCount: number;
  } | null>(null);
  const [manualCropOpen, setManualCropOpen] = useState(false);
  const [hasPendingListening, setHasPendingListening] = useState(false);
  const [editingExam, setEditingExam] = useState<{
    id: string;
    title: string;
    category: string;
    client_exam_id?: string | null;
    exam_id?: string | null;
    full_test?: boolean;
    listening_job_id?: string;
    reading_job_id?: string;
    edit_session_id?: string;
    base_revision?: number;
  } | null>(null);

  useEffect(() => {
    const editingRaw = sessionStorage.getItem("editing-exam");
    if (editingRaw) {
      try {
        setEditingExam(JSON.parse(editingRaw));
      } catch {
        sessionStorage.removeItem("editing-exam");
      }
    }
    setHasPendingListening(
      Boolean(sessionStorage.getItem("pending-listening-exam")),
    );
    const queryJob =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("job")
        : null;
    if (
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("tab") === "solutions"
    ) {
      setReviewTab("solutions");
    }
    const jobId = queryJob || sessionStorage.getItem("extraction-job");
    if (!jobId) {
      router.replace("/");
      return;
    }
    apiFetch(`/api/extractions/${jobId}`, { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Không tải được draft");
        if (payload.schema_version !== 2) {
          sessionStorage.removeItem("quiz-data");
          throw new Error("Dữ liệu cũ không còn tương thích");
        }
        const nextDraft = payload as ExamDraft;
        nextDraft.audios =
          nextDraft.audios || (nextDraft.audio ? [nextDraft.audio] : []);
        nextDraft.solutions = nextDraft.solutions || [];
        savedSolutionsRef.current = JSON.stringify(nextDraft.solutions);
        // Older cached drafts may predate the missing-question placeholder
        // rule. Show their exact missing numbers immediately; the next Save
        // persists the same coverage check on the backend.
        setDraft(addMissingQuestionSlots(nextDraft));
      })
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra"),
      )
      .finally(() => setLoading(false));
  }, [router]);

  useEffect(() => {
    if (!draft) return;
    const serialized = JSON.stringify(draft.solutions || []);
    if (serialized === savedSolutionsRef.current) return;
    const timer = window.setTimeout(() => {
      void apiFetch(`/api/extractions/${draft.job_id}/draft`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ solutions: draft.solutions || [] }),
      }).then((response) => {
        if (response.ok) savedSolutionsRef.current = serialized;
      });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [draft?.job_id, draft?.solutions]);

  const sortedQuestions = useMemo(() => {
    if (!draft) return [];
    return [...draft.questions].sort((a, b) => {
      const aManual = needsManualEntry(a, draft.exam_type) ? 0 : 1;
      const bManual = needsManualEntry(b, draft.exam_type) ? 0 : 1;
      const aAnswer = a.correct ? 1 : 0;
      const bAnswer = b.correct ? 1 : 0;
      return aManual - bManual || aAnswer - bAnswer || a.number - b.number;
    });
  }, [draft]);

  const partCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    draft?.questions.forEach((question) => {
      counts[question.part] = (counts[question.part] || 0) + 1;
    });
    return counts;
  }, [draft]);
  const missingAnswerNumbers = useMemo(
    () =>
      draft?.questions
        .filter((question) => !question.correct)
        .map((question) => question.number)
        .sort((a, b) => a - b) || [],
    [draft],
  );
  const missingQuestionNumbers = useMemo(() => {
    if (!draft) return [];
    const found = new Set(draft.questions.map((question) => question.number));
    return expectedQuestionNumbers(draft).filter(
      (number) => !found.has(number),
    );
  }, [draft]);
  const manualEntryNumbers = useMemo(
    () =>
      draft?.questions
        .filter((question) => needsManualEntry(question, draft.exam_type))
        .map((question) => question.number)
        .sort((a, b) => a - b) || [],
    [draft],
  );
  const sourcePageCount = useMemo(() => {
    const metadataCount = Number(draft?.metadata.page_count);
    if (Number.isInteger(metadataCount) && metadataCount >= 1) return metadataCount;
    return Math.max(
      1,
      ...(draft?.stimuli.flatMap((stimulus) => stimulus.page_numbers) || [1]),
    );
  }, [draft]);

  function replaceQuestion(number: number, update: Partial<Question>) {
    if (!draft) return;
    setDraft({
      ...draft,
      questions: draft.questions.map((question) => {
        if (question.number !== number) return question;
        const next = { ...question, ...update };
        return {
          ...next,
          issues: cleanQuestionIssues(next, draft.exam_type),
        };
      }),
    });
  }

  function addDetectedMissingQuestions() {
    if (!draft || !missingQuestionNumbers.length) return;
    const nextDraft = addMissingQuestionSlots(draft);
    setDraft(nextDraft);
    setError(
      `Đã tạo ô nhập thủ công cho câu: ${missingQuestionNumbers.join(", ")}.`,
    );
  }

  function focusQuestion(number: number) {
    window.document
      .getElementById(`question-${number}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function applyAnswerKey(
    answers: Record<number, string>,
    source: string,
  ) {
    if (!draft) return;
    const invalid: string[] = [];
    let applied = 0;
    const questions = draft.questions.map((question) => {
      const letter = answers[question.number]?.toUpperCase();
      if (!letter) return question;
      if (!question.option_letters.includes(letter)) {
        invalid.push(`${question.number}${letter}`);
        return question;
      }
      applied += 1;
      return { ...question, correct: letter };
    });
    for (const [rawNumber, letter] of Object.entries(answers)) {
      const number = Number(rawNumber);
      if (!draft.questions.some((question) => question.number === number)) {
        invalid.push(`${number}${letter}`);
      }
    }
    setDraft({ ...draft, questions });
    try {
      await patchDraft(questions, draft.stimuli);
    } catch {
      //
    }
    setError(
      applied ? null : "Không tìm thấy answer key hợp lệ cho các câu hỏi.",
    );
  }

  async function patchDraft(
    questions: Question[],
    stimuli: Stimulus[],
  ): Promise<ExamDraft> {
    if (!draft) throw new Error("Draft chưa sẵn sàng");
    const response = await apiFetch(`/api/extractions/${draft.job_id}/draft`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions, stimuli, solutions: draft.solutions || [] }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Không lưu được draft");
    return payload as ExamDraft;
  }

  async function saveDraft() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await patchDraft(draft.questions, draft.stimuli);
      setDraft(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không lưu được");
    } finally {
      setSaving(false);
    }
  }

  async function saveCrop(
    stimulusId: string,
    assetId: string,
    selection: CropSelection,
  ) {
    if (!draft) return;
    const stimuli = draft.stimuli.map((stimulus) =>
      stimulus.id === stimulusId
        ? {
            ...stimulus,
            issues: stimulus.issues.filter((issue) => issue !== "crop_review"),
            assets: stimulus.assets.map((asset) =>
              asset.id === assetId
                ? { ...asset, page: selection.page, bbox: selection.bbox }
                : asset,
            ),
          }
        : stimulus,
    );
    try {
      const updated = await patchDraft(draft.questions, stimuli);
      setDraft(updated);
      setCropTarget(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không lưu được crop");
    }
  }

  async function createManualCrop(selection: CropSelection) {
    if (!draft || !selection.questionNumbers?.length) return;
    try {
      const response = await apiFetch(
        `/api/extractions/${draft.job_id}/manual-stimulus`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page: selection.page,
            bbox: selection.bbox,
            question_numbers: selection.questionNumbers,
            title: "Ảnh cắt thủ công",
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Không tạo được ảnh thủ công");
      }
      setDraft(payload as ExamDraft);
      setManualCropOpen(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Không tạo được ảnh thủ công",
      );
    }
  }

  const [showFinalizeModal, setShowFinalizeModal] = useState(false);
  const [modalTitle, setModalTitle] = useState("");
  const [modalTag, setModalTag] = useState("");
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [newTagInput, setNewTagInput] = useState("");
  const [tagError, setTagError] = useState<string | null>(null);
  const [existingTitles, setExistingTitles] = useState<string[]>([]);
  const [titleDuplicateError, setTitleDuplicateError] = useState<string | null>(null);

  async function openFinalizeModal() {
    if (!draft) return;
    const defaultTitle = editingExam?.title || (hasPendingListening
      ? "TOEIC Full Test"
      : draft.filename
        ? draft.filename.replace(/\.[^/.]+$/, "")
        : "Đề thi TOEIC");
    setModalTitle(defaultTitle);
    setModalTag(editingExam?.category || "");
    setTitleDuplicateError(null);
    setTagError(null);
    setShowFinalizeModal(true);

    try {
      const tagRes = await apiFetch(isDesktop() ? "/api/desktop/tags" : "/api/v1/tags");
      if (tagRes.ok) {
        const data = await tagRes.json();
        setAvailableTags(data.items || []);
      }
    } catch {
      //
    }

    try {
      let examRes = await apiFetch(
        isDesktop() ? "/api/desktop/exams" : "/api/v1/exam-bank?page_size=50&include_archived=true",
      );
      if (!isDesktop() && examRes.status === 403) {
        examRes = await apiFetch("/api/v1/exams?page_size=100");
      }
      if (examRes.ok) {
        const data = await examRes.json();
        const titles = (data.items || [])
          .filter((e: any) => e.id !== editingExam?.id && e.client_exam_id !== editingExam?.client_exam_id)
          .map((e: any) => (e.title || "").trim().toLowerCase());
        setExistingTitles(titles);
        if (titles.includes(defaultTitle.trim().toLowerCase())) {
          setTitleDuplicateError("Tên đề thi đã tồn tại. Vui lòng đặt tên khác.");
        }
      }
    } catch {
      //
    }
  }

  function handleTitleChange(val: string) {
    setModalTitle(val);
    if (existingTitles.includes(val.trim().toLowerCase())) {
      setTitleDuplicateError("Tên đề thi đã tồn tại. Vui lòng đặt tên khác.");
    } else {
      setTitleDuplicateError(null);
    }
  }

  async function handleCreateTag() {
    const clean = newTagInput.trim();
    if (!clean) return;
    try {
      setTagError(null);
      const res = await apiFetch(isDesktop() ? "/api/desktop/tags" : "/api/v1/tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: clean }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(payload.detail || "Không tạo được Tag");
      }
      const savedName = String(payload.name || clean).trim();
      if (!availableTags.includes(savedName)) {
        setAvailableTags([...availableTags, savedName]);
      }
      setModalTag(savedName);
      setNewTagInput("");
    } catch (reason) {
      setTagError(reason instanceof Error ? reason.message : "Không tạo được Tag");
    }
  }

  async function finalizedLibraryPath() {
    const role = await resolveIdentity(true).catch(() => null);
    return role === "teacher" || role === "admin" || role === "student"
      ? "/exam-bank"
      : "/my-exams";
  }

  async function finalize(mode: "quiz" | "continue-reading" = "quiz") {
    if (!draft) return;
    if (mode === "continue-reading") {
      setSaving(true);
      setError(null);
      try {
        const saved = await patchDraft(draft.questions, draft.stimuli);
        if (editingExam?.full_test && editingExam.reading_job_id) {
          sessionStorage.setItem("extraction-job", editingExam.reading_job_id);
          window.location.assign(
            `/review?job=${encodeURIComponent(editingExam.reading_job_id)}&edit=1&step=reading`,
          );
          return;
        }
        const answerKey = Object.fromEntries(
          saved.questions
            .filter((question) => question.correct)
            .map((question) => [String(question.number), question.correct]),
        );
        const response = await apiFetch(`/api/extractions/${draft.job_id}/finalize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            answer_key: answerKey,
            title: "Listening Component",
            category: "",
            is_full_test_component: true,
          }),
        });
        if (!response.ok) {
          const errPayload = (await response.json().catch(() => ({}))) as { detail?: string };
          throw new Error(errPayload.detail || "Không lưu được đề Listening");
        }
        const exam = (await response.json()) as FinalExam;
        sessionStorage.setItem("pending-listening-exam", JSON.stringify(exam));
        sessionStorage.removeItem("extraction-job");
        window.location.assign("/?next=reading");
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
      } finally {
        setSaving(false);
      }
      return;
    }

    openFinalizeModal();
  }

  async function executeFinalize() {
    if (!draft || titleDuplicateError || !modalTitle.trim()) return;
    setSaving(true);
    setError(null);
    setShowFinalizeModal(false);
    try {
      const saved = await patchDraft(draft.questions, draft.stimuli);
      if (editingExam?.edit_session_id) {
        const response = await apiFetch(
          `/api/v1/exam-bank/edit-sessions/${editingExam.edit_session_id}/finalize`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              title: modalTitle.trim(),
              tag: modalTag.trim(),
            }),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = payload.detail;
          throw new Error(
            typeof detail === "string"
              ? detail
              : detail?.message || "Không finalize được edit session",
          );
        }
        sessionStorage.removeItem("editing-exam");
        sessionStorage.removeItem("extraction-job");
        window.location.assign("/exam-bank");
        return;
      }
      if (
        editingExam?.full_test &&
        (editingExam.client_exam_id || editingExam.exam_id) &&
        editingExam.listening_job_id &&
        editingExam.reading_job_id
      ) {
        const editTarget = editingExam.client_exam_id
          ? `/api/desktop/exams/${editingExam.client_exam_id}/edit/finalize`
          : `/api/v1/exams/${editingExam.exam_id}/edit/finalize`;
        const response = await apiFetch(
          editTarget,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              listening_job_id: editingExam.listening_job_id,
              reading_job_id: editingExam.reading_job_id,
              title: modalTitle.trim(),
              category: modalTag.trim(),
            }),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || "Không lưu được Full Test");
        }
        sessionStorage.removeItem("editing-exam");
        sessionStorage.removeItem("extraction-job");
        window.location.assign(await finalizedLibraryPath());
        return;
      }
      const preferences = JSON.parse(
        sessionStorage.getItem("quiz-preferences") || "{}",
      ) as { count?: number | null; shuffle?: boolean };
      const answerKey = Object.fromEntries(
        saved.questions
          .filter((question) => question.correct)
          .map((question) => [String(question.number), question.correct]),
      );
      const pendingRaw = saved.exam_type === "reading"
        ? sessionStorage.getItem("pending-listening-exam")
        : null;
      const response = await apiFetch(`/api/extractions/${draft.job_id}/finalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
          title: modalTitle.trim(),
          category: modalTag.trim(),
          answer_key: answerKey,
          count: preferences.count || null,
            shuffle: Boolean(preferences.shuffle),
            client_exam_id: editingExam?.client_exam_id || null,
            is_full_test_component: Boolean(pendingRaw),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không tạo được đề");
      const exam = payload as FinalExam;
      let finalExam = exam;

      if (pendingRaw) {
        const listening = JSON.parse(pendingRaw) as FinalExam;
        let combined: FinalExam = {
          schema_version: 2,
          job_id: `${listening.job_id}+${exam.job_id}`,
          exam_type: "combined",
          requested_count: listening.requested_count + exam.requested_count,
          returned_count: listening.returned_count + exam.returned_count,
          total: listening.total + exam.total,
          questions: [...listening.questions, ...exam.questions].sort((a, b) => a.number - b.number),
          stimuli: [...listening.stimuli, ...exam.stimuli],
          audio: listening.audio || exam.audio || null,
          audios: listening.audios?.length ? listening.audios : listening.audio ? [listening.audio] : exam.audios || [],
          solutions: [...(listening.solutions || []), ...(exam.solutions || [])],
          title: modalTitle.trim(),
          category: modalTag.trim(),
        };
        if (isDesktop() && listening.client_exam_id && exam.client_exam_id) {
          const combineResponse = await apiFetch("/api/desktop/exams/combine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              listening_exam_id: listening.client_exam_id,
              reading_exam_id: exam.client_exam_id,
              title: modalTitle.trim(),
              category: modalTag.trim(),
            }),
          });
          const combinePayload = await combineResponse.json().catch(() => ({}));
          if (!combineResponse.ok) {
            throw new Error(
              combinePayload.detail || "Không thể hợp nhất đề TOEIC 200 câu",
            );
          }
          combined = combinePayload as FinalExam;
        } else if (listening.exam_id && exam.exam_id) {
          const combineResponse = await apiFetch("/api/v1/exams/combine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              listening_exam_id: listening.exam_id,
              reading_exam_id: exam.exam_id,
              title: modalTitle.trim(),
              category: modalTag.trim(),
            }),
          });
          const combinePayload = await combineResponse.json().catch(() => ({}));
          if (!combineResponse.ok) {
            throw new Error(
              combinePayload.detail || "Không thể hợp nhất đề TOEIC 200 câu",
            );
          }
          combined = combinePayload as FinalExam;
        } else {
          throw new Error(
            "Thiếu mã đề Listening hoặc Reading; hai phần chưa thể hợp nhất.",
          );
        }
        sessionStorage.removeItem("pending-listening-exam");
        finalExam = combined;
      }
      // Save tag & sync category
      if (modalTag.trim()) {
        if (isDesktop() && finalExam.client_exam_id) {
          const targetId = finalExam.client_exam_id;
          const token = getAccessToken();
          if (token && targetId) {
            await apiFetch(`/api/desktop/exams/${targetId}/sync`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ access_token: token, category: modalTag.trim() }),
            });
          }
        }
      }
      sessionStorage.removeItem("editing-exam");
      sessionStorage.removeItem("extraction-job");
      if (isDesktop() && !editingExam) {
        consumeDesktopExamQuota();
      }
      const classroomReturn = sessionStorage.getItem("classroom-exam-return");
      if (classroomReturn) {
        if (isDesktop() && finalExam.client_exam_id) {
          const token = getAccessToken();
          if (!token) {
            throw new Error(
              "Cần kích hoạt phiên Teacher để đồng bộ đề trước khi giao vào lớp.",
            );
          }
          const syncResponse = await apiFetch(
            `/api/desktop/exams/${finalExam.client_exam_id}/sync`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                access_token: token,
                category: modalTag.trim(),
              }),
            },
          );
          const syncPayload = await syncResponse.json().catch(() => ({}));
          if (!syncResponse.ok) {
            throw new Error(
              syncPayload.detail ||
                "Đề phải được đồng bộ lên máy chủ trước khi giao vào lớp.",
            );
          }
        }
        sessionStorage.removeItem("classroom-exam-return");
        router.push(classroomReturn);
      } else {
        window.location.assign(await finalizedLibraryPath());
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tạo được đề");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <ExamifyLoader message="Đang tải bản review..." />;
  }
  if (!draft) {
    return (
      <div className="mx-auto mt-20 max-w-lg rounded-xl border bg-white p-6 text-red-700">
        {error || "Không tìm thấy dữ liệu đề."}
      </div>
    );
  }
  const audioAutocut = draft.metadata.audio_autocut as
    | {
        status?: string;
        raw_wave_count?: number;
        alignment_confidence?: number;
        quiz_item_audio_count?: number;
      }
    | undefined;
  const detectedRange = questionRangeForDraft(draft);

  return (
    <main
      className="flex min-h-0 max-w-full flex-col overflow-hidden bg-slate-50"
      style={{ flex: "1 1 0" }}
    >
      <header className="relative z-40 shrink-0 border-b border-slate-300 bg-white shadow-[0_3px_14px_rgba(31,78,121,0.08)]">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
          <div>
            <h1 className="font-bold text-slate-900">Kiểm tra đề đã tạo</h1>
            <p className="text-xs text-slate-500">
              {draft.filename} · {draft.exam_type === "listening" ? "Listening" : "Reading"}
            </p>
          </div>
          <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:justify-end">
            <button
              onClick={saveDraft}
              disabled={saving}
              className="ui-btn-secondary flex items-center gap-2 px-3 py-2 text-sm"
            >
              <Save className="h-4 w-4" /> Lưu
            </button>
            {draft.exam_type === "listening" && (
              <button
                onClick={() => finalize("continue-reading")}
                disabled={saving}
                className="ui-btn-secondary flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50"
              >
                <Files className="h-4 w-4" />
                {editingExam?.full_test
                  ? "Tiếp tục chỉnh sửa Reading"
                  : "Tiếp tục tạo Reading"}
              </button>
            )}
            <button
              onClick={() => finalize("quiz")}
              disabled={saving}
              className="ui-btn-primary flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FileCheck2 className="h-4 w-4" />
              )}
              {draft.exam_type === "reading" && (hasPendingListening || editingExam?.full_test)
                ? "Hoàn tất đề đầy đủ"
                : draft.exam_type === "listening"
                  ? "Chỉ tạo Listening"
                  : "Tạo bài thi"}{" "}
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      <div className="shrink-0 border-b border-slate-200 bg-white px-5 py-2">
        <div className="mx-auto flex max-w-7xl gap-2">
          <button
            type="button"
            onClick={() => setReviewTab("content")}
            className={`rounded-lg px-4 py-2 text-sm font-bold ${reviewTab === "content" ? "bg-[#1f4e79] text-white" : "text-slate-600 hover:bg-slate-100"}`}
          >
            Nội dung đề
          </button>
          <button
            type="button"
            onClick={() => setReviewTab("solutions")}
            className={`rounded-lg px-4 py-2 text-sm font-bold ${reviewTab === "solutions" ? "bg-[#1f4e79] text-white" : "text-slate-600 hover:bg-slate-100"}`}
          >
            Giải chi tiết ({draft.solutions?.length || 0})
          </button>
        </div>
      </div>

      <div className="review-scrollbar mx-auto grid min-h-0 w-full max-w-7xl flex-1 gap-5 overflow-y-auto px-5 py-6 lg:grid-cols-[280px_minmax(0,1fr)] lg:overflow-hidden">
        <aside className="review-scrollbar space-y-4 lg:h-full lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:pr-2">
          <section className="rounded-xl border border-slate-300 bg-white p-4 shadow-[0_3px_12px_rgba(31,78,121,0.08)]">
            <h2 className="text-sm font-bold text-slate-900">Tổng quan</h2>
            <div className="mt-3 text-3xl font-bold text-[#1f4e79]">
              {draft.questions.length}
              <span className="ml-1 text-sm font-medium text-slate-500">câu</span>
            </div>
            <p className="mt-1 text-xs font-semibold text-slate-500">
              Dải câu nhận diện: {detectedRange[0] <= detectedRange[1]
                ? `${detectedRange[0]}–${detectedRange[1]}`
                : "Chưa xác định"}
              {draft.requested_count ? ` · Tạo ${draft.requested_count} câu` : ""}
            </p>
            <div className="mt-3 space-y-2 text-xs">
              {Object.entries(partCounts).map(([part, count]) => (
                <div key={part} className="flex justify-between">
                  <span className="text-slate-600">{part}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            {missingAnswerNumbers.length > 0 && (
              <div className="mt-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
                <div className="flex items-start gap-1.5 font-bold">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    Chưa có đáp án câu: {missingAnswerNumbers.join(", ")}.
                  </span>
                </div>
              </div>
            )}
            {(manualEntryNumbers.length > 0 || missingQuestionNumbers.length > 0) && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">
                <div className="flex items-start gap-1.5 font-bold">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>OCR thiếu nội dung; cần nhập thủ công.</span>
                </div>
                {missingQuestionNumbers.length > 0 && (
                  <button
                    type="button"
                    onClick={addDetectedMissingQuestions}
                    className="mt-2 rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] font-bold text-red-700 hover:bg-red-100"
                  >
                    Tạo câu {missingQuestionNumbers.join(", ")}
                  </button>
                )}
                {manualEntryNumbers.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {manualEntryNumbers.map((number) => (
                      <button
                        key={number}
                        type="button"
                        onClick={() => focusQuestion(number)}
                        className="rounded-md border border-red-200 bg-white px-1.5 py-1 text-[11px] font-bold text-red-700 hover:bg-red-100"
                        title={`Nhập thủ công câu ${number}`}
                      >
                        {number}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {draft.audios.length > 0 && (
              <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <summary className="cursor-pointer text-xs font-bold text-[#1f4e79]">
                  Audio Listening · {draft.audios.length} file
                </summary>
                {audioAutocut?.status === "ready" && (
                  <p className="mt-2 rounded-md bg-brand-50 px-2 py-1.5 text-[11px] font-semibold text-brand-800">
                    FFmpeg đã ánh xạ {audioAutocut.raw_wave_count ?? "?"} raw wave
                    thành {audioAutocut.quiz_item_audio_count ?? 54} audio câu/nhóm
                    {typeof audioAutocut.alignment_confidence === "number"
                      ? ` · độ tin cậy ${Math.round(audioAutocut.alignment_confidence * 100)}%`
                      : ""}
                  </p>
                )}
                <div className="mt-2 space-y-2">
                  {draft.audios.map((audio) => (
                    <div key={audio.id}>
                      <p className="truncate text-[11px] font-semibold text-slate-600">
                        {audio.scope === "question"
                          ? `Câu ${audio.question_numbers?.[0] ?? "?"}`
                          : audio.scope === "group"
                            ? `Nhóm câu ${(audio.question_numbers || []).join("–")}`
                            : audio.part === "full"
                              ? "Full"
                              : audio.part === "directions_part_1"
                                ? "Hướng dẫn Part 1"
                              : `Part ${audio.part.slice(-1)}`}
                        : {audio.filename}
                      </p>
                      <AuthenticatedAudio
                        className="mt-1 h-8 w-full"
                        controls
                        preload="none"
                        source={audio.url}
                      />
                    </div>
                  ))}
                </div>
              </details>
            )}
          </section>

          <AnswerKeyImport
            jobId={draft.job_id}
            examType={draft.exam_type}
            value={answerText}
            onChange={setAnswerText}
            onApply={applyAnswerKey}
          />

          {error && (
            <div
              className={`rounded-xl border p-3 text-xs ${
                error.startsWith("Đã áp dụng")
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-red-200 bg-red-50 text-red-700"
              }`}
            >
              {error}
            </div>
          )}
        </aside>

        <div className="review-scrollbar min-w-0 space-y-5 lg:h-full lg:min-h-0 lg:overflow-auto lg:overscroll-contain lg:pr-2">
          {reviewTab === "solutions" ? (
            <SolutionEditor
              examType={draft.exam_type}
              value={draft.solutions || []}
              onChange={(solutions) => setDraft({ ...draft, solutions })}
            />
          ) : (<>
          <section className="rounded-xl border border-slate-300 bg-white p-4 shadow-[0_4px_16px_rgba(31,78,121,0.08)]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="font-bold text-slate-900">Passage và hình ảnh</h2>
                  <p className="mt-0.5 text-xs text-slate-500">
                    Có thể cắt lại từ bất kỳ trang PDF gốc nào khi OCR cắt sai.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setManualCropOpen(true)}
                  className="ui-btn-secondary flex items-center gap-1.5 px-3 py-2 text-xs"
                >
                  <Plus className="h-4 w-4" /> Thêm ảnh thủ công
                </button>
              </div>
              {draft.stimuli.length > 0 ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {draft.stimuli.map((stimulus) => (
                  <article
                    key={stimulus.id}
                    className="overflow-hidden rounded-lg border border-slate-300 bg-white shadow-[0_2px_8px_rgba(31,78,121,0.07)]"
                  >
                    {stimulus.title && (
                      <p className="border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-[#1f4e79]">
                        {stimulus.title}
                      </p>
                    )}
                    <div className="space-y-2 bg-slate-50 p-2">
                      {stimulus.assets.map((asset, assetIndex) => (
                        <div
                          key={asset.id}
                          className="relative overflow-hidden rounded-md border bg-white"
                        >
                          <AuthenticatedImage
                            source={asset.url}
                            alt={`${stimulus.id}, tài liệu ${assetIndex + 1}`}
                            className="h-40 w-full object-contain"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              setCropTarget({
                                stimulusId: stimulus.id,
                                asset,
                                assetIndex,
                                assetCount: stimulus.assets.length,
                              })
                            }
                            className="absolute bottom-2 right-2 rounded-md border border-slate-300 bg-white p-1.5 text-[#1f4e79] shadow-md hover:bg-slate-50"
                            title={`Chỉnh crop tài liệu ${assetIndex + 1}`}
                          >
                            <Crop className="h-4 w-4" />
                          </button>
                        </div>
                      ))}
                    </div>
                    <div className="flex items-center justify-between border-t p-2">
                      <div className="min-w-0 flex-1 pr-2">
                        <p className="truncate text-xs font-bold">{stimulus.id}</p>
                        <p className="text-[11px] text-slate-500">
                          Câu {stimulus.question_numbers.join(", ")} ·{" "}
                          {stimulus.assets.length} tài liệu
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setDraft({
                            ...draft,
                            stimuli: draft.stimuli.filter((s) => s.id !== stimulus.id),
                            questions: draft.questions.map((q) =>
                              q.stimulus_id === stimulus.id ? { ...q, stimulus_id: null } : q
                            ),
                          });
                        }}
                        className="flex shrink-0 items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs font-bold text-red-600 hover:bg-red-100 transition shadow-sm"
                        title="Xóa hình/passage này"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Xóa ảnh
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              ) : (
                <p className="mt-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                  Chưa có ảnh được nhận diện. Dùng “Thêm ảnh thủ công” để chọn vùng từ PDF gốc.
                </p>
              )}
            </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-bold text-slate-900">Câu hỏi đã trích xuất</h2>
              <button
                type="button"
                onClick={addDetectedMissingQuestions}
                disabled={!missingQuestionNumbers.length}
                className="ui-btn-secondary flex items-center gap-1 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
                {missingQuestionNumbers.length
                  ? `Tạo ${missingQuestionNumbers.length} câu thiếu`
                  : "Đã đủ số câu"}
              </button>
            </div>

            {sortedQuestions.map((question) => {
              const missingAnswer = !question.correct;
              const manualEntryRequired = needsManualEntry(
                question,
                draft.exam_type,
              );
              return (
                <details
                  key={`${question.number}-${question.group_id}`}
                  id={`question-${question.number}`}
                  open={manualEntryRequired}
                  className={`rounded-xl border bg-white shadow-[0_3px_12px_rgba(31,78,121,0.07)] ${
                    manualEntryRequired
                      ? "border-red-300"
                      : missingAnswer
                        ? "border-amber-300"
                        : "border-slate-200"
                  }`}
                >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="rounded-md bg-[#1f4e79] px-2 py-1 text-xs font-bold text-white shadow-sm">
                      {question.number}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">
                        {question.text || `Câu ${question.number}`}
                      </p>
                      <p className="text-xs text-slate-500">{question.part}</p>
                    </div>
                  </div>
                  {manualEntryRequired ? (
                    <span className="rounded-full bg-red-100 px-2 py-1 text-[11px] font-bold text-red-800">
                      Cần nhập thủ công
                    </span>
                  ) : missingAnswer ? (
                    <span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-bold text-amber-800">
                      Chưa có đáp án
                    </span>
                  ) : (
                    <Check className="h-4 w-4 text-emerald-600" />
                  )}
                </summary>

                <div className="border-t p-4">
                  <div className="grid gap-3 sm:grid-cols-[110px_1fr]">
                    <label className="text-xs font-semibold text-slate-600">
                      Số câu
                      <input
                        type="number"
                        value={question.number}
                        onChange={(event) =>
                          replaceQuestion(question.number, {
                            number: Number(event.target.value),
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2 text-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                      />
                    </label>
                    <label className="text-xs font-semibold text-slate-600">
                      Nội dung câu hỏi
                      <textarea
                        value={question.text}
                        onChange={(event) =>
                          replaceQuestion(question.number, {
                            text: event.target.value,
                          })
                        }
                        rows={2}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                      />
                    </label>
                  </div>

                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {question.option_letters.map((letter) => (
                      <div key={letter} className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            replaceQuestion(question.number, {
                              correct: question.correct === letter ? null : letter,
                            })
                          }
                          className={`h-8 w-8 shrink-0 rounded-full text-xs font-bold ${
                            question.correct === letter
                              ? "bg-emerald-600 text-white"
                              : "border border-slate-300 bg-white text-[#1f4e79] shadow-sm"
                          }`}
                          title="Đặt làm đáp án đúng"
                        >
                          {letter}
                        </button>
                        <input
                          value={question.options[letter] || ""}
                          onChange={(event) =>
                            replaceQuestion(question.number, {
                              options: {
                                ...question.options,
                                [letter]: event.target.value,
                              },
                            })
                          }
                          placeholder={
                            draft.exam_type === "listening" && question.number <= 31
                              ? "Không có text trong PDF"
                              : `Đáp án ${letter}`
                          }
                          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                        />
                      </div>
                    ))}
                  </div>

                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <label className="text-xs font-semibold text-slate-600">
                      Hình/passage liên kết
                      <select
                        value={question.stimulus_id || ""}
                        onChange={(event) =>
                          replaceQuestion(question.number, {
                            stimulus_id: event.target.value || null,
                            group_id:
                              event.target.value ||
                              question.group_id ||
                              `q-${question.number}`,
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2 text-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                      >
                        <option value="">Không có</option>
                        {draft.stimuli.map((stimulus) => (
                          <option key={stimulus.id} value={stimulus.id}>
                            {stimulus.id}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="text-xs font-semibold text-slate-600">
                      Group ID
                      <input
                        value={question.group_id || ""}
                        onChange={(event) =>
                          replaceQuestion(question.number, {
                            group_id: event.target.value || null,
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                      />
                    </label>
                  </div>

                  <button
                    onClick={() =>
                      setDraft({
                        ...draft,
                        questions: draft.questions.filter(
                          (item) => item.number !== question.number,
                        ),
                      })
                    }
                    className="mt-4 flex items-center gap-1 text-xs font-semibold text-red-600"
                  >
                    <Trash2 className="h-4 w-4" /> Xóa câu
                  </button>
                </div>
                </details>
              );
            })}
          </section>
          </>)}
        </div>
      </div>

      {showFinalizeModal && (
        <div className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/45 p-0 sm:items-center sm:p-4">
          <section className="max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border border-slate-300 bg-white p-5 shadow-2xl sm:rounded-2xl sm:p-8">
            <span className="inline-flex rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-extrabold text-brand-800">
              Hoàn tất & Đặt tên đề
            </span>
            <h2 className="mt-3 text-2xl font-extrabold text-[#1f4e79]">Thông tin đề thi</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Đặt tên và chọn Tag để dễ dàng tìm kiếm trong thư viện My Exams.
            </p>

            <div className="mt-5 space-y-4">
              <label className="block text-sm font-bold text-slate-700">
                Tên đề thi <span className="text-red-500">*</span>
                <input
                  type="text"
                  value={modalTitle}
                  onChange={(e) => handleTitleChange(e.target.value)}
                  placeholder="Nhập tên đề thi (không được trùng)..."
                  className={`mt-1.5 w-full rounded-lg border px-3 py-2.5 text-sm outline-none focus:ring-2 ${
                    titleDuplicateError
                      ? "border-red-500 focus:border-red-500 focus:ring-red-500/10"
                      : "border-slate-300 focus:border-[#1f4e79] focus:ring-[#1f4e79]/10"
                  }`}
                  required
                />
              </label>
              {titleDuplicateError && (
                <p className="text-xs font-semibold text-red-600">{titleDuplicateError}</p>
              )}

              <div>
                <span className="block text-sm font-bold text-slate-700">Chọn Tag có sẵn</span>
                {availableTags.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {availableTags.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => setModalTag(modalTag === tag ? "" : tag)}
                        className={`rounded-full border px-3 py-1 text-xs font-bold transition ${
                          modalTag === tag
                            ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                            : "border-slate-300 bg-slate-50 text-slate-700 hover:border-[#1f4e79]"
                        }`}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="mt-1 text-xs text-slate-400">Chưa có Tag nào. Bạn có thể tạo Tag bên dưới.</p>
                )}
              </div>

              <div className="pt-2">
                <span className="block text-xs font-bold text-slate-600">Hoặc tạo Tag mới</span>
                <div className="mt-1.5 flex gap-2">
                  <input
                    type="text"
                    value={newTagInput}
                    onChange={(e) => setNewTagInput(e.target.value)}
                    placeholder="Tạo tag mới (VD: ETS 2025)..."
                    className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs outline-none focus:border-[#1f4e79]"
                  />
                  <button
                    type="button"
                    onClick={handleCreateTag}
                    disabled={!newTagInput.trim()}
                    className="ui-btn-secondary px-3 py-2 text-xs disabled:opacity-50"
                  >
                    + Tạo Tag
                  </button>
                </div>
                {tagError && (
                  <p className="mt-2 text-xs font-semibold text-red-600">{tagError}</p>
                )}
              </div>
            </div>

            <div className="mt-8 flex justify-end gap-3 border-t border-slate-200 pt-4">
              <button
                type="button"
                onClick={() => setShowFinalizeModal(false)}
                className="ui-btn-secondary px-4 py-2.5 text-xs font-bold"
              >
                Hủy
              </button>
              <button
                type="button"
                onClick={executeFinalize}
                disabled={saving || !modalTitle.trim() || Boolean(titleDuplicateError)}
                className="ui-btn-primary px-5 py-2.5 text-xs font-bold disabled:opacity-50"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                Xác nhận & Lưu đề
              </button>
            </div>
          </section>
        </div>
      )}

      {cropTarget && (
        <CropEditor
          jobId={draft.job_id}
          asset={cropTarget.asset}
          pageCount={sourcePageCount}
          label="Chỉnh vùng ảnh"
          onCancel={() => setCropTarget(null)}
          onSave={(selection) =>
            saveCrop(cropTarget.stimulusId, cropTarget.asset.id, selection)
          }
        />
      )}
      {manualCropOpen && (
        <CropEditor
          jobId={draft.job_id}
          mode="manual"
          pageCount={sourcePageCount}
          availableQuestionNumbers={draft.questions.map((question) => question.number)}
          asset={{
            id: "manual-source",
            url: "",
            page: 1,
            bbox: [0.05, 0.05, 0.95, 0.95],
            width: 0,
            height: 0,
          }}
          label="Chọn từ PDF gốc"
          onCancel={() => setManualCropOpen(false)}
          onSave={createManualCrop}
        />
      )}
    </main>
  );
}
