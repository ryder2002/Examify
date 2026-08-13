"use client";

import { Download, MonitorDown, PlusSquare, Share2, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { resolveAuthState } from "@/lib/api";

type InstallChoice = { outcome: "accepted" | "dismissed"; platform: string };

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<InstallChoice>;
}

interface NavigatorWithStandalone extends Navigator {
  standalone?: boolean;
}

type InstallPlatform = "android" | "ios" | "mac-safari" | "desktop";

function isInstalled() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as NavigatorWithStandalone).standalone === true ||
    document.referrer.startsWith("android-app://")
  );
}

function installPlatform(): InstallPlatform {
  const userAgent = navigator.userAgent;
  const isAppleMobile =
    /iPad|iPhone|iPod/i.test(userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (isAppleMobile) return "ios";
  if (/Android/i.test(userAgent)) return "android";
  if (/Macintosh/i.test(userAgent) && /Safari/i.test(userAgent) && !/Chrome|CriOS|Edg/i.test(userAgent)) {
    return "mac-safari";
  }
  return "desktop";
}

function InstallInstructions({ platform }: { platform: InstallPlatform }) {
  if (platform === "ios") {
    return (
      <ol className="space-y-3 text-sm text-slate-600">
        <li className="flex gap-3"><Share2 className="mt-0.5 h-5 w-5 shrink-0 text-[#1f4e79]" /><span>Mở Examify bằng <strong>Safari</strong>, rồi nhấn nút <strong>Chia sẻ</strong>.</span></li>
        <li className="flex gap-3"><PlusSquare className="mt-0.5 h-5 w-5 shrink-0 text-[#1f4e79]" /><span>Chọn <strong>Thêm vào Màn hình chính</strong> rồi nhấn <strong>Thêm</strong>.</span></li>
      </ol>
    );
  }
  if (platform === "android") {
    return <p className="text-sm leading-6 text-slate-600">Trong Chrome, mở menu <strong>⋮</strong> rồi chọn <strong>Cài đặt ứng dụng</strong> hoặc <strong>Thêm vào màn hình chính</strong>.</p>;
  }
  if (platform === "mac-safari") {
    return <p className="text-sm leading-6 text-slate-600">Trong Safari, chọn <strong>Tệp (File) → Thêm vào Dock (Add to Dock)</strong>, sau đó xác nhận tên <strong>Examify</strong>.</p>;
  }
  return <p className="text-sm leading-6 text-slate-600">Nhấn biểu tượng <strong>Cài đặt</strong> ở bên phải thanh địa chỉ, hoặc mở menu trình duyệt và chọn <strong>Cài đặt Examify</strong>.</p>;
}

export default function PwaBootstrap() {
  const pathname = usePathname();
  const [offline, setOffline] = useState(false);
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(true);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);
  const [platform, setPlatform] = useState<InstallPlatform>("desktop");
  const [secureContext, setSecureContext] = useState(true);
  const examInProgress = pathname === "/quiz" || pathname.startsWith("/quiz/");

  const onboardingKey = (userId: string) =>
    `examify-pwa-install-onboarding-v1:${userId}`;

  useEffect(() => {
    const tauriRuntime = "__TAURI_INTERNALS__" in window;
    setInstalled(tauriRuntime || isInstalled());
    setPlatform(installPlatform());
    setSecureContext(window.isSecureContext);

    const maybeShowFirstLoginDialog = () => {
      void resolveAuthState()
        .then((state) => {
          const userId = state.user?.id;
          if (
            state.authenticated &&
            userId &&
            !tauriRuntime &&
            !isInstalled() &&
            !localStorage.getItem(onboardingKey(userId))
          ) {
            setShowInstallDialog(true);
          }
        })
        .catch(() => undefined);
    };

    const registerServiceWorker = () => {
      if ("serviceWorker" in navigator) {
        void navigator.serviceWorker
          .register("/sw.js", { scope: "/", updateViaCache: "none" })
          .then((registration) => registration.update())
          .catch(() => undefined);
      }
    };
    if (document.readyState === "complete") registerServiceWorker();
    else window.addEventListener("load", registerServiceWorker, { once: true });

    const onBeforeInstall = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as BeforeInstallPromptEvent);
      setInstalled(false);
    };
    const onInstalled = () => {
      setInstallPrompt(null);
      setShowInstructions(false);
      setInstalled(true);
    };
    const updateNetworkState = () => {
      setOffline(!navigator.onLine);
      document.documentElement.dataset.network = navigator.onLine ? "online" : "offline";
      window.dispatchEvent(new Event(navigator.onLine ? "app-online" : "app-offline"));
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onInstalled);
    window.addEventListener("smart-exam-auth-changed", maybeShowFirstLoginDialog);
    window.addEventListener("online", updateNetworkState);
    window.addEventListener("offline", updateNetworkState);
    updateNetworkState();
    return () => {
      window.removeEventListener("load", registerServiceWorker);
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
      window.removeEventListener("smart-exam-auth-changed", maybeShowFirstLoginDialog);
      window.removeEventListener("online", updateNetworkState);
      window.removeEventListener("offline", updateNetworkState);
    };
  }, [examInProgress]);

  useEffect(() => {
    // Handles an already-authenticated refresh as well as a login event that
    // happened before this bootstrap component mounted.
    void resolveAuthState()
      .then((state) => {
        const userId = state.user?.id;
        if (
          state.authenticated &&
          userId &&
          !isInstalled() &&
          !localStorage.getItem(onboardingKey(userId))
        ) {
          setShowInstallDialog(true);
        }
      })
      .catch(() => undefined);
  }, [pathname]);

  const markOnboardingSeen = async () => {
    const state = await resolveAuthState().catch(() => null);
    const userId = state?.user?.id;
    if (userId) localStorage.setItem(onboardingKey(userId), "1");
  };

  const requestInstall = async () => {
    if (!installPrompt) {
      setShowInstallDialog(false);
      setShowInstructions(true);
      return;
    }
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === "accepted") setInstalled(true);
    setInstallPrompt(null);
    await markOnboardingSeen();
    setShowInstallDialog(false);
  };

  const skipInstall = () => {
    void markOnboardingSeen();
    setShowInstallDialog(false);
  };

  const openInstall = () => {
    if (installPrompt) {
      void requestInstall();
      return;
    }
    setShowInstructions(true);
  };

  return (
    <>
      {offline && (
        <div className="fixed inset-x-0 bottom-0 z-[100] border-t border-amber-300 bg-amber-50 px-4 py-2 text-center text-xs font-bold text-amber-900 [padding-bottom:calc(0.5rem+env(safe-area-inset-bottom))]">
          Đang offline · Đáp án được lưu trên thiết bị và sẽ đồng bộ khi có internet.
        </div>
      )}
      {!installed && !examInProgress && (
        <button
          type="button"
          onClick={openInstall}
          className="fixed bottom-4 right-4 z-[105] inline-flex items-center gap-2 rounded-full bg-[#1f4e79] px-4 py-3 text-sm font-extrabold text-white shadow-xl transition hover:bg-[#163b5f] focus:outline-none focus:ring-4 focus:ring-blue-200 [bottom:calc(1rem+env(safe-area-inset-bottom))]"
          aria-label="Cài đặt ứng dụng Examify"
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          Cài đặt Examify
        </button>
      )}
      {showInstallDialog && !installed && (
        <div className="fixed inset-0 z-[115] flex items-end justify-center bg-slate-950/50 p-3 sm:items-center sm:p-4">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="pwa-first-login-title"
            className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl sm:p-6"
          >
            <div className="flex items-start gap-3">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-[#1f4e79]">
                <MonitorDown className="h-6 w-6" aria-hidden="true" />
              </div>
              <div>
                <h2 id="pwa-first-login-title" className="font-extrabold text-slate-900">
                  Cài đặt Examify trên thiết bị?
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  Cài đặt để mở nhanh hơn, học ổn định hơn và hỗ trợ làm bài offline.
                </p>
              </div>
            </div>
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <InstallInstructions platform={platform} />
            </div>
            <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button type="button" onClick={skipInstall} className="ui-btn-secondary px-4 py-2.5 text-sm">
                Bỏ qua
              </button>
              <button type="button" onClick={() => void requestInstall()} className="ui-btn-primary px-4 py-2.5 text-sm">
                {installPrompt ? "Cài đặt ngay" : platform === "ios" ? "Xem hướng dẫn" : "Mở hướng dẫn cài đặt"}
              </button>
            </div>
          </section>
        </div>
      )}
      {showInstructions && (
        <div className="fixed inset-0 z-[110] flex items-end justify-center bg-slate-950/45 p-4 sm:items-center" role="presentation" onMouseDown={() => setShowInstructions(false)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="pwa-install-title"
            className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-blue-50 text-[#1f4e79]"><MonitorDown className="h-6 w-6" aria-hidden="true" /></div>
                <div><h2 id="pwa-install-title" className="font-extrabold text-slate-900">Cài đặt Examify</h2><p className="text-xs text-slate-500">Điện thoại, iPad và máy tính</p></div>
              </div>
              <button type="button" onClick={() => setShowInstructions(false)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="Đóng hướng dẫn cài đặt"><X className="h-5 w-5" /></button>
            </div>
            <div className="mt-5"><InstallInstructions platform={platform} /></div>
            {!secureContext && (
              <p className="mt-4 rounded-lg bg-amber-50 p-3 text-xs font-semibold text-amber-900">Trình duyệt chỉ cho cài PWA khi website chạy qua HTTPS.</p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
