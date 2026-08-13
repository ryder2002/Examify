import type { Metadata, Viewport } from "next";
import "./globals.css";
import DesktopBootstrap from "@/components/DesktopBootstrap";
import AuthGate from "@/components/AuthGate";

import AppFooter from "@/components/AppFooter";
import PwaBootstrap from "@/components/PwaBootstrap";

export const metadata: Metadata = {
  applicationName: "Examify",
  title: {
    default: "Examify - Hệ thống tạo & làm bài thi online",
    template: "%s | Examify",
  },
  description: "Chuyển đổi PDF scan thành bài thi, lưu trữ và làm bài trực tuyến",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/icons/apple-touch-icon-180.png", sizes: "180x180", type: "image/png" }],
    shortcut: "/icons/icon-192.png",
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Examify",
  },
  formatDetection: { telephone: false },
  other: {
    "msapplication-TileColor": "#1f4e79",
    "msapplication-config": "/browserconfig.xml",
  },
};

export const viewport: Viewport = {
  themeColor: "#1f4e79",
  colorScheme: "light",
  viewportFit: "cover",
  minimumScale: 1,
  maximumScale: 5,
  userScalable: true,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi">
      <body className="flex min-h-screen flex-col bg-slate-50 antialiased">
        <DesktopBootstrap />
        <PwaBootstrap />
        <AuthGate>
          <div className="app-shell-content flex flex-1 flex-col">{children}</div>
          <AppFooter />
        </AuthGate>
      </body>
    </html>
  );
}
