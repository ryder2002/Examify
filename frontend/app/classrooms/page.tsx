"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Archive,
  ArrowRight,
  BookOpenCheck,
  Copy,
  GraduationCap,
  Loader2,
  Plus,
  Users,
  X,
} from "lucide-react";

import Header from "@/components/Header";
import ExamifyLoader from "@/components/ExamifyLoader";
import {
  apiFetch,
  resolveIdentity,
  storedClassSessions,
  type StoredClassSession,
} from "@/lib/api";

type Classroom = {
  id: string;
  name: string;
  description: string;
  join_code?: string;
  status: "active" | "archived";
  member_count: number;
  assignment_count: number;
  membership_status?: "active" | "removed";
};

export default function ClassroomsPage() {
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [classes, setClasses] = useState<Classroom[]>([]);
  const [sessions, setSessions] = useState<StoredClassSession[]>([]);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [resolvedClass, setResolvedClass] = useState<{ id: string; name: string } | null>(null);
  const [showJoin, setShowJoin] = useState(false);

  useEffect(() => {
    setSessions(storedClassSessions());
    void resolveIdentity()
      .then(async (nextRole) => {
        setRole(nextRole);
        if (nextRole === "teacher") {
          const response = await apiFetch("/api/v1/teacher/classrooms", {
            cache: "no-store",
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.detail || "Không tải được lớp học");
          setClasses(payload.items || []);
        } else if (nextRole === "student") {
          const response = await apiFetch("/api/v1/student/classrooms", { cache: "no-store" });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.detail || "Không tải được lớp học");
          setClasses(payload.items || []);
          setShowJoin(
            !(payload.items || []).some(
              (item: Classroom) =>
                item.membership_status === "active" && item.status === "active",
            ),
          );
        }
      })
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Không tải được lớp học"),
      )
      .finally(() => setReady(true));
  }, []);

  async function createClassroom(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/teacher/classrooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, description: newDescription }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không tạo được lớp");
      setClasses((current) => [payload, ...current]);
      setNewName("");
      setNewDescription("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tạo được lớp");
    } finally {
      setLoading(false);
    }
  }

  async function resolveCode(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/student/classrooms/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: joinCode }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Mã lớp không hợp lệ");
      setResolvedClass(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Mã lớp không hợp lệ");
    } finally {
      setLoading(false);
    }
  }

  async function joinClassroom(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const legacy = sessions.find((item) => item.classroomId === resolvedClass?.id);
      const response = await apiFetch("/api/v1/student/classrooms/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: joinCode,
          legacy_session_token: legacy?.token || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không tham gia được lớp");
      setResolvedClass(null);
      setShowJoin(false);
      router.push(
        `/classrooms/detail?id=${encodeURIComponent(payload.classroom.id)}`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tham gia được lớp");
    } finally {
      setLoading(false);
    }
  }

  if (!ready) {
    return (
      <main className="min-h-screen bg-slate-50">
        <Header />
        <ExamifyLoader fullScreen={false} message="Đang tải lớp học..." />
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-8 sm:px-8">
        <div className="flex items-center gap-3">
          <span className="rounded-xl border border-slate-200 bg-white p-3 text-[#1f4e79] shadow-sm">
            <GraduationCap className="h-7 w-7" />
          </span>
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">
              Examify Classroom
            </p>
            <h1 className="text-3xl font-extrabold text-[#1f4e79]">
              {role === "teacher" ? "Lớp học của tôi" : "Tham gia lớp học"}
            </h1>
          </div>
        </div>

        {error && (
          <div className="mt-6 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
            {error}
          </div>
        )}

        {role === "teacher" ? (
          <div className="mt-8 grid gap-6 lg:grid-cols-[360px_1fr]">
            <form onSubmit={createClassroom} className="ui-card h-fit p-6">
              <div className="flex items-center gap-2">
                <Plus className="h-5 w-5 text-[#1f4e79]" />
                <h2 className="text-lg font-extrabold text-[#1f4e79]">Tạo lớp mới</h2>
              </div>
              <label className="mt-5 block">
                <span className="text-sm font-bold text-slate-700">Tên lớp</span>
                <input
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder="Ví dụ: TOEIC 650+ Tối thứ 3"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5 outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <label className="mt-4 block">
                <span className="text-sm font-bold text-slate-700">Mô tả</span>
                <textarea
                  value={newDescription}
                  onChange={(event) => setNewDescription(event.target.value)}
                  rows={4}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2.5 outline-none focus:border-[#1f4e79]"
                />
              </label>
              <button disabled={loading || !newName.trim()} className="ui-btn-primary mt-5 w-full py-2.5">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                Tạo lớp học
              </button>
            </form>

            <section>
              {classes.length ? (
                <div className="grid gap-4 md:grid-cols-2">
                  {classes.map((classroom) => (
                    <article key={classroom.id} className="ui-card p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h2 className="text-xl font-extrabold text-[#1f4e79]">
                            {classroom.name}
                          </h2>
                          <p className="mt-1 line-clamp-2 text-sm text-slate-500">
                            {classroom.description || "Chưa có mô tả"}
                          </p>
                        </div>
                        {classroom.status === "archived" && (
                          <Archive className="h-5 w-5 text-slate-400" />
                        )}
                      </div>
                      <div className="mt-5 flex flex-wrap gap-2 text-xs font-bold text-slate-600">
                        <span className="rounded-full border bg-slate-50 px-3 py-1">
                          <Users className="mr-1 inline h-3.5 w-3.5" />
                          {classroom.member_count} học viên
                        </span>
                        <span className="rounded-full border bg-slate-50 px-3 py-1">
                          <BookOpenCheck className="mr-1 inline h-3.5 w-3.5" />
                          {classroom.assignment_count} bài
                        </span>
                      </div>
                      <div className="mt-5 flex items-center justify-between rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-2">
                        <span className="font-mono text-lg font-extrabold tracking-[0.18em] text-[#1f4e79]">
                          {classroom.join_code}
                        </span>
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(classroom.join_code || "")}
                          className="rounded-md p-2 text-slate-500 hover:bg-white hover:text-[#1f4e79]"
                        >
                          <Copy className="h-4 w-4" />
                        </button>
                      </div>
                      <button
                        onClick={() =>
                          router.push(
                            `/classrooms/detail?id=${encodeURIComponent(classroom.id)}`,
                          )
                        }
                        className="ui-btn-primary mt-4 w-full py-2"
                      >
                        Quản lý lớp <ArrowRight className="h-4 w-4" />
                      </button>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="ui-card flex min-h-64 flex-col items-center justify-center p-8 text-center">
                  <GraduationCap className="h-12 w-12 text-slate-300" />
                  <h2 className="mt-4 text-lg font-extrabold text-[#1f4e79]">Chưa có lớp học</h2>
                  <p className="mt-1 text-sm text-slate-500">Tạo lớp đầu tiên để bắt đầu giao đề.</p>
                </div>
              )}
            </section>
          </div>
        ) : (
          <div className={`mt-8 grid gap-6 ${showJoin ? "lg:grid-cols-[420px_1fr]" : ""}`}>
            {showJoin && <form onSubmit={resolveCode} className="ui-card h-fit p-6 sm:p-8">
              <h2 className="text-xl font-extrabold text-[#1f4e79]">Nhập mã lớp học</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                Nhập mã do giáo viên cung cấp. Lớp học và lịch sử sẽ được gắn với tài khoản của bạn.
              </p>
              <input
                value={joinCode}
                onChange={(event) => setJoinCode(event.target.value.toUpperCase())}
                placeholder="ABCD2345"
                maxLength={12}
                className="mt-6 w-full rounded-xl border border-slate-300 px-4 py-4 text-center font-mono text-2xl font-extrabold uppercase tracking-[0.25em] outline-none focus:border-[#1f4e79]"
                required
              />
              <button disabled={loading || joinCode.length < 4} className="ui-btn-primary mt-4 w-full py-3">
                {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <ArrowRight className="h-5 w-5" />}
                Xác nhận mã lớp
              </button>
            </form>}

            <section>
              <div className="flex items-center justify-between gap-3"><h2 className="text-sm font-extrabold uppercase tracking-wider text-slate-500">Lớp học của tôi</h2>{classes.some((item) => item.membership_status === "active") && !showJoin && <button onClick={() => setShowJoin(true)} className="ui-btn-secondary px-3 py-2 text-xs"><Plus className="h-4 w-4" /> Thêm lớp</button>}</div>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {classes.map((classroom) => (
                  <button
                    key={classroom.id}
                    onClick={() =>
                      router.push(
                        `/classrooms/detail?id=${encodeURIComponent(classroom.id)}`,
                      )
                    }
                    disabled={classroom.membership_status !== "active" || classroom.status !== "active"}
                    className="ui-card flex items-center justify-between p-5 text-left transition hover:-translate-y-0.5 hover:border-[#1f4e79]"
                  >
                    <div>
                      <strong className="block text-[#1f4e79]">{classroom.name}</strong>
                      <span className="mt-1 block text-xs text-slate-500">{classroom.description || "Lớp học Examify"}</span>
                    </div>
                    <ArrowRight className="h-5 w-5 text-slate-400" />
                  </button>
                ))}
                {!classes.length && (
                  <div className="ui-card col-span-full p-8 text-center text-sm text-slate-500">
                    Bạn chưa tham gia lớp học nào.
                  </div>
                )}
              </div>
            </section>
          </div>
        )}
      </div>

      {resolvedClass && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <form onSubmit={joinClassroom} className="w-full max-w-md rounded-2xl border bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Mã lớp hợp lệ</p>
                <h2 className="mt-1 text-2xl font-extrabold text-[#1f4e79]">{resolvedClass.name}</h2>
              </div>
              <button type="button" onClick={() => setResolvedClass(null)} className="rounded-lg p-2 hover:bg-slate-100">
                <X className="h-5 w-5" />
              </button>
            </div>
            <p className="mt-6 text-sm leading-6 text-slate-500">Hệ thống sẽ dùng họ tên trong tài khoản để giáo viên nhận diện kết quả của bạn.</p>
            <button disabled={loading} className="ui-btn-primary mt-6 w-full py-3">
              {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <GraduationCap className="h-5 w-5" />}
              Vào lớp học
            </button>
          </form>
        </div>
      )}
    </main>
  );
}
