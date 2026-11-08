// Service worker: only registered in a secure context (HTTPS, e.g. `tailscale serve`, or localhost).
// - App shell (HTML, JS, CSS, icons): network first, cache as fallback, so an update shows on the next load
//   and the app still opens when the gateway is briefly unreachable.
// - Posters (image.tmdb.org, artworks.thetvdb.com …): cache first, capped.
// - /v1/* (the gateway API): never cached here. The app keeps its own last-known catalogue.
const SHELL = "ayr-shell-v1";
const IMAGES = "ayr-images-v1";
const MAX_IMAGES = 600;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "index.html", "css/app.css", "js/main.js", "manifest.webmanifest"])));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== SHELL && k !== IMAGES).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    if (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/docs") || url.pathname === "/openapi.json") return;
    event.respondWith(networkFirst(req));
  } else if (req.destination === "image") {
    event.respondWith(cacheFirst(req));
  }
});

async function networkFirst(req) {
  const cache = await caches.open(SHELL);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    return (await cache.match(req)) || (await cache.match("index.html")) || Response.error();
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(IMAGES);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") {
    await cache.put(req, res.clone());
    const keys = await cache.keys();
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_IMAGES))) await cache.delete(k);
  }
  return res;
}
