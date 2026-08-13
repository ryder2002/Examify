"use client";

import {
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  Clock,
  GraduationCap,
  List,
  ChevronLeft,
  ChevronRight,
  BookOpen,
  Flag,
  Maximize2,
  Minimize2,
} from "lucide-react";

import DictionaryPanel from "@/components/DictionaryPanel";
import AudioWavePlayer from "@/components/AudioWavePlayer";
import HiddenExamAudio from "@/components/HiddenExamAudio";
import Part1DirectionsView from "@/components/Part1DirectionsView";
import QuestionCard from "@/components/QuestionCard";
import ExamifyLoader from "@/components/ExamifyLoader";
import ResumeAttemptDialog from "@/components/ResumeAttemptDialog";
import { apiFetch, assetUrl, isDesktop } from "@/lib/api";
import {
  loadAttemptDraft,
  loadAttemptDraftAsync,
  reconcileAttemptDraft,
  removeAttemptDraft,
  saveAttemptDraft,
} from "@/lib/attempt-draft";
import {
  dictionarySourceForText,
  isDictionaryAvailable,
  normalizeSelectedDictionaryText,
  type DictionaryLookupRequest,
} from "@/lib/dictionary";
import { moveQuizCursor } from "@/lib/quiz-navigation";
import {
  createClientId,
  type FinalExam,
  type Question,
  type QuizResult,
} from "@/lib/utils";

type QuizGroup = {
  id: string;
  questions: Question[];
};

type QuizMode = "practice" | "exam";
type AnswerSyncStatus = "idle" | "pending" | "saving" | "saved";
type AnswerChanges = Record<number, string | null>;
type PendingSyncBatch = {
  batchId: string;
  baseRevision: number;
  changes: AnswerChanges;
};
type ClassEvent = {
  client_event_id: string;
  event_type: string;
  occurred_at: string;
  detail: Record<string, string | number | boolean>;
};

type ClassQuizContext = {
  accountAuth: boolean;
  classroomId: string;
  assignmentId: string;
  antiCheatEnabled: boolean;
  listeningNavigationLocked?: boolean;
};

const NAV_PARTS = [
  { number: 1, start: 1, end: 6, label: "Photographs" },
  { number: 2, start: 7, end: 31, label: "Question–Response" },
  { number: 3, start: 32, end: 70, label: "Conversations" },
  { number: 4, start: 71, end: 100, label: "Talks" },
  { number: 5, start: 101, end: 130, label: "Incomplete Sentences" },
  { number: 6, start: 131, end: 146, label: "Text Completion" },
  { number: 7, start: 147, end: 200, label: "Reading Comprehension" },
] as const;

function formatTimer(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function QuizCountdown({
  initialSeconds,
  onTick,
}: {
  initialSeconds: number;
  onTick: (seconds: number) => void;
}) {
  const [seconds, setSeconds] = useState(initialSeconds);
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  useEffect(() => setSeconds(initialSeconds), [initialSeconds]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setSeconds((current) => {
        const next = current > 0 ? current - 1 : 0;
        onTickRef.current(next);
        return next;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return <span>{formatTimer(seconds)}</span>;
}

function storeSubmittedQuiz(
  result: QuizResult,
  attemptId: string | null,
  classContext: ClassQuizContext | null,
) {
  sessionStorage.setItem("quiz-result", JSON.stringify(result));
  sessionStorage.removeItem("quiz-group-index");
  sessionStorage.removeItem("quiz-question-index");
  sessionStorage.removeItem("quiz-question-number");
  sessionStorage.removeItem("quiz-attempt-id");
  sessionStorage.removeItem("quiz-initial-answers");
  sessionStorage.removeItem("quiz-flagged-questions");
  sessionStorage.removeItem("quiz-time-left");
  sessionStorage.removeItem("quiz-class-session");
  sessionStorage.removeItem("quiz-attempt-source");
  sessionStorage.removeItem("quiz-resume-pending");
  if (attemptId) {
    removeAttemptDraft(attemptId);
    sessionStorage.removeItem(`smart-exam-quiz-mounted-${attemptId}`);
    sessionStorage.removeItem(`smart-exam-submit-key-${attemptId}`);
  }
  if (classContext) {
    sessionStorage.setItem(
      "quiz-class-return",
      `/classrooms/detail?id=${encodeURIComponent(classContext.classroomId)}`,
    );
  }
}

export function buildQuizGroups(questions: Question[]): QuizGroup[] {
  const groups: QuizGroup[] = [];
  const byId = new Map<string, QuizGroup>();

  for (const question of [...questions].sort((a, b) => a.number - b.number)) {
    const shouldGroup =
      question.part.startsWith("Part 3") ||
      question.part.startsWith("Part 4") ||
      question.part.startsWith("Part 6") ||
      question.part.startsWith("Part 7");
    const id = shouldGroup
      ? question.group_id || `q-${question.number}`
      : `q-${question.number}`;
    let group = byId.get(id);
    if (!group) {
      group = { id, questions: [] };
      byId.set(id, group);
      groups.push(group);
    }
    group.questions.push(question);
  }
  return groups;
}

function normalizeQuizExam(exam: FinalExam): FinalExam {
  return {
    ...exam,
    audios: (
      exam.audios?.length ? exam.audios : exam.audio ? [exam.audio] : []
    ).map((audio) => ({ ...audio, part: audio.part || "full" })),
  };
}

export default function QuizPage() {
  const router = useRouter();
  const [data, setData] = useState<FinalExam | null>(null);
  const [currentGroupIndex, setCurrentGroupIndex] = useState(0);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [flaggedQuestions, setFlaggedQuestions] = useState<Set<number>>(
    () => new Set(),
  );
  const [showDrawer, setShowDrawer] = useState(false);
  const [dictionaryOpen, setDictionaryOpen] = useState(false);
  const [dictionaryLookupRequest, setDictionaryLookupRequest] =
    useState<DictionaryLookupRequest | null>(null);
  const [zoomedImage, setZoomedImage] = useState<string | null>(null);
  const [durationSeconds, setDurationSeconds] = useState(0);
  const [timeLeft, setTimeLeft] = useState(0);
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [quizMode, setQuizMode] = useState<QuizMode>("practice");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [classContext, setClassContext] = useState<ClassQuizContext | null>(null);
  const [antiCheatWarning, setAntiCheatWarning] = useState<string | null>(null);
  const [answerSyncStatus, setAnswerSyncStatus] =
    useState<AnswerSyncStatus>("idle");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showingPart1Directions, setShowingPart1Directions] = useState(true);
  const [multiTabWarning, setMultiTabWarning] = useState(false);
  const [resumeQuestionNumber, setResumeQuestionNumber] = useState<number | null>(null);
  const dictionaryLookupId = useRef(0);
  const lastQuickLookup = useRef({ key: "", at: 0 });
  const classMountReported = useRef(false);
  const heartbeatState = useRef({
    answeredCount: 0,
    currentQuestionNumber: null as number | null,
    timeLeft: 0,
    isFullscreen: false,
  });
  const latestAnswers = useRef<Record<number, string>>({});
  const flaggedQuestionsRef = useRef<Set<number>>(new Set());
  const answerRevision = useRef(0);
  const acceptedRevision = useRef(0);
  const pendingChanges = useRef<AnswerChanges>({});
  const pendingBatch = useRef<PendingSyncBatch | null>(null);
  const hydratedAttempt = useRef<string | null>(null);
  const answerSaveTimer = useRef<number | null>(null);
  const answerSaveRequest = useRef<Promise<boolean> | null>(null);
  const answerSaveRetry = useRef(0);
  const sendAnswerDeltasRef = useRef<() => Promise<boolean>>(async () => false);
  const persistLocalProgressRef = useRef<
    ((currentAnswers?: Record<number, string>) => void) | null
  >(null);
  const secondaryTab = useRef(false);
  const tabId = useRef("");
  const flushClassEventsRef = useRef<() => void>(() => undefined);
  const classEventsRequest = useRef(false);
  const mounted = useRef(true);

  const scheduleAnswerSave = useCallback((delayMs: number) => {
    if (answerSaveTimer.current !== null) {
      window.clearTimeout(answerSaveTimer.current);
    }
    answerSaveTimer.current = window.setTimeout(() => {
      answerSaveTimer.current = null;
      void sendAnswerDeltasRef.current();
    }, delayMs);
  }, []);

  const sendAnswerDeltas = useCallback(async (): Promise<boolean> => {
    if (!attemptId) return true;
    if (secondaryTab.current) return false;
    if (answerSaveRequest.current) return answerSaveRequest.current;
    const queuedChanges = pendingChanges.current;
    if (!pendingBatch.current && Object.keys(queuedChanges).length === 0) return true;
    const batch = pendingBatch.current || {
      batchId: createClientId(),
      baseRevision: acceptedRevision.current,
      changes: Object.fromEntries(
        Object.entries(queuedChanges).slice(0, 50),
      ) as AnswerChanges,
    };
    pendingBatch.current = batch;
    const timeLeftSeconds = heartbeatState.current.timeLeft;
    const input = classContext
      ? `/api/v1/student/attempts/${attemptId}/sync`
      : `/api/v1/attempts/${attemptId}/sync`;

    // Persist the exact UUID/base/payload before network I/O. A browser crash
    // after commit but before the HTTP response will retry the same batch.
    saveAttemptDraft({
      attemptId,
      revision: answerRevision.current,
      acceptedRevision: acceptedRevision.current,
      answers: latestAnswers.current,
      flaggedQuestions: [...flaggedQuestionsRef.current].sort((a, b) => a - b),
      timeLeftSeconds,
      currentQuestionNumber: heartbeatState.current.currentQuestionNumber,
      updatedAt: Date.now(),
      pendingChanges: pendingChanges.current,
      pendingBatch: batch,
    });

    const request = (async () => {
      let saved = false;
      if (mounted.current) setAnswerSyncStatus("saving");
      try {
        const response = await apiFetch(input, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          keepalive: true,
          body: JSON.stringify({
            batch_id: batch.batchId,
            base_revision: batch.baseRevision,
            changes: batch.changes,
            time_left_seconds: timeLeftSeconds,
            presence: {
              answered_count: heartbeatState.current.answeredCount,
              current_question_number: heartbeatState.current.currentQuestionNumber,
              is_fullscreen: heartbeatState.current.isFullscreen,
              visibility_state: document.visibilityState,
            },
          }),
        });
        const payload = (await response.json().catch(() => ({}))) as {
          accepted_revision?: number;
          detail?: string | {
            message?: string;
            server_revision?: number;
            answers?: Record<number, string>;
          };
          status?: string;
        };
        if (response.status === 409 && typeof payload.detail === "object") {
          const serverRevision = Number(
            payload.detail.server_revision ?? acceptedRevision.current,
          );
          const merged = { ...(payload.detail.answers || {}) };
          for (const [rawNumber, letter] of Object.entries(pendingChanges.current)) {
            const number = Number(rawNumber);
            if (letter === null) delete merged[number];
            else merged[number] = letter;
          }
          acceptedRevision.current = serverRevision;
          pendingBatch.current = null;
          latestAnswers.current = merged;
          setAnswers(merged);
          persistLocalProgressRef.current?.(merged);
          if (mounted.current) {
            setAnswerSyncStatus("pending");
            setSubmitError(
              "Phát hiện một bản ghi khác của bài thi; đáp án trên máy đã được giữ và đang hòa giải.",
            );
          }
          scheduleAnswerSave(250 + Math.floor(Math.random() * 500));
          return false;
        }
        if (!response.ok) {
          throw new Error(
            typeof payload.detail === "string"
              ? payload.detail
              : "Không lưu được đáp án",
          );
        }
        const acknowledged = Number(
          payload.accepted_revision ?? batch.baseRevision + 1,
        );
        if (Number.isSafeInteger(acknowledged)) {
          acceptedRevision.current = Math.max(
            acceptedRevision.current,
            acknowledged,
          );
        }
        answerSaveRetry.current = 0;
        saved = true;
        for (const [rawNumber, sentLetter] of Object.entries(batch.changes)) {
          const number = Number(rawNumber);
          if (pendingChanges.current[number] === sentLetter) {
            const remaining = { ...pendingChanges.current };
            delete remaining[number];
            pendingChanges.current = remaining;
          }
        }
        pendingBatch.current = null;
        saveAttemptDraft({
          attemptId,
          revision: answerRevision.current,
          acceptedRevision: acceptedRevision.current,
          answers: latestAnswers.current,
          flaggedQuestions: [...flaggedQuestionsRef.current].sort(
            (left, right) => left - right,
          ),
          timeLeftSeconds: heartbeatState.current.timeLeft,
          currentQuestionNumber: heartbeatState.current.currentQuestionNumber,
          updatedAt: Date.now(),
          pendingChanges: pendingChanges.current,
        });
        if (payload.status === "submitted") {
          if (mounted.current) {
            setAnswerSyncStatus("saved");
            setSubmitError(
              "Máy chủ đã chốt bài. Nhấn Nộp bài để tải biên nhận kết quả.",
            );
          }
          return true;
        }
        if (mounted.current) {
          setAnswerSyncStatus(
            Object.keys(pendingChanges.current).length === 0 ? "saved" : "pending",
          );
        }
        return true;
      } catch {
        answerSaveRetry.current += 1;
        const backoff = Math.min(
          30_000,
          1_000 * 2 ** Math.min(answerSaveRetry.current - 1, 5),
        );
        if (mounted.current) setAnswerSyncStatus("pending");
        scheduleAnswerSave(backoff + Math.floor(Math.random() * 500));
        return false;
      } finally {
        answerSaveRequest.current = null;
        if (
          saved &&
          Object.keys(pendingChanges.current).length > 0
        ) {
          scheduleAnswerSave(0);
        }
      }
    })();
    answerSaveRequest.current = request;
    return request;
  }, [attemptId, classContext, scheduleAnswerSave]);
  sendAnswerDeltasRef.current = sendAnswerDeltas;

  const persistLocalProgress = useCallback(
    (
      currentAnswers = latestAnswers.current,
      currentFlags = flaggedQuestionsRef.current,
    ) => {
      try {
        sessionStorage.setItem(
          "quiz-initial-answers",
          JSON.stringify(currentAnswers),
        );
        sessionStorage.setItem(
          "quiz-flagged-questions",
          JSON.stringify([...currentFlags].sort((left, right) => left - right)),
        );
        sessionStorage.setItem(
          "quiz-time-left",
          String(heartbeatState.current.timeLeft),
        );
      } catch {
        // The durable attempt draft below remains the primary recovery copy.
      }
      if (!attemptId) return;
      saveAttemptDraft({
        attemptId,
        revision: answerRevision.current,
        acceptedRevision: acceptedRevision.current,
        answers: currentAnswers,
        flaggedQuestions: [...currentFlags].sort(
          (left, right) => left - right,
        ),
        timeLeftSeconds: heartbeatState.current.timeLeft,
        currentQuestionNumber: heartbeatState.current.currentQuestionNumber,
        updatedAt: Date.now(),
        pendingChanges: pendingChanges.current,
        ...(pendingBatch.current ? { pendingBatch: pendingBatch.current } : {}),
      });
    },
    [attemptId],
  );
  persistLocalProgressRef.current = persistLocalProgress;

  const recordAnswer = useCallback(
    (number: number, letter: string) => {
      if (secondaryTab.current) {
        setMultiTabWarning(true);
        return;
      }
      if (latestAnswers.current[number] === letter) return;
      const next = { ...latestAnswers.current, [number]: letter };
      answerRevision.current += 1;
      pendingChanges.current = {
        ...pendingChanges.current,
        [number]: letter,
      };
      latestAnswers.current = next;
      // Persist synchronously in the click handler. This closes the gap where a
      // refresh could happen before React effects or the autosave request run.
      persistLocalProgress(next);
      setAnswers(next);
    },
    [persistLocalProgress],
  );

  const toggleQuestionFlag = useCallback(
    (number: number) => {
      const next = new Set(flaggedQuestionsRef.current);
      if (next.has(number)) next.delete(number);
      else next.add(number);
      flaggedQuestionsRef.current = next;
      persistLocalProgress(latestAnswers.current, next);
      setFlaggedQuestions(next);
    },
    [persistLocalProgress],
  );

  const handleCountdownTick = useCallback((seconds: number) => {
    heartbeatState.current.timeLeft = seconds;
    if (seconds === 0 || seconds % 5 === 0) setTimeLeft(seconds);
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (answerSaveTimer.current !== null) {
        window.clearTimeout(answerSaveTimer.current);
      }
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let active = true;
    const raw = sessionStorage.getItem("quiz-data");
    if (!raw) {
      router.replace("/");
      return;
    }
    try {
      const parsed = JSON.parse(raw) as FinalExam;
      if (parsed.schema_version !== 2) {
        sessionStorage.removeItem("quiz-data");
        router.replace("/");
        return;
      }
      const normalizedExam = normalizeQuizExam(parsed);
      const expectedSlug = sessionStorage.getItem("quiz-slug");
      if (
        expectedSlug &&
        !isDesktop() &&
        window.location.pathname !== `/quiz/${expectedSlug}`
      ) {
        router.replace(`/quiz/${encodeURIComponent(expectedSlug)}`);
      }
      const hasListening = parsed.questions.some((question) => question.number <= 100);
      const hasReading = parsed.questions.some((question) => question.number >= 101);
      const storedDuration = sessionStorage.getItem("quiz-duration");
      const duration = storedDuration
        ? Number(storedDuration)
        : hasListening && hasReading
          ? 120 * 60
          : hasListening
            ? 45 * 60
            : 75 * 60;
      setDurationSeconds(duration);
      setData(normalizedExam);
      const storedAttemptId = sessionStorage.getItem("quiz-attempt-id");
      const initialAnswers = sessionStorage.getItem("quiz-initial-answers");
      const parsedInitialAnswers = initialAnswers
        ? (JSON.parse(initialAnswers) as Record<number, string>)
        : {};
      const durableDraft = storedAttemptId
        ? loadAttemptDraft(storedAttemptId)
        : null;
      const restoredAnswers = durableDraft?.answers || parsedInitialAnswers;
      const rawFlags = sessionStorage.getItem("quiz-flagged-questions");
      const sessionFlags = rawFlags
        ? (JSON.parse(rawFlags) as unknown[])
            .map(Number)
            .filter((number) => Number.isInteger(number) && number > 0)
        : [];
      const restoredFlags = new Set(
        durableDraft?.flaggedQuestions || sessionFlags,
      );
      const rawTimeLeft = sessionStorage.getItem("quiz-time-left");
      const rawStoredTimeLeft =
        rawTimeLeft === null ? Number.NaN : Number(rawTimeLeft);
      const restoredTimeLeft = durableDraft?.timeLeftSeconds ??
        (Number.isFinite(rawStoredTimeLeft) && rawStoredTimeLeft >= 0
          ? rawStoredTimeLeft
          : duration);
      latestAnswers.current = restoredAnswers;
      pendingChanges.current = durableDraft?.pendingChanges || {};
      pendingBatch.current = durableDraft?.pendingBatch || null;
      flaggedQuestionsRef.current = restoredFlags;
      heartbeatState.current.timeLeft = Math.min(duration, restoredTimeLeft);
      if (storedAttemptId) {
        answerRevision.current =
          durableDraft?.revision ||
          (Object.keys(restoredAnswers).length > 0 ? 1 : 0);
        acceptedRevision.current = durableDraft?.acceptedRevision || 0;
        hydratedAttempt.current = storedAttemptId;
        setAnswerSyncStatus(
          Object.keys(pendingChanges.current).length > 0 ? "pending" : "saved",
        );
      }
      setAnswers(restoredAnswers);
      setFlaggedQuestions(restoredFlags);
      setTimeLeft(Math.min(duration, restoredTimeLeft));
      setAttemptId(storedAttemptId);
      setQuizMode(sessionStorage.getItem("quiz-mode") === "exam" ? "exam" : "practice");
      const rawClassContext = sessionStorage.getItem("quiz-class-session");
      const storedClassContext = rawClassContext
        ? (JSON.parse(rawClassContext) as ClassQuizContext)
        : null;
      setClassContext(storedClassContext);

      const rawGroupIndex = sessionStorage.getItem("quiz-group-index");
      const rawQuestionIndex = sessionStorage.getItem("quiz-question-index");
      const rawQuestionNumber = sessionStorage.getItem("quiz-question-number");

      let restoredGroupIdx = 0;
      let restoredQuestionIdx = 0;

      const groups = buildQuizGroups(normalizedExam.questions);
      const targetQuestionNumber =
        durableDraft?.currentQuestionNumber || Number(rawQuestionNumber);
      if (Number.isInteger(targetQuestionNumber) && targetQuestionNumber > 0) {
        const targetNumber = targetQuestionNumber;
        const groupIdx = groups.findIndex((g) =>
          g.questions.some((q) => q.number === targetNumber),
        );
        if (groupIdx !== -1) {
          const qIdx = groups[groupIdx].questions.findIndex(
            (q) => q.number === targetNumber,
          );
          restoredGroupIdx = groupIdx;
          restoredQuestionIdx = Math.max(0, qIdx);
        }
      } else if (rawGroupIndex !== null && rawQuestionIndex !== null) {
        const gIdx = Number(rawGroupIndex);
        const qIdx = Number(rawQuestionIndex);
        if (
          Number.isInteger(gIdx) &&
          gIdx >= 0 &&
          gIdx < groups.length &&
          Number.isInteger(qIdx) &&
          qIdx >= 0
        ) {
          restoredGroupIdx = gIdx;
          restoredQuestionIdx = Math.min(qIdx, groups[gIdx].questions.length - 1);
        }
      }

      setCurrentGroupIndex(restoredGroupIdx);
      setCurrentQuestionIndex(restoredQuestionIdx);
      const restoredQuestionNumber =
        groups[restoredGroupIdx]?.questions[restoredQuestionIdx]?.number ||
        groups[0]?.questions[0]?.number ||
        1;
      const resumeRequested =
        sessionStorage.getItem("quiz-resume-pending") === "1" ||
        restoredQuestionNumber > (groups[0]?.questions[0]?.number || 1) ||
        Object.keys(restoredAnswers).length > 0;
      if (storedAttemptId && resumeRequested) {
        setResumeQuestionNumber(restoredQuestionNumber);
      }

      if (storedAttemptId) {
        void loadAttemptDraftAsync(storedAttemptId).then((indexedDraft) => {
          if (!active || !indexedDraft) return;
          const currentRevision = answerRevision.current;
          if (indexedDraft.revision < currentRevision) return;
          latestAnswers.current = indexedDraft.answers;
          flaggedQuestionsRef.current = new Set(indexedDraft.flaggedQuestions);
          answerRevision.current = indexedDraft.revision;
          acceptedRevision.current = indexedDraft.acceptedRevision;
          pendingChanges.current = indexedDraft.pendingChanges || {};
          pendingBatch.current = indexedDraft.pendingBatch || null;
          setAnswers(indexedDraft.answers);
          setFlaggedQuestions(new Set(indexedDraft.flaggedQuestions));
          setTimeLeft(indexedDraft.timeLeftSeconds);
          heartbeatState.current.timeLeft = indexedDraft.timeLeftSeconds;
          const indexedQuestion = indexedDraft.currentQuestionNumber;
          if (indexedQuestion) {
            const indexedGroup = groups.findIndex((group) =>
              group.questions.some((question) => question.number === indexedQuestion),
            );
            if (indexedGroup >= 0) {
              const indexedQuestionIndex = groups[indexedGroup].questions.findIndex(
                (question) => question.number === indexedQuestion,
              );
              setCurrentGroupIndex(indexedGroup);
              setCurrentQuestionIndex(Math.max(0, indexedQuestionIndex));
              setResumeQuestionNumber(indexedQuestion);
            }
          }
          setAnswerSyncStatus(
            Object.keys(pendingChanges.current).length > 0 ? "pending" : "saved",
          );
        });
      }

      // Refresh the durable server attempt on every reload. Reconciliation keeps
      // a newer local revision instead of replacing it with an older server copy.
      if (storedAttemptId) {
        const attemptInput = storedClassContext
          ? `/api/v1/student/attempts/${storedAttemptId}/state`
          : `/api/v1/attempts/${storedAttemptId}/state`;
        void apiFetch(attemptInput, { cache: "no-store" })
          .then(async (response) => {
            const payload = await response.json().catch(() => ({}));
            if (!active || !response.ok) return;
            if (payload.status === "submitted") {
              const resultInput = storedClassContext
                ? `/api/v1/student/attempts/${storedAttemptId}/result`
                : `/api/v1/attempts/${storedAttemptId}`;
              const resultResponse = await apiFetch(resultInput, { cache: "no-store" });
              const resultPayload = await resultResponse.json().catch(() => ({}));
              if (!active || !resultResponse.ok || !resultPayload.exam) {
                setSubmitError(
                  "Bài đã được máy chủ chốt nhưng chưa tải được biên nhận. Hãy kiểm tra mạng và tải lại.",
                );
                return;
              }
              storeSubmittedQuiz({
                ...resultPayload,
                schema_version: 2,
                exam: resultPayload.exam,
                answers: resultPayload.answers || payload.answers || {},
                duration_seconds: Number(resultPayload.duration_seconds || duration),
                time_left_seconds: Number(resultPayload.time_left_seconds || 0),
                submitted_at: resultPayload.submitted_at || payload.submitted_at,
                status: "submitted",
                receipt_id: resultPayload.receipt_id || payload.receipt_id,
              }, storedAttemptId, storedClassContext);
              router.replace("/result");
              return;
            }
            const refreshedDuration = Number(
              payload.time_left_seconds ?? duration,
            );
            const refreshedAnswers = (payload.answers || {}) as Record<number, string>;
            const serverRevision = Number(payload.accepted_revision || 0);
            const reconciled = reconcileAttemptDraft(
              storedAttemptId,
              refreshedAnswers,
              serverRevision,
              refreshedDuration,
              loadAttemptDraft(storedAttemptId),
              Number(payload.current_question_number) || null,
            );
            latestAnswers.current = reconciled.answers;
            const refreshedFlags = new Set(reconciled.flaggedQuestions);
            flaggedQuestionsRef.current = refreshedFlags;
            answerRevision.current = reconciled.revision;
            acceptedRevision.current = reconciled.acceptedRevision;
            pendingChanges.current = reconciled.pendingChanges || {};
            pendingBatch.current = reconciled.pendingBatch || null;
            saveAttemptDraft(reconciled);
            setAnswers(reconciled.answers);
            setFlaggedQuestions(refreshedFlags);
            setAnswerSyncStatus(
              Object.keys(pendingChanges.current).length > 0 ? "pending" : "saved",
            );
            setTimeLeft(reconciled.timeLeftSeconds);
            sessionStorage.setItem(
              "quiz-initial-answers",
              JSON.stringify(reconciled.answers),
            );
            sessionStorage.setItem(
              "quiz-flagged-questions",
              JSON.stringify(reconciled.flaggedQuestions),
            );
            sessionStorage.setItem(
              "quiz-time-left",
              String(reconciled.timeLeftSeconds),
            );
          })
          .catch(() => undefined);
      }
    } catch {
      router.replace("/");
    }
    return () => {
      active = false;
    };
  }, [router]);

  useEffect(() => {
    let active = true;
    const syncFullscreen = () => {
      if (!isDesktop()) {
        setIsFullscreen(Boolean(document.fullscreenElement));
      }
    };
    document.addEventListener("fullscreenchange", syncFullscreen);
    if (isDesktop()) {
      import("@tauri-apps/api/window")
        .then(({ getCurrentWindow }) => getCurrentWindow().isFullscreen())
        .then((value) => {
          if (active) setIsFullscreen(value);
        })
        .catch(() => undefined);
    } else {
      syncFullscreen();
    }
    return () => {
      active = false;
      document.removeEventListener("fullscreenchange", syncFullscreen);
    };
  }, []);

  useEffect(() => {
    latestAnswers.current = answers;
    if (!attemptId || !data || hydratedAttempt.current !== attemptId) return;
    sessionStorage.setItem("quiz-initial-answers", JSON.stringify(answers));
    saveAttemptDraft({
      attemptId,
      revision: answerRevision.current,
      acceptedRevision: acceptedRevision.current,
      answers,
      flaggedQuestions: [...flaggedQuestionsRef.current].sort(
        (left, right) => left - right,
      ),
      timeLeftSeconds: timeLeft,
      currentQuestionNumber: heartbeatState.current.currentQuestionNumber,
      updatedAt: Date.now(),
      pendingChanges: pendingChanges.current,
      ...(pendingBatch.current ? { pendingBatch: pendingBatch.current } : {}),
    });
    if (Object.keys(pendingChanges.current).length > 0) {
      setAnswerSyncStatus("pending");
      scheduleAnswerSave(9_000 + Math.floor(Math.random() * 2_000));
    }
    // Saving is driven by answer revisions; timer changes must not create writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answers, attemptId, data, scheduleAnswerSave]);

  useEffect(() => {
    if (!data) return;
    const persist = () => persistLocalProgress();
    const persistWhenHidden = () => {
      if (document.visibilityState === "hidden") {
        persist();
        void sendAnswerDeltasRef.current();
      }
    };
    const timer = window.setInterval(persist, 5000);
    const persistAndFlush = () => {
      persist();
      void sendAnswerDeltasRef.current();
    };
    window.addEventListener("pagehide", persistAndFlush);
    document.addEventListener("visibilitychange", persistWhenHidden);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("pagehide", persistAndFlush);
      document.removeEventListener("visibilitychange", persistWhenHidden);
    };
  }, [data, persistLocalProgress]);

  useEffect(() => {
    if (!attemptId) return;
    const syncOnReconnect = () => scheduleAnswerSave(0);
    window.addEventListener("online", syncOnReconnect);
    return () => window.removeEventListener("online", syncOnReconnect);
  }, [attemptId, scheduleAnswerSave]);

  useEffect(() => {
    if (!attemptId || typeof window === "undefined") return;
    if (!tabId.current) tabId.current = createClientId();
    const leaseKey = `smart-exam-attempt-owner-${attemptId}`;
    const leaseTtlMs = 15_000;
    const channel =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel(`examify-attempt-${attemptId}`);

    const readLease = (): { tabId: string; at: number } | null => {
      try {
        const value = JSON.parse(localStorage.getItem(leaseKey) || "null") as {
          tabId?: string;
          at?: number;
        } | null;
        if (!value?.tabId || !Number.isFinite(value.at)) return null;
        return { tabId: value.tabId, at: Number(value.at) };
      } catch {
        return null;
      }
    };
    const setSecondary = (value: boolean) => {
      secondaryTab.current = value;
      setMultiTabWarning(value);
    };
    const claimOrObserve = () => {
      const now = Date.now();
      const lease = readLease();
      if (
        !lease ||
        lease.tabId === tabId.current ||
        now - lease.at > leaseTtlMs
      ) {
        try {
          localStorage.setItem(
            leaseKey,
            JSON.stringify({ tabId: tabId.current, at: now }),
          );
          const confirmed = readLease();
          const ownsLease = confirmed?.tabId === tabId.current;
          setSecondary(!ownsLease);
          if (ownsLease) channel?.postMessage({ type: "owner", tabId: tabId.current });
        } catch {
          // Storage restrictions should not make the exam unusable.
          setSecondary(false);
        }
        return;
      }
      setSecondary(true);
    };
    channel?.addEventListener("message", claimOrObserve);
    claimOrObserve();
    const timer = window.setInterval(claimOrObserve, 5_000);
    return () => {
      window.clearInterval(timer);
      channel?.removeEventListener("message", claimOrObserve);
      channel?.close();
      const lease = readLease();
      if (lease?.tabId === tabId.current) localStorage.removeItem(leaseKey);
    };
  }, [attemptId]);

  const reportClassEvent = useCallback(
    (eventType: string, detail: Record<string, string | number | boolean> = {}) => {
      if (!attemptId || !classContext?.antiCheatEnabled) return;
      const warningByType: Record<string, string> = {
        visibility_hidden: "Bạn đã rời tab. Sự kiện này đã được báo cho giáo viên.",
        window_blur: "Cửa sổ làm bài mất tiêu điểm. Sự kiện đã được ghi nhận.",
        fullscreen_exit: "Bạn đã thoát toàn màn hình. Hãy quay lại chế độ toàn màn hình.",
        offline: "Mất kết nối mạng. Đáp án sẽ tiếp tục đồng bộ khi có mạng.",
        copy: "Thao tác sao chép đã được ghi nhận.",
        paste: "Thao tác dán đã được ghi nhận.",
        context_menu: "Thao tác mở menu chuột phải đã được ghi nhận.",
      };
      if (warningByType[eventType]) {
        setAntiCheatWarning(warningByType[eventType]);
        window.setTimeout(() => setAntiCheatWarning(null), 4500);
      }
      const event: ClassEvent = {
        client_event_id: createClientId(),
        event_type: eventType,
        occurred_at: new Date().toISOString(),
        detail,
      };
      try {
        const key = `smart-exam-pending-events-${attemptId}`;
        const current = JSON.parse(
          localStorage.getItem(key) || "[]",
        ) as ClassEvent[];
        const queued = [...current.slice(-99), event];
        localStorage.setItem(key, JSON.stringify(queued));
        if (queued.length >= 20) flushClassEventsRef.current();
      } catch {
        // Presence still marks this client as connected if browser storage is full.
      }
    },
    [attemptId, classContext],
  );

  const flushClassEvents = useCallback(() => {
    if (!attemptId || !classContext || classEventsRequest.current) return;
    const key = `smart-exam-pending-events-${attemptId}`;
    try {
      const events = (
        JSON.parse(localStorage.getItem(key) || "[]") as ClassEvent[]
      ).slice(0, 20);
      if (!events.length) return;
      classEventsRequest.current = true;
      const sentIds = new Set(events.map((event) => event.client_event_id));
      void apiFetch(
        `/api/v1/student/attempts/${attemptId}/events`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          keepalive: true,
          body: JSON.stringify({ events }),
        },
      ).then((response) => {
        if (!response.ok) return;
        const current = JSON.parse(
          localStorage.getItem(key) || "[]",
        ) as ClassEvent[];
        const remaining = current.filter(
          (event) => !sentIds.has(event.client_event_id),
        );
        if (remaining.length) localStorage.setItem(key, JSON.stringify(remaining));
        else localStorage.removeItem(key);
      }).finally(() => {
        classEventsRequest.current = false;
      });
    } catch {
      localStorage.removeItem(key);
      classEventsRequest.current = false;
    }
  }, [attemptId, classContext]);
  flushClassEventsRef.current = flushClassEvents;

  useEffect(() => {
    const groups = data ? buildQuizGroups(data.questions) : [];
    heartbeatState.current = {
      answeredCount: Object.keys(answers).length,
      currentQuestionNumber:
        groups[currentGroupIndex]?.questions[currentQuestionIndex]?.number ?? null,
      timeLeft,
      isFullscreen,
    };
    latestAnswers.current = answers;
  }, [answers, currentGroupIndex, currentQuestionIndex, data, isFullscreen, timeLeft]);

  useEffect(() => {
    if (!attemptId || !classContext) return;
    const heartbeat = () => {
      const current = heartbeatState.current;
      void apiFetch(
        `/api/v1/student/attempts/${attemptId}/heartbeat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            answered_count: current.answeredCount,
            current_question_number: current.currentQuestionNumber,
            time_left_seconds: current.timeLeft,
            is_fullscreen: current.isFullscreen,
            visibility_state: document.visibilityState,
          }),
        },
      ).catch(() => undefined);
    };
    heartbeat();
    const timer = window.setInterval(heartbeat, 10000);
    return () => window.clearInterval(timer);
  }, [attemptId, classContext]);

  useEffect(() => {
    if (!classContext?.antiCheatEnabled) return;
    const visibility = () =>
      reportClassEvent(
        document.visibilityState === "hidden"
          ? "visibility_hidden"
          : "visibility_visible",
      );
    const blur = () => reportClassEvent("window_blur");
    const focus = () => reportClassEvent("window_focus");
    const offline = () => reportClassEvent("offline");
    const online = () => {
      flushClassEvents();
      reportClassEvent("online");
    };
    const copy = () => reportClassEvent("copy");
    const paste = () => reportClassEvent("paste");
    const contextMenu = () => reportClassEvent("context_menu");
    const fullscreen = () =>
      reportClassEvent(
        document.fullscreenElement || (isDesktop() && isFullscreen)
          ? "fullscreen_enter"
          : "fullscreen_exit",
      );
    const unload = () => reportClassEvent("unload");
    const reloadKey = attemptId ? `smart-exam-quiz-mounted-${attemptId}` : "";
    if (reloadKey && !classMountReported.current) {
      if (sessionStorage.getItem(reloadKey)) reportClassEvent("reload");
      sessionStorage.setItem(reloadKey, "1");
      classMountReported.current = true;
    }
    document.addEventListener("visibilitychange", visibility);
    document.addEventListener("fullscreenchange", fullscreen);
    window.addEventListener("blur", blur);
    window.addEventListener("focus", focus);
    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    document.addEventListener("copy", copy);
    document.addEventListener("paste", paste);
    document.addEventListener("contextmenu", contextMenu);
    window.addEventListener("beforeunload", unload);
    flushClassEvents();
    const flushTimer = window.setInterval(flushClassEvents, 5_000);
    return () => {
      window.clearInterval(flushTimer);
      document.removeEventListener("visibilitychange", visibility);
      document.removeEventListener("fullscreenchange", fullscreen);
      window.removeEventListener("blur", blur);
      window.removeEventListener("focus", focus);
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
      document.removeEventListener("copy", copy);
      document.removeEventListener("paste", paste);
      document.removeEventListener("contextmenu", contextMenu);
      window.removeEventListener("beforeunload", unload);
    };
  }, [
    attemptId,
    classContext,
    flushClassEvents,
    isFullscreen,
    reportClassEvent,
  ]);

  const quizGroups = useMemo(
    () => (data ? buildQuizGroups(data.questions) : []),
    [data],
  );

  useEffect(() => {
    const question =
      quizGroups[currentGroupIndex]?.questions[currentQuestionIndex];
    if (!question) return;
    const frame = window.requestAnimationFrame(() => {
      document
        .getElementById(`quiz-question-${question.number}`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [currentGroupIndex, currentQuestionIndex, quizGroups]);

  useEffect(() => {
    if (!data || !quizGroups || quizGroups.length === 0) return;
    const currentQ = quizGroups[currentGroupIndex]?.questions[currentQuestionIndex];
    if (currentQ) {
      heartbeatState.current.currentQuestionNumber = currentQ.number;
      sessionStorage.setItem("quiz-group-index", String(currentGroupIndex));
      sessionStorage.setItem("quiz-question-index", String(currentQuestionIndex));
      sessionStorage.setItem("quiz-question-number", String(currentQ.number));
      persistLocalProgress();
    }
  }, [data, currentGroupIndex, currentQuestionIndex, persistLocalProgress, quizGroups]);
  const navigatorSections = useMemo(
    () =>
      NAV_PARTS.map((part) => ({
        ...part,
        questions:
          data?.questions.filter(
            (question) =>
              question.number >= part.start && question.number <= part.end,
          ) || [],
      })).filter((part) => part.questions.length > 0),
    [data],
  );

  useEffect(() => {
    if (!data || durationSeconds <= 0 || timeLeft !== 0) return;
    void handleSubmit(true);
    // handleSubmit intentionally reads the latest answer/timer state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, durationSeconds, timeLeft]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!data || showDrawer || dictionaryOpen || zoomedImage) return;
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target?.tagName || "")
      ) {
        return;
      }
      const groups = buildQuizGroups(data.questions);
      const group = groups[currentGroupIndex];
      const question = group?.questions[currentQuestionIndex];
      if (!question) return;
      if (["1", "2", "3", "4"].includes(event.key)) {
        const letter = ["A", "B", "C", "D"][Number(event.key) - 1];
        if (question.option_letters.includes(letter)) {
          event.preventDefault();
          recordAnswer(question.number, letter);
        }
        return;
      }
      if (event.key === "ArrowRight") {
        const isListening = question.number <= 100;
        const navigationLocked =
          quizMode === "exam" ||
          classContext?.listeningNavigationLocked === true ||
          sessionStorage.getItem("quiz-listening-navigation-locked") === "true";
        if (navigationLocked && isListening) return;
        const next = moveQuizCursor(
          groups,
          { groupIndex: currentGroupIndex, questionIndex: currentQuestionIndex },
          1,
        );
        if (
          next.groupIndex === currentGroupIndex &&
          next.questionIndex === currentQuestionIndex
        ) return;
        event.preventDefault();
        setCurrentGroupIndex(next.groupIndex);
        setCurrentQuestionIndex(next.questionIndex);
        return;
      }
      if (event.key === "ArrowLeft") {
        const isListening = question.number <= 100;
        const firstReading = groups.findIndex(
          (item) => item.questions[0]?.number >= 101,
        );
        const hasPrevious = currentGroupIndex > 0 || currentQuestionIndex > 0;
        const navigationLocked =
          quizMode === "exam" ||
          classContext?.listeningNavigationLocked === true ||
          sessionStorage.getItem("quiz-listening-navigation-locked") === "true";
        const allowed =
          hasPrevious &&
          (!navigationLocked ||
            (!isListening &&
              (firstReading === -1 ||
                currentGroupIndex > firstReading ||
                currentQuestionIndex > 0)));
        if (allowed) {
          const previous = moveQuizCursor(
            groups,
            { groupIndex: currentGroupIndex, questionIndex: currentQuestionIndex },
            -1,
          );
          event.preventDefault();
          setCurrentGroupIndex(previous.groupIndex);
          setCurrentQuestionIndex(previous.questionIndex);
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    currentGroupIndex,
    currentQuestionIndex,
    classContext,
    data,
    dictionaryOpen,
    quizMode,
    recordAnswer,
    showDrawer,
    zoomedImage,
  ]);

  useEffect(() => {
    // Dictionary state must never survive a Practice -> Mock transition,
    // including transitions triggered by route/session restoration.
    if (quizMode !== "practice") {
      setDictionaryOpen(false);
      setDictionaryLookupRequest(null);
    }
  }, [quizMode]);

  if (!data || data.questions.length === 0) {
    return <ExamifyLoader message="Đang tải đề thi..." />;
  }

  if (resumeQuestionNumber !== null) {
    return (
      <ResumeAttemptDialog
        questionNumber={resumeQuestionNumber}
        onContinue={() => {
          sessionStorage.setItem("quiz-resume-pending", "0");
          setResumeQuestionNumber(null);
        }}
        onLeave={() => {
          const classReturn = sessionStorage.getItem("quiz-class-return");
          const source = sessionStorage.getItem("quiz-attempt-source");
          const classroomPath = classContext?.classroomId
            ? `/classrooms/detail?id=${encodeURIComponent(classContext.classroomId)}`
            : null;
          router.replace(
            classReturn ||
              classroomPath ||
              (source === "bank" ? "/exam-bank" : "/my-exams"),
          );
        }}
      />
    );
  }

  const currentGroup = quizGroups[currentGroupIndex];
  const currentQuestions = currentGroup.questions;
  const currentQ = currentQuestions[currentQuestionIndex] || currentQuestions[0];
  const currentStimulus = data.stimuli.find(
    (stimulus) =>
      stimulus.id ===
      currentQuestions.find((question) => question.stimulus_id)?.stimulus_id,
  );
  const totalQ = data.questions.length;
  const totalGroups = quizGroups.length;
  const answeredCount = Object.keys(answers).length;
  const firstNumber = currentQuestions[0].number;
  const lastNumber = currentQuestions[currentQuestions.length - 1].number;
  const questionRange =
    firstNumber === lastNumber ? String(firstNumber) : `${firstNumber}–${lastNumber}`;
  const examLabel =
    data.exam_type === "combined"
      ? currentQ.number <= 100
        ? "Listening"
        : "Reading"
      : data.exam_type === "listening"
        ? "Listening"
        : "Reading";
  const isListeningSection = examLabel === "Listening";
  // The directions screen is an introduction, not a question. Keep all
  // question controls hidden until the learner explicitly enters question 1.
  const showingDirectionIntro =
    isListeningSection && currentQ.number === 1 && showingPart1Directions;
  const isExamMode = quizMode === "exam";
  const dictionaryAvailable = isDictionaryAvailable(quizMode);

  const listeningNavigationLocked =
    isExamMode ||
    classContext?.listeningNavigationLocked === true ||
    sessionStorage.getItem("quiz-listening-navigation-locked") === "true";
  const navigatorLocked = isListeningSection && listeningNavigationLocked;
  const currentPartNumber =
    NAV_PARTS.find(
      (part) => currentQ.number >= part.start && currentQ.number <= part.end,
    )?.number || 1;
  const audioRefs = data.audios?.length
    ? data.audios
    : data.audio
      ? [data.audio]
      : [];
  const selectedAudio =
    audioRefs.find(
      (audio) =>
        (audio.scope === "question" || audio.scope === "group") &&
        audio.question_numbers?.some((number) =>
          currentQuestions.some((question) => question.number === number),
        ),
    ) ||
    audioRefs.find((audio) => audio.part === `part_${currentPartNumber}`) ||
    audioRefs.find((audio) => audio.part === "full");
  const visibleNavigatorSections = navigatorLocked
    ? []
    : isExamMode
      ? navigatorSections.filter((part) => part.number >= 5)
      : navigatorSections;
  const firstReadingGroupIndex = quizGroups.findIndex(
    (group) => group.questions[0]?.number >= 101,
  );
  const canGoPrevious =
    (currentGroupIndex > 0 || currentQuestionIndex > 0) &&
    (!listeningNavigationLocked ||
      (!isListeningSection &&
        (firstReadingGroupIndex === -1 ||
          currentGroupIndex > firstReadingGroupIndex ||
          currentQuestionIndex > 0)));
  const canGoNext =
    (!listeningNavigationLocked || !isListeningSection) &&
    (currentGroupIndex < totalGroups - 1 ||
      currentQuestionIndex < currentQuestions.length - 1);

  const navigateQuestion = (direction: -1 | 1) => {
    if (listeningNavigationLocked && isListeningSection) return;
    const next = moveQuizCursor(
      quizGroups,
      { groupIndex: currentGroupIndex, questionIndex: currentQuestionIndex },
      direction,
    );
    const nextQuestion = quizGroups[next.groupIndex]?.questions[next.questionIndex];
    if (
      listeningNavigationLocked &&
      direction === -1 &&
      nextQuestion &&
      nextQuestion.number <= 100
    ) {
      return;
    }
    setCurrentGroupIndex(next.groupIndex);
    setCurrentQuestionIndex(next.questionIndex);
  };

  const advanceFromListeningAudio = () => {
    const nextGroupIndex = currentGroupIndex + 1;
    if (nextGroupIndex >= quizGroups.length) return;
    setCurrentGroupIndex(nextGroupIndex);
    setCurrentQuestionIndex(0);
  };

  const finishListeningAudio = () => {
    if (firstReadingGroupIndex === -1) return;
    setCurrentGroupIndex(firstReadingGroupIndex);
    setCurrentQuestionIndex(0);
  };

  const handleSelect = (number: number, letter: string) => {
    recordAnswer(number, letter);
  };

  const handleQuickDictionaryLookup = (
    event: ReactMouseEvent<HTMLDivElement>,
  ) => {
    if (quizMode !== "practice" || !dictionaryAvailable || typeof window === "undefined") return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;

    const commonAncestor = selection.getRangeAt(0).commonAncestorContainer;
    const selectionElement =
      commonAncestor.nodeType === Node.ELEMENT_NODE
        ? (commonAncestor as Element)
        : commonAncestor.parentElement;
    if (!selectionElement || !event.currentTarget.contains(selectionElement)) return;

    const text = normalizeSelectedDictionaryText(selection.toString());
    if (!text) return;
    const source = dictionarySourceForText(text);
    const key = `${source}:${text.toLocaleLowerCase()}`;
    const now = Date.now();
    if (
      lastQuickLookup.current.key === key &&
      now - lastQuickLookup.current.at < 300
    ) {
      return;
    }
    lastQuickLookup.current = { key, at: now };
    dictionaryLookupId.current += 1;
    setDictionaryLookupRequest({
      id: dictionaryLookupId.current,
      text,
      source,
    });
    setDictionaryOpen(true);
  };

  const toggleFullscreen = async () => {
    try {
      if (isDesktop()) {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const appWindow = getCurrentWindow();
        const current = await appWindow.isFullscreen();
        await appWindow.setFullscreen(!current);
        setIsFullscreen(!current);
        return;
      }
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Browsers may reject fullscreen outside a user gesture. The button can
      // be pressed again without interrupting the active exam.
    }
  };

  async function handleSubmit(replace = false) {
    if (!data || isSubmitting) return;
    if (secondaryTab.current) {
      setMultiTabWarning(true);
      setSubmitError("Tab này đang ở chế độ chỉ đọc. Hãy nộp bài từ tab đang hoạt động.");
      return;
    }
    const localResult: QuizResult = {
      schema_version: 2,
      exam: data,
      answers,
      duration_seconds: durationSeconds,
      time_left_seconds: heartbeatState.current.timeLeft,
      submitted_at: new Date().toISOString(),
    };
    if (!attemptId) {
      storeSubmittedQuiz(localResult, null, classContext);
      if (replace) router.replace("/result");
      else router.push("/result");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    if (answerSaveTimer.current !== null) {
      window.clearTimeout(answerSaveTimer.current);
      answerSaveTimer.current = null;
    }
    try {
      const input = classContext
        ? `/api/v1/student/attempts/${attemptId}/submit`
        : `/api/v1/attempts/${attemptId}/submit`;
      const idempotencyStorageKey = `smart-exam-submit-key-${attemptId}`;
      const idempotencyKey =
        sessionStorage.getItem(idempotencyStorageKey) || createClientId();
      sessionStorage.setItem(idempotencyStorageKey, idempotencyKey);
      const response = await apiFetch(input, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          answers: latestAnswers.current,
          time_left_seconds: heartbeatState.current.timeLeft,
          client_revision: answerRevision.current,
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as QuizResult & {
        accepted_revision?: number;
        detail?: string;
        status?: string;
      };
      if (!response.ok) {
        throw new Error(payload.detail || "Máy chủ chưa xác nhận bài nộp");
      }
      if (payload.status !== "submitted" || !payload.submitted_at || !payload.receipt_id) {
        throw new Error("Máy chủ chưa trả biên nhận bài nộp hợp lệ");
      }
      const acknowledged = Number(
        payload.accepted_revision ?? answerRevision.current,
      );
      if (Number.isSafeInteger(acknowledged)) {
        acceptedRevision.current = Math.max(
          acceptedRevision.current,
          acknowledged,
        );
      }
      const confirmedResult: QuizResult = {
        ...localResult,
        ...payload,
        // The exam loaded before submission is intentionally sanitized. Once
        // the server accepts a personal attempt it returns the immutable
        // version with answer keys revealed; keep that snapshot for Results.
        // Classroom release policies may omit it, so retain a safe fallback.
        exam: payload.exam?.questions?.length ? payload.exam : data,
        answers: payload.answers || latestAnswers.current,
        submitted_at: payload.submitted_at,
      };
      storeSubmittedQuiz(confirmedResult, attemptId, classContext);
      if (replace) router.replace("/result");
      else router.push("/result");
    } catch (reason) {
      setSubmitError(
        reason instanceof Error
          ? `${reason.message}. Đáp án vẫn được giữ trên máy; hãy kiểm tra mạng và thử lại.`
          : "Máy chủ chưa xác nhận bài nộp. Đáp án vẫn được giữ trên máy.",
      );
      setAnswerSyncStatus("pending");
      scheduleAnswerSave(0);
    } finally {
      if (mounted.current) setIsSubmitting(false);
    }
  }

  return (
    <div className="quiz-shell flex h-[100dvh] flex-col overflow-hidden bg-slate-50 font-sans text-slate-900">
      {antiCheatWarning && (
        <div className="fixed left-1/2 top-3 z-[80] w-[min(92vw,620px)] -translate-x-1/2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-center text-sm font-bold text-amber-900 shadow-xl">
          {antiCheatWarning}
        </div>
      )}
      {multiTabWarning && (
        <div className="fixed left-1/2 top-3 z-[95] w-[min(92vw,680px)] -translate-x-1/2 rounded-xl border border-amber-400 bg-amber-50 px-4 py-3 text-center text-sm font-bold text-amber-950 shadow-xl">
          Bài thi đang mở ở tab khác. Tab này chỉ đọc để tránh ghi đè đáp án.
        </div>
      )}
      {submitError && (
        <div
          role="alert"
          className="fixed left-1/2 top-3 z-[90] w-[min(92vw,680px)] -translate-x-1/2 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-center text-sm font-bold text-red-900 shadow-xl"
        >
          {submitError}
        </div>
      )}
      <header className="flex min-h-[64px] shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[#1b456d] bg-[#1f4e79] px-3 py-2 text-white shadow-[0_4px_14px_rgba(31,78,121,0.24)] sm:min-h-[72px] sm:px-8 sm:py-3">
        <div className="flex items-center gap-3">
          <Image
            src="/logo.png"
            alt="Examify Logo"
            width={512}
            height={512}
            priority
            unoptimized
            className="h-9 sm:h-11 w-auto object-contain rounded-lg overflow-hidden"
          />
        </div>

        <div className="order-3 w-full text-center text-sm font-semibold tracking-wide sm:order-none sm:w-auto sm:text-base">
          {examLabel}: Q. {currentQ.number}
          <span className="ml-2 rounded border border-white/35 bg-white/10 px-2 py-1 text-[10px] font-extrabold uppercase tracking-wider">
            {isExamMode ? "Luyện thi" : "Luyện tập"}
          </span>
        </div>

        <div className="flex items-center gap-3">
          {attemptId && (
            <span className="hidden text-xs font-semibold text-white/85 lg:inline">
              {answerSyncStatus === "saving"
                ? "Đang lưu…"
                : answerSyncStatus === "pending"
                  ? "Đã lưu trên máy · chờ đồng bộ"
                  : answerSyncStatus === "saved"
                    ? "Đã lưu"
                    : ""}
            </span>
          )}
          <div className="rounded-md border border-white/50 bg-white px-3 py-1 text-sm font-bold text-[#1f4e79] shadow-sm">
            {answeredCount}/{totalQ}
          </div>

          <div className="flex items-center gap-1.5 rounded-md border border-white/35 bg-white/10 px-3 py-1 text-sm font-semibold text-white shadow-sm">
            <Clock className="h-4 w-4" />
            <QuizCountdown
              initialSeconds={timeLeft}
              onTick={handleCountdownTick}
            />
          </div>

          <button
            onClick={() => void handleSubmit()}
            disabled={isSubmitting}
            className="rounded-md border border-white bg-white px-5 py-2 text-sm font-bold text-[#1f4e79] shadow-md transition hover:bg-slate-100 disabled:cursor-wait disabled:opacity-70"
          >
            {isSubmitting ? "Đang nộp…" : "Submit"}
          </button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-300 bg-white px-4 py-2.5 text-base font-extrabold shadow-sm sm:px-6">
        <div className="text-black font-extrabold text-base">
          {currentStimulus
            ? currentQ.number <= 100
              ? "Look at the image and select your answer."
              : "Read the document and answer the questions."
            : "Select the best answer."}
        </div>
        {!isExamMode && isListeningSection && selectedAudio && (
          <div className="order-3 w-full lg:order-none lg:w-auto">
            <AudioWavePlayer
              key={selectedAudio.id}
              audio={selectedAudio}
              autoPlay={false}
            />
          </div>
        )}
        {isExamMode && isListeningSection && audioRefs.length > 0 && (
          <HiddenExamAudio
            audios={audioRefs}
            active={isListeningSection}
            currentQuestionNumber={lastNumber}
            currentQuestionNumbers={currentQuestions.map(
              (question) => question.number,
            )}
            showingDirections={showingDirectionIntro}
            onHideDirections={() => setShowingPart1Directions(false)}
            onAutoAdvance={advanceFromListeningAudio}
            onListeningComplete={finishListeningAudio}
          />
        )}
        <div className="text-black font-extrabold text-base">
          {currentQuestions.length > 1
            ? `Nhóm câu ${questionRange}`
            : "Câu hỏi"}
        </div>
      </div>

      {/* 3. MAIN WORKSPACE */}
      {showingDirectionIntro ? (
        <div className="flex flex-1 items-center justify-center overflow-y-auto p-3 sm:p-6 bg-slate-100/60">
          <Part1DirectionsView
            onContinue={() => setShowingPart1Directions(false)}
            isPlaying={true}
          />
        </div>
      ) : (
        <div
          data-quiz-mode={quizMode}
          onMouseUp={
            dictionaryAvailable ? handleQuickDictionaryLookup : undefined
          }
          className={`flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 sm:gap-5 sm:p-6 ${
            currentStimulus ? "lg:flex-row" : "items-center"
          }`}
        >
        {currentStimulus && (
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-xl border border-slate-300 bg-white p-4 shadow-[0_4px_16px_rgba(31,78,121,0.08)]">
            {currentStimulus.title && (
              <h2 className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-extrabold text-black">
                {currentStimulus.title}
              </h2>
            )}
            <div className="space-y-3">
              {currentStimulus.assets.map((asset) => (
                <div key={asset.id} className="group relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={assetUrl(asset.url)}
                    alt={`Tài liệu cho câu ${currentStimulus.question_numbers.join(", ")}`}
                    className="mx-auto max-h-[calc(100vh-12rem)] max-w-full object-contain"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setDictionaryOpen(false);
                      setZoomedImage(assetUrl(asset.url));
                    }}
                    className="absolute right-2 top-2 rounded-lg border border-white/40 bg-[#1f4e79]/90 p-2 text-white opacity-0 shadow-lg transition group-hover:opacity-100"
                    title="Phóng to"
                  >
                    <Maximize2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div
            className={`min-h-0 overflow-y-auto overscroll-contain ${
            currentStimulus ? "flex-1" : "w-full max-w-5xl"
          }`}
        >
          <div className="space-y-4">
            {currentQuestions.map((question, questionIndex) => (
              <div
                id={`quiz-question-${question.number}`}
                key={question.number}
                onClick={() => {
                  if (!navigatorLocked) setCurrentQuestionIndex(questionIndex);
                }}
                className={`rounded-2xl transition ${
                  questionIndex === currentQuestionIndex
                    ? "ring-2 ring-[#1f4e79] ring-offset-2"
                    : ""
                }`}
              >
                <QuestionCard
                  index={data.questions.findIndex(
                    (item) => item.number === question.number,
                  )}
                  question={question}
                  selected={answers[question.number] ?? null}
                  showAnswer={false}
                  onSelect={(letter) => handleSelect(question.number, letter)}
                  flagged={flaggedQuestions.has(question.number)}
                  onToggleFlag={() => toggleQuestionFlag(question.number)}
                />
              </div>
            ))}
          </div>

        </div>
      </div>
      )}

      {!showingDirectionIntro && (
      <footer className="flex h-14 shrink-0 items-center justify-between border-t border-slate-300 bg-white px-4 shadow-[0_-3px_12px_rgba(31,78,121,0.06)] sm:px-6">
        <div className="flex items-center gap-2">
          <span className="hidden text-xs font-medium text-slate-400 lg:inline">
            {navigatorLocked
              ? "Phím 1–4: A–D · Listening tự chuyển theo audio"
              : "Phím 1–4: A–D · ←/→: điều hướng"}
          </span>
          <button
            type="button"
            onClick={toggleFullscreen}
            className="flex h-9 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-xs font-bold text-[#1f4e79] shadow-sm transition hover:border-[#1f4e79] hover:bg-slate-50"
            title={isFullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
            aria-label={isFullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
            aria-pressed={isFullscreen}
          >
            {isFullscreen ? (
              <Minimize2 className="h-4 w-4" />
            ) : (
              <Maximize2 className="h-4 w-4" />
            )}
            <span className="hidden sm:inline">
              {isFullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => toggleQuestionFlag(currentQ.number)}
            className={`flex h-9 items-center gap-2 rounded-lg border px-3 text-xs font-bold shadow-sm transition ${
              flaggedQuestions.has(currentQ.number)
                ? "border-amber-500 bg-amber-400 text-amber-950 hover:bg-amber-300"
                : "border-slate-300 bg-white text-slate-600 hover:border-amber-500 hover:bg-amber-50 hover:text-amber-800"
            }`}
            title={
              flaggedQuestions.has(currentQ.number)
                ? `Bỏ cờ câu ${currentQ.number}`
                : `Gắn cờ câu ${currentQ.number}`
            }
            aria-label={
              flaggedQuestions.has(currentQ.number)
                ? `Bỏ cờ câu ${currentQ.number}`
                : `Gắn cờ câu ${currentQ.number}`
            }
            aria-pressed={flaggedQuestions.has(currentQ.number)}
          >
            <Flag className="h-4 w-4" fill="currentColor" />
            <span className="hidden sm:inline">
              {flaggedQuestions.has(currentQ.number) ? "Đã gắn cờ" : "Gắn cờ"}
            </span>
          </button>

          {dictionaryAvailable && (
            <button
              type="button"
              onClick={() => setDictionaryOpen((current) => !current)}
              className={`flex h-9 w-9 items-center justify-center rounded-lg border shadow-md transition ${
                dictionaryOpen
                  ? "border-[#b58855] bg-[#b58855] text-white"
                  : "border-slate-300 bg-white text-[#1f4e79] hover:border-[#1f4e79] hover:bg-slate-50"
              }`}
              title={dictionaryOpen ? "Thu nhỏ từ điển" : "Mở từ điển"}
              aria-label={dictionaryOpen ? "Thu nhỏ từ điển" : "Mở từ điển"}
              aria-expanded={dictionaryOpen}
            >
              <BookOpen className="h-5 w-5" />
            </button>
          )}

          <button
            type="button"
            disabled={navigatorLocked}
            onClick={() => {
              setDictionaryOpen(false);
              setShowDrawer(!showDrawer);
            }}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#193e63] bg-[#1f4e79] text-white shadow-md hover:bg-[#173a5c] disabled:cursor-not-allowed disabled:opacity-40"
            title={navigatorLocked ? "Danh sách câu bị khóa trong phần Listening của Luyện thi" : "Danh sách câu hỏi"}
          >
            <List className="h-5 w-5" />
          </button>

          <button
            type="button"
            disabled={!canGoPrevious}
            onClick={() => navigateQuestion(-1)}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-300 bg-white text-[#1f4e79] shadow-md hover:border-[#1f4e79] hover:bg-slate-50 disabled:opacity-40"
            title="Câu trước"
          >
            <ChevronLeft className="h-5 w-5 stroke-[3]" />
          </button>

          <button
            type="button"
            disabled={!canGoNext}
            onClick={() => navigateQuestion(1)}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#193e63] bg-[#1f4e79] text-white shadow-md hover:bg-[#173a5c] disabled:opacity-40"
            title="Câu tiếp theo"
          >
            <ChevronRight className="h-5 w-5 stroke-[3]" />
          </button>
        </div>
      </footer>
      )}

      {dictionaryAvailable && (
        <DictionaryPanel
          open={dictionaryOpen}
          onMinimize={() => setDictionaryOpen(false)}
          lookupRequest={dictionaryLookupRequest}
        />
      )}

      {/* 5. QUESTION LIST DRAWER / MODAL */}
      {showDrawer && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 p-0 sm:items-center sm:p-4">
          <div className="flex max-h-[92dvh] w-full max-w-6xl flex-col rounded-t-2xl border border-slate-300 bg-white p-4 shadow-2xl sm:rounded-2xl sm:p-8">
            <div className="mb-5 flex items-center justify-between border-b border-slate-200 pb-4">
              <div>
                <h3 className="text-xl font-extrabold text-[#1f4e79]">
                  Question Navigator
                </h3>
                <p className="mt-1 text-sm text-slate-500">
                  Chọn nhanh câu hỏi theo từng Part của bài thi.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowDrawer(false)}
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 bg-white text-lg font-bold text-slate-500 shadow-sm hover:border-[#1f4e79] hover:text-[#1f4e79]"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 space-y-5 overflow-y-auto pr-2">
              {visibleNavigatorSections.map((part) => (
                <section
                  key={part.number}
                  className="rounded-xl border border-slate-300 bg-slate-50 p-4"
                >
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1f4e79] text-sm font-extrabold text-white shadow-sm">
                        {part.number}
                      </span>
                      <div>
                        <h4 className="text-sm font-extrabold text-[#1f4e79]">
                          Part {part.number} · {part.label}
                        </h4>
                        <p className="text-xs text-slate-500">
                          Questions {part.start}–{part.end}
                        </p>
                      </div>
                    </div>
                    <span className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-bold text-slate-600">
                      {
                        part.questions.filter((question) => answers[question.number])
                          .length
                      }
                      /{part.questions.length} đã làm
                    </span>
                  </div>
                  <div className="grid grid-cols-5 gap-2 sm:grid-cols-7 md:grid-cols-9 lg:grid-cols-12">
                    {part.questions.map((question) => {
                      const groupIndex = quizGroups.findIndex((group) =>
                        group.questions.some(
                          (item) => item.number === question.number,
                        ),
                      );
                      const questionIndex = quizGroups[groupIndex]?.questions.findIndex(
                        (item) => item.number === question.number,
                      ) ?? 0;
                      const isCurrent =
                        groupIndex === currentGroupIndex &&
                        questionIndex === currentQuestionIndex;
                      const isAnswered = Boolean(answers[question.number]);
                      const isFlagged = flaggedQuestions.has(question.number);
                      return (
                        <button
                          key={question.number}
                          type="button"
                          onClick={() => {
                            if (
                              listeningNavigationLocked &&
                              question.number <= 100
                            ) {
                              return;
                            }
                            setCurrentGroupIndex(groupIndex);
                            setCurrentQuestionIndex(questionIndex);
                            setShowDrawer(false);
                          }}
                          aria-label={`Câu ${question.number}${
                            isAnswered ? ", đã trả lời" : ", chưa trả lời"
                          }${isFlagged ? ", đã gắn cờ" : ""}${
                            isCurrent ? ", câu hiện tại" : ""
                          }`}
                          title={
                            isFlagged
                              ? `Câu ${question.number} · Đã gắn cờ`
                              : isAnswered
                                ? `Câu ${question.number} · Đã trả lời`
                                : `Câu ${question.number} · Chưa trả lời`
                          }
                          className={`flex h-11 items-center justify-center rounded-lg border text-sm font-extrabold transition-all ${
                            isFlagged
                              ? "border-amber-600 bg-amber-400 text-amber-950 shadow-sm hover:bg-amber-300"
                              : isAnswered
                                ? "border-emerald-700 bg-emerald-600 text-white shadow-sm hover:bg-emerald-500"
                                : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79] hover:text-[#1f4e79]"
                          } ${
                            isCurrent
                              ? "ring-2 ring-[#1f4e79] ring-offset-2"
                              : ""
                          }`}
                        >
                          {question.number}
                        </button>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
            <div className="mt-5 flex flex-wrap gap-4 border-t border-slate-200 pt-4 text-xs font-semibold text-slate-600">
              <span className="flex items-center gap-2">
                <i className="h-3 w-3 rounded-sm border border-[#1f4e79] bg-white ring-2 ring-[#1f4e79] ring-offset-1" />{" "}
                Câu hiện tại
              </span>
              <span className="flex items-center gap-2">
                <i className="h-3 w-3 rounded-sm border border-emerald-700 bg-emerald-600" />{" "}
                Đã trả lời
              </span>
              <span className="flex items-center gap-2">
                <i className="h-3 w-3 rounded-sm border border-amber-600 bg-amber-400" />{" "}
                Đã gắn cờ
              </span>
              <span className="flex items-center gap-2">
                <i className="h-3 w-3 rounded-sm border border-slate-300 bg-white" />{" "}
                Chưa trả lời
              </span>
            </div>
          </div>
        </div>
      )}

      {zoomedImage && (
        <button
          type="button"
          onClick={() => setZoomedImage(null)}
          className="fixed inset-0 z-[80] flex cursor-zoom-out items-center justify-center bg-slate-950/90 p-4"
          title="Đóng ảnh"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={zoomedImage}
            alt="Ảnh phóng to"
            className="max-h-full max-w-full object-contain [touch-action:pinch-zoom]"
          />
        </button>
      )}
    </div>
  );
}
