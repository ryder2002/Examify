"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  Clock3,
  FileQuestion,
  RefreshCcw,
  XCircle,
} from "lucide-react";

import type { Question, QuizResult } from "@/lib/utils";
import { apiFetch, isDesktop, resolveIdentity } from "@/lib/api";
import { toeicScores } from "@/lib/toeic-score";
import ExamifyLoader from "@/components/ExamifyLoader";
import SolutionUnavailableDialog from "@/components/SolutionUnavailableDialog";
import { quizPath } from "@/lib/exam-route";

type ResultStatus = "correct" | "wrong" | "unanswered" | "ungraded";
type Filter = "all" | ResultStatus;

const PARTS = [
  { number: 1, start: 1, end: 6 },
  { number: 2, start: 7, end: 31 },
  { number: 3, start: 32, end: 70 },
  { number: 4, start: 71, end: 100 },
  { number: 5, start: 101, end: 130 },
  { number: 6, start: 131, end: 146 },
  { number: 7, start: 147, end: 200 },
] as const;

function statusOf(question: Question, answers: Record<number, string>): ResultStatus {
  const selected = answers[question.number];
  if (!selected) return "unanswered";
  if (!question.correct) return "ungraded";
  return selected === question.correct ? "correct" : "wrong";
}

function formatDuration(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return [hours, minutes, rest]
    .map((value) => value.toString().padStart(2, "0"))
    .join(":");
}

export default function ResultPage() {
  const router = useRouter();
  const [historyAttemptId, setHistoryAttemptId] = useState<string | null>(null);
  const [historyAttemptResolved, setHistoryAttemptResolved] = useState(false);
  const [classReturn, setClassReturn] = useState<string | null>(null);
  const [result, setResult] = useState<QuizResult | null>(null);
  const [historyView, setHistoryView] = useState(false);
  const [solutionUnavailable, setSolutionUnavailable] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedNumber, setSelectedNumber] = useState<number | null>(null);

  useEffect(() => {
    setHistoryAttemptId(new URLSearchParams(window.location.search).get("attempt"));
    setClassReturn(sessionStorage.getItem("quiz-class-return"));
    setHistoryAttemptResolved(true);
  }, []);

  useEffect(() => {
    if (!historyAttemptResolved || !historyAttemptId) return;
    async function loadHistoryAttempt() {
      try {
        let parsed: QuizResult | null = null;
        if (isDesktop()) {
          const [historyRes, examsRes] = await Promise.all([
            apiFetch("/api/desktop/attempts/history", { cache: "no-store" }),
            apiFetch("/api/desktop/exams", { cache: "no-store" }),
          ]);
          const history = historyRes.ok ? await historyRes.json() : { items: [] };
          const exams = examsRes.ok ? await examsRes.json() : { items: [] };
          const attempt = (history.items || []).find((item: { id: string }) => item.id === historyAttemptId);
          const exam = (exams.items || []).find((item: { client_exam_id: string }) => item.client_exam_id === attempt?.client_exam_id);
          if (attempt && exam?.payload) {
            parsed = {
              schema_version: 2,
              exam: exam.payload,
              answers: attempt.answers || {},
              duration_seconds: attempt.duration_seconds,
              time_left_seconds: Math.max(0, attempt.duration_seconds - attempt.time_spent_seconds),
              submitted_at: attempt.submitted_at,
              attempt_id: attempt.id,
              has_solutions: Boolean(exam.payload.solutions?.length),
              scores: {
                toeic: attempt.score_toeic,
                listening: attempt.listening_score,
                reading: attempt.reading_score,
                correct: attempt.correct_count,
                graded: attempt.total_questions,
              },
            };
          }
        } else {
          const role = await resolveIdentity();
          let response = await apiFetch(
            role === "student"
              ? `/api/v1/student/attempts/${historyAttemptId}/result`
              : `/api/v1/attempts/${historyAttemptId}`,
            { cache: "no-store" },
          );
          if (role === "student" && response.status === 404) {
            response = await apiFetch(`/api/v1/attempts/${historyAttemptId}`, {
              cache: "no-store",
            });
          }
          if (response.ok) {
            const attempt = await response.json();
            parsed = {
              schema_version: 2,
              exam: attempt.exam,
              answers: attempt.answers || {},
              duration_seconds: attempt.duration_seconds,
              time_left_seconds: attempt.time_left_seconds,
              submitted_at: attempt.submitted_at,
              attempt_id: attempt.attempt_id || attempt.id || historyAttemptId,
              has_solutions: Boolean(attempt.has_solutions),
              score_released: attempt.score_released,
              answers_released: attempt.answers_released,
              scores: attempt.scores,
            };
          }
        }
        if (!parsed?.exam?.questions?.length) throw new Error("Không tìm thấy chi tiết bài làm");
        setHistoryView(true);
        setResult(parsed);
        const firstWrong = parsed.exam.questions.find((question) => statusOf(question, parsed.answers) === "wrong");
        setSelectedNumber(firstWrong?.number || parsed.exam.questions[0].number);
      } catch {
        router.replace("/history");
      }
    }
    void loadHistoryAttempt();
  }, [historyAttemptId, historyAttemptResolved, router]);

  useEffect(() => {
    if (!historyAttemptResolved) return;
    if (historyAttemptId) return;
    const raw = sessionStorage.getItem("quiz-result");
    if (!raw) {
      router.replace("/");
      return;
    }
    try {
      const parsed = JSON.parse(raw) as QuizResult;
      if (parsed.schema_version !== 2 || !parsed.exam?.questions?.length) {
        throw new Error("Invalid result");
      }
      setResult(parsed);
      const firstWrong = parsed.exam.questions.find(
        (question) => statusOf(question, parsed.answers) === "wrong",
      );
      setSelectedNumber(firstWrong?.number || parsed.exam.questions[0].number);

      if (isDesktop() && !sessionStorage.getItem("attempt-saved")) {
        sessionStorage.setItem("attempt-saved", "true");
        const questions = parsed.exam.questions;
        const scores = toeicScores(questions, parsed.answers);
        const totalCorrect = questions.filter((q) => parsed.answers[q.number] === q.correct).length;

        apiFetch("/api/desktop/attempts/history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            client_exam_id: parsed.exam.client_exam_id || parsed.exam.exam_id || "",
            exam_title: parsed.exam.title || "Đề thi TOEIC",
            exam_type: parsed.exam.exam_type || "combined",
            score_toeic: scores.scoreToeic,
            listening_score: scores.listeningScore,
            reading_score: scores.readingScore,
            correct_count: totalCorrect,
            total_questions: questions.length,
            duration_seconds: parsed.duration_seconds,
            time_spent_seconds: Math.max(0, parsed.duration_seconds - parsed.time_left_seconds),
            mode: sessionStorage.getItem("quiz-mode") || "practice",
            answers: parsed.answers,
          }),
        }).catch(() => undefined);
      }
    } catch {
      sessionStorage.removeItem("quiz-result");
      router.replace("/");
    }
  }, [historyAttemptId, historyAttemptResolved, router]);

  const summary = useMemo(() => {
    const counts: Record<ResultStatus, number> = {
      correct: 0,
      wrong: 0,
      unanswered: 0,
      ungraded: 0,
    };
    result?.exam.questions.forEach((question) => {
      counts[statusOf(question, result.answers)] += 1;
    });
    return counts;
  }, [result]);

  if (!result) {
    return <ExamifyLoader message="Đang tải kết quả..." />;
  }

  const toeic = toeicScores(result.exam.questions, result.answers);
  const displayedToeic = result.scores?.toeic ?? toeic.scoreToeic;
  const displayedListening = result.scores?.listening ?? toeic.listeningScore;
  const displayedReading = result.scores?.reading ?? toeic.readingScore;
  const usedSeconds = Math.max(
    0,
    result.duration_seconds - result.time_left_seconds,
  );
  const selectedQuestion =
    result.exam.questions.find((question) => question.number === selectedNumber) ||
    result.exam.questions[0];
  const selectedAnswer = result.answers[selectedQuestion.number];
  const selectedStatus = statusOf(selectedQuestion, result.answers);
  const summaryCards = [
    {
      label: "Đúng",
      value: result.scores?.correct ?? summary.correct,
      icon: CheckCircle2,
      color: "text-emerald-700",
    },
    {
      label: "Sai",
      value: result.scores
        ? Math.max(0, result.scores.graded - result.scores.correct)
        : summary.wrong,
      icon: XCircle,
      color: "text-red-700",
    },
    {
      label: "Chưa làm",
      value: summary.unanswered,
      icon: FileQuestion,
      color: "text-slate-600",
    },
    {
      label: "Thời gian",
      value: formatDuration(usedSeconds),
      icon: Clock3,
      color: "text-[#1f4e79]",
    },
  ];

  const statusClasses: Record<ResultStatus, string> = {
    correct: "border-emerald-600 bg-emerald-50 text-emerald-800",
    wrong: "border-red-500 bg-red-50 text-red-800",
    unanswered: "border-slate-300 bg-white text-slate-500",
    ungraded: "border-amber-500 bg-amber-50 text-amber-800",
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <header className="border-b border-[#1b456d] bg-[#1f4e79] text-white shadow-lg">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4 px-6 py-5 sm:px-8">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-300">
              Examify
            </p>
            <h1 className="mt-1 text-2xl font-extrabold">Exam Result</h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                if (result.has_solutions && result.attempt_id) {
                  router.push(`/solutions?attempt=${encodeURIComponent(result.attempt_id)}`);
                } else {
                  setSolutionUnavailable(true);
                }
              }}
              className="inline-flex items-center gap-2 rounded-lg border border-white/40 bg-white px-4 py-2.5 text-sm font-bold text-[#1f4e79] shadow hover:bg-slate-50"
            >
              <BookOpen className="h-4 w-4" /> Xem giải chi tiết
            </button>
            <button
              type="button"
              onClick={() =>
                router.push(
                  classReturn || (historyView ? "/history" : quizPath(result.exam)),
                )
              }
              className="inline-flex items-center gap-2 rounded-lg border border-white/40 bg-white/10 px-4 py-2.5 text-sm font-bold shadow hover:bg-white/20"
            >
              <RefreshCcw className="h-4 w-4" />{" "}
              {classReturn ? "Quay lại lớp" : historyView ? "Quay lại lịch sử" : "Làm lại"}
            </button>
            <button
              type="button"
              onClick={() => router.push(classReturn || "/")}
              className="inline-flex items-center gap-2 rounded-lg border border-white bg-white px-4 py-2.5 text-sm font-bold text-[#1f4e79] shadow"
            >
              <ArrowLeft className="h-4 w-4" /> {classReturn ? "Lớp học" : "Tạo đề mới"}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] px-5 py-7 sm:px-8">
        <section className="mx-auto max-w-5xl rounded-3xl border border-brand-200 bg-gradient-to-br from-white via-brand-50 to-brand-100 px-5 py-8 text-center shadow-[0_16px_45px_rgba(31,78,121,0.14)] sm:px-10 sm:py-10">
          <p className="text-sm font-extrabold uppercase tracking-[0.28em] text-brand-700">Tổng điểm TOEIC</p>
          <p className="mt-3 text-7xl font-black tracking-tight text-[#12344d] sm:text-8xl">
              {result.score_released === false ? "Chờ công bố" : `${displayedToeic || "—"}/990`}
          </p>
          {result.score_released === false && (
            <p className="mt-2 text-sm font-semibold text-slate-600">
              Giáo viên chưa công bố điểm
            </p>
          )}
          <div className="mx-auto mt-7 grid max-w-2xl gap-4 sm:grid-cols-2">
            <div className="rounded-2xl border border-blue-200 bg-white/90 p-5 shadow-sm">
              <p className="text-sm font-extrabold uppercase tracking-wider text-blue-700">Listening</p>
              <p className="mt-2 text-4xl font-black text-blue-950">{result.score_released === false ? "—" : `${displayedListening || "—"}/495`}</p>
              <p className="mt-1 text-xs font-semibold text-blue-700">{`${toeic.listeningCorrect} câu đúng · /100`}</p>
            </div>
            <div className="rounded-2xl border border-emerald-200 bg-white/90 p-5 shadow-sm">
              <p className="text-sm font-extrabold uppercase tracking-wider text-emerald-700">Reading</p>
              <p className="mt-2 text-4xl font-black text-emerald-950">{result.score_released === false ? "—" : `${displayedReading || "—"}/495`}</p>
              <p className="mt-1 text-xs font-semibold text-emerald-700">{`${toeic.readingCorrect} câu đúng · /100`}</p>
            </div>
          </div>
        </section>

        <section className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {summaryCards.map((card) => {
            const Icon = card.icon;
            return (
            <div
              key={card.label}
              className="rounded-xl border border-slate-300 bg-white p-5 shadow-[0_3px_12px_rgba(31,78,121,0.08)]"
            >
              <div className="flex items-center justify-between">
                <p className="text-sm font-bold text-slate-600">{card.label}</p>
                <Icon className={`h-5 w-5 ${card.color}`} />
              </div>
              <p className="mt-3 text-3xl font-extrabold text-slate-950">
                {card.value}
              </p>
            </div>
            );
          })}
        </section>

        <div className="mt-6 flex flex-wrap gap-2">
          {(
            [
              ["all", "Tất cả", result.exam.questions.length],
              ["correct", "Đúng", summary.correct],
              ["wrong", "Sai", summary.wrong],
              ["unanswered", "Chưa làm", summary.unanswered],
              ["ungraded", "Chưa chấm", summary.ungraded],
            ] as const
          ).map(([value, label, count]) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              className={`rounded-lg border px-4 py-2 text-sm font-bold shadow-sm ${
                filter === value
                  ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                  : "border-slate-300 bg-white text-slate-600 hover:border-[#1f4e79]"
              }`}
            >
              {label} ({count})
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(420px,0.7fr)]">
          <div className="space-y-4">
            {PARTS.map((part) => {
              const allPartQuestions = result.exam.questions.filter(
                (question) =>
                  question.number >= part.start &&
                  question.number <= part.end,
              );
              const correctPartCount = allPartQuestions.filter(
                (question) => statusOf(question, result.answers) === "correct",
              ).length;
              const totalPartCount = allPartQuestions.length;

              const questions = allPartQuestions.filter(
                (question) =>
                  filter === "all" ||
                  statusOf(question, result.answers) === filter,
              );
              if (!questions.length) return null;
              return (
                <section
                  key={part.number}
                  className="rounded-xl border border-slate-300 bg-white p-5 shadow-[0_3px_12px_rgba(31,78,121,0.08)]"
                >
                  <div className="mb-4 flex items-center justify-between border-b border-slate-200 pb-3">
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1f4e79] text-sm font-extrabold text-white">
                        {part.number}
                      </span>
                      <div>
                        <h2 className="flex items-center gap-2 font-extrabold text-[#1f4e79]">
                          Part {part.number}
                        </h2>
                        <p className="text-xs text-slate-500">
                          Questions {part.start}–{part.end}
                        </p>
                      </div>
                    </div>
                    {totalPartCount > 0 && (
                      <span className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-800 shadow-sm">
                        Đúng {correctPartCount}/{totalPartCount} câu
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-5 gap-2 sm:grid-cols-8 lg:grid-cols-10">
                    {questions.map((question) => {
                      const status = statusOf(question, result.answers);
                      return (
                        <button
                          key={question.number}
                          type="button"
                          onClick={() => setSelectedNumber(question.number)}
                          className={`h-11 rounded-lg border text-sm font-extrabold shadow-sm ${
                            statusClasses[status]
                          } ${
                            selectedNumber === question.number
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
              );
            })}
          </div>

          <aside className="xl:sticky xl:top-6 xl:self-start">
            <section className="rounded-xl border border-slate-300 bg-white p-6 shadow-[0_5px_18px_rgba(31,78,121,0.12)]">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
                    Chi tiết câu hỏi
                  </p>
                  <h2 className="mt-1 text-xl font-extrabold text-[#1f4e79]">
                    Câu {selectedQuestion.number}
                  </h2>
                </div>
                <span
                  className={`rounded-full border px-3 py-1 text-xs font-extrabold ${statusClasses[selectedStatus]}`}
                >
                  {selectedStatus === "correct"
                    ? "Đúng"
                    : selectedStatus === "wrong"
                      ? "Sai"
                      : selectedStatus === "unanswered"
                        ? "Chưa làm"
                        : "Chưa có key"}
                </span>
              </div>
              <p className="mt-5 text-base font-bold leading-7 text-slate-900">
                {selectedQuestion.text || "Chọn đáp án đúng."}
              </p>
              <div className="mt-5 space-y-2.5">
                {selectedQuestion.option_letters.map((letter) => {
                  const isCorrect = selectedQuestion.correct === letter;
                  const isSelected = selectedAnswer === letter;
                  return (
                    <div
                      key={letter}
                      className={`rounded-lg border px-4 py-3 text-sm ${
                        isCorrect
                          ? "border-emerald-600 bg-emerald-50 text-emerald-900"
                          : isSelected
                            ? "border-red-500 bg-red-50 text-red-900"
                            : "border-slate-300 bg-white text-slate-700"
                      }`}
                    >
                      <span className="font-extrabold">{letter}.</span>{" "}
                      {selectedQuestion.options[letter] || "—"}
                      {isCorrect && (
                        <span className="float-right text-xs font-bold">
                          Đáp án đúng
                        </span>
                      )}
                      {isSelected && !isCorrect && (
                        <span className="float-right text-xs font-bold">
                          Bạn đã chọn
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="mt-5 grid grid-cols-2 gap-3 rounded-lg bg-slate-50 p-4 text-sm">
                <div>
                  <p className="text-xs font-semibold text-slate-500">Bạn chọn</p>
                  <p className="mt-1 font-extrabold">{selectedAnswer || "Chưa chọn"}</p>
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-500">Đáp án đúng</p>
                  <p className="mt-1 font-extrabold">
                    {selectedQuestion.correct || "Chưa có answer key"}
                  </p>
                </div>
              </div>
            </section>
          </aside>
        </div>
      </div>
      <SolutionUnavailableDialog
        open={solutionUnavailable}
        onClose={() => setSolutionUnavailable(false)}
      />
    </main>
  );
}
