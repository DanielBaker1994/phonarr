// Talks only to the Phonarr gateway (mirrors ios/Phonarr/Core/APIClient.swift).
// The web frontend is served by the gateway itself, so requests are same-origin (/v1/...).
// Radarr/Sonarr API keys are sent once when saving a connection and never stored in the browser.
import { cfg, text } from "./config.js";
import { log, registerSecret } from "./log.js";

export class APIError extends Error {
  constructor(kind, message, status = 0, requestId = null) {
    super(message);
    this.kind = kind;       // "config" | "auth" | "unavailable" | "not_found" | "request" | "transport" | "internal" | …
    this.status = status;
    this.requestId = requestId;   // matches "[<id>]" in the gateway's log lines
  }
  get unauthorized() { return this.status === 401; }
  get transport() { return this.kind === "transport"; }
}

const TOKEN_KEY = "ayr.token";

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

/** crypto.randomUUID only exists in secure contexts (HTTPS / localhost). Plain HTTP over Tailscale isn't one. */
export function uuid() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export const api = {
  token: readToken(),

  setToken(token) {
    this.token = token || "";
    registerSecret(this.token);
    try {
      if (this.token) localStorage.setItem(TOKEN_KEY, this.token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* private mode: token lasts for this tab only */ }
  },

  get paired() { return !!this.token; },

  async raw(path, { method = "GET", query, body, headers = {}, quiet = false, long = false } = {}) {
    if (!this.token) throw new APIError("config", text("errors.not_signed_in"));
    // Sent to the gateway, which uses it in its own log lines, so one id finds a call in both logs.
    const rid = `web-${uuid().slice(0, 8)}`;
    const url = new URL(path, location.origin);
    for (const [k, v] of Object.entries(query || {})) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
    const init = {
      method,
      cache: "no-store",
      headers: { Authorization: `Bearer ${this.token}`, Accept: "application/json", "X-Request-Id": rid, ...headers },
    };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
    const label = `${method} ${url.pathname}`;
    const started = performance.now();
    const ctrl = new AbortController();
    // Slow-by-nature calls (API check, debug report) get a longer timeout and a "may still finish" message.
    const timeoutMs = long ? cfg("api.long_timeout_ms", 120000) : cfg("api.timeout_ms", 20000);
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res;
    try {
      res = await fetch(url, { ...init, signal: ctrl.signal });
    } catch (e) {
      log.error("api_failed", { label, rid, name: e.name, error: e.message });
      const message = e.name !== "AbortError" ? text("errors.unreachable")
        : long ? text("errors.timeout_long", { seconds: Math.round(timeoutMs / 1000) }) : text("errors.timeout");
      throw new APIError("transport", message, 0, rid);
    } finally {
      clearTimeout(timer);
    }
    const ms = Math.round(performance.now() - started);
    if (res.status >= 400) {
      const bodyText = await res.text();
      log.warn("api_error", { label, rid, status: res.status, ms, body: bodyText.slice(0, cfg("api.log_body_chars", 300)) });
      if (res.status === 401) {
        throw new APIError("auth", text("errors.auth"), 401, rid);
      }
      let err = null;
      try { err = JSON.parse(bodyText).error; } catch { /* not JSON */ }
      throw new APIError(err?.kind || "unavailable", err?.message || text("errors.http_status", { status: res.status }),
        res.status, err?.request_id || rid);
    }
    if (!(quiet && res.status === 304)) log.info("api_ok", { label, rid, status: res.status, ms });
    return res;
  },

  async json(path, opts) {
    const res = await this.raw(path, opts);
    try { return await res.json(); } catch (e) {
      log.error("api_failed", { label: path, rid: res.headers.get("X-Request-Id") || "-", name: "parse", error: e.message });
      throw new APIError("unavailable", text("errors.bad_response", { error: e.message }));
    }
  },

  /** GET /v1/health needs no token: is the gateway up, and can it reach Radarr and Sonarr? */
  async health() {
    const started = performance.now();
    let res;
    try {
      res = await fetch("/v1/health", { cache: "no-store" });
    } catch (e) {
      throw new APIError("transport", text("errors.unreachable"));
    }
    try {
      return { ...(await res.json()), ms: Math.round(performance.now() - started) };
    } catch (e) {
      // It answered, just not as the gateway: a proxy page or another app on the port, not "unreachable".
      throw new APIError("unavailable", text("errors.health_not_json", { status: res.status }), res.status);
    }
  },

  // ---- Endpoints (see README "Gateway API") ----
  config: () => api.json("/v1/config"),
  testConnection: (service, baseUrl, apiKey) =>
    api.json(`/v1/config/${service}/test`, { method: "POST", body: { base_url: baseUrl, api_key: apiKey || null } }),
  saveConnection: (service, baseUrl, apiKey) =>
    api.json(`/v1/config/${service}`, { method: "PUT", body: { base_url: baseUrl, api_key: apiKey || null } }),
  removeConnection: (service) => api.json(`/v1/config/${service}`, { method: "DELETE" }),

  options: () => api.json("/v1/options"),
  saveDefaults: (d) => api.json("/v1/defaults", { method: "PUT", body: d }),
  requestPlan: (type) => api.json("/v1/request-plan", { query: { type } }),

  /** `q` (2+ characters) also searches Radarr/Sonarr, so titles outside discovery can be found and added. */
  discover: (type, includeTracked, q) =>
    api.json("/v1/discover", { query: { type, include_tracked: includeTracked ? "true" : "false", q } }),
  detail: (type, id) => api.json(`/v1/titles/${type}/${id}`),
  similar: (type, id, mix, limit = 20) =>
    api.json(`/v1/titles/${type}/${id}/similar`, { query: { mix: mix ? "true" : "false", limit } }),

  request: (type, id, override, idempotencyKey) =>
    api.json("/v1/requests", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: { type, id, quality_profile_id: override?.quality_profile_id ?? null,
              root_folder: override?.root_folder ?? null, season_monitor: override?.season_monitor ?? null },
    }),
  requestBatch: (cards, movie, series) =>
    api.json("/v1/requests/batch", {
      method: "POST",
      body: { items: cards.map((c) => ({ type: c.type, id: c.id })), movie: movie || null, series: series || null },
    }),
  retry: (type, id) => api.json(`/v1/requests/${type}/${id}/retry`, { method: "POST" }),

  /** Returns { snapshot: null } on 304 Not Modified. */
  async status(viewing, etag) {
    const res = await api.raw("/v1/status", {
      query: { viewing }, headers: etag ? { "If-None-Match": etag } : {}, quiet: true,
    });
    const pollAfter = Number(res.headers.get("X-Poll-After")) || null;
    if (res.status === 304) return { snapshot: null, etag, pollAfter };
    const snapshot = await res.json();
    return { snapshot, etag: res.headers.get("ETag"), pollAfter: snapshot.next_poll_seconds ?? pollAfter };
  },
  activity: () => api.json("/v1/activity", { query: { viewing: "activity" } }),

  compatReports: () => api.json("/v1/compat"),
  runCompatCheck: () => api.json("/v1/compat/check", { method: "POST", long: true }),

  /** The gateway's last log lines, parsed (Settings → Logs), with the HTTP Date header and when the call
   *  went out and came back, so the screen can work out the clock difference. */
  async debugLogs({ lines, level, rid } = {}) {
    const sentAt = Date.now();
    const res = await api.raw("/v1/debug/logs", { query: { lines, level, rid } });
    const receivedAt = Date.now();
    return { ...(await res.json()), dateHeader: res.headers.get("Date"), sentAt, receivedAt };
  },

  /** Gateway debug zip with this browser's log and device info added (POST /v1/debug/bundle). */
  async debugBundle(log, info) {
    const res = await api.raw("/v1/debug/bundle", { method: "POST", body: { name: "web", log, info }, long: true });
    return res.blob();
  },
};

registerSecret(api.token);
