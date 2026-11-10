// Filter sheet, sort menu and removable filter chips (mirrors ios/Phonarr/Views/FilterViews.swift).
// Only filters with real choices in the current catalogue (the gateway's facets) are shown.
import { html, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { SORTS, sortLabel, emptyFilters, activeCount, neighboursFor, runtimeSteps, YEAR_MODES, DEFAULT_SORT } from "../filters.js";
import { sourceLabel, capitalize } from "../models.js";
import { Group, SelectRow, Sheet } from "./components.js";

const sortsFor = (type) => SORTS.filter(([k]) => type === "movie" || k !== "popularity");

export function SortMenu({ type, filters, onChange }) {
  const [open, setOpen] = useState(false);
  return html`<span class="menu-anchor">
    <button class="icon-btn" aria-label="Sort" aria-expanded=${open} onClick=${() => setOpen(!open)}><${Icon} name="sort" /></button>
    ${open && html`<div class="popover" onClick=${() => setOpen(false)}>
      ${sortsFor(type).map(([k, label]) => html`<button class=${`popover-item ${filters.sort === k ? "on" : ""}`}
          onClick=${() => onChange({ ...filters, sort: k })}>
        <span class="tick">${filters.sort === k && html`<${Icon} name="check" size=${16} />`}</span>${label}</button>`)}
    </div>`}
  </span>`;
}

function Chips({ values, label = (v) => v, isOn, toggle }) {
  return html`<div class="chips">
    ${values.map((v) => {
      const on = isOn(v.value);
      return html`<button class=${`chip ${on ? "on" : ""}`} aria-pressed=${on} onClick=${() => toggle(v.value)}>
        ${label(v.value)} <span class="chip-count">${v.count}</span></button>`;
    })}
  </div>`;
}

function OptionRow({ label, values, value, onChange }) {
  if (!values || values.length < 2) return null;
  return html`<${SelectRow} label=${label} value=${value ?? ""} placeholder="Any"
    options=${[["", "Any"], ...values.map((v) => [v.value, `${v.value} (${v.count})`])]} onChange=${(v) => onChange(v || null)} />`;
}

/** Genre chips cycle off → include → exclude → off. */
function GenreChips({ values, f, set }) {
  const stateOf = (g) => (f.genres.includes(g) ? 1 : f.exclude_genres.includes(g) ? -1 : 0);
  const cycle = (g) => {
    const s = stateOf(g);
    const genres = f.genres.filter((x) => x !== g), exclude = f.exclude_genres.filter((x) => x !== g);
    if (s === 0) genres.push(g); else if (s === 1) exclude.push(g);
    set({ genres, exclude_genres: exclude, neighbours_off: f.neighbours_off.filter((x) => x !== g) });
  };
  return html`<div class="chips">
    ${values.map((v) => {
      const s = stateOf(v.value);
      return html`<button class=${`chip ${s === 1 ? "on" : s === -1 ? "excluded" : ""}`} aria-pressed=${s !== 0}
          aria-label=${`${v.value}: ${s === 1 ? "included" : s === -1 ? "excluded" : "off"}`} onClick=${() => cycle(v.value)}>
        ${s === -1 ? "− " : ""}${v.value} <span class="chip-count">${v.count}</span></button>`;
    })}
  </div>`;
}

function NeighbourChips({ f, set, map }) {
  const near = neighboursFor(f.genres, map, f.exclude_genres);
  if (!f.genres.length || !near.length) return null;
  const toggle = (g) => set({ neighbours_off: f.neighbours_off.includes(g) ? f.neighbours_off.filter((x) => x !== g) : [...f.neighbours_off, g] });
  return html`<${Group} header="Neighbouring genres"
      footer="Genres whose titles overlap with your picks. Titles with these count as matches. Tap one to leave it out.">
    <label class="frow"><span class="grow">Include neighbours</span>
      <input type="checkbox" class="switch" checked=${f.neighbours} onChange=${(e) => set({ neighbours: e.target.checked })} /></label>
    ${f.neighbours && html`<div class="group-pad"><div class="chips">
      ${near.map((n) => {
        const on = !f.neighbours_off.includes(n.value);
        return html`<button class=${`chip neighbour ${on ? "on" : ""}`} aria-pressed=${on} onClick=${() => toggle(n.value)}>
          ${n.value} <span class="chip-count">${Math.round(n.overlap * 100)}%</span></button>`;
      })}
    </div></div>`}
  <//>`;
}

function YearGroup({ f, set, facets }) {
  const years = facets.years;
  const options = [];
  if (years) for (let y = years.max; y >= years.min; y--) options.push([String(y), String(y)]);
  const mode = (m) => set({ year_mode: m, decades: m === "decades" ? f.decades : [],
    year_min: m === "range" ? (f.year_min ?? years?.min ?? null) : null, year_max: m === "range" ? (f.year_max ?? years?.max ?? null) : null });
  return html`<${Group} header="Year">
    <div class="group-pad"><div class="segmented small" role="group" aria-label="Year filter">
      ${YEAR_MODES.filter(([m]) => m !== "range" || years).map(([m, label]) => html`<button class=${f.year_mode === m ? "on" : ""}
        aria-pressed=${f.year_mode === m} onClick=${() => mode(m)}>${label}</button>`)}
    </div></div>
    ${f.year_mode === "decades" && facets.decades?.length > 0 && html`<div class="group-pad">
      <${Chips} values=${facets.decades} label=${(v) => `${v}s`} isOn=${(v) => f.decades.includes(Number(v))}
        toggle=${(v) => set({ decades: f.decades.includes(Number(v)) ? f.decades.filter((d) => d !== Number(v)) : [...f.decades, Number(v)] })} />
    </div>`}
    ${f.year_mode === "range" && years && html`
      <${SelectRow} label="From" value=${String(f.year_min ?? years.min)} options=${options} onChange=${(v) => set({ year_min: Number(v) })} />
      <${SelectRow} label="To" value=${String(f.year_max ?? years.max)} options=${options} onChange=${(v) => set({ year_max: Number(v) })} />`}
  <//>`;
}

function LengthGroup({ type, f, set }) {
  const steps = runtimeSteps();
  const opts = steps.map((m) => [String(m), m ? `${m} min` : "Any"]);
  return html`<${Group} header=${type === "movie" ? "Length" : "Episode length"}>
    <${SelectRow} label="At least" value=${String(f.runtime_min || 0)} options=${opts} onChange=${(v) => set({ runtime_min: Number(v) })} />
    <${SelectRow} label="At most" value=${String(f.runtime_max || 0)} options=${opts} onChange=${(v) => set({ runtime_max: Number(v) })} />
  <//>`;
}

export function FilterSheet({ type, filters, onChange, facets, count, onClose }) {
  const f = filters;
  const set = (patch) => onChange({ ...f, ...patch });
  const isDefault = activeCount(f) === 0 && f.english_only && f.sort === DEFAULT_SORT;
  const reset = html`<button class="link" disabled=${isDefault} onClick=${() => onChange(emptyFilters())}>Reset</button>`;
  const done = html`<button class="link strong" onClick=${onClose}>Show ${count(f)}</button>`;
  return html`<${Sheet} title="Filters" onClose=${onClose} left=${reset} right=${done}>
    <${Group} footer=${facets.non_english ? `${facets.non_english} titles aren't in English. Titles with no language listed are kept.` : null}>
      <label class="frow"><span class="grow">English only</span>
        <input type="checkbox" class="switch" checked=${f.english_only} onChange=${(e) => set({ english_only: e.target.checked, language: null })} /></label>
      <${SelectRow} label="Sort by" value=${f.sort} options=${sortsFor(type)} onChange=${(v) => set({ sort: v || DEFAULT_SORT })} />
    <//>
    ${facets.genres?.length > 0 && html`<${Group} header="Genres" footer="Tap once to include, again to exclude.">
      <div class="group-pad"><${GenreChips} values=${facets.genres} f=${f} set=${set} /></div>
    <//>`}
    <${NeighbourChips} f=${f} set=${set} map=${facets.genre_neighbours} />
    <${YearGroup} f=${f} set=${set} facets=${facets} />
    ${facets.rated > 0 && html`<${Group} header="Minimum rating"
        footer=${`${facets.rated} titles have a rating. Unrated titles are hidden when this is set.`}>
      <label class="frow">
        <input type="range" min="0" max="9" step="0.1" value=${f.min_rating} class="grow"
          onInput=${(e) => set({ min_rating: Number(e.target.value) })} aria-label="Minimum rating" />
        <span class="row-value num">${f.min_rating === 0 ? "Any" : `${f.min_rating.toFixed(1)}+`}</span>
      </label>
    <//>`}
    <${LengthGroup} type=${type} f=${f} set=${set} />
    <${Group} header="More">
      ${facets.sources?.length > 1 && html`<${SelectRow} label="Found in" value=${f.source ?? ""} placeholder="Anywhere"
        options=${[["", "Anywhere"], ...facets.sources.map((v) => [v.value, `${sourceLabel(v.value)} (${v.count})`])]}
        onChange=${(v) => set({ source: v || null })} />`}
      ${!f.english_only && html`<${OptionRow} label="Language" values=${facets.languages} value=${f.language} onChange=${(v) => set({ language: v })} />`}
      <${OptionRow} label=${type === "movie" ? "Studio" : "Network"} values=${facets.studios} value=${f.studio} onChange=${(v) => set({ studio: v })} />
      <${OptionRow} label="Collection" values=${facets.collections} value=${f.collection} onChange=${(v) => set({ collection: v })} />
      <${OptionRow} label="Certification" values=${facets.certifications} value=${f.certification} onChange=${(v) => set({ certification: v })} />
      <${OptionRow} label="Status" values=${facets.statuses} value=${f.series_status} onChange=${(v) => set({ series_status: v })} />
    <//>
  <//>`;
}

/** The year filter as chip text, or null when it's off. */
export function yearLabel(f) {
  if (f.year_mode === "decades" && f.decades.length) return [...f.decades].sort().map((d) => `${d}s`).join(" + ");
  if (f.year_mode === "range" && (f.year_min || f.year_max)) {
    const [a, b] = [f.year_min, f.year_max];
    return a && b ? `${Math.min(a, b)}–${Math.max(a, b)}` : a ? `${a} on` : `Up to ${b}`;
  }
  return null;
}

export function lengthLabel(f) {
  if (!f.runtime_min && !f.runtime_max) return null;
  if (f.runtime_min && f.runtime_max) return `${f.runtime_min}–${f.runtime_max} min`;
  return f.runtime_min ? `${f.runtime_min}+ min` : `Up to ${f.runtime_max} min`;
}

/** Removable chips for the filters that are on, plus "N of M". */
export function ActiveFilterBar({ filters: f, onChange, shown, total }) {
  if (activeCount(f) === 0 && f.sort === DEFAULT_SORT && !f.english_only) return null;
  const set = (patch) => onChange({ ...f, ...patch });
  const chip = (text, remove, icon = "x") => html`<button class="chip on small" onClick=${remove} aria-label=${`Remove filter ${text}`}>
    ${text} <${Icon} name=${icon} size=${11} stroke=${3} /></button>`;
  const year = yearLabel(f), length = lengthLabel(f);
  return html`<div class="filter-bar">
    <span class="small muted">${shown} of ${total}</span>
    ${f.sort !== DEFAULT_SORT && chip(sortLabel(f.sort), () => set({ sort: DEFAULT_SORT }), "sort")}
    ${f.english_only && chip("English only", () => set({ english_only: false }))}
    ${f.genres.map((g) => chip(g, () => set({ genres: f.genres.filter((x) => x !== g) })))}
    ${f.exclude_genres.map((g) => chip(`No ${g}`, () => set({ exclude_genres: f.exclude_genres.filter((x) => x !== g) })))}
    ${year && chip(year, () => set({ year_mode: "any", decades: [], year_min: null, year_max: null }))}
    ${length && chip(length, () => set({ runtime_min: 0, runtime_max: 0 }))}
    ${f.min_rating > 0 && chip(`★ ${f.min_rating.toFixed(1)}+`, () => set({ min_rating: 0 }))}
    ${f.source && chip(sourceLabel(f.source), () => set({ source: null }))}
    ${f.language && chip(f.language, () => set({ language: null }))}
    ${f.studio && chip(f.studio, () => set({ studio: null }))}
    ${f.collection && chip(f.collection, () => set({ collection: null }))}
    ${f.certification && chip(f.certification, () => set({ certification: null }))}
    ${f.series_status && chip(capitalize(f.series_status), () => set({ series_status: null }))}
    ${activeCount(f) > 1 && html`<button class="link small strong" onClick=${() => onChange(emptyFilters(f.sort))}>Clear all</button>`}
  </div>`;
}
