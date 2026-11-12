// Labels and small helpers shared by the views (mirrors ios/Phonarr/Core/Models.swift).
// The gateway's JSON is used as-is (snake_case); nothing is re-shaped on the client.

export const MEDIA_TYPES = ["movie", "series"];
export const SERVICE_FOR = { movie: "radarr", series: "sonarr" };
export const typeLabel = (t) => (t === "movie" ? "Movie" : "TV Show");
export const tabTitle = (t) => (t === "movie" ? "Movies" : "TV Shows");
export const noun = (t) => (t === "movie" ? "films" : "shows");
export const cardKey = (c) => `${c.type}:${c.id}`;

export const STATE_LABEL = {
  not_requested: "Not requested",
  submitting: "Requesting…",
  queued: "Queued",
  searching: "Searching",
  downloading: "Downloading",
  paused: "Paused",
  stalled: "Stalled",
  importing: "Importing",
  complete: "In library",
  failed: "Failed",
  unknown: "Unknown",
};

export const STATE_ICON = {
  not_requested: "plus",
  submitting: "upload",
  queued: "clock",
  searching: "search",
  downloading: "download",
  paused: "pause",
  stalled: "alert",
  importing: "tray",
  complete: "check",
  failed: "warning",
  unknown: "question",
};

const ACTIVE = new Set(["submitting", "queued", "downloading", "importing", "paused", "stalled"]);
export const isActive = (state) => ACTIVE.has(state);
/** Actually moving: what "In progress" shows. Queued, paused, stalled and waiting for a release are not. */
const DOWNLOADING = new Set(["downloading", "importing"]);
export const isDownloading = (state) => DOWNLOADING.has(state);
export const stateLabel = (s) => STATE_LABEL[s] || STATE_LABEL.unknown;
export const IDLE = { state: "not_requested" };
export const SUBMITTING = { state: "submitting" };

export const SEASON_MODES = ["all", "first", "latest", "future"];
export function seasonLabel(mode) {
  return { first: "First season", latest: "Latest season", future: "Future episodes only" }[mode] || "All seasons";
}

/** "HD-1080p · /movies · All seasons" */
export function planSummary(plan) {
  return [plan?.quality_profile, plan?.root_folder, plan?.season_monitor && seasonLabel(plan.season_monitor)]
    .filter(Boolean).join(" · ");
}

export const SOURCE_LABEL = {
  library: "In your library",
  list: "Your titles list",
  import_list: "Radarr import lists",
  recommended: "Recommended by Radarr",
  tvmaze: "Popular on TVmaze",
  search: "Search result",
};
export const sourceLabel = (s) => SOURCE_LABEL[s] || (s ? s[0].toUpperCase() + s.slice(1) : "");

export function ratingSource(key) {
  return { imdb: "IMDb", tmdb: "TMDB", tvdb: "TVDB", rottenTomatoes: "RT", metacritic: "Metacritic", trakt: "Trakt" }[key] || key;
}

/** Rotten Tomatoes and Metacritic are 0–100; the rest are 0–10. */
export function ratingText(key, value) {
  return key === "rottenTomatoes" || key === "metacritic" ? `${Math.round(value)}%` : value.toFixed(1);
}

export const capitalize = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

export const COMPAT_LABEL = {
  ok: "Compatible",
  info: "Compatible (some optional data missing)",
  warning: "Check recommended",
  error: "Something the app needs has changed",
};
export const COMPAT_ICON = { ok: "check-circle", info: "info", warning: "warning", error: "x-octagon" };
export const COMPAT_ORDER = { ok: 0, info: 1, warning: 2, error: 3 };

export function relativeTime(epochSeconds) {
  const diff = Math.round(epochSeconds - Date.now() / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(diff, "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}
