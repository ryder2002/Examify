"use client";

import { FileText } from "lucide-react";
import PolicyPage from "@/components/PolicyPage";

export default function TermsPage() {
  return <PolicyPage policyKey="terms" eyebrow="Examify Policy" icon={<FileText className="h-6 w-6" />} fallbackTitle="Điều khoản dịch vụ" />;
}
