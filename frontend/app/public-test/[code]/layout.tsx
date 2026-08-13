export function generateStaticParams() {
  // Tauri needs a finite static-export path. Public links copied by the
  // desktop app always point to the remote web origin, while the normal
  // server build continues to resolve every real share code dynamically.
  return [{ code: "desktop-placeholder" }];
}

export default function PublicTestCodeLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
