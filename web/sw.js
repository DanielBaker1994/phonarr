// Service worker: only registered in a secure context (HTTPS, e.g. `tailscale serve`, or localhost).
// - App shell (HTML, JS, CSS, icons): network first, cache as fallback, so an update shows on the next load
//   and the app still opens when the gateway is briefly unreachable.
// - Posters (image.tmdb.org, artworks.thetvdb.com …): cache first, capped. The gateway already sends small sizes
//   (TMDB w342, TheTVDB _t thumbnails). The image is returned straight away and stored afterwards, so a slow or
//   failed cache write (storage full: these hosts send no CORS headers, so browsers store opaque responses and
//   count each one at several MB) never delays or breaks a poster.
// - /v1/* (the gateway API): never cached here. The app keeps its own last-known catalogue.
const SHELL = "ayr-shell-v1";
const IMAGES = "ayr-images-v2";   // v1 held full-size originals; the new name drops that cache on activate
const MAX_IMAGES = 600;
const TRIM_EVERY = 25;            // writes between cap checks (listing the cache on every poster is slow)
let writes = 0;

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
    event.respondWith(cacheFirst(event, req));
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

async function cacheFirst(event, req) {
  let cache = null;
  try {
    cache = await caches.open(IMAGES);
    const hit = await cache.match(req);
    if (hit) return hit;
  } catch (e) { /* storage unavailable: just fetch */ }
  const res = await fetch(req);
  if (cache && (res.ok || res.type === "opaque")) event.waitUntil(store(cache, req, res.clone()));
  return res;
}

async function store(cache, req, res) {
  try {
    await cache.put(req, res);
    if (++writes % TRIM_EVERY === 0) await trim(cache, MAX_IMAGES);
  } catch (e) {
    await trim(cache, MAX_IMAGES / 2).catch(() => {});   // quota hit: drop the oldest half, try again next time
  }
}

async function trim(cache, keep) {
  const keys = await cache.keys();                       // insertion order: oldest first
  await Promise.all(keys.slice(0, Math.max(0, keys.length - keep)).map((k) => cache.delete(k)));
}
