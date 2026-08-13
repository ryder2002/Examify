"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BookOpen, FolderOpen, GraduationCap, History, LogOut, Menu, PlusCircle, ShieldCheck, X } from "lucide-react";
import {
  cachedIdentity,
  resolveIdentity,
  resolveIdentityAtStartup,
  logoutSession,
  watchIdentityRole,
} from "@/lib/api";

import Image from "next/image";

export default function Header() {
  const [role, setRole] = useState<string | null>(null);
  const [identityReady, setIdentityReady] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    let active = true;
    const refreshIdentity = (force = false, startup = false) => {
      const identity = startup
        ? resolveIdentityAtStartup()
        : resolveIdentity(force);
      identity.then((nextRole) => {
        if (!active) return;
        setRole(nextRole);
        setIdentityReady(true);
      });
    };
    const onAuthChange = () => refreshIdentity(true);
    const cached = cachedIdentity();
    if (cached.ready) {
      setRole(cached.role);
      setIdentityReady(true);
    }
    refreshIdentity(false, true);
    const stopWatchingIdentity = watchIdentityRole((nextRole) => {
      if (!active) return;
      setRole(nextRole);
      setIdentityReady(true);
    });
    window.addEventListener("smart-exam-auth-changed", onAuthChange);
    return () => {
      active = false;
      stopWatchingIdentity();
      window.removeEventListener("smart-exam-auth-changed", onAuthChange);
    };
  }, []);

  async function logout() {
    await logoutSession();
    window.location.assign("/login");
  }

  return (
    <header className="sticky top-3 z-40 mx-3 sm:mx-6 my-2">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white/95 px-5 py-3 shadow-md backdrop-blur-md sm:px-8">
        <Link href={role === "admin" ? "/admin" : role === "student" || role === "teacher" ? "/exam-bank" : "/"} className="flex items-center group py-1">
          <div className="relative flex h-14 sm:h-18 w-auto items-center transition group-hover:scale-105">
            <Image
              src="/logo.png"
              alt="Examify Logo"
              width={512}
              height={512}
              unoptimized
              className="h-12 sm:h-16 w-auto object-contain rounded-xl overflow-hidden"
            />
          </div>
        </Link>
        <button
          type="button"
          className="rounded-lg border border-slate-300 p-2 text-[#1f4e79] md:hidden"
          aria-label={mobileNavOpen ? "Đóng menu" : "Mở menu"}
          aria-expanded={mobileNavOpen}
          onClick={() => setMobileNavOpen((current) => !current)}
        >
          {mobileNavOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <nav
          className={`${mobileNavOpen ? "flex" : "hidden"} w-full flex-col items-stretch gap-2 border-t border-slate-200 pt-3 md:flex md:w-auto md:flex-row md:items-center md:border-0 md:pt-0`}
          onClick={() => setMobileNavOpen(false)}
        >
          {(role === "teacher" || role === "user") && (
            <>
              <Link href="/" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <PlusCircle className="h-5 w-5" /> Tạo đề
              </Link>
              <Link href={role === "teacher" ? "/exam-bank" : "/my-exams"} className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <FolderOpen className="h-5 w-5" /> {role === "teacher" ? "Kho đề thi" : "My Exams"}
              </Link>
              {role === "teacher" && (
                <Link href="/classrooms" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                  <GraduationCap className="h-5 w-5" /> Lớp học
                </Link>
              )}
              <Link href="/history" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <History className="h-5 w-5" /> Lịch sử
              </Link>
            </>
          )}
          {role === "student" && (
            <>
              <Link href="/exam-bank" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <FolderOpen className="h-5 w-5" /> Kho đề thi
              </Link>
              <Link href="/classrooms" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <GraduationCap className="h-5 w-5" /> Lớp học
              </Link>
              <Link href="/history" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <History className="h-5 w-5" /> Lịch sử
              </Link>
            </>
          )}
          {role === "admin" && (
            <>
              <Link href="/admin/guides" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <BookOpen className="h-5 w-5" /> Hướng dẫn
              </Link>
              <Link href="/admin" className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
                <ShieldCheck className="h-5 w-5" /> Admin
              </Link>
            </>
          )}
          {identityReady && role && (
            <button onClick={logout} className="ui-btn-secondary px-4 py-2.5 text-base font-bold">
              <LogOut className="h-5 w-5" /> Đăng xuất
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}
