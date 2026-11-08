// Client-side filtering, sorting and Discover shelves over the discovery pool, for instant results.
// Mirrors backend/app/discovery.py and ios/Phonarr/Core/Filters.swift: keep all three in step.
// Pure functions with no DOM access, so `node --test web/tests/*.test.mjs` can check them.

export const SORTS = [
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

export function emptyFilters(sort = "title") {
  return { sort, genres: [], decade: null, runtime: null, min_rating: 0, language: null, studio: null,
           collection: null, certification: null, source: null, series_status: null };
}

export function activeCount(f) {
  return f.genres.length + [f.decade, f.runtime, f.min_rating > 0 || null, ...VALUE_FILTERS.map(([k]) => f[k])]
    .filter((v) => v !== null && v !== undefined).length;
}

const fold = (s) => (s || "").toLocaleLowerCase();

export function applyFilters(cards, f, query = "") {
  const q = fold(query.trim());
  const genres = new Set(f.genres);
  const out = cards.filter((c) => {
    if (q && !fold(c.title).includes(q) && !fold(c.original_title).includes(q)) return false;
    if (genres.size && !(c.genres || []).some((g) => genres.has(g))) return false;
    if (f.decade && c.decade !== f.decade) return false;
    if (f.runtime) {
      const [lo, hi] = RUNTIME_BANDS[f.runtime].range;
      if (!c.runtime || c.runtime < lo || c.runtime > hi) return false;
    }
    if (f.min_rating > 0 && (c.rating || 0) < f.min_rating) return false;
    for (const [key, field] of VALUE_FILTERS) {
      if (f[key] && c[field] !== f[key]) return false;
    }
    return true;
  });
  return sortCards(out, f.sort);
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

export function sortCards(cards, sort) {
  switch (sort) {
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
export function buildShelves(cards, minimum = 3) {
  const shelves = [];
  const add = (id, title, items) => {
    if (items.length >= minimum) shelves.push({ id, title, items: items.slice(0, 20) });
  };
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
