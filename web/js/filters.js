// Client-side filtering, sorting and Discover shelves over the discovery pool, for instant results.
// Mirrors backend/app/discovery.py and ios/Phonarr/Core/Filters.swift: keep all three in step.
// Pure functions with no DOM access (config only), so `node --test web/tests/*.test.mjs` can check them.
import { cfg } from "./config.js";

export const SORTS = [
  ["trending", "New & trending"],
  ["title", "Title A–Z"],
  ["year_desc", "Newest first"],
  ["year_asc", "Oldest first"],
  ["rating", "Highest rated"],
  ["popularity", "Most popular"],
  ["runtime_asc", "Shortest"],
  ["runtime_desc", "Longest"],
  ["recent", "Recently added"],
];
export const sortLabel = (s) => (SORTS.find(([k]) => k === s) || SORTS[0])[1];

/** Length choices for the from/to pickers, in minutes (0 = any). */
export const runtimeSteps = () => cfg("filters.runtime_steps", [0, 30, 45, 60, 80, 90, 100, 110, 120, 135, 150, 180]);

export const RUNTIME_BANDS = {
  short: { range: [0, 59], label: "Under 1 h" },
  brisk: { range: [60, 89], label: "60–89 min" },
  feature: { range: [90, 119], label: "90–119 min" },
  long: { range: [120, 10000], label: "2 h +" },
};
export const runtimeLabel = (b) => RUNTIME_BANDS[b]?.label || b;

/** Single-value filters: [state key, card field]. */
export const VALUE_FILTERS = [
  ["language", "language"],
  ["studio", "studio"],
  ["collection", "collection"],
  ["certification", "certification"],
  ["source", "source"],
  ["series_status", "series_status"],
];

export const YEAR_MODES = [["any", "Any"], ["decades", "Decades"], ["range", "Range"]];
export const DEFAULT_SORT = "rating";

/** Defaults: highest rated first, English only on, neighbouring genres included once a genre is picked. */
export function emptyFilters(sort = DEFAULT_SORT) {
  return { sort, english_only: true, genres: [], exclude_genres: [], neighbours: true, neighbours_off: [],
           year_mode: "any", decades: [], year_min: null, year_max: null, runtime_min: 0, runtime_max: 0,
           min_rating: 0, language: null, studio: null, collection: null, certification: null, source: null,
           series_status: null };
}

/** Filters that narrow the list, beyond the defaults (English only is a default, so it isn't counted). */
export function activeCount(f) {
  const year = f.year_mode === "decades" ? (f.decades.length ? 1 : 0)
    : f.year_mode === "range" && (f.year_min || f.year_max) ? 1 : 0;
  return f.genres.length + f.exclude_genres.length + year + (f.runtime_min || f.runtime_max ? 1 : 0)
    + (f.min_rating > 0 ? 1 : 0) + VALUE_FILTERS.filter(([k]) => f[k] != null).length;
}

const fold = (s) => (s || "").toLocaleLowerCase();

/** Same as discovery.py genre_neighbours: genres whose titles overlap (shared ÷ either) by at least minOverlap. */
export function genreNeighbours(cards, minOverlap = cfg("filters.neighbour_min_overlap", 0.15)) {
  const titles = new Map();
  cards.forEach((c, i) => { for (const g of c.genres || []) { if (!titles.has(g)) titles.set(g, new Set()); titles.get(g).add(i); } });
  const out = {};
  for (const [a, ta] of titles) {
    const near = [];
    for (const [b, tb] of titles) {
      if (a === b) continue;
      let both = 0;
      for (const i of ta) if (tb.has(i)) both++;
      const overlap = both / (ta.size + tb.size - both);
      if (both && overlap >= minOverlap) near.push({ value: b, overlap: Math.round(overlap * 1000) / 1000 });
    }
    if (near.length) out[a] = near.sort((x, y) => y.overlap - x.overlap || x.value.localeCompare(y.value));
  }
  return out;
}

/** Neighbours of the picked genres, strongest first, leaving out the picks and anything in `skip`. */
export function neighboursFor(picks, map, skip = []) {
  const best = new Map();
  for (const p of picks) for (const n of map?.[p] || []) {
    if (picks.includes(n.value) || skip.includes(n.value)) continue;
    best.set(n.value, Math.max(best.get(n.value) || 0, n.overlap));
  }
  return [...best.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([g, overlap]) => ({ value: g, overlap }));
}

/** Titles with no language at all are kept: missing data never hides a title. */
export function isEnglish(card) {
  const lang = fold(card.language);
  return !lang || cfg("filters.english_names", ["English"]).some((n) => fold(n) === lang);
}

/** The genres a title may carry to match: the picks plus their neighbours (unless switched off). */
export function matchingGenres(f, neighbourMap) {
  if (!f.genres.length) return null;
  const extra = f.neighbours ? neighboursFor(f.genres, neighbourMap, [...f.exclude_genres]).map((n) => n.value)
    .filter((g) => !f.neighbours_off.includes(g)) : [];
  return new Set([...f.genres, ...extra]);
}

export function applyFilters(cards, f, query = "", neighbourMap = null) {
  const q = fold(query.trim());
  const genres = matchingGenres(f, neighbourMap || (f.genres.length && f.neighbours ? genreNeighbours(cards) : null));
  const excluded = new Set(f.exclude_genres);
  const [yLo, yHi] = [f.year_min, f.year_max].filter((y) => y).length === 2
    ? [Math.min(f.year_min, f.year_max), Math.max(f.year_min, f.year_max)] : [f.year_min, f.year_max];
  const out = cards.filter((c) => {
    if (q && !fold(c.title).includes(q) && !fold(c.original_title).includes(q)) return false;
    if (f.english_only && !isEnglish(c)) return false;
    if (excluded.size && (c.genres || []).some((g) => excluded.has(g))) return false;
    if (genres && !(c.genres || []).some((g) => genres.has(g))) return false;
    if (f.year_mode === "decades" && f.decades.length && !f.decades.includes(c.decade)) return false;
    if (f.year_mode === "range") {
      if (yLo && (c.year || 0) < yLo) return false;
      if (yHi && (c.year || 9999) > yHi) return false;
    }
    if (f.runtime_min && (c.runtime || 0) < f.runtime_min) return false;
    if (f.runtime_max && (!c.runtime || c.runtime > f.runtime_max)) return false;
    if (f.min_rating > 0 && (c.rating || 0) < f.min_rating) return false;
    for (const [key, field] of VALUE_FILTERS) {
      if (f[key] && c[field] !== f[key]) return false;
    }
    return true;
  });
  return sortCards(out, f.sort);
}

/** For a card that only matched through a neighbouring genre: that genre, so the card can say "via Thriller". */
export function viaNeighbour(card, f, neighbourMap) {
  if (!f.genres.length || (card.genres || []).some((g) => f.genres.includes(g))) return null;
  const genres = matchingGenres(f, neighbourMap);
  return (card.genres || []).find((g) => genres?.has(g)) || null;
}

/** The rating sources a poster shows under it, in this order, with short labels. */
export const RATING_STRIP = [["imdb", "IMDb"], ["tmdb", "TMDB"], ["rottenTomatoes", "RT"], ["metacritic", "Meta"], ["tvdb", "TVDB"]];
export function ratingStrip(card) {
  const r = card.ratings || {};
  return RATING_STRIP.filter(([k]) => r[k] != null).map(([k, label]) =>
    [label, k === "rottenTomatoes" ? `${Math.round(r[k])}%` : k === "metacritic" ? `${Math.round(r[k])}` : Number(r[k]).toFixed(1)]);
}

const byTitle = (a, b) => (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" });

/** Items missing the sort key always go last; ties break by title. */
function by(cards, key, descending) {
  return [...cards].sort((a, b) => {
    const x = key(a), y = key(b);
    const hasX = x !== null && x !== undefined, hasY = y !== null && y !== undefined;
    if (hasX && hasY) {
      if (x === y) return byTitle(a, b);
      return (descending ? x > y : x < y) ? -1 : 1;
    }
    if (hasX) return -1;
    if (hasY) return 1;
    return byTitle(a, b);
  });
}

/** New and trending: titles from the last `filters.trending_years` years, scored by TMDB popularity (films) or
 *  total votes across sources (TV has no popularity). Older or unscored titles get null. Same as discovery.py. */
export function trendingScore(card, thisYear = new Date().getFullYear()) {
  if ((card.year || 0) <= thisYear - cfg("filters.trending_years", 2)) return null;
  if (card.popularity != null) return card.popularity;
  const votes = Object.values(card.votes || {}).reduce((a, b) => a + b, 0);
  return votes || null;
}

export function sortCards(cards, sort, thisYear = new Date().getFullYear()) {
  switch (sort) {
    case "trending": return by(cards, (c) => trendingScore(c, thisYear), true);
    case "year_desc": return by(cards, (c) => c.year, true);
    case "year_asc": return by(cards, (c) => c.year, false);
    case "rating": return by(cards, (c) => c.rating, true);
    case "popularity": return by(cards, (c) => c.popularity, true);
    case "runtime_asc": return by(cards, (c) => c.runtime, false);
    case "runtime_desc": return by(cards, (c) => c.runtime, true);
    case "recent": return by(cards, (c) => c.added, true);
    default: return [...cards].sort(byTitle);
  }
}

/** Discover shelves generated from metadata, so the home screen isn't just "latest" and "popular". */
export function buildShelves(cards, minimum = 3, thisYear = new Date().getFullYear()) {
  const shelves = [];
  const add = (id, title, items) => {
    if (items.length >= minimum) shelves.push({ id, title, items: items.slice(0, 20) });
  };
  add("trending", "New & trending", sortCards(cards.filter((c) => trendingScore(c, thisYear) != null), "trending", thisYear));
  add("rated", "Highest rated", cards.filter((c) => c.rating != null).sort((a, b) => b.rating - a.rating));
  add("early", "Before 1930", cards.filter((c) => (c.year ?? 9999) < 1930).sort((a, b) => (a.year ?? 0) - (b.year ?? 0)));
  add("short", "Under 90 minutes", cards.filter((c) => c.runtime > 0 && c.runtime < 90));
  const counts = new Map();
  for (const c of cards) for (const g of c.genres || []) counts.set(g, (counts.get(g) || 0) + 1);
  const topGenres = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([g]) => g);
  for (const g of topGenres) add(`genre-${g}`, g, cards.filter((c) => (c.genres || []).includes(g)));
  const decades = [...new Set(cards.map((c) => c.decade).filter((d) => d >= 1930))].sort((a, b) => a - b);
  for (const d of decades) add(`decade-${d}`, `The ${d}s`, cards.filter((c) => c.decade === d));
  return shelves;
}
