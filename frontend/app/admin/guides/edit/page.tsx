"use client";

import { Suspense } from "react";
import GuideForm from "@/components/GuideForm";

export default function EditGuidePage() {
  return <Suspense fallback={null}><GuideForm mode="edit" /></Suspense>;
}
