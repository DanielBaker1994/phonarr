// App state and actions (mirrors ios/Phonarr/Core/AppModel.swift).
// One mutable `state` object; actions change it and call emit(), and the root view re-renders.
// Polling follows the gateway's next_poll_seconds, sends If-None-Match, and stops while the tab is hidden.
import { api, uuid } from "./api.js";
import { log } from "./log.js";
import { text } from "./config.js";
import { emptyFilters } from "./filters.js";
import { IDLE, SUBMITTING, MEDIA_TYPES, SERVICE_FOR, cardKey } from "./models.js";

const listeners = new Set();
export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export function emit() { for (const fn of listeners) fn(); }

export const pref = {
  get(key, fallback) {
    try { const v = localStorage.getItem(`ayr.${key}`); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`ayr.${key}`, JSON.stringify(value)); } catch (e) { log.warn("cache_failed", { key, error: e.message }); }
  },
};

export const state = {
  // Pairing
  paired: api.paired,
  pairingMessage: null,
  onboardingComplete: pref.get("onboardingComplete", false),
  gatewayReachable: true,

  // Server state
  config: null,
  options: null,
  services: {},
  statuses: {},          // "movie:603" -> status
  inFlight: {},          // key -> idempotency key

  // Discovery
  catalog: { movie: [], series: [] },
  facets: { movie: {}, series: {} },
  filters: { movie: emptyFilters(), series: emptyFilters() },
  loading: new Set(),
  discoverError: {},
  includeTracked: pref.get("includeTracked", false),
  requestPlans: {},

  /** Customize Request sheet target: { cards: [...], onSent? } */
  customizing: null,
  /** Long-pressed poster's action sheet: { card, onSelect? } */
  menu: null,

  // Activity
  activity: [],
  activityLoaded: false,
  toast: null,
};

// ------------------------------------------------------------------ toast
let toastTimer;
export function toast(text) {
  state.toast = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { state.toast = null; emit(); }, 2500);
  emit();
}

/** Central error handling for background loads: auth failures re-pair, offline flips the banner. */
function report(error, { show = true } = {}) {
  if (error?.unauthorized) handleAuthFailure();
  else if (error?.transport) { state.gatewayReachable = false; emit(); }
  else if (show) toast(error.message);
}

// ------------------------------------------------------------------ pairing / config
export async function pair(token, remember = true) {
  const t = token.trim();
  const previous = api.token;
  const saved = api.setToken(t, remember);
  try {
    state.config = await api.config();
  } catch (e) {
    api.setToken(previous && previous !== t ? previous : "");
    throw e;
  }
  state.paired = true;
  state.pairingMessage = null;
  state.gatewayReachable = true;
  log.info("signed_in", { remember, saved });
  pref.set("rememberToken", remember);
  if (!saved) toast(text("signin.storage_blocked", { host: location.host }));
  // Gateway already set up (another browser or the iPhone app did it): skip the setup steps.
  if (state.config.radarr.configured && state.config.sonarr.configured) setOnboardingComplete(true);
  emit();
  await startSession();
}

export function setOnboardingComplete(v) {
  state.onboardingComplete = v;
  pref.set("onboardingComplete", v);
  emit();
}

/** The gateway rejected our token (it was rotated): back to sign-in, explained. */
export function handleAuthFailure() {
  if (!state.paired) return;
  log.warn("token_rejected");
  const setupDone = state.onboardingComplete;
  unpair();
  setOnboardingComplete(setupDone);
  state.pairingMessage = "The gateway no longer accepts the saved access token. Enter the current ARR_GATEWAY_TOKEN.";
  emit();
}

export function unpair() {
  stopPolling();
  api.setToken("");
  Object.assign(state, { paired: false, config: null, options: null, statuses: {}, activity: [],
                         catalog: { movie: [], series: [] }, requestPlans: {} });
  setOnboardingComplete(false);
  try { localStorage.removeItem("ayr.catalog"); } catch { /* ignore */ }
  emit();
}

export const isConfigured = (type) => !!state.config?.[SERVICE_FOR[type]]?.configured;
/** Show a section unless we know its server isn't connected (config not loaded yet = assume yes). */
export const hasSection = (type) => state.config === null || isConfigured(type);
export const needsServiceSetup = () => !state.config || (!state.config.radarr.configured && !state.config.sonarr.configured);
export const isServiceDown = (type) => state.services[SERVICE_FOR[type]]?.reachable === false;

export async function refreshConfig() {
  try { state.config = await api.config(); state.gatewayReachable = true; emit(); } catch (e) { report(e); }
}

export async function refreshOptions() {
  try { state.options = await api.options(); emit(); } catch (e) { report(e); }
}

export async function saveConnection(service, url, apiKey, save) {
  const t = save ? await api.saveConnection(service, url, apiKey) : await api.testConnection(service, url, apiKey);
  log.info("connection_done", { verb: save ? "Saved" : "Tested", service, app: t.app_name || "?", version: t.version || "" });
  if (save) {
    await refreshConfig();
    await refreshOptions();
    await reloadDiscovery();
  }
  return t;
}

export async function removeConnection(service) {
  try {
    state.config = await api.removeConnection(service);
    log.info("removed_connection", { service });
    emit();
    await refreshOptions();
    await reloadDiscovery();
  } catch (e) { toast(e.message); }
}

export async function saveDefaults(d) {
  try {
    const saved = await api.saveDefaults(d);
    if (state.options) state.options = { ...state.options, defaults: saved };
    emit();
    await refreshPlans();
  } catch (e) { toast(e.message); }
}

export async function refreshPlans() {
  for (const type of MEDIA_TYPES) {
    if (!isConfigured(type)) continue;
    try { state.requestPlans[type] = await api.requestPlan(type); } catch { /* shown elsewhere */ }
  }
  emit();
}

export function setIncludeTracked(v) {
  state.includeTracked = v;
  pref.set("includeTracked", v);
  emit();
  reloadDiscovery();
}

// ------------------------------------------------------------------ discovery
export async function reloadDiscovery() {
  await Promise.all(MEDIA_TYPES.map(loadDiscover));
}

export async function loadDiscover(type) {
  state.loading.add(type);
  emit();
  try {
    const r = await api.discover(type, state.includeTracked);
    state.catalog[type] = r.items;
    state.facets[type] = r.facets || {};
    state.discoverError[type] = r.error?.message || null;
    if (r.request_plan) state.requestPlans[type] = r.request_plan;
    state.gatewayReachable = true;
    for (const item of r.items) if (!state.statuses[cardKey(item)]) state.statuses[cardKey(item)] = item.status;
    saveCachedCatalog();
  } catch (e) {
    state.discoverError[type] = e.message;
    report(e, { show: false });
  } finally {
    state.loading.delete(type);
    emit();
  }
}

/** Radarr/Sonarr title search for anything not already in discovery (2+ characters). */
export async function search(type, query) {
  try {
    const r = await api.discover(type, true, query);
    const found = r.items.filter((c) => c.source === "search");
    for (const item of found) if (!state.statuses[cardKey(item)]) state.statuses[cardKey(item)] = item.status;
    return { items: found, error: r.error?.message || null };
  } catch (e) {
    report(e, { show: false });
    return { items: [], error: e.message };
  }
}

export function statusOf(type, id, fallback = IDLE) {
  const key = `${type}:${id}`;
  if (state.inFlight[key]) return SUBMITTING;
  return state.statuses[key] || fallback || IDLE;
}
export const statusFor = (card) => statusOf(card.type, card.id, card.status);
export const isSelectable = (card) => ["not_requested", "failed"].includes(statusFor(card).state);

// ------------------------------------------------------------------ requests
// key -> idempotency key of an add whose answer never arrived (connection lost mid-request). The next tap
// sends the same key, so the gateway can tell it's the same request (KB-01).
const unansweredAdds = {};

/** One-tap add with the saved defaults. `override` is one-off and never saved as a default. */
export const add = log.traced("store.add", async (type, id, override = null) => {
  const key = `${type}:${id}`;
  if (state.inFlight[key]) return;                    // repeated taps
  const current = statusOf(type, id);
  if (current.state === "failed") return retry(type, id);
  if (current.state !== "not_requested") return;
  const before = state.statuses[key];
  const idem = unansweredAdds[key] || uuid();
  state.inFlight[key] = idem;
  emit();
  try {
    log.info("request", { key, profile: override?.quality_profile_id ?? "default", root: override?.root_folder ?? "default", seasons: override?.season_monitor ?? "default" });
    const r = await api.request(type, id, override, idem);
    delete unansweredAdds[key];
    state.statuses[key] = r.status;
    log.info("request_result", { key, state: r.status.state, dedup: r.deduplicated ? " (already tracked)" : "" });
    toast(r.deduplicated ? "Already in your library" : "Added to library");
  } catch (e) {
    log.error("request_failed", { key, error: e.message });
    if (e.transport) {
      // We can't tell whether the gateway got it, so don't claim Failed: put the title back as it was,
      // keep the key for the next tap, and let the poll below show what really happened.
      unansweredAdds[key] = idem;
      if (before === undefined) delete state.statuses[key];
      else state.statuses[key] = before;
      toast(text("errors.add_unreached"));
    } else {
      delete unansweredAdds[key];
      state.statuses[key] = { state: "failed", message: e.message, error_kind: e.kind };
      if (e.unauthorized) handleAuthFailure();
    }
  } finally {
    delete state.inFlight[key];
    emit();
  }
  refreshNow();
});

/** Multi-select: add every not-yet-requested card in one call; failed ones are retried. Returns failed keys. */
export const addBatch = log.traced("store.addBatch", async (cards, movie = null, series = null) => {
  const fresh = cards.filter((c) => !state.inFlight[cardKey(c)] && statusFor(c).state === "not_requested");
  const failed = cards.filter((c) => !state.inFlight[cardKey(c)] && statusFor(c).state === "failed");
  const failedKeys = new Set();
  if (fresh.length) {
    for (const c of fresh) state.inFlight[cardKey(c)] = "batch";
    emit();
    log.info("batch", { count: fresh.length });
    try {
      const r = await api.requestBatch(fresh, movie, series);
      for (const item of r.results) {
        const key = `${item.type}:${item.id}`;
        if (item.ok && item.status) state.statuses[key] = item.status;
        if (!item.ok) {
          failedKeys.add(key);
          state.statuses[key] = { state: "failed", message: item.error?.message, error_kind: item.error?.kind };
        }
      }
      const parts = [`Added ${r.added}`];
      if (r.already) parts.push(`${r.already} already in library`);
      if (r.failed) parts.push(`${r.failed} failed`);
      toast(parts.join(" · "));
    } catch (e) {
      log.error("batch_failed", { error: e.message });
      toast(e.message);
      for (const c of fresh) failedKeys.add(cardKey(c));
      if (e.unauthorized) handleAuthFailure();
    } finally {
      for (const c of fresh) delete state.inFlight[cardKey(c)];
      emit();
    }
  }
  for (const c of failed) await retry(c.type, c.id);
  refreshNow();
  return failedKeys;
});

export const retry = log.traced("store.retry", async (type, id) => {
  const key = `${type}:${id}`;
  if (state.inFlight[key]) return;
  state.inFlight[key] = uuid();
  emit();
  try {
    log.info("retry", { key });
    const r = await api.retry(type, id);
    state.statuses[key] = r.status;
    toast("Retrying");
  } catch (e) {
    log.error("retry_failed", { key, error: e.message });
    state.statuses[key] = { state: "failed", message: e.message, error_kind: e.kind };
  } finally {
    delete state.inFlight[key];
    emit();
  }
  refreshNow();
});

/** + on a poster / Add to Library: use the defaults, or open Customize once if none can be resolved. */
export function quickAdd(card) {
  if (state.requestPlans[card.type]?.problem) customize([card]);
  else add(card.type, card.id);
}

export function customize(cards, onSent = null) {
  state.customizing = { cards, onSent };
  emit();
}

// ------------------------------------------------------------------ activity
export async function loadActivity() {
  try {
    const r = await api.activity();
    state.activity = r.items;
    state.services = r.services;
    for (const item of r.items) {
      const key = cardKey(item);
      if (!state.inFlight[key]) state.statuses[key] = item.status;
    }
  } catch (e) {
    report(e);
  } finally {
    state.activityLoaded = true;
    emit();
  }
}

// ------------------------------------------------------------------ polling
let pollTimer = null;
let etag = null;
let viewers = 0;      // Activity or a details page is on screen
// Bumped by every refreshNow/stopPolling. A loop whose poll returns after a newer loop started (or after
// polling stopped) exits instead of rescheduling itself, so there is never more than one loop (KB-04).
let pollGeneration = 0;

export function beginWatching() {
  viewers += 1;
  if (viewers === 1) refreshNow();
}
export function endWatching() { viewers = Math.max(0, viewers - 1); }

export function stopPolling() {
  pollGeneration += 1;
  clearTimeout(pollTimer);
  pollTimer = null;
}

export function refreshNow() {
  if (!state.paired || document.hidden) return;
  stopPolling();
  const generation = pollGeneration;
  const loop = async () => {
    const wait = await pollOnce();
    if (generation !== pollGeneration) return;
    if (state.paired && !document.hidden) pollTimer = setTimeout(loop, wait * 1000);
  };
  pollTimer = setTimeout(loop, 0);
}

async function pollOnce() {
  const viewing = viewers > 0 ? "activity" : "browse";
  try {
    const { snapshot, etag: newTag, pollAfter } = await api.status(viewing, etag);
    if (!state.gatewayReachable) log.info("gateway_back");
    state.gatewayReachable = true;
    if (snapshot) {
      etag = newTag;
      state.services = snapshot.services;
      const merged = { ...state.statuses, ...snapshot.items };
      // Items no longer tracked by the gateway revert to "not requested".
      for (const k of Object.keys(merged)) {
        if (!snapshot.items[k] && !["not_requested", "failed"].includes(merged[k]?.state)) merged[k] = IDLE;
      }
      state.statuses = merged;
      emit();
      if (viewers > 0) await loadActivity();
    }
    return Math.min(Math.max(pollAfter ?? 30, 5), 300);
  } catch (e) {
    report(e, { show: false });
    return state.gatewayReachable ? 30 : 15;
  }
}

// Like the iPhone app's scene phase: no polling at all while the tab or home-screen app is hidden.
document.addEventListener("visibilitychange", () => {
  log.info("visibility", { state: document.visibilityState });
  if (document.hidden) stopPolling();
  else refreshNow();
});

// ------------------------------------------------------------------ session start + cache
export async function startSession() {
  if (!state.paired) return;
  await refreshConfig();
  await Promise.all([refreshOptions(), reloadDiscovery()]);
  refreshNow();
}

function saveCachedCatalog() {
  // Without overviews to stay well inside the ~5 MB localStorage quota.
  const slim = (list) => list.map(({ overview, keywords, ...rest }) => rest);
  pref.set("catalog", { movie: slim(state.catalog.movie), series: slim(state.catalog.series) });
}

function loadCachedCatalog() {
  const c = pref.get("catalog", null);
  if (!c || !state.paired) return;
  state.catalog = { movie: c.movie || [], series: c.series || [] };
  for (const item of [...state.catalog.movie, ...state.catalog.series]) state.statuses[cardKey(item)] = item.status;
}
loadCachedCatalog();

export const trackedCount = () => Object.values(state.statuses).filter((s) => s.state !== "not_requested").length;
