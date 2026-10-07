/* FactoryConnect service worker — offline app shell + web push.
 *
 * - App shell: navigations are network-first and fall back to the cached index.html, so the
 *   technician app (/m) and the operator tablet (/op, own manifest /op.webmanifest) open without signal. Hashed build assets (/assets/*) are cache-first.
 * - Code splitting: every screen is a separate chunk. On install the worker precaches the chunks listed in
 *   /asset-manifest.json (written by the build) so a screen never opened before still works offline, and on
 *   activate it deletes the chunks of previous deploys (the cache would otherwise grow with every release).
 * - API calls (/api, /ws) are NEVER handled here: offline data is kept by the app itself
 *   (src/services/offline.ts) and always shown flagged as "salvo às …".
 * - Push: shows the notification (emergency calls stay on screen and vibrate) and forwards it to open
 *   tabs so they can raise the full-screen alert; a click opens the screen that answers it.
 * Registered with ?dev=1 under `vite` dev: push only, no caching.
 */
const DEV = new URL(self.location.href).searchParams.has("dev");
const CACHE = "fm-shell-v3";
// replaced at build time (vite.config.ts → fm-asset-manifest): a new deploy = a new worker
const BUILD = "__FM_BUILD_ID__";
const MANIFEST = "/asset-manifest.json";

async function buildAssets() {
  try {
    const res = await fetch(MANIFEST, { cache: "no-cache" });
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data.files) ? data.files.filter((f) => typeof f === "string" && f.startsWith("/assets/")) : null;
  } catch {
    return null;
  }
}

/** Precache this build's chunks, 4 at a time (a single failure must not abort the install). */
async function precacheAssets(cache) {
  const files = await buildAssets();
  if (!files) return;
  const queue = files.slice();
  const worker = async () => {
    for (let f = queue.shift(); f; f = queue.shift()) {
      if (await cache.match(f)) continue;
      try {
        const res = await fetch(f);
        if (res.ok) await cache.put(f, res);
      } catch { /* offline during install: cached on first use instead */ }
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
}

/** Drop hashed chunks that no longer belong to the current build. */
async function pruneAssets(cache) {
  const files = await buildAssets();
  if (!files) return;
  const keep = new Set(files);
  const keys = await cache.keys();
  await Promise.all(keys.map((req) => {
    const p = new URL(req.url).pathname;
    return p.startsWith("/assets/") && !keep.has(p) && !/hls/.test(p) ? cache.delete(req) : undefined;
  }));
}
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/op.webmanifest", "/favicon.svg", "/theme-init.js", "/manifest-init.js",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/op-192.png", "/icons/op-512.png"];

self.addEventListener("install", (event) => {
  if (DEV) {
    self.skipWaiting();
    return;
  }
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL).catch(() => undefined).then(() => precacheAssets(c)))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => caches.open(CACHE).then(pruneAssets).catch(() => undefined))
      .then(() => self.clients.claim()),
  );
});

function isApi(url) {
  return url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/");
}

async function networkFirstShell(request) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(request);
    if (res.ok && (res.headers.get("content-type") || "").includes("text/html")) {
      await cache.put("/index.html", res.clone());
    }
    return res;
  } catch (err) {
    const cached = (await cache.match("/index.html")) || (await cache.match("/"));
    if (cached) return cached;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) await cache.put(request, res.clone());
  return res;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  const net = fetch(request)
    .then((res) => {
      if (res.ok) void cache.put(request, res.clone());
      return res;
    })
    .catch(() => hit);
  return hit || net;
}

self.addEventListener("fetch", (event) => {
  if (DEV) return;
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || isApi(url)) return;
  if (req.mode === "navigate") {
    event.respondWith(networkFirstShell(req));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(req));
    return;
  }
  if (SHELL.includes(url.pathname) || url.pathname.startsWith("/icons/")) {
    event.respondWith(staleWhileRevalidate(req));
  }
});

// ── web push ─────────────────────────────────────────────────────

function parsePush(event) {
  if (!event.data) return null;
  try {
    const d = event.data.json();
    return d && typeof d === "object" ? (d.notification && typeof d.notification === "object" ? d.notification : d) : null;
  } catch {
    return { id: String(Date.now()), type: "generic", title: "FactoryConnect", body: event.data.text() };
  }
}

function emergency(n) {
  return n.severity === "emergency" || n.priority === "emergency" || /emergenc/i.test(n.type || n.kind || "");
}

self.addEventListener("push", (event) => {
  const n = parsePush(event);
  if (!n) return;
  const urgent = emergency(n);
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of clients) c.postMessage({ type: "push", notification: n });
    await self.registration.showNotification(n.title || "FactoryConnect", {
      body: n.body || "",
      tag: n.id ? `fm-${n.id}` : undefined,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: n.url || n.link || (n.work_order_id ? `/m/work-orders/${n.work_order_id}` : "/m/work-orders") },
      requireInteraction: urgent,
      renotify: urgent,
      vibrate: urgent ? [400, 150, 400, 150, 800] : [150],
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/m/work-orders";
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of clients) {
      if ("focus" in c) {
        await c.focus();
        if ("navigate" in c) await c.navigate(target).catch(() => undefined);
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
