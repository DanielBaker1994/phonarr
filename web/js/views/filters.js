// Filter sheet, sort menu and removable filter chips (mirrors ios/Phonarr/Views/FilterViews.swift).
// Only filters with real choices in the current catalogue (the gateway's facets) are shown.
import { html, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { SORTS, sortLabel, runtimeLabel, emptyFilters, activeCount } from "../filters.js";
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

export function FilterSheet({ type, filters, onChange, facets, count, onClose }) {
  const f = filters;
  const set = (patch) => onChange({ ...f, ...patch });
  const reset = html`<button class="link" disabled=${activeCount(f) === 0} onClick=${() => onChange(emptyFilters(f.sort))}>Reset</button>`;
  const done = html`<button class="link strong" onClick=${onClose}>Show ${count(f)}</button>`;
  return html`<${Sheet} title="Filters" onClose=${onClose} left=${reset} right=${done}>
    <${Group} header="Sort">
      <${SelectRow} label="Sort by" value=${f.sort} options=${sortsFor(type)} onChange=${(v) => set({ sort: v || "title" })} />
    <//>
    ${facets.genres?.length > 0 && html`<${Group} header="Genres"><div class="group-pad">
      <${Chips} values=${facets.genres} isOn=${(v) => f.genres.includes(v)}
        toggle=${(v) => set({ genres: f.genres.includes(v) ? f.genres.filter((g) => g !== v) : [...f.genres, v] })} />
    </div><//>`}
    ${facets.decades?.length > 1 && html`<${Group} header="Decade"><div class="group-pad">
      <${Chips} values=${facets.decades} label=${(v) => `${v}s`} isOn=${(v) => f.decade === Number(v)}
        toggle=${(v) => set({ decade: f.decade === Number(v) ? null : Number(v) })} />
    </div><//>`}
    ${facets.runtime?.length > 0 && html`<${Group} header=${type === "movie" ? "Length" : "Episode length"}><div class="group-pad">
      <${Chips} values=${facets.runtime} label=${runtimeLabel} isOn=${(v) => f.runtime === v}
        toggle=${(v) => set({ runtime: f.runtime === v ? null : v })} />
    </div><//>`}
    ${facets.rated > 0 && html`<${Group} header="Minimum rating"
        footer=${`${facets.rated} titles have a rating. Unrated titles are hidden when this is set.`}>
      <label class="frow">
        <input type="range" min="0" max="9" step="0.5" value=${f.min_rating} class="grow"
          onInput=${(e) => set({ min_rating: Number(e.target.value) })} aria-label="Minimum rating" />
        <span class="row-value num">${f.min_rating === 0 ? "Any" : `${f.min_rating.toFixed(1)}+`}</span>
      </label>
    <//>`}
    <${Group} header="More">
      ${facets.sources?.length > 1 && html`<${SelectRow} label="Found in" value=${f.source ?? ""} placeholder="Anywhere"
        options=${[["", "Anywhere"], ...facets.sources.map((v) => [v.value, `${sourceLabel(v.value)} (${v.count})`])]}
        onChange=${(v) => set({ source: v || null })} />`}
      <${OptionRow} label="Language" values=${facets.languages} value=${f.language} onChange=${(v) => set({ language: v })} />
      <${OptionRow} label=${type === "movie" ? "Studio" : "Network"} values=${facets.studios} value=${f.studio} onChange=${(v) => set({ studio: v })} />
      <${OptionRow} label="Collection" values=${facets.collections} value=${f.collection} onChange=${(v) => set({ collection: v })} />
      <${OptionRow} label="Certification" values=${facets.certifications} value=${f.certification} onChange=${(v) => set({ certification: v })} />
      <${OptionRow} label="Status" values=${facets.statuses} value=${f.series_status} onChange=${(v) => set({ series_status: v })} />
    <//>
  <//>`;
}

/** Removable chips for the filters that are on, plus "N of M". */
export function ActiveFilterBar({ filters: f, onChange, shown, total }) {
  if (activeCount(f) === 0 && f.sort === "title") return null;
  const set = (patch) => onChange({ ...f, ...patch });
  const chip = (text, remove, icon = "x") => html`<button class="chip on small" onClick=${remove} aria-label=${`Remove filter ${text}`}>
    ${text} <${Icon} name=${icon} size=${11} stroke=${3} /></button>`;
  return html`<div class="filter-bar">
    <span class="small muted">${shown} of ${total}</span>
    ${f.sort !== "title" && chip(sortLabel(f.sort), () => set({ sort: "title" }), "sort")}
    ${f.genres.map((g) => chip(g, () => set({ genres: f.genres.filter((x) => x !== g) })))}
    ${f.decade && chip(`${f.decade}s`, () => set({ decade: null }))}
    ${f.runtime && chip(runtimeLabel(f.runtime), () => set({ runtime: null }))}
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
