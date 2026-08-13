"use client";

import { Suspense } from "react";
import GuideForm from "@/components/GuideForm";

export default function NewGuidePage() {
  return <Suspense fallback={null}><GuideForm mode="new" /></Suspense>;
}
