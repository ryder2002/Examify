"use client";

import { usePathname } from "next/navigation";

import Footer from "@/components/Footer";

export default function AppFooter() {
  const pathname = usePathname();
  if (pathname === "/quiz" || pathname.startsWith("/quiz/")) return null;
  return <Footer />;
}
