"use client";

import { useEffect } from "react";
import {
  apiFetch,
  desktopRuntime,
  isDesktop,
} from "@/lib/api";
import { getDeviceIdentity } from "@/lib/device";
import { startDesktopSyncCoordinator } from "@/lib/desktop-sync";

export default function DesktopBootstrap() {
  useEffect(() => {
    if (!isDesktop()) return;
    const stopSync = startDesktopSyncCoordinator();
    void (async () => {
      await desktopRuntime();
      try {
        const deviceKey = await getDeviceIdentity();
        await apiFetch("/api/v1/desktop/auth/upgrade-device", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device_key: deviceKey }),
        });
      } catch {
        // First run has no authenticated legacy device yet. The same upgrade
        // is naturally retried on a later application start after login.
      }
      window.dispatchEvent(new Event("smart-exam-runtime-ready"));
    })().catch(() => undefined);
    return stopSync;
  }, []);
  return null;
}
