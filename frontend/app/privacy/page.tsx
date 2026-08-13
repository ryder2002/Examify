"use client";

import { Shield } from "lucide-react";
import PolicyPage from "@/components/PolicyPage";

export default function PrivacyPage() {
  return <PolicyPage policyKey="privacy" eyebrow="Examify Policy" icon={<Shield className="h-6 w-6" />} fallbackTitle="Chính sách bảo mật" />;
}
