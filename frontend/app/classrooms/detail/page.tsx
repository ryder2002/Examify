"use client";

import { Suspense } from "react";

import ClassroomDetail from "@/components/ClassroomDetail";


export default function ClassroomDetailPage() {
  return (
    <Suspense fallback={null}>
      <ClassroomDetail />
    </Suspense>
  );
}
