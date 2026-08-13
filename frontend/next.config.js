/** @type {import('next').NextConfig} */
const desktopBuild = process.env.DESKTOP_BUILD === "1";
const nextConfig = {
  reactStrictMode: true,
  output: desktopBuild ? "export" : "standalone",
  images: { unoptimized: true },
  trailingSlash: desktopBuild,
  experimental: {
    // One Listening request can contain a 50 MiB PDF plus four Part audio files.
    // The backend enforces the 50 MiB limit independently for every file.
    proxyClientMaxBodySize: "300mb",
    proxyTimeout: 120000,
  },
  ...(desktopBuild
    ? {}
    : {
        async headers() {
          return [
            {
              source: "/sw.js",
              headers: [{ key: "Cache-Control", value: "no-cache, no-store, must-revalidate" }],
            },
            {
              source: "/manifest.webmanifest",
              headers: [
                { key: "Content-Type", value: "application/manifest+json" },
                { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
              ],
            },
            ...[
              "/logo.png",
              "/logo.png",
              "/icon.png",
              "/favicon.ico",
              "/browserconfig.xml",
            ].map((source) => ({
              source,
              headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
            })),
            {
              source: "/icons/:path*",
              headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
            },
          ];
        },
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*`,
            },
          ];
        },
      }),
};

module.exports = nextConfig;
