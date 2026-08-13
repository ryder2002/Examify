"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Award,
  BarChart3,
  BookOpen,
  Calendar,
  Clock,
  FileCheck2,
  FolderOpen,
  History as HistoryIcon,
  Eye,
  Loader2,
  Plus,
  Target,
  Trophy,
  TrendingUp,
} from "lucide-react";

import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import SolutionUnavailableDialog from "@/components/SolutionUnavailableDialog";
import { apiFetch, isDesktop, resolveIdentity } from "@/lib/api";

type AttemptHistoryItem = {
  id: string;
  client_exam_id: string;
  exam_title: string;
  exam_type: string;
  score_toeic: number | null;
  listening_score: number | null;
  reading_score: number | null;
  correct_count: number | null;
  total_questions: number;
  duration_seconds: number;
  time_spent_seconds: number;
  mode: string;
  submitted_at: string;
  answers?: Record<number, string>;
  classroom_name?: string;
  source?: "bank" | "classroom";
  has_solutions?: boolean;
};

const HISTORY_PAGE_SIZE = 50;

export default function HistoryPage() {
  const router = useRouter();
  const [history, setHistory] = useState<AttemptHistoryItem[]>([]);
  const [historyEndpoint, setHistoryEndpoint] = useState<string | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [solutionUnavailable, setSolutionUnavailable] = useState(false);

  useEffect(() => {
    async function loadHistory() {
      setLoading(true);
      setError(null);
      try {
        const currentRole = await resolveIdentity(true);
        setRole(currentRole);
        const endpoint = isDesktop()
          ? "/api/desktop/attempts/history"
          : currentRole === "student"
            ? "/api/v1/student/history"
            : "/api/v1/attempts/history";
        const paginatedEndpoint = endpoint.startsWith("/api/v1/")
          ? `${endpoint}?page=1&page_size=${HISTORY_PAGE_SIZE}`
          : endpoint;
        const res = await apiFetch(paginatedEndpoint, { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          const items = (data.items || []) as AttemptHistoryItem[];
          setHistory(items);
          setHistoryEndpoint(
            endpoint.startsWith("/api/v1/") ? endpoint : null,
          );
          setHistoryPage(Number(data.page) || 1);
          setHistoryTotal(Number(data.total) || items.length);
        } else {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail || "Không tải được lịch sử làm bài");
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
      } finally {
        setLoading(false);
      }
    }
    void loadHistory();
  }, []);

  async function loadMoreHistory() {
    if (!historyEndpoint || loadingMore || history.length >= historyTotal) return;
    setLoadingMore(true);
    setError(null);
    try {
      const nextPage = historyPage + 1;
      const response = await apiFetch(
        `${historyEndpoint}?page=${nextPage}&page_size=${HISTORY_PAGE_SIZE}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Không tải thêm được lịch sử");
      }
      const nextItems = (payload.items || []) as AttemptHistoryItem[];
      setHistory((current) => {
        const known = new Set(current.map((item) => item.id));
        return [
          ...current,
          ...nextItems.filter((item) => !known.has(item.id)),
        ];
      });
      setHistoryPage(Number(payload.page) || nextPage);
      setHistoryTotal(Number(payload.total) || historyTotal);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    } finally {
      setLoadingMore(false);
    }
  }

  const stats = useMemo(() => {
    if (!history.length) {
      return {
        avgScore: 0,
        maxScore: 0,
        totalAttempts: 0,
        totalTimeSeconds: 0,
        avgListening: 0,
        avgReading: 0,
      };
    }
    const visibleScores = history.map((item) => item.score_toeic).filter((score): score is number => score != null);
    const totalScore = visibleScores.reduce((sum, score) => sum + score, 0);
    const maxScore = visibleScores.length ? Math.max(...visibleScores) : 0;
    const totalTime = history.reduce((sum, item) => sum + (item.time_spent_seconds || 0), 0);
    const totalListening = history.reduce((sum, item) => sum + (item.listening_score || 0), 0);
    const totalReading = history.reduce((sum, item) => sum + (item.reading_score || 0), 0);

    return {
      avgScore: visibleScores.length ? Math.round(totalScore / visibleScores.length) : 0,
      maxScore,
      totalAttempts: historyTotal || history.length,
      totalTimeSeconds: totalTime,
      avgListening: Math.round(totalListening / history.length),
      avgReading: Math.round(totalReading / history.length),
    };
  }, [history, historyTotal]);

  function formatHoursMinutes(totalSeconds: number) {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes} phút`;
  }

  // Chart data calculation
  const chartData = useMemo(() => {
    if (history.length < 2) return null;
    const sorted = [...history].sort(
      (a, b) => new Date(a.submitted_at).getTime() - new Date(b.submitted_at).getTime(),
    );
    const maxScore = 990;
    const points = sorted.filter((item) => item.score_toeic != null).map((item, index, scored) => {
      const x = (index / Math.max(1, scored.length - 1)) * 500 + 40;
      const y = 200 - ((item.score_toeic || 0) / maxScore) * 160;
      return { x, y, score: item.score_toeic, title: item.exam_title, date: new Date(item.submitted_at).toLocaleDateString("vi-VN") };
    });

    const pathD = points.reduce((acc, point, index) => {
      return index === 0 ? `M ${point.x} ${point.y}` : `${acc} L ${point.x} ${point.y}`;
    }, "");

    return { points, pathD };
  }, [history]);

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="rounded-lg border border-slate-200 bg-white p-1.5 text-[#1f4e79]">
                <HistoryIcon className="h-5 w-5" />
              </span>
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">
                Thống kê học tập
              </p>
            </div>
            <h1 className="mt-1 text-3xl font-extrabold text-[#1f4e79]">Lịch sử làm bài thi</h1>
            <p className="mt-1 text-sm text-slate-500">
              Tổng hợp điểm trung bình, điểm cao nhất và biểu đồ tiến trình TOEIC.
            </p>
          </div>

          <div className="flex flex-wrap gap-3">
            <button onClick={() => router.push(role === "user" ? "/my-exams" : "/exam-bank")} className="ui-btn-secondary">
              <FolderOpen className="h-4 w-4" /> {role === "user" ? "My Exams" : "Kho đề thi"}
            </button>
            {role !== "student" && <button onClick={() => router.push("/")} className="ui-btn-primary">
              <Plus className="h-4 w-4" /> Tạo đề mới
            </button>}
          </div>
        </div>

        {/* Overview Stats */}
        <section className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              label: "Điểm trung bình",
              value: `${stats.avgScore} / 990`,
              icon: Target,
              sub: `LC ${stats.avgListening} · RC ${stats.avgReading}`,
              bg: "bg-blue-50 text-blue-900 border-blue-200",
            },
            {
              label: "Điểm cao nhất",
              value: `${stats.maxScore} / 990`,
              icon: Trophy,
              sub: "Kỷ lục TOEIC cá nhân",
              bg: "bg-amber-50 text-amber-900 border-amber-200",
            },
            {
              label: "Tổng lượt thi",
              value: `${stats.totalAttempts} bài thi`,
              icon: FileCheck2,
              sub: "Bài làm đã nộp",
              bg: "bg-emerald-50 text-emerald-900 border-emerald-200",
            },
            {
              label: "Thời gian luyện tập",
              value: formatHoursMinutes(stats.totalTimeSeconds),
              icon: Clock,
              sub: "Tổng thời gian làm bài",
              bg: "bg-purple-50 text-purple-900 border-purple-200",
            },
          ].map(({ label, value, icon: Icon, sub, bg }) => (
            <div key={label} className={`rounded-2xl border p-5 shadow-sm transition hover:shadow-md ${bg}`}>
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wider opacity-80">{label}</span>
                <Icon className="h-6 w-6 opacity-80" />
              </div>
              <p className="mt-3 text-3xl font-extrabold">{value}</p>
              <p className="mt-1 text-xs opacity-75">{sub}</p>
            </div>
          ))}
        </section>

        {/* Charts & Analytics Section */}
        {history.length > 0 && (
          <div className="mt-8 grid gap-6 lg:grid-cols-3">
            {/* Score Evolution Chart */}
            <section className="ui-card p-6 lg:col-span-2">
              <div className="flex items-center justify-between border-b border-slate-200 pb-4">
                <div className="flex items-center gap-2">
                  <TrendingUp className="h-5 w-5 text-[#1f4e79]" />
                  <h2 className="text-lg font-extrabold text-[#1f4e79]">Biểu đồ tiến trình điểm TOEIC</h2>
                </div>
                <span className="text-xs font-semibold text-slate-500">Mốc điểm tối đa 990</span>
              </div>

              {chartData ? (
                <div className="mt-6 overflow-x-auto">
                  <div className="min-w-[580px]">
                    <svg viewBox="0 0 580 240" className="w-full overflow-visible">
                      {/* Grid lines */}
                      {[0, 250, 500, 750, 990].map((scoreLevel) => {
                        const y = 200 - (scoreLevel / 990) * 160;
                        return (
                          <g key={scoreLevel}>
                            <line x1="40" y1={y} x2="560" y2={y} stroke="#e2e8f0" strokeDasharray="4 4" />
                            <text x="32" y={y + 4} textAnchor="end" className="text-[10px] fill-slate-400 font-bold">
                              {scoreLevel}
                            </text>
                          </g>
                        );
                      })}

                      {/* Line Path */}
                      <path d={chartData.pathD} fill="none" stroke="#1f4e79" strokeWidth="3" strokeLinecap="round" />

                      {/* Points */}
                      {chartData.points.map((pt, idx) => (
                        <g key={idx} className="group cursor-pointer">
                          <circle
                            cx={pt.x}
                            cy={pt.y}
                            r="6"
                            className="fill-[#1f4e79] stroke-white stroke-2 transition group-hover:r-8 group-hover:fill-[#b58855]"
                          />
                          {/* Tooltip */}
                          <title>{`${pt.title}: ${pt.score} điểm (${pt.date})`}</title>
                          <text
                            x={pt.x}
                            y={pt.y - 12}
                            textAnchor="middle"
                            className="text-[11px] font-extrabold fill-[#1f4e79] opacity-0 group-hover:opacity-100 transition"
                          >
                            {pt.score}
                          </text>
                        </g>
                      ))}
                    </svg>
                  </div>
                </div>
              ) : (
                <div className="flex h-48 items-center justify-center text-sm text-slate-500">
                  Cần làm ít nhất 2 bài thi để hiển thị biểu đồ tiến trình điểm số.
                </div>
              )}
            </section>

            {/* Skill Breakdown */}
            <section className="ui-card p-6">
              <div className="flex items-center gap-2 border-b border-slate-200 pb-4">
                <BarChart3 className="h-5 w-5 text-[#1f4e79]" />
                <h2 className="text-lg font-extrabold text-[#1f4e79]">Kỹ năng trung bình</h2>
              </div>

              <div className="mt-6 space-y-6">
                <div>
                  <div className="flex justify-between text-sm font-bold">
                    <span className="text-slate-700">Listening</span>
                    <span className="text-[#1f4e79]">{stats.avgListening} / 495</span>
                  </div>
                  <div className="mt-2 h-3.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full bg-blue-600 rounded-full transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.round((stats.avgListening / 495) * 100))}%` }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">Độ chính xác tương đương ~{Math.round((stats.avgListening / 495) * 100)}%</p>
                </div>

                <div>
                  <div className="flex justify-between text-sm font-bold">
                    <span className="text-slate-700">Reading</span>
                    <span className="text-[#1f4e79]">{stats.avgReading} / 495</span>
                  </div>
                  <div className="mt-2 h-3.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full bg-emerald-600 rounded-full transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.round((stats.avgReading / 495) * 100))}%` }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">Độ chính xác tương đương ~{Math.round((stats.avgReading / 495) * 100)}%</p>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* History Table */}
        <section className="ui-card mt-8 p-6">
          <h2 className="text-lg font-extrabold text-[#1f4e79]">Chi tiết lượt bài thi</h2>

          {loading ? (
            <ExamifyLoader fullScreen={false} message="Đang tải lịch sử..." />
          ) : history.length === 0 ? (
            <div className="py-12 text-center text-slate-500">
              <HistoryIcon className="mx-auto h-12 w-12 text-slate-300" />
              <p className="mt-3 text-base font-bold text-slate-700">Chưa có lịch sử làm bài</p>
              <p className="mt-1 text-xs">Hãy chọn một đề thi trong My Exams để bắt đầu bài làm đầu tiên.</p>
            </div>
          ) : (
            <div className="mt-4">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[700px] text-left text-sm">
                <thead className="text-xs uppercase text-slate-500 border-b border-slate-200">
                  <tr>
                    <th className="px-4 py-3">Tên đề thi</th>
                    <th className="px-4 py-3">Chế độ</th>
                    <th className="px-4 py-3">Điểm TOEIC</th>
                    <th className="px-4 py-3">Độ chính xác</th>
                    <th className="px-4 py-3">Thời gian làm</th>
                    <th className="px-4 py-3">Ngày làm</th>
                    <th className="px-4 py-3 text-right">Chi tiết</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((item) => (
                    <tr key={item.id} className="border-b border-slate-100 hover:bg-slate-50/80 transition">
                      <td className="px-4 py-3.5">
                        <strong className="block text-slate-900">{item.exam_title}</strong>
                        <span className="text-xs text-slate-500 uppercase font-semibold">
                          {item.exam_type} · {item.source === "classroom" ? "Lớp học" : "Kho đề"}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`rounded-full border px-2.5 py-1 text-xs font-bold ${
                            item.mode === "exam"
                              ? "border-amber-300 bg-amber-50 text-amber-900"
                              : "border-slate-300 bg-slate-50 text-slate-700"
                          }`}
                        >
                          {item.mode === "exam" ? "Thi thử" : "Luyện tập"}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 font-extrabold text-[#1f4e79]">
                        {item.score_toeic == null ? (
                          <span className="text-xs text-slate-500">Chưa công bố</span>
                        ) : (
                          <><span className="text-lg">{item.score_toeic}</span> <span className="text-xs font-normal text-slate-500">/ 990</span></>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        {item.correct_count == null ? (
                          <span className="text-xs text-slate-500">Chưa công bố</span>
                        ) : (
                          <><span className="font-bold text-slate-800">
                            {item.correct_count} / {item.total_questions}
                          </span>{" "}
                          <span className="text-xs text-slate-500">
                            ({Math.round((item.correct_count / (item.total_questions || 1)) * 100)}%)
                          </span></>
                        )}
                      </td>
                      <td className="px-4 py-3.5 text-slate-600">
                        {Math.floor(item.time_spent_seconds / 60)} phút {item.time_spent_seconds % 60}s
                      </td>
                      <td className="px-4 py-3.5 text-slate-500 text-xs">
                        {new Date(item.submitted_at).toLocaleDateString("vi-VN", {
                          hour: "2-digit",
                          minute: "2-digit",
                          day: "2-digit",
                          month: "2-digit",
                          year: "numeric",
                        })}
                      </td>
                      <td className="px-4 py-3.5 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              if (item.has_solutions) {
                                router.push(`/solutions?attempt=${encodeURIComponent(item.id)}`);
                              } else {
                                setSolutionUnavailable(true);
                              }
                            }}
                            className="ui-btn-primary whitespace-nowrap px-3 py-2 text-xs"
                          >
                            <BookOpen className="h-4 w-4" /> Xem giải chi tiết
                          </button>
                          <button
                            type="button"
                            onClick={() => router.push(`/result?attempt=${encodeURIComponent(item.id)}`)}
                            className="ui-btn-secondary whitespace-nowrap px-3 py-2 text-xs"
                          >
                            <Eye className="h-4 w-4" /> Xem chi tiết
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
                </table>
              </div>
              {history.length < historyTotal && (
                <div className="mt-5 flex flex-col items-center gap-2 border-t border-slate-200 pt-5">
                  <p className="text-xs font-medium text-slate-500">
                    Đã hiển thị {history.length}/{historyTotal} lượt làm bài
                  </p>
                  <button
                    type="button"
                    onClick={() => void loadMoreHistory()}
                    disabled={loadingMore}
                    className="ui-btn-secondary px-5 py-2.5 text-sm disabled:cursor-wait disabled:opacity-60"
                  >
                    {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
                    {loadingMore ? "Đang tải…" : "Tải thêm lịch sử"}
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
      <SolutionUnavailableDialog
        open={solutionUnavailable}
        onClose={() => setSolutionUnavailable(false)}
      />
    </main>
  );
}
