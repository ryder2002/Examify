"use client";

import { use, useEffect, useState, useRef, useMemo } from "react";
import Link from "next/link";
import {
  AlertCircle,
  CheckCircle2,
  XCircle,
  Check,
  X,
  Filter,
  Clock,
  FileText,
  Headphones,
  Loader2,
  Play,
  Pause,
  Send,
  User,
  Volume2,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Award,
  Flag,
  List,
  Maximize2,
  Minimize2,
} from "lucide-react";
import Image from "next/image";
import { assetUrl } from "@/lib/api";
import type { AudioRef } from "@/lib/utils";
import AudioWavePlayer from "@/components/AudioWavePlayer";
import Part1DirectionsView from "@/components/Part1DirectionsView";

type ExamPayload = {
  id: string;
  title: string;
  category?: string;
  duration_minutes: number;
  question_count: number;
  questions: Array<{
    number: number;
    part: string;
    text: string;
    options: Record<string, string>;
    correct?: string | null;
    stimulus_id?: string;
    audio_url?: string;
    audio?: { url: string; part?: string; filename?: string };
  }>;
  stimuli: Array<{
    id: string;
    source_id: string;
    title: string;
    kind: string;
    assets: Array<{ url: string }>;
  }>;
  audios: Array<{
    id: string;
    url: string;
    filename: string;
    part?: string;
    scope?: "full" | "part" | "question" | "group";
    question_numbers?: number[];
    group_id?: string | null;
  }>;
};

const NAV_PARTS = [
  { number: 1, label: "Photographs", start: 1, end: 6 },
  { number: 2, label: "Question-Response", start: 7, end: 31 },
  { number: 3, label: "Conversations", start: 32, end: 70 },
  { number: 4, label: "Talks", start: 71, end: 100 },
  { number: 5, label: "Incomplete Sentences", start: 101, end: 130 },
  { number: 6, label: "Text Completion", start: 131, end: 146 },
  { number: 7, label: "Reading Comprehension", start: 147, end: 200 },
];

export default function PublicTestPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = use(params);

  // Core States
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exam, setExam] = useState<ExamPayload | null>(null);
  const [submissionId, setSubmissionId] = useState<string | null>(null);
  const [submissionToken, setSubmissionToken] = useState<string | null>(null);

  // Student Form State
  const [studentName, setStudentName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // Exam Taking States
  const [phase, setPhase] = useState<"form" | "quiz" | "result">("form");
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [timeLeftSeconds, setTimeLeftSeconds] = useState(20 * 60);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any | null>(null);
  const [reviewFilter, setReviewFilter] = useState<"all" | "correct" | "incorrect" | "unanswered">("all");

  // UI Interactive States
  const [flaggedQuestions, setFlaggedQuestions] = useState<Set<number>>(new Set());
  const [showDrawer, setShowDrawer] = useState(false);
  const [zoomedImage, setZoomedImage] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Fetch Public Exam Metadata
  useEffect(() => {
    async function loadTest() {
      try {
        setLoading(true);
        const res = await fetch(`/api/v1/public-tests/${code}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || "Link bài thi không hợp lệ hoặc đã bị khóa");
        }
        const data = await res.json();
        setExam(data.exam);
        const durationMins = data.exam?.duration_minutes && data.exam?.duration_minutes !== 75 ? data.exam.duration_minutes : 20;
        setTimeLeftSeconds(durationMins * 60);
      } catch (err: any) {
        setError(err.message || "Không thể tải bài thi");
      } finally {
        setLoading(false);
      }
    }
    void loadTest();
  }, [code]);

  // Restore saved question index
  useEffect(() => {
    if (typeof window === "undefined") return;
    const savedIdx = sessionStorage.getItem(`public-test-index-${code}`);
    if (savedIdx !== null) {
      const idx = Number(savedIdx);
      if (Number.isInteger(idx) && idx >= 0) {
        setCurrentQuestionIndex(idx);
      }
    }
  }, [code]);

  // Persist current question index
  useEffect(() => {
    if (phase === "quiz") {
      sessionStorage.setItem(`public-test-index-${code}`, String(currentQuestionIndex));
    }
  }, [code, currentQuestionIndex, phase]);

  // Countdown Timer
  useEffect(() => {
    if (phase !== "quiz") return;
    const timer = setInterval(() => {
      setTimeLeftSeconds((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          void handleSubmit();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [phase]);

  // Start test action
  const handleStartTest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!studentName.trim()) {
      setFormError("Vui lòng nhập họ và tên của bạn");
      return;
    }
    setFormError(null);
    setStarting(true);

    try {
      const res = await fetch(`/api/v1/public-tests/${code}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          student_name: studentName.trim(),
          phone: "",
          email: "",
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Không thể bắt đầu làm bài");
      }

      const data = await res.json();
      setSubmissionId(data.submission_id);
      setSubmissionToken(data.submission_token);
      setExam(data.exam);

      // Always reset question index, answers, flags, and storage for a fresh test attempt
      setCurrentQuestionIndex(0);
      setAnswers({});
      setFlaggedQuestions(new Set());
      setResult(null);
      if (typeof window !== "undefined") {
        sessionStorage.removeItem(`public-test-index-${code}`);
      }
      const durationMins = data.exam?.duration_minutes && data.exam?.duration_minutes !== 75 ? data.exam.duration_minutes : 20;
      setTimeLeftSeconds(durationMins * 60);

      setPhase("quiz");
    } catch (err: any) {
      setFormError(err.message || "Lỗi khi tạo bài làm");
    } finally {
      setStarting(false);
    }
  };

  // Submit test
  const handleSubmit = async () => {
    if (!submissionId || !submissionToken || submitting) return;
    setSubmitting(true);

    try {
      const durationMins = exam?.duration_minutes && exam?.duration_minutes !== 75 ? exam.duration_minutes : 20;
      const totalTime = durationMins * 60;
      const spentSeconds = Math.max(1, totalTime - timeLeftSeconds);

      const res = await fetch(
        `/api/v1/public-tests/submissions/${submissionId}/submit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            submission_token: submissionToken,
            answers,
            time_spent_seconds: spentSeconds,
          }),
        }
      );

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Không thể nộp bài");
      }

      const data = await res.json();
      setResult(data);
      if (typeof window !== "undefined") {
        sessionStorage.removeItem(`public-test-index-${code}`);
      }
      setSubmissionToken(null);
      setPhase("result");
    } catch (err: any) {
      alert(err.message || "Lỗi khi nộp bài");
    } finally {
      setSubmitting(false);
    }
  };

  // Toggle Flag
  const toggleQuestionFlag = (qNum: number) => {
    setFlaggedQuestions((prev) => {
      const next = new Set(prev);
      if (next.has(qNum)) next.delete(qNum);
      else next.add(qNum);
      return next;
    });
  };

  // Fullscreen toggle
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        setIsFullscreen(false);
      } else {
        await document.documentElement.requestFullscreen();
        setIsFullscreen(true);
      }
    } catch {}
  };

  // Helpers
  const formatTimer = (totalSecs: number) => {
    const hours = Math.floor(totalSecs / 3600);
    const mins = Math.floor((totalSecs % 3600) / 60);
    const secs = totalSecs % 60;
    return `${hours.toString().padStart(2, "0")}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  const currentQ = exam?.questions[currentQuestionIndex];
  const totalQ = exam?.questions.length || 0;
  const answeredCount = Object.keys(answers).length;

  // Determine section type
  const isListeningSection = currentQ ? (currentQ.number <= 100 || /^part_[1-4]$/i.test(currentQ.part || "")) : true;
  const examLabel = isListeningSection ? "Listening" : "Reading";

  // Navigation locking rules
  const navigatorLocked = isListeningSection;
  const canGoPrevious = !isListeningSection && currentQuestionIndex > 0;
  const canGoNext = !isListeningSection && currentQuestionIndex < totalQ - 1;

  const navigateQuestion = (direction: -1 | 1) => {
    const nextIdx = currentQuestionIndex + direction;
    if (nextIdx >= 0 && nextIdx < totalQ) {
      setCurrentQuestionIndex(nextIdx);
    }
  };

  // Select Option
  const handleSelectOption = (qNum: number, optionLetter: string) => {
    setAnswers((prev) => ({
      ...prev,
      [String(qNum)]: optionLetter,
    }));
  };

  // Keyboard navigation
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (phase !== "quiz" || !currentQ || showDrawer || zoomedImage) return;
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target?.tagName || "")
      ) {
        return;
      }
      if (["1", "2", "3", "4"].includes(event.key)) {
        const letter = ["A", "B", "C", "D"][Number(event.key) - 1];
        if (currentQ.options && currentQ.options[letter] !== undefined) {
          event.preventDefault();
          handleSelectOption(currentQ.number, letter);
        }
        return;
      }
      if (event.key === "ArrowRight" && canGoNext) {
        event.preventDefault();
        navigateQuestion(1);
        return;
      }
      if (event.key === "ArrowLeft" && canGoPrevious) {
        event.preventDefault();
        navigateQuestion(-1);
        return;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [phase, currentQ, showDrawer, zoomedImage, canGoNext, canGoPrevious]);

  // Audio Resolution for Current Question
  const currentAudio = useMemo((): AudioRef | null => {
    if (!exam || !currentQ) return null;
    const qNum = currentQ.number;
    const audios = exam.audios || [];

    // 1. Direct question audio URL
    if (currentQ.audio_url) {
      return { id: `q-${qNum}`, url: currentQ.audio_url, filename: `q${qNum}.mp3`, part: `q${qNum}` };
    }
    if (currentQ.audio?.url) {
      return { id: `q-${qNum}`, url: currentQ.audio.url, filename: `q${qNum}.mp3`, part: `q${qNum}` };
    }

    if (audios.length === 0) return null;

    // 2. Structured question/group audio generated by the FFmpeg auto-cutter.
    const structured = audios.find((audio) =>
      audio.question_numbers?.includes(qNum),
    );
    if (structured?.url) return structured as AudioRef;

    // 3. Backward-compatible filename/part matching.
    const specific = audios.find((a: any) => {
      const p = String(a.part || "").toLowerCase();
      const fn = String(a.filename || "").toLowerCase();
      return p === `q${qNum}` || p === String(qNum) || p === `question_${qNum}` || fn.includes(`q${qNum}`) || fn.includes(`question_${qNum}`);
    });
    if (specific?.url) return specific as AudioRef;

    // 4. Audio matching question index if 1:1 mapped
    if (audios.length === exam.questions.length && audios[currentQuestionIndex]?.url) {
      return audios[currentQuestionIndex] as AudioRef;
    }

    // 5. Part audio (e.g. part_2)
    const partNum = currentQ.part ? currentQ.part.replace(/[^0-9]/g, "") : "2";
    const partAudio = audios.find((a: any) => a.part === `part_${partNum}`);
    if (partAudio?.url) return partAudio as AudioRef;

    // 6. Full audio
    const fullAudio = audios.find((a: any) => a.part === "full");
    if (fullAudio?.url) return fullAudio as AudioRef;

    // Fallback to first audio
    return audios[0] as AudioRef;
  }, [exam, currentQ, currentQuestionIndex]);

  // Audio Ended Handler: Move to next question automatically!
  const handleAudioEnded = () => {
    if (isListeningSection && currentQuestionIndex < totalQ - 1) {
      setCurrentQuestionIndex((prev) => prev + 1);
    }
  };

  // Find stimulus for current question
  const currentStimulus = exam?.stimuli?.find(
    (s) => s.id === currentQ?.stimulus_id || s.source_id === currentQ?.stimulus_id
  );

  // Visible sections for Drawer
  const navigatorSections = useMemo(() => {
    if (!exam) return [];
    return NAV_PARTS.map((part) => ({
      ...part,
      questions: exam.questions.filter(
        (q) => q.number >= part.start && q.number <= part.end
      ),
    })).filter((part) => part.questions.length > 0);
  }, [exam]);

  if (loading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 p-4">
        <Loader2 className="h-10 w-10 animate-spin text-[#1f4e79]" />
        <p className="mt-4 text-sm font-bold text-slate-600">Đang nạp dữ liệu bài thi...</p>
      </div>
    );
  }

  if (error || !exam) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 p-4">
        <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-xl text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-red-50 text-red-500">
            <AlertCircle className="h-8 w-8" />
          </div>
          <h2 className="mt-4 text-xl font-black text-slate-900">Không Tìm Thấy Bài Thi</h2>
          <p className="mt-2 text-sm text-slate-600">{error || "Đường link bài thi này không tồn tại hoặc đã hết hạn."}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-slate-50 font-sans text-slate-900">
      {/* PHASE 1: STUDENT ENTRANCE FORM */}
      {phase === "form" && (
        <div className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
          <div className="w-full max-w-lg rounded-3xl bg-white p-8 shadow-2xl border border-slate-200/80">
            <div className="text-center">
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-50 text-[#1f4e79] mb-3 shadow-inner">
                <Sparkles className="h-8 w-8" />
              </div>
              <span className="rounded-full bg-brand-50 px-3.5 py-1 text-xs font-extrabold text-[#1f4e79] border border-brand-200">
                Bài Test Đầu Vào TOEIC
              </span>
              <h2 className="mt-3 text-2xl font-black tracking-tight text-slate-900">
                {exam.title}
              </h2>
              <div className="mt-2 flex items-center justify-center gap-4 text-xs font-bold text-slate-500">
                <span>{exam.question_count || exam.questions?.length || 25} câu hỏi</span>
                <span>•</span>
                <span>{exam.duration_minutes && exam.duration_minutes !== 75 ? exam.duration_minutes : 20} phút</span>
                <span>•</span>
                <span className="text-brand-600">Miễn phí</span>
              </div>
            </div>

            <form onSubmit={handleStartTest} className="mt-6 space-y-4">
              {formError && (
                <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs font-semibold text-red-700 flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {formError}
                </div>
              )}

              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1">
                  Họ và tên học viên <span className="text-red-500">*</span>
                </label>
                <div className="relative">
                  <User className="absolute left-3.5 top-3 h-4 w-4 text-slate-400" />
                  <input
                    type="text"
                    required
                    placeholder="Ví dụ: Nguyễn Văn A"
                    value={studentName}
                    onChange={(e) => setStudentName(e.target.value)}
                    className="w-full rounded-xl border border-slate-300 pl-10 pr-4 py-2.5 text-sm font-medium text-slate-900 focus:border-[#1f4e79] focus:outline-none focus:ring-2 focus:ring-[#1f4e79]/20"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={starting}
                className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-[#1f4e79] py-3.5 text-sm font-extrabold text-white shadow-lg hover:bg-[#1e3a5f] active:scale-98 transition-all"
              >
                {starting ? (
                  <>
                    <Loader2 className="h-5 w-5 animate-spin" /> Đang chuẩn bị bài thi...
                  </>
                ) : (
                  <>Bắt Đầu Làm Bài</>
                )}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* PHASE 2: QUIZ INTERACTION - MATCHING QUIZ INTERFACE */}
      {phase === "quiz" && currentQ && (
        <>
          {/* HEADER BAR */}
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
                Bài Test
              </span>
            </div>

            <div className="flex items-center gap-3">
              <div className="rounded-md border border-white/50 bg-white px-3 py-1 text-sm font-bold text-[#1f4e79] shadow-sm">
                {answeredCount}/{totalQ}
              </div>

              <div className="flex items-center gap-1.5 rounded-md border border-white/35 bg-white/10 px-3 py-1 text-sm font-semibold text-white shadow-sm">
                <Clock className="h-4 w-4" />
                <span>{formatTimer(timeLeftSeconds)}</span>
              </div>

              <button
                onClick={() => {
                  if (confirm("Bạn có chắc chắn muốn nộp bài ngay bây giờ?")) {
                    void handleSubmit();
                  }
                }}
                disabled={submitting}
                className="rounded-md border border-white bg-white px-5 py-2 text-sm font-bold text-[#1f4e79] shadow-md transition hover:bg-slate-100 disabled:opacity-70"
              >
                {submitting ? "Đang nộp…" : "Submit"}
              </button>
            </div>
          </header>

          {/* SUB-HEADER / INSTRUCTION BAR */}
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-300 bg-white px-4 py-2.5 text-base font-extrabold shadow-sm sm:px-6">
            <div className="text-black font-extrabold text-base">
              {currentStimulus
                ? currentQ.number <= 100
                  ? "Look at the image and select your answer."
                  : "Read the document and answer the questions."
                : "Select the best answer."}
            </div>

            {/* Hidden Background Audio for Listening */}
            {isListeningSection && currentAudio && (
              <audio
                key={`${currentAudio.id || currentAudio.url}-${currentQ.number}`}
                src={assetUrl(currentAudio.url)}
                autoPlay
                onEnded={handleAudioEnded}
                className="hidden"
              />
            )}

            <div className="text-black font-extrabold text-base">
              Câu hỏi
            </div>
          </div>

          {/* MAIN WORKSPACE */}
          <div
            className={`flex flex-1 flex-col gap-3 overflow-hidden p-3 sm:gap-5 sm:p-6 ${
              currentStimulus ? "lg:flex-row" : "items-center"
            }`}
          >
            {/* Stimulus Panel (Images / Texts) */}
            {currentStimulus && (
              <div className="flex-1 overflow-y-auto rounded-xl border border-slate-300 bg-white p-4 shadow-[0_4px_16px_rgba(31,78,121,0.08)]">
                {currentStimulus.title && (
                  <h2 className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-extrabold text-black">
                    {currentStimulus.title}
                  </h2>
                )}
                <div className="space-y-3">
                  {currentStimulus.assets?.map((asset, idx) => (
                    <div key={idx} className="group relative">
                      <img
                        src={assetUrl(asset.url)}
                        alt={`Tài liệu cho câu ${currentQ.number}`}
                        className="mx-auto max-h-[calc(100vh-12rem)] max-w-full object-contain"
                      />
                      <button
                        type="button"
                        onClick={() => setZoomedImage(assetUrl(asset.url))}
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

            {/* Question Panel */}
            <div
              className={`overflow-y-auto ${
                currentStimulus ? "flex-1" : "w-full max-w-5xl"
              }`}
            >
              <div className="rounded-2xl border border-slate-300 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between border-b border-slate-200 pb-3 mb-4">
                  <div className="flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#1f4e79] text-sm font-black text-white shadow-sm">
                      {currentQ.number}
                    </span>
                    <span className="text-sm font-extrabold text-slate-700">
                      {currentQ.part || `Part ${currentQ.number <= 6 ? 1 : currentQ.number <= 31 ? 2 : currentQ.number <= 70 ? 3 : currentQ.number <= 100 ? 4 : 5}`}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => toggleQuestionFlag(currentQ.number)}
                    className={`flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs font-bold transition ${
                      flaggedQuestions.has(currentQ.number)
                        ? "border-amber-500 bg-amber-400 text-amber-950"
                        : "border-slate-300 bg-white text-slate-600 hover:border-amber-500"
                    }`}
                  >
                    <Flag className="h-3.5 w-3.5" fill="currentColor" />
                    <span>{flaggedQuestions.has(currentQ.number) ? "Đã gắn cờ" : "Gắn cờ"}</span>
                  </button>
                </div>

                <div className="text-base font-extrabold text-slate-900 mb-6 leading-relaxed">
                  {currentQ.text || `${currentQ.number}. Mark your answer on your answer sheet.`}
                </div>

                {/* Options List */}
                <div className="space-y-3">
                  {Object.entries(currentQ.options || { A: "", B: "", C: "" }).map(([letter, val]) => {
                    const isSelected = answers[String(currentQ.number)] === letter;
                    return (
                      <button
                        key={letter}
                        type="button"
                        onClick={() => handleSelectOption(currentQ.number, letter)}
                        className={`flex w-full items-center gap-4 rounded-xl border p-4 text-left text-sm font-semibold transition-all ${
                          isSelected
                            ? "border-[#1f4e79] bg-brand-50/60 text-[#1f4e79] ring-2 ring-[#1f4e79] shadow-sm"
                            : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                        }`}
                      >
                        <div
                          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-bold text-xs ${
                            isSelected
                              ? "bg-[#1f4e79] text-white"
                              : "bg-slate-100 text-slate-600 border border-slate-300"
                          }`}
                        >
                          {letter}
                        </div>
                        <div className="flex-1 text-sm font-extrabold text-slate-800">
                          {val || letter}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* FOOTER BAR */}
          <footer className="flex h-14 shrink-0 items-center justify-between border-t border-slate-300 bg-white px-4 shadow-[0_-3px_12px_rgba(31,78,121,0.06)] sm:px-6">
            <div className="flex items-center gap-2">
              <span className="hidden text-xs font-medium text-slate-400 lg:inline">
                Phím 1–4: A–D · ←/→: điều hướng
              </span>
              <button
                type="button"
                onClick={toggleFullscreen}
                className="flex h-9 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-xs font-bold text-[#1f4e79] shadow-sm transition hover:border-[#1f4e79] hover:bg-slate-50"
                title={isFullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
              >
                {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
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
                    : "border-slate-300 bg-white text-slate-600 hover:border-amber-500 hover:bg-amber-50"
                }`}
              >
                <Flag className="h-4 w-4" fill="currentColor" />
                <span className="hidden sm:inline">
                  {flaggedQuestions.has(currentQ.number) ? "Đã gắn cờ" : "Gắn cờ"}
                </span>
              </button>

              <button
                type="button"
                disabled={navigatorLocked}
                onClick={() => setShowDrawer(!showDrawer)}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#193e63] bg-[#1f4e79] text-white shadow-md hover:bg-[#173a5c] disabled:cursor-not-allowed disabled:opacity-40"
                title={navigatorLocked ? "Danh sách câu bị khóa trong phần Listening" : "Danh sách câu hỏi"}
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

          {/* QUESTION DRAWER MODAL */}
          {showDrawer && (
            <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 p-0 sm:items-center sm:p-4">
              <div className="flex max-h-[92dvh] w-full max-w-6xl flex-col rounded-t-2xl border border-slate-300 bg-white p-4 shadow-2xl sm:rounded-2xl sm:p-8">
                <div className="mb-5 flex items-center justify-between border-b border-slate-200 pb-4">
                  <div>
                    <h3 className="text-xl font-extrabold text-[#1f4e79]">Question Navigator</h3>
                    <p className="mt-1 text-sm text-slate-500">Chọn nhanh câu hỏi theo từng Part của bài thi.</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowDrawer(false)}
                    className="flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 bg-white text-lg font-bold text-slate-500 hover:border-[#1f4e79] hover:text-[#1f4e79]"
                  >
                    ✕
                  </button>
                </div>
                <div className="flex-1 space-y-5 overflow-y-auto pr-2">
                  {navigatorSections.map((part) => (
                    <section key={part.number} className="rounded-xl border border-slate-300 bg-slate-50 p-4">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-3">
                          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1f4e79] text-sm font-extrabold text-white">
                            {part.number}
                          </span>
                          <div>
                            <h4 className="text-sm font-extrabold text-[#1f4e79]">
                              Part {part.number} · {part.label}
                            </h4>
                            <p className="text-xs text-slate-500">Questions {part.start}–{part.end}</p>
                          </div>
                        </div>
                        <span className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-bold text-slate-600">
                          {part.questions.filter((q) => answers[String(q.number)]).length}/{part.questions.length} đã làm
                        </span>
                      </div>
                      <div className="grid grid-cols-5 gap-2 sm:grid-cols-7 md:grid-cols-9 lg:grid-cols-12">
                        {part.questions.map((q) => {
                          const qIdx = exam.questions.findIndex((item) => item.number === q.number);
                          const isCurrent = qIdx === currentQuestionIndex;
                          const isAnswered = Boolean(answers[String(q.number)]);
                          const isFlagged = flaggedQuestions.has(q.number);
                          const isListeningQ = q.number <= 100;
                          return (
                            <button
                              key={q.number}
                              type="button"
                              disabled={isListeningQ}
                              onClick={() => {
                                setCurrentQuestionIndex(qIdx);
                                setShowDrawer(false);
                              }}
                              className={`flex h-11 items-center justify-center rounded-lg border text-sm font-extrabold transition-all ${
                                isFlagged
                                  ? "border-amber-600 bg-amber-400 text-amber-950 shadow-sm"
                                  : isAnswered
                                  ? "border-emerald-700 bg-emerald-600 text-white shadow-sm"
                                  : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79] hover:text-[#1f4e79]"
                              } ${isCurrent ? "ring-2 ring-[#1f4e79] ring-offset-2" : ""} disabled:opacity-50 disabled:cursor-not-allowed`}
                            >
                              {q.number}
                            </button>
                          );
                        })}
                      </div>
                    </section>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ZOOM IMAGE MODAL */}
          {zoomedImage && (
            <button
              type="button"
              onClick={() => setZoomedImage(null)}
              className="fixed inset-0 z-[80] flex cursor-zoom-out items-center justify-center bg-slate-950/90 p-4"
              title="Đóng ảnh"
            >
              <img src={zoomedImage} alt="Zoomed" className="max-h-full max-w-full object-contain" />
            </button>
          )}
        </>
      )}

      {/* PHASE 3: RESULT SCREEN */}
      {phase === "result" && result && (() => {
        const qCount = result.question_count || exam.questions.length || 1;
        const totalCorrect = result.total_correct ?? 0;

        const detailedMap: Record<string, { selected: string; correct: string; isCorrect: boolean }> = {};
        let cCount = 0;
        let iCount = 0;
        let uCount = 0;

        exam.questions.forEach((q) => {
          const qNum = String(q.number);
          const backendItem = result.answers?.[qNum];
          const selected = (backendItem?.selected || answers[qNum] || "").trim().toUpperCase();
          const correct = (backendItem?.correct || q.correct || "").trim().toUpperCase();
          const isRight = backendItem?.is_correct ?? (selected && selected === correct);

          if (isRight) cCount++;
          else if (selected) iCount++;
          else uCount++;

          detailedMap[qNum] = { selected, correct, isCorrect: isRight };
        });

        const filteredQuestions = exam.questions.filter((q) => {
          const info = detailedMap[String(q.number)];
          if (reviewFilter === "correct") return info.isCorrect;
          if (reviewFilter === "incorrect") return Boolean(info.selected && !info.isCorrect);
          if (reviewFilter === "unanswered") return !info.selected;
          return true;
        });

        return (
          <div className="mx-auto w-full max-w-7xl p-4 sm:p-6 space-y-6 overflow-y-auto">
            {/* Top Summary Card */}
            <div className="rounded-3xl bg-white p-6 sm:p-8 shadow-xl border border-slate-200/80">
              <div className="text-center">
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 mb-3 shadow-inner">
                  <Award className="h-9 w-9" />
                </div>
                <h2 className="text-2xl sm:text-3xl font-black text-slate-900">Hoàn Thành Bài Thi!</h2>
                <p className="mt-1 text-sm text-slate-500 font-bold">Thí sinh: {studentName}</p>
              </div>

              {(() => {
                const isPassed = totalCorrect >= Math.ceil(qCount * 0.48);
                const duckImgSrc = isPassed ? "/duck2.png" : "/duck1.png";
                return (
                  <div className="mt-6 flex flex-col sm:flex-row items-center justify-center gap-6 sm:gap-10">
                    {/* Duck image on Left */}
                    <div className="w-full max-w-[280px] sm:max-w-[340px] md:max-w-[380px] shrink-0 flex items-center justify-center p-2">
                      <img
                        src={duckImgSrc}
                        alt={isPassed ? "Đạt kết quả tốt" : "Cố gắng hơn ở lần sau"}
                        className="w-full h-auto max-h-[340px] object-contain transition-all"
                      />
                    </div>

                    {/* Score box on Right */}
                    <div className="w-full max-w-xl flex-1 rounded-2xl bg-brand-50 p-6 border border-brand-200 text-center shadow-sm">
                      <span className="text-xs font-extrabold text-brand-800 uppercase tracking-wider">
                        Số câu trả lời đúng
                      </span>
                      <p className="mt-1 text-5xl font-black text-[#1f4e79]">
                        {totalCorrect} / {qCount}
                      </p>
                      <p className="mt-2 text-xs font-bold text-brand-600">
                        Tỷ lệ chính xác: {Math.round((totalCorrect / qCount) * 100)}%
                      </p>
                    </div>
                  </div>
                );
              })()}

              {/* Breakdown by Part */}
              {result.part_breakdown && Object.keys(result.part_breakdown).length > 0 && (
                <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <h4 className="text-xs font-extrabold text-slate-700 uppercase tracking-wider mb-3">
                    Kết quả chi tiết từng Part
                  </h4>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                    {Object.entries(result.part_breakdown).map(([partName, stat]: any) => (
                      <div key={partName} className="flex flex-col items-center justify-center rounded-xl bg-white p-3 font-bold border border-slate-200 shadow-sm text-center">
                        <span className="text-slate-500 text-[11px]">{partName}</span>
                        <span className="text-brand-700 text-sm font-black mt-0.5">
                          {stat.correct} / {stat.total}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-3 text-center text-xs font-semibold text-amber-800">
                ✓ Kết quả làm bài đã được gửi về hệ thống giáo viên. Dưới đây là chi tiết đáp án từng câu hỏi!
              </div>

              <div className="mt-6 flex justify-center">
                <button
                  type="button"
                  onClick={() => {
                    setStudentName("");
                    setSubmissionId(null);
                    setResult(null);
                    setAnswers({});
                    setFlaggedQuestions(new Set());
                    setCurrentQuestionIndex(0);
                    if (typeof window !== "undefined") {
                      sessionStorage.removeItem(`public-test-index-${code}`);
                    }
                    setPhase("form");
                  }}
                  className="rounded-xl bg-[#1f4e79] px-6 py-3 text-xs font-black text-white shadow-md hover:bg-[#16324f] transition-all"
                >
                  Làm Bài Thi Mới / Thí Sinh Khác
                </button>
              </div>
            </div>

            {/* DETAILED ANSWER REVIEW SECTION */}
            <div className="rounded-3xl bg-white p-6 sm:p-8 shadow-xl border border-slate-200/80 space-y-6">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-100 pb-4">
                <div>
                  <h3 className="text-xl font-black text-slate-900">Chi Tiết Bài Làm</h3>
                  <p className="text-xs font-bold text-slate-500">Đối chiếu đáp án đã chọn với đáp án chính xác</p>
                </div>

                {/* Filter Tabs */}
                <div className="flex items-center gap-1 rounded-xl bg-slate-100 p-1 text-xs font-bold">
                  <button
                    onClick={() => setReviewFilter("all")}
                    className={`rounded-lg px-3 py-1.5 transition-all ${
                      reviewFilter === "all" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-900"
                    }`}
                  >
                    Tất cả ({exam.questions.length})
                  </button>
                  <button
                    onClick={() => setReviewFilter("correct")}
                    className={`rounded-lg px-3 py-1.5 transition-all ${
                      reviewFilter === "correct" ? "bg-emerald-600 text-white shadow-sm" : "text-slate-500 hover:text-emerald-700"
                    }`}
                  >
                    ✓ Đúng ({cCount})
                  </button>
                  <button
                    onClick={() => setReviewFilter("incorrect")}
                    className={`rounded-lg px-3 py-1.5 transition-all ${
                      reviewFilter === "incorrect" ? "bg-rose-600 text-white shadow-sm" : "text-slate-500 hover:text-rose-700"
                    }`}
                  >
                    ✕ Sai ({iCount})
                  </button>
                  <button
                    onClick={() => setReviewFilter("unanswered")}
                    className={`rounded-lg px-3 py-1.5 transition-all ${
                      reviewFilter === "unanswered" ? "bg-amber-500 text-white shadow-sm" : "text-slate-500 hover:text-amber-700"
                    }`}
                  >
                    Bỏ qua ({uCount})
                  </button>
                </div>
              </div>

              {/* Quick Jump List Grid */}
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <span className="text-xs font-bold text-slate-500 block mb-2">Danh mục câu hỏi (Bấm để nhảy nhanh):</span>
                <div className="grid grid-cols-6 sm:grid-cols-10 md:grid-cols-12 gap-1.5">
                  {exam.questions.map((q) => {
                    const info = detailedMap[String(q.number)];
                    const isRight = info?.isCorrect;
                    const isSelected = Boolean(info?.selected);

                    let bgClass = "bg-amber-100 text-amber-800 border-amber-300";
                    if (isRight) bgClass = "bg-emerald-600 text-white border-emerald-700";
                    else if (isSelected) bgClass = "bg-rose-600 text-white border-rose-700";

                    return (
                      <a
                        key={q.number}
                        href={`#review-q-${q.number}`}
                        className={`flex h-8 w-full items-center justify-center rounded-lg border text-xs font-black shadow-2xs hover:scale-105 transition-transform ${bgClass}`}
                      >
                        {q.number}
                      </a>
                    );
                  })}
                </div>
              </div>

              {/* Question Review List */}
              <div className="space-y-6">
                {filteredQuestions.map((q) => {
                  const info = detailedMap[String(q.number)];
                  const userSel = info?.selected;
                  const correctAns = info?.correct;
                  const isRight = info?.isCorrect;

                  return (
                    <div
                      id={`review-q-${q.number}`}
                      key={q.number}
                      className={`rounded-2xl border p-5 sm:p-6 transition-all ${
                        isRight
                          ? "border-emerald-200 bg-emerald-50/20"
                          : userSel
                          ? "border-rose-200 bg-rose-50/20"
                          : "border-amber-200 bg-amber-50/20"
                      }`}
                    >
                      <div className="flex items-center justify-between border-b border-slate-200/80 pb-3 mb-4">
                        <div className="flex items-center gap-2">
                          <span className="font-extrabold text-sm text-slate-900">
                            Câu {q.number} ({q.part})
                          </span>
                        </div>
                        <div>
                          {isRight ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-3 py-1 text-xs font-black text-emerald-800 border border-emerald-300">
                              <CheckCircle2 className="h-3.5 w-3.5" /> Chính xác
                            </span>
                          ) : userSel ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-3 py-1 text-xs font-black text-rose-800 border border-rose-300">
                              <XCircle className="h-3.5 w-3.5" /> Trả lời sai
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-3 py-1 text-xs font-black text-amber-800 border border-amber-300">
                              Chưa trả lời
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Question Text */}
                      <p className="text-base font-extrabold text-slate-900 mb-4">
                        {q.text || `Câu hỏi số ${q.number}`}
                      </p>

                      {/* Options breakdown */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                        {Object.entries(q.options || {}).map(([optKey, optVal]) => {
                          const isUserChoice = userSel === optKey;
                          const isCorrectChoice = correctAns === optKey;

                          let styleClass = "border-slate-200 bg-white text-slate-700";
                          if (isCorrectChoice) {
                            styleClass = "border-emerald-500 bg-emerald-100/80 text-emerald-900 font-extrabold ring-2 ring-emerald-500";
                          } else if (isUserChoice && !isRight) {
                            styleClass = "border-rose-400 bg-rose-100/80 text-rose-900 font-extrabold line-through opacity-90";
                          }

                          return (
                            <div
                              key={optKey}
                              className={`flex items-center justify-between rounded-xl border p-3.5 text-xs transition-all ${styleClass}`}
                            >
                              <div className="flex items-center gap-2.5">
                                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-bold text-xs ${
                                  isCorrectChoice
                                    ? "bg-emerald-600 text-white"
                                    : isUserChoice
                                    ? "bg-rose-600 text-white"
                                    : "bg-slate-100 text-slate-600"
                                }`}>
                                  {optKey}
                                </span>
                                <span className="font-bold">{optVal || optKey}</span>
                              </div>
                              {isCorrectChoice && (
                                <span className="text-[11px] font-black text-emerald-700 bg-emerald-200/70 px-2 py-0.5 rounded-md">
                                  ✓ Đáp án đúng
                                </span>
                              )}
                              {isUserChoice && !isCorrectChoice && (
                                <span className="text-[11px] font-black text-rose-700 bg-rose-200/70 px-2 py-0.5 rounded-md">
                                  ✕ Lựa chọn của bạn
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
