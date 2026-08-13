import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const nextDir = path.resolve(".next");
const buildManifestPath = path.join(
  nextDir,
  "server/app/quiz/page/build-manifest.json",
);
const clientManifestPath = path.join(
  nextDir,
  "server/app/quiz/page_client-reference-manifest.js",
);

if (!fs.existsSync(buildManifestPath) || !fs.existsSync(clientManifestPath)) {
  throw new Error("Không tìm thấy production build; chạy `npm run build` trước.");
}

const buildManifest = JSON.parse(fs.readFileSync(buildManifestPath, "utf8"));
const clientManifestSource = fs.readFileSync(clientManifestPath, "utf8");
const marker = 'globalThis.__RSC_MANIFEST["/quiz/page"] = ';
const markerOffset = clientManifestSource.indexOf(marker);
if (markerOffset < 0) {
  throw new Error("Không đọc được client reference manifest của /quiz.");
}
const clientManifest = JSON.parse(
  clientManifestSource
    .slice(markerOffset + marker.length)
    .replace(/;\s*$/, ""),
);
const quizModule = clientManifest.clientModules["[project]/app/quiz/page.tsx"];
if (!quizModule) {
  throw new Error("Không tìm thấy client module app/quiz/page.tsx.");
}

const chunkFiles = [
  ...(buildManifest.polyfillFiles ?? []),
  ...(buildManifest.rootMainFiles ?? []),
  ...quizModule.chunks.map((chunk) => chunk.replace(/^\/_next\//, "")),
];
const uniqueChunkFiles = [...new Set(chunkFiles)];
const chunkBuffers = uniqueChunkFiles.map((chunk) =>
  fs.readFileSync(path.join(nextDir, chunk)),
);
const gzipBytes = chunkBuffers.reduce(
  (total, chunk) => total + zlib.gzipSync(chunk, { level: 9 }).length,
  0,
);
const budgetBytes = 250 * 1024;
const routeSource = Buffer.concat(chunkBuffers).toString("utf8");
const forbiddenPackages = ["@tiptap/", "node_modules/xlsx/"];
const leakedPackages = forbiddenPackages.filter((name) => routeSource.includes(name));

console.log(
  `/quiz JavaScript gzip: ${(gzipBytes / 1024).toFixed(1)} KiB / ${(budgetBytes / 1024).toFixed(0)} KiB`,
);
if (leakedPackages.length) {
  throw new Error(`Admin-only package lọt vào /quiz: ${leakedPackages.join(", ")}`);
}
if (gzipBytes > budgetBytes) {
  throw new Error("Bundle /quiz vượt performance budget.");
}
