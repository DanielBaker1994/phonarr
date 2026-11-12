// Discover, Movies and TV Shows (mirrors ios/Phonarr/Views/BrowseViews.swift).
import { html, useEffect, useRef, useState } from "../../vendor/preact-htm.js";
import { cfg } from "../config.js";
import { Icon } from "../icons.js";
import { activeCount, applyFilters, buildShelves, viaNeighbour } from "../filters.js";
import { cardKey, noun, tabTitle, planSummary, isDownloading } from "../models.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { ConnectServerCard, Empty, Notice, Page, PosterCell, PosterRow, ServiceBanner, Spinner } from "./components.js";
import { ActiveFilterBar, FilterSheet, SortMenu } from "./filters.js";

// ------------------------------------------------------------------ Discover
export function DiscoverView() {
  useEffect(() => { S.loadActivity(); }, []);
  const movies = state.catalog.movie, series = state.catalog.series;
  const recommended = movies.filter((c) => c.source === "recommended" && S.statusFor(c).state === "not_requested");
  const allShelves = buildShelves([...movies, ...series]);
  const trending = allShelves.find((s) => s.id === "trending");   // shown near the top, the rest further down
  const shelves = allShelves.filter((s) => s !== trending);

  // From Activity (the catalogue hides requested titles after a reload), plus anything just added here.
  const seen = new Set();
  const inProgress = [...state.activity.map((a) => ({ ...a, genres: [] })), ...movies, ...series].filter((c) => {
    return isDownloading(S.statusFor(c).state) && !seen.has(cardKey(c)) && seen.add(cardKey(c));
  });

  const empty = (S.hasSection("movie") || S.hasSection("series")) && !movies.length && !series.length && !state.loading.size;
  const refresh = html`<button class="icon-btn" aria-label="Refresh" onClick=${() => { S.reloadDiscovery(); S.loadActivity(); }}>
    ${state.loading.size ? html`<${Spinner} small />` : html`<${Icon} name="retry" />`}</button>`;

  return html`<${Page} title="Discover" actions=${refresh}>
    <div class="stack">
      <${ServiceBanner} />
      ${inProgress.length > 0 && html`<${PosterRow} title="In progress" items=${inProgress} />`}
      ${trending && html`<${PosterRow} title=${trending.title} items=${trending.items} />`}
      ${recommended.length > 0 && html`<${PosterRow} title="Recommended by Radarr" items=${recommended} />`}
      ${S.hasSection("series") && html`<${PosterRow} title="TV to add" items=${series} seeAll="#/tv" />`}
      ${shelves.map((s) => html`<${PosterRow} key=${s.id} title=${s.title} items=${s.items} />`)}
      ${["movie", "series"].filter((t) => !S.hasSection(t)).map((t) => html`<div class="pad"><${ConnectServerCard} type=${t} /></div>`)}
      ${empty && html`<${Empty} icon="sparkles" title="Nothing to discover yet">
        Search in Movies or TV Shows to find anything Radarr or Sonarr can look up. Discovery also fills in from your
        Radarr import lists, Radarr's recommendations, and the titles list on the gateway (titles.json).<//>`}
    </div>
  <//>`;
}

// ------------------------------------------------------------------ Movies / TV Shows grid
export function MediaGridView({ type }) {
  if (!S.hasSection(type)) {
    return html`<${Page} title=${tabTitle(type)}><div class="pad"><${ConnectServerCard} type=${type} /></div><//>`;
  }
  return html`<${Grid} key=${type} type=${type} />`;
}

function Grid({ type }) {
  const [query, setQuery] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [remote, setRemote] = useState({ items: [], error: null, busy: false });
  const [sending, setSending] = useState(false);

  const filters = state.filters[type];
  const setFilters = (f) => { state.filters[type] = f; S.emit(); };
  const all = state.catalog[type];
  const neighbourMap = state.facets[type]?.genre_neighbours || null;
  const shown = applyFilters(all, filters, query, neighbourMap);
  // No cap: every match is reachable. Render a page at a time and add the next as the end scrolls into view.
  const pageSize = cfg("filters.page_size", 60);
  const [limit, setLimit] = useState(pageSize);
  const sentinel = useRef(null);
  useEffect(() => setLimit(pageSize), [type, query, JSON.stringify(filters)]);
  useEffect(() => {
    if (!sentinel.current || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) setLimit((n) => n + pageSize); }, { rootMargin: "600px" });
    io.observe(sentinel.current);
    return () => io.disconnect();
  }, [sentinel.current, limit, shown.length > limit]);
  const shownKeys = new Set(shown.map(cardKey));
  const remoteShown = applyFilters(remote.items, filters, "", neighbourMap).filter((c) => !shownKeys.has(cardKey(c)));
  const visible = [...shown, ...remoteShown];
  const loading = state.loading.has(type);

  useEffect(() => { if (!all.length) S.loadDiscover(type); }, [type]);

  // Radarr/Sonarr search for 2+ characters, debounced while typing.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setRemote({ items: [], error: null, busy: false }); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      setRemote((r) => ({ ...r, busy: true }));
      const r = await S.search(type, q);
      if (!cancelled) setRemote({ items: r.items, error: r.error, busy: false });
    }, 450);
    return () => { cancelled = true; clearTimeout(t); };
  }, [query, type]);

  // Drop anything that got requested elsewhere (another device, Radarr itself) from the selection.
  useEffect(() => {
    const live = [...selected].filter((k) => ["not_requested", "failed"].includes((state.statuses[k] || { state: "not_requested" }).state));
    if (live.length !== selected.size) setSelected(new Set(live));
  }, [[...selected].map((k) => state.statuses[k]?.state).join()]);

  const selection = selecting ? {
    has: (k) => selected.has(k),
    toggle: (k) => { const n = new Set(selected); n.has(k) ? n.delete(k) : n.add(k); setSelected(n); },
  } : null;
  const startSelect = (card) => () => { setSelecting(true); setSelected(new Set([cardKey(card)])); };
  const finish = (failed) => { setSelected(new Set(failed)); if (!failed.size) setSelecting(false); };

  const cell = (c) => html`<${PosterCell} key=${cardKey(c)} card=${c} caption=${caption(c, filters, neighbourMap)}
    selection=${selection} onSelect=${startSelect(c)} />`;

  const selectable = visible.filter(S.isSelectable);
  const actions = selecting
    ? html`<button class="link" onClick=${() => setSelected(selected.size < selectable.length ? new Set(selectable.map(cardKey)) : new Set())}>
        ${selected.size < selectable.length ? "Select All" : "Deselect All"}</button>
       <button class="link strong" onClick=${() => { setSelecting(false); setSelected(new Set()); }}>Done</button>`
    : html`<button class="link" disabled=${!visible.length} onClick=${() => setSelecting(true)}>Select</button>
       <${SortMenu} type=${type} filters=${filters} onChange=${setFilters} />
       <button class="icon-btn" aria-label="Filters" onClick=${() => setShowFilters(true)}>
         <${Icon} name="filter" />${filters && activeBadge(filters)}</button>`;

  const title = selecting ? (selected.size ? `${selected.size} selected` : `Select ${noun(type)}`) : tabTitle(type);

  return html`<${Page} title=${title} actions=${actions} cls=${selecting ? "selecting" : ""}>
    <div class="searchbar">
      <${Icon} name="search" size=${16} />
      <input type="search" placeholder=${`Search ${noun(type)} or find new ones`} value=${query}
        onInput=${(e) => setQuery(e.target.value)} autocomplete="off" autocapitalize="off" spellcheck="false" />
    </div>
    <${ServiceBanner} only=${type} />
    ${state.discoverError[type] && html`<${Notice}>${state.discoverError[type]}<//>`}
    <${ActiveFilterBar} filters=${filters} onChange=${setFilters} shown=${shown.length} total=${all.length} />
    ${loading && !all.length ? html`<div class="center pad"><${Spinner} /></div>` : html`<div class="grid">${shown.slice(0, limit).map(cell)}</div>
      ${shown.length > limit && html`<div class="load-more" ref=${sentinel}>
        <button class="btn" onClick=${() => setLimit(limit + pageSize)}>Show more (${shown.length - limit} left)</button></div>`}`}
    ${query.trim().length >= 2 && html`<section class="pad">
      <h3 class="section-title">From ${type === "movie" ? "Radarr" : "Sonarr"} search ${remote.busy && html`<${Spinner} small />`}</h3>
      ${remote.error && html`<${Notice}>${remote.error}<//>`}
      ${!remote.busy && !remoteShown.length && !remote.error && html`<p class="muted">No other matches for “${query.trim()}”.</p>`}
      <div class="grid flush">${remoteShown.map(cell)}</div>
    </section>`}
    ${!shown.length && query.trim().length < 2 && !loading && html`<${Empty} icon=${type === "movie" ? "film" : "tv"}
        title=${all.length ? "No matches" : "Nothing to add"}>
      ${all.length ? `Try removing a filter, or search to find more ${noun(type)}.`
                   : `Everything discovery found is already requested. Search to find more ${noun(type)}.`}<//>`}
    ${selecting && html`<${SelectionBar} type=${type} chosen=${visible.filter((c) => selected.has(cardKey(c)))}
        sending=${sending} setSending=${setSending} finish=${finish} />`}
    ${showFilters && html`<${FilterSheet} type=${type} filters=${filters} onChange=${setFilters}
        facets=${state.facets[type] || {}} count=${(f) => applyFilters(all, f, query, neighbourMap).length} onClose=${() => setShowFilters(false)} />`}
  <//>`;
}

function activeBadge(f) {
  const n = activeCount(f);
  return n ? html`<span class="dot-count">${n}</span>` : null;
}

/** Bottom bar in multi-select: what will be used, Options… (one-off overrides for the batch), Add N. */
function SelectionBar({ type, chosen, sending, setSending, finish }) {
  const plan = state.requestPlans[type];
  const retries = chosen.filter((c) => S.statusFor(c).state === "failed").length;
  const options = () => S.customize(chosen, () => finish(new Set()));
  const addAll = async () => {
    if (plan?.problem) return options();
    setSending(true);
    const failed = await S.addBatch(chosen);
    setSending(false);
    finish(failed);
  };
  return html`<div class="selection-bar">
    ${plan?.problem ? html`<${Notice}>${plan.problem.message}<//>`
      : plan && html`<div class="small muted clamp1">Uses ${planSummary(plan)}</div>`}
    <div class="selection-actions">
      <button class="btn" disabled=${!chosen.length || sending} onClick=${options}>Options…</button>
      <button class="btn primary" disabled=${!chosen.length || sending || S.isServiceDown(type)} onClick=${addAll}>
        ${sending ? html`<${Spinner} small />` : !chosen.length ? "Add" : retries === chosen.length ? `Retry ${chosen.length}` : `Add ${chosen.length}`}
      </button>
    </div>
  </div>`;
}

/** Under each poster: the genre a title matched through ("via Thriller"), or the length when sorting by it.
 * The rating is always shown by the poster cell itself. */
function caption(card, filters, neighbourMap) {
  const via = viaNeighbour(card, filters, neighbourMap);
  if (via) return `via ${via}`;
  if ((filters.sort === "runtime_asc" || filters.sort === "runtime_desc") && card.runtime) return `${card.runtime} min`;
  return null;
}

