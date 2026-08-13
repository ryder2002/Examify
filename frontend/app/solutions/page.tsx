"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, BookOpen, Headphones } from "lucide-react";

import { AuthenticatedAudio, AuthenticatedImage } from "@/components/AuthenticatedMedia";
import ExamifyLoader from "@/components/ExamifyLoader";
import Header from "@/components/Header";
import SolutionUnavailableDialog from "@/components/SolutionUnavailableDialog";
import { apiFetch, isDesktop } from "@/lib/api";
import {
  solutionGroupStatus,
  solutionQuestionStatus,
  type SolutionReviewStatus,
} from "@/lib/solution-status";
import { solutionTextParagraphs } from "@/lib/solution-text";
import type { AudioRef, Question, SolutionEntry, Stimulus } from "@/lib/utils";

type SolutionPayload = {
  attempt_id: string;
  title: string;
  exam_type: "listening" | "reading" | "combined";
  questions: Question[];
  stimuli: Stimulus[];
  audio: AudioRef | null;
  audios: AudioRef[];
  solutions: SolutionEntry[];
  student_answers: Record<string, string>;
};

function partFor(number: number): number {
  return number <= 6 ? 1 : number <= 31 ? 2 : number <= 70 ? 3 : number <= 100 ? 4 : number <= 130 ? 5 : number <= 146 ? 6 : 7;
}

function groupsFor(questions: Question[]): number[][] {
  const available = new Set(questions.map((question) => question.number));
  const groups: number[][] = [];
  for (let number = 1; number <= 31; number += 1) {
    if (available.has(number)) groups.push([number]);
  }
  for (let start = 32; start <= 70; start += 3) {
    const group = [start, start + 1, start + 2].filter((number) => available.has(number));
    if (group.length) groups.push(group);
  }
  for (let start = 71; start <= 100; start += 3) {
    const group = [start, start + 1, start + 2].filter((number) => available.has(number));
    if (group.length) groups.push(group);
  }
  for (let number = 101; number <= 200; number += 1) {
    if (available.has(number)) groups.push([number]);
  }
  return groups;
}

function groupLabel(numbers: number[]): string {
  return numbers.length === 1 ? `${numbers[0]}` : `${numbers[0]}–${numbers.at(-1)}`;
}

export default function SolutionsPage() {
  const router = useRouter();
  const [payload, setPayload] = useState<SolutionPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [solutionUnavailable, setSolutionUnavailable] = useState(false);
  const contentScrollRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const attemptId = new URLSearchParams(window.location.search).get("attempt");
    if (!attemptId) {
      router.replace("/history");
      return;
    }
    const resolvedAttemptId = attemptId;
    let active = true;
    async function loadSolutions() {
      try {
        let result: SolutionPayload;
        if (isDesktop()) {
          const [historyResponse, examsResponse] = await Promise.all([
            apiFetch("/api/desktop/attempts/history", { cache: "no-store" }),
            apiFetch("/api/desktop/exams", { cache: "no-store" }),
          ]);
          const history = historyResponse.ok
            ? await historyResponse.json()
            : { items: [] };
          const exams = examsResponse.ok
            ? await examsResponse.json()
            : { items: [] };
          const attempt = (history.items || []).find(
            (item: { id: string }) => item.id === resolvedAttemptId,
          );
          const exam = (exams.items || []).find(
            (item: { client_exam_id: string }) =>
              item.client_exam_id === attempt?.client_exam_id,
          );
          if (!attempt || !exam?.payload) {
            throw new Error("Không tìm thấy lời giải local của lượt làm");
          }
          result = {
            attempt_id: attempt.id,
            title: exam.payload.title || exam.title || "Đề thi TOEIC",
            exam_type: exam.payload.exam_type || "combined",
            questions: exam.payload.questions || [],
            stimuli: exam.payload.stimuli || [],
            audio: exam.payload.audio || null,
            audios: exam.payload.audios || [],
            solutions: exam.payload.solutions || [],
            student_answers: Object.fromEntries(
              Object.entries(attempt.answers || {}).map(([number, answer]) => [
                String(number),
                String(answer || ""),
              ]),
            ),
          };
        } else {
          const response = await apiFetch(
            `/api/v1/attempts/${encodeURIComponent(resolvedAttemptId)}/solutions`,
            { cache: "no-store" },
          );
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(body.detail || "Không tải được giải chi tiết");
          }
          result = body as SolutionPayload;
        }
        if (!active) return;
        setPayload(result);
        const first = groupsFor(result.questions)[0];
        if (first) {
          setSelectedKey(first.join("-"));
          const firstHasSolution = result.solutions.some(
            (entry) =>
              entry.question_numbers.length === first.length &&
              entry.question_numbers.every((number, index) => number === first[index]),
          );
          if (!firstHasSolution) setSolutionUnavailable(true);
        }
      } catch (reason) {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Không tải được giải chi tiết",
          );
        }
      }
    }
    void loadSolutions();
    return () => {
      active = false;
    };
  }, [router]);

  const groups = useMemo(() => groupsFor(payload?.questions || []), [payload]);
  const selected = groups.find((group) => group.join("-") === selectedKey) || groups[0] || [];
  const selectedQuestions = (payload?.questions || []).filter((question) => selected.includes(question.number));
  const selectedPart = selected.length ? partFor(selected[0]) : 1;
  const solution = (payload?.solutions || []).find((entry) =>
    entry.question_numbers.length === selected.length &&
    entry.question_numbers.every((number, index) => number === selected[index]),
  );
  const selectedStimulusIds = new Set(selectedQuestions.map((question) => question.stimulus_id).filter(Boolean));
  const selectedStimuli = (payload?.stimuli || []).filter(
    (stimulus) => selectedStimulusIds.has(stimulus.id) || stimulus.question_numbers.some((number) => selected.includes(number)),
  );
  const audio = useMemo(() => {
    if (!payload || !selected.length) return null;
    const all = [...(payload.audios || []), ...(payload.audio ? [payload.audio] : [])];
    const unique = [...new Map(all.map((item) => [item.id, item])).values()];
    return (
      unique.find((item) =>
        (item.scope === "question" || item.scope === "group") &&
        (item.question_numbers || []).some((number) => selected.includes(number)),
      ) ||
      unique.find((item) => item.group_id && selectedQuestions.some((question) => question.group_id === item.group_id)) ||
      unique.find((item) => item.part === `part_${selectedPart}`) ||
      unique.find((item) => item.scope === "full" || item.part === "full") ||
      null
    );
  }, [payload, selected, selectedPart, selectedQuestions]);

  useEffect(() => {
    if (selectedKey) contentScrollRef.current?.scrollTo({ top: 0 });
  }, [selectedKey]);

  if (!payload && !error) return <ExamifyLoader message="Đang tải giải chi tiết..." />;
  if (!payload) {
    return <main className="mx-auto mt-20 max-w-xl rounded-xl border border-red-200 bg-red-50 p-6 text-red-800">{error}</main>;
  }

  const parts = [...new Set(groups.map((group) => partFor(group[0])))];
  const statusStyles: Record<SolutionReviewStatus, string> = {
    correct: "border-emerald-500 bg-emerald-50 text-emerald-800",
    wrong: "border-red-500 bg-red-50 text-red-800",
    unanswered: "border-slate-300 bg-white text-slate-600",
    ungraded: "border-amber-400 bg-amber-50 text-amber-800",
  };
  const statusLabels: Record<SolutionReviewStatus, string> = {
    correct: "Đúng",
    wrong: "Sai",
    unanswered: "Chưa làm",
    ungraded: "Chưa chấm",
  };
  return (
    <main className="min-h-screen bg-slate-50 lg:flex lg:h-[100dvh] lg:flex-col lg:overflow-hidden">
      <div className="lg:shrink-0">
        <Header />
      </div>
      <div className="mx-auto flex w-full max-w-[1920px] flex-1 flex-col px-3 pb-4 sm:px-5 xl:px-6 lg:min-h-0 lg:overflow-hidden">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 py-3">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-slate-500">Review sau nộp bài</p>
            <h1 className="mt-1 text-2xl font-extrabold text-[#1f4e79]">{payload.title}</h1>
          </div>
          <button type="button" onClick={() => router.back()} className="ui-btn-secondary">
            <ArrowLeft className="h-4 w-4" /> Quay lại kết quả
          </button>
        </div>

        <div className="mt-2 grid gap-5 lg:min-h-0 lg:flex-1 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="ui-card h-fit p-4 lg:h-auto lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:[scrollbar-gutter:stable]">
            <h2 className="font-bold text-slate-900">Điều hướng</h2>
            <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] font-bold">
              {(Object.keys(statusLabels) as SolutionReviewStatus[]).map((status) => (
                <span key={status} className={`rounded-lg border px-2 py-1.5 text-center ${statusStyles[status]}`}>
                  {statusLabels[status]}
                </span>
              ))}
            </div>
            {parts.map((part) => (
              <div key={part} className="mt-4">
                <p className="text-xs font-extrabold uppercase text-[#1f4e79]">Part {part}</p>
                <div className="mt-2 grid grid-cols-5 gap-2">
                  {groups.filter((group) => partFor(group[0]) === part).map((group) => {
                    const key = group.join("-");
                    const groupQuestions = payload.questions.filter((question) =>
                      group.includes(question.number),
                    );
                    const status = solutionGroupStatus(
                      groupQuestions,
                      payload.student_answers,
                    );
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setSelectedKey(key);
                          const hasSolution = payload.solutions.some(
                            (entry) =>
                              entry.question_numbers.length === group.length &&
                              entry.question_numbers.every(
                                (number, index) => number === group[index],
                              ),
                          );
                          if (!hasSolution) setSolutionUnavailable(true);
                        }}
                        title={`${groupLabel(group)} · ${statusLabels[status]}`}
                        className={`rounded-lg border px-2 py-2 text-[11px] font-extrabold ${statusStyles[status]} ${
                          selectedKey === key
                            ? "ring-2 ring-[#1f4e79] ring-offset-2"
                            : "hover:brightness-95"
                        }`}
                      >
                        {groupLabel(group)}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </aside>

          <section
            ref={contentScrollRef}
            className="space-y-5 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:pb-6 lg:pr-2 lg:[scrollbar-gutter:stable]"
          >
            <div className="ui-card p-6 lg:p-7">
              <div className="flex items-center gap-2">
                {selectedPart <= 4 ? <Headphones className="h-5 w-5 text-[#1f4e79]" /> : <BookOpen className="h-5 w-5 text-[#1f4e79]" />}
                <h2 className="text-xl font-extrabold text-slate-900">Part {selectedPart} · Câu {groupLabel(selected)}</h2>
              </div>
              {audio && (
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="mb-2 text-xs font-bold text-slate-600">{audio.filename}</p>
                  <AuthenticatedAudio source={audio.url} controls preload="metadata" className="w-full" />
                </div>
              )}
              {selectedStimuli.flatMap((stimulus) => stimulus.assets).map((asset) => (
                <AuthenticatedImage key={asset.id} source={asset.url} alt="Passage/hình câu hỏi" className="mt-5 max-h-[760px] w-full rounded-xl border bg-white object-contain" />
              ))}
            </div>

            {selectedQuestions.map((question) => {
              const selectedAnswer = payload.student_answers[String(question.number)] || "";
              const questionStatus = solutionQuestionStatus(
                question,
                payload.student_answers,
              );
              return (
                <article key={question.number} className="ui-card p-6 lg:p-7">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <h3 className="max-w-5xl text-xl font-extrabold leading-9 text-slate-900">Câu {question.number}. {question.text}</h3>
                    <span className={`rounded-full border px-3 py-1 text-xs font-extrabold ${statusStyles[questionStatus]}`}>
                      {statusLabels[questionStatus]}
                    </span>
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    {question.option_letters.map((letter) => (
                      <div
                        key={letter}
                        className={`rounded-xl border p-4 text-[17px] leading-8 ${
                          question.correct === letter
                            ? "border-emerald-400 bg-emerald-50 text-emerald-900"
                            : selectedAnswer === letter
                              ? "border-red-300 bg-red-50 text-red-900"
                              : "border-slate-200 bg-white text-slate-700"
                        }`}
                      >
                        <strong>{letter}.</strong> {question.options[letter] || ""}
                        {question.correct === letter ? " · Đáp án đúng" : selectedAnswer === letter ? " · Bạn chọn" : ""}
                      </div>
                    ))}
                  </div>
                </article>
              );
            })}

            <div className="ui-card p-5 lg:p-6">
              {!solution ? (
                <p className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center font-semibold text-slate-500">
                  Chưa có giải chi tiết
                </p>
              ) : (
                <div className="grid gap-5 xl:grid-cols-2">
                  <section className="min-w-0 rounded-2xl border border-brand-100 bg-brand-50/35 p-5 lg:p-6">
                    <h3 className="text-xl font-extrabold text-[#173254]">{selectedPart <= 4 ? "Transcript" : "Giải thích"}</h3>
                    <div className="mt-4 space-y-4 text-lg leading-8 text-slate-900">
                      {solutionTextParagraphs(
                        solution.transcript || solution.explanation || "Chưa có nội dung",
                      ).map((paragraph, index) => (
                        <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>
                      ))}
                    </div>
                  </section>
                  <section className="min-w-0 rounded-2xl border border-sky-100 bg-sky-50/35 p-5 lg:p-6">
                    <h3 className="text-xl font-extrabold text-[#173254]">Dịch</h3>
                    <div className="mt-4 space-y-4 text-lg leading-8 text-slate-900">
                      {solutionTextParagraphs(
                        solution.translation || "Chưa có bản dịch",
                      ).map((paragraph, index) => (
                        <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>
                      ))}
                    </div>
                  </section>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
      <SolutionUnavailableDialog
        open={solutionUnavailable}
        onClose={() => setSolutionUnavailable(false)}
      />
    </main>
  );
}
