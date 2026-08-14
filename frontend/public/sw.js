const CACHE_VERSION = "examify-pwa-v8-icons-transparent";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGE_CACHE = `${CACHE_VERSION}-pages`;
const OFFLINE_ASSET_CACHE = "examify-offline-assets-v1";
const PRECACHE = [
  "/",
  "/offline.html",
  "/manifest.webmanifest",
  "/logo.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (key) =>
                !key.startsWith(CACHE_VERSION) && key !== OFFLINE_ASSET_CACHE,
            )
            .map((key) => caches.delete(key)),
        ),
      ),
  );
  self.clients.claim();
});

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - maxEntries)).map((key) => cache.delete(key)));
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
    void trimCache(STATIC_CACHE, 160);
  }
  return response;
}

async function networkFirstAsset(request) {
  try {
    const response = await fetch(request, { cache: "no-cache" });
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      await cache.put(request, response.clone());
      void trimCache(STATIC_CACHE, 160);
    }
    return response;
  } catch {
    return (await caches.match(request)) || Response.error();
  }
}

async function networkFirstPage(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(PAGE_CACHE);
      await cache.put(request, response.clone());
      void trimCache(PAGE_CACHE, 20);
    }
    return response;
  } catch {
    return (await caches.match(request)) || (await caches.match("/offline.html"));
  }
}

async function offlineAssetFirst(request) {
  const cache = await caches.open(OFFLINE_ASSET_CACHE);
  const cached = await cache.match(request, { ignoreVary: true });
  if (cached) return cached;
  const url = new URL(request.url);
  if (
    request.destination === "image" &&
    url.origin === self.location.origin &&
    !url.pathname.startsWith("/api/")
  ) {
    return networkFirstAsset(request);
  }
  return fetch(request);
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);

  // An offline pack explicitly caches the authorized media URL. Audio elements
  // commonly issue a Range request, so ignore Vary and return the cached full
  // response (HTTP 200 is valid for a client that requested a byte range).
  if (
    event.request.destination === "audio" ||
    event.request.destination === "video" ||
    event.request.destination === "image"
  ) {
    event.respondWith(offlineAssetFirst(event.request));
    return;
  }

  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  const mutableBrandAsset =
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/browserconfig.xml" ||
    url.pathname === "/favicon.ico" ||
    url.pathname === "/icon.png" ||
    url.pathname === "/logo.png" ||
    url.pathname.startsWith("/icons/");
  if (mutableBrandAsset) {
    event.respondWith(networkFirstAsset(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(networkFirstPage(event.request));
    return;
  }

  const cacheable =
    url.pathname.startsWith("/_next/static/") ||
    url.pathname.startsWith("/icons/") ||
    event.request.destination === "style" ||
    event.request.destination === "script" ||
    event.request.destination === "font";
  if (cacheable) event.respondWith(cacheFirst(event.request));
});
