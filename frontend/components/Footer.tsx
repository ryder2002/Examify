"use client";

import Link from "next/link";
import Image from "next/image";
import { useState } from "react";
import { BookOpen, ExternalLink, Shield, FileText, X } from "lucide-react";
import { isDesktop } from "@/lib/api";

const SUPPORT_URL = "https://zalo.me/g/3ekaczmgbnytxav4jj8s";

function ZaloIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className} fill="none">
      <path d="M5 7.5A4.5 4.5 0 0 1 9.5 3h13A4.5 4.5 0 0 1 27 7.5v10A4.5 4.5 0 0 1 22.5 22H15l-5.6 5.1c-.7.6-1.8.1-1.8-.8V22A4.5 4.5 0 0 1 5 17.5v-10Z" fill="currentColor" />
      <path d="M9.3 9h13.4l-8.2 9h8.2v3H9.2l8.3-9H9.3V9Z" fill="white" />
    </svg>
  );
}

export default function Footer() {
  const [supportOpen, setSupportOpen] = useState(false);

  async function openSupportLink() {
    if (isDesktop()) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("open_support_group");
      return;
    }
    window.open(SUPPORT_URL, "_blank", "noopener,noreferrer");
  }

  return (
    <>
      <footer className="mt-auto px-3 sm:px-6 pb-6 pt-4 text-slate-600">
        <div className="mx-auto flex max-w-[1500px] flex-col items-center justify-between gap-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-md sm:flex-row sm:px-8">
        <div className="flex items-center gap-4">
          <Image
            src="/logo.png"
            alt="Examify Logo"
            width={512}
            height={512}
            unoptimized
            className="h-10 sm:h-14 w-auto object-contain rounded-xl overflow-hidden"
          />
          <p className="text-sm font-normal text-slate-500">
            © 2026. All rights reserved.
          </p>
        </div>

        <nav className="flex flex-wrap items-center gap-3 text-sm font-semibold text-slate-600">
          <Link href="/guides" className="flex items-center gap-1.5 transition hover:text-[#1f4e79]">
            <BookOpen className="h-4 w-4 text-slate-400" />
            Hướng dẫn sử dụng
          </Link>
          <Link href="/terms" className="flex items-center gap-1.5 transition hover:text-[#1f4e79]">
            <FileText className="h-4 w-4 text-slate-400" />
            Điều khoản dịch vụ
          </Link>
          <Link href="/privacy" className="flex items-center gap-1.5 transition hover:text-[#1f4e79]">
            <Shield className="h-4 w-4 text-slate-400" />
            Chính sách bảo mật
          </Link>
          <button
            type="button"
            onClick={() => setSupportOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#0068ff] px-3 py-2 text-white shadow-sm transition hover:bg-[#0059d9] hover:shadow-md"
          >
            <ZaloIcon className="h-4 w-4" />
            Nhóm hỗ trợ
          </button>
        </nav>
      </div>
      </footer>
      {supportOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Nhóm hỗ trợ Examify"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) setSupportOpen(false);
          }}
        >
          <div className="relative w-full max-w-md rounded-3xl border border-white/50 bg-white p-6 text-center shadow-2xl sm:p-8">
            <button
              type="button"
              onClick={() => setSupportOpen(false)}
              className="absolute right-4 top-4 rounded-full border border-slate-200 bg-white p-2 text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
              aria-label="Đóng"
            >
              <X className="h-5 w-5" />
            </button>
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-[#0068ff] text-white shadow-lg">
              <ZaloIcon className="h-7 w-7" />
            </div>
            <h2 className="mt-4 text-2xl font-extrabold text-[#1f4e79]">
              Nhóm hỗ trợ Examify
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Quét QR hoặc mở liên kết để tham gia nhóm Zalo.
            </p>
            <div className="mx-auto mt-5 w-fit overflow-hidden rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
              <Image
                src="/ZALO.jpg"
                alt="QR nhóm hỗ trợ Zalo"
                width={260}
                height={260}
                unoptimized
                className="h-56 w-56 rounded-xl object-contain sm:h-64 sm:w-64"
              />
            </div>
            <button
              type="button"
              onClick={() => void openSupportLink()}
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#0068ff] px-4 py-3 font-bold text-white shadow-md transition hover:-translate-y-0.5 hover:bg-[#0059d9] hover:shadow-lg"
            >
              <ExternalLink className="h-5 w-5" />
              Mở liên kết nhóm hỗ trợ
            </button>
            <p className="mt-3 break-all text-xs font-medium text-slate-500">
              {SUPPORT_URL}
            </p>
          </div>
        </div>
      )}
    </>
  );
}
