// Shared building blocks: posters, badges, status lines, banners, sheets, page header.
// Mirrors ios/Phonarr/Views/PosterCard.swift and the shared parts of BrowseViews.swift.
import { html, useEffect, useRef, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { STATE_ICON, stateLabel, isActive, planSummary, SERVICE_FOR, capitalize, cardKey } from "../models.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { back, go, openTitle } from "../router.js";

// ------------------------------------------------------------------ page chrome
export function Page({ title, backTo, actions, children, large = true, cls = "" }) {
  return html`<section class=${`page ${cls}`}>
    <header class=${`nav ${large && !backTo ? "nav-large" : ""}`}>
      <div class="nav-side">
        ${backTo !== undefined && html`<button class="link back" onClick=${() => back(backTo || undefined)} aria-label="Back">
          <${Icon} name="back" size=${22} /><span>Back</span></button>`}
      </div>
      <h1 class="nav-title">${title}</h1>
      <div class="nav-side nav-actions">${actions}</div>
    </header>
    <div class="page-body">${children}</div>
  </section>`;
}

export function Spinner({ small }) {
  return html`<span class=${`spinner ${small ? "small" : ""}`} role="status" aria-label="Loading"></span>`;
}

export function Empty({ icon, title, children }) {
  return html`<div class="empty">
    <${Icon} name=${icon} size=${40} stroke=${1.5} />
    <h3>${title}</h3>
    <p>${children}</p>
  </div>`;
}

export function Notice({ icon = "warning", tone = "warn", children }) {
  return html`<div class=${`notice ${tone}`}><${Icon} name=${icon} size=${16} /><span>${children}</span></div>`;
}

// ------------------------------------------------------------------ posters
function StatusBadge({ status }) {
  const s = status.state;
  if (s === "not_requested" || s === "unknown") return null;
  if (s === "submitting") return html`<span class="badge round"><${Spinner} small /></span>`;
  if (s === "failed") {
    return html`<span class="badge round bad"><${Icon} name=${status.error_kind === "config" ? "gear" : "warning"} size=${13} stroke=${2.5} /></span>`;
  }
  if (s === "complete") return html`<span class="badge round good"><${Icon} name="check" size=${13} stroke=${3} /></span>`;
  return html`<span class="badge pill">
    <${Icon} name=${STATE_ICON[s]} size=${12} stroke=${2.5} />
    ${s === "downloading" && status.progress != null && html`<span class="num">${Math.round(status.progress * 100)}%</span>`}
  </span>`;
}

function progressTone(s) {
  if (s === "paused" || s === "stalled") return "warn";
  if (s === "importing") return "good";
  if (s === "searching" || s === "queued") return "muted";
  return "accent";
}

function PosterProgress({ status }) {
  const s = status.state;
  if (!isActive(s) && s !== "searching") return null;
  const p = status.progress;
  return html`<div class=${`poster-progress ${progressTone(s)}`}>
    ${p != null ? html`<i style=${`width:${Math.round(p * 100)}%`}></i>` : s !== "paused" && html`<i class="indeterminate"></i>`}
  </div>`;
}

export function Poster({ src, title }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return src && !failed
    ? html`<img class="poster-img" src=${src} alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"
        onError=${() => setFailed(true)} />`
    : html`<div class="poster-img placeholder" aria-hidden="true"><${Icon} name="film" size=${28} stroke=${1.5} /><span>${title || ""}</span></div>`;
}

/** The poster frame with badge, progress bar, + / ↻ button and selection mark. */
export function PosterCard({ card, status, quick, mark }) {
  const failed = status.state === "failed";
  return html`<div class=${`poster ${failed ? "is-failed" : ""} ${mark === "selected" ? "is-selected" : ""}`}>
    <${Poster} src=${card.poster} title=${card.title} />
    <div class="poster-badge"><${StatusBadge} status=${status} /></div>
    <${PosterProgress} status=${status} />
    ${mark && html`<span class=${`mark ${mark}`}>${mark === "selected" && html`<${Icon} name="check" size=${14} stroke=${3} />`}</span>`}
    ${quick && html`<button class=${`quick ${quick.kind}`} onClick=${(e) => { e.preventDefault(); e.stopPropagation(); quick.run(); }}
        aria-label=${quick.kind === "add" ? `Add ${card.title || ""} to library` : `Retry ${card.title || ""}`}>
        <${Icon} name=${quick.kind === "add" ? "plus" : "retry"} size=${17} stroke=${2.8} /></button>`}
  </div>`;
}

function quickAction(card, status) {
  if (status.state === "not_requested") {
    if (S.isServiceDown(card.type)) return null;   // the banner explains; a tap would just fail
    return { kind: "add", run: () => S.quickAdd(card) };
  }
  if (status.state === "failed") return { kind: "retry", run: () => S.retry(card.type, card.id) };
  return null;
}

/** Long-press (touch) or right-click (mouse) opens the poster's action sheet. */
function useLongPress(onLong) {
  const timer = useRef(null);
  const fired = useRef(false);
  const start = useRef(null);
  const cancel = () => { clearTimeout(timer.current); timer.current = null; };
  return {
    onPointerDown: (e) => {
      if (e.pointerType === "mouse") return;
      fired.current = false;
      start.current = [e.clientX, e.clientY];
      timer.current = setTimeout(() => { fired.current = true; onLong(); }, 500);
    },
    onPointerMove: (e) => {
      if (timer.current && start.current && Math.hypot(e.clientX - start.current[0], e.clientY - start.current[1]) > 10) cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onContextMenu: (e) => { e.preventDefault(); cancel(); if (e.pointerType !== "touch") onLong(); },
    onClickCapture: (e) => { if (fired.current) { e.preventDefault(); e.stopPropagation(); fired.current = false; } },
  };
}

/**
 * A poster in a row or grid.
 * - Click the poster: opens details (no popup).
 * - Click the + : adds it now with your defaults (no popup). On a failed title it's Retry.
 * - Long-press / right-click: Add (shows the profile it uses), Customize Request…, Select.
 * - In multi-select mode (`selection` set) a click toggles the tick instead.
 */
export function PosterCell({ card, size, caption, selection, onSelect }) {
  const status = S.statusFor(card);
  const press = useLongPress(() => { state.menu = { card, onSelect }; S.emit(); });
  if (selection) {
    const selectable = S.isSelectable(card);
    const selected = selection.has(cardKey(card));
    return html`<button class=${`cell ${selectable ? "" : "dim"}`} style=${width(size)} disabled=${!selectable} aria-pressed=${selected}
        title=${selectable ? "Select" : "Already requested"} onClick=${() => selection.toggle(cardKey(card))}>
      <${PosterCard} card=${card} status=${status} mark=${selectable ? (selected ? "selected" : "unselected") : null} />
      <${CellText} card=${card} caption=${caption} />
    </button>`;
  }
  return html`<a class="cell" style=${width(size)} href=${`#/title/${card.type}/${card.id}`} ...${press}
      onClick=${(e) => { e.preventDefault(); openTitle(card); }}
      aria-label=${`${card.title || "Untitled"}${card.year ? `, ${card.year}` : ""}, ${stateLabel(status.state)}`}>
    <${PosterCard} card=${card} status=${status} quick=${quickAction(card, status)} />
    <${CellText} card=${card} caption=${caption} />
  </a>`;
}

const width = (size) => (size ? `width:${size}px` : "");

function CellText({ card, caption }) {
  return html`<div class="cell-text">
    <div class="cell-title">${card.title || "Untitled"}</div>
    ${card.year && html`<div class="cell-sub">${card.year}</div>`}
    ${caption && html`<div class="cell-sub">${caption}</div>`}
  </div>`;
}

/** Action sheet for a long-pressed poster (state.menu). */
export function PosterMenu() {
  const m = state.menu;
  if (!m) return null;
  const { card, onSelect } = m;
  const status = S.statusFor(card);
  const plan = state.requestPlans[card.type];
  const close = () => { state.menu = null; S.emit(); };
  const act = (fn) => () => { close(); fn(); };
  return html`<${Sheet} title=${card.title || "Untitled"} onClose=${close} compact>
    <div class="menu">
      ${status.state === "not_requested" && html`
        <button class="menu-item" disabled=${!!plan?.problem} onClick=${act(() => S.add(card.type, card.id))}>
          <${Icon} name="plus" /><span>Add to Library<small>${plan ? plan.problem?.message || `Uses ${planSummary(plan)}` : ""}</small></span>
        </button>
        <button class="menu-item" onClick=${act(() => S.customize([card]))}><${Icon} name="sliders" /><span>Customize Request…</span></button>`}
      ${status.state === "failed" && html`
        <button class="menu-item" onClick=${act(() => S.retry(card.type, card.id))}><${Icon} name="retry" /><span>Retry</span></button>`}
      ${onSelect && S.isSelectable(card) && html`
        <button class="menu-item" onClick=${act(onSelect)}><${Icon} name="check-circle" /><span>Select</span></button>`}
      <button class="menu-item" onClick=${act(() => openTitle(card))}><${Icon} name="info" /><span>Open details</span></button>
    </div>
  <//>`;
}

export function PosterRow({ title, items, seeAll }) {
  return html`<section class="row">
    <div class="row-head">
      <h2>${title}</h2>
      ${seeAll && items.length > 0 && html`<a class="link" href=${seeAll}>See all</a>`}
    </div>
    ${items.length === 0
      ? html`<p class="muted pad">${state.loading.size ? "Loading…" : "Nothing new to add."}</p>`
      : html`<div class="scroller">${items.map((c) => html`<${PosterCell} key=${cardKey(c)} card=${c} size=${124} />`)}</div>`}
  </section>`;
}

// ------------------------------------------------------------------ status
export function StatusLine({ status }) {
  const s = status.state;
  if (s === "not_requested") return null;
  const tone = s === "failed" ? "bad" : s === "complete" ? "good" : "";
  return html`<div class="status-line">
    <div class=${`status-head ${tone}`}>
      <${Icon} name=${STATE_ICON[s]} size=${16} />
      <span>${stateLabel(s)}</span>
      ${s === "downloading" && status.progress != null && html`<span class="num">${Math.round(status.progress * 100)}%</span>`}
      ${status.stale && html`<span class="muted">(last known)</span>`}
    </div>
    ${status.progress != null && isActive(s) && html`<div class=${`bar ${s === "stalled" || s === "paused" ? "warn" : ""}`}><i style=${`width:${Math.round(status.progress * 100)}%`}></i></div>`}
    ${status.episodes_wanted > 0 && html`<div class="small muted">${status.episodes_have} of ${status.episodes_wanted} episodes</div>`}
    ${status.message && s !== "failed" && html`<div class="small muted clamp2">${status.message}</div>`}
  </div>`;
}

export function ServiceBanner({ only }) {
  const down = ["radarr", "sonarr"].filter((svc) => {
    const type = svc === "radarr" ? "movie" : "series";
    return (!only || only === type) && state.services[svc]?.reachable === false;
  });
  return html`
    ${!state.gatewayReachable && html`<${Notice} icon="offline">Can't reach the gateway. Showing saved results.<//>`}
    ${down.map((svc) => html`<${Notice} key=${svc} icon="offline">${state.services[svc]?.error?.message || `${capitalize(svc)} is unavailable.`}<//>`)}`;
}

/** Shown in place of a section when its server isn't connected. Never blocks the other section. */
export function ConnectServerCard({ type }) {
  const service = SERVICE_FOR[type];
  const what = type === "movie" ? "films" : "TV shows";
  return html`<div class="card connect">
    <h3><${Icon} name=${type === "movie" ? "film" : "tv"} /> ${type === "movie" ? "Films need Radarr" : "TV shows need Sonarr"}</h3>
    <p class="muted">Connect ${capitalize(service)} to browse and add ${what}. Everything else keeps working without it.</p>
    <button class="btn" onClick=${() => go(`#/settings/${service}`)}>Connect ${capitalize(service)}</button>
  </div>`;
}

// ------------------------------------------------------------------ sheets and toast
export function Sheet({ title, onClose, left, right, children, compact }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("sheet-open");
    return () => { document.removeEventListener("keydown", onKey); document.body.classList.remove("sheet-open"); };
  }, []);
  return html`<div class="sheet-backdrop" onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class=${`sheet ${compact ? "compact" : ""}`} role="dialog" aria-modal="true" aria-label=${title}>
      <header class="sheet-head">
        <div class="nav-side">${left || html`<button class="link" onClick=${onClose}>${compact ? "Close" : "Cancel"}</button>`}</div>
        <h2 class="nav-title">${title}</h2>
        <div class="nav-side nav-actions">${right}</div>
      </header>
      <div class="sheet-body">${children}</div>
    </div>
  </div>`;
}

export function Toast() {
  return state.toast ? html`<div class="toast" role="status">${state.toast}</div>` : null;
}

// ------------------------------------------------------------------ forms (iOS-style grouped lists)
export function Group({ header, footer, children }) {
  return html`<div class="group">
    ${header && html`<div class="group-header">${header}</div>`}
    <div class="group-body">${children}</div>
    ${footer && html`<div class="group-footer">${footer}</div>`}
  </div>`;
}

export function Row({ label, children, onClick, href, chevron }) {
  const inner = html`<span class="row-label">${label}</span><span class="row-value">${children}</span>
    ${(chevron || href) && html`<${Icon} name="chevron" size=${16} cls="muted" />`}`;
  if (href) return html`<a class="frow" href=${href} onClick=${(e) => { e.preventDefault(); go(href); }}>${inner}</a>`;
  if (onClick) return html`<button class="frow" onClick=${onClick}>${inner}</button>`;
  return html`<div class="frow">${inner}</div>`;
}

/** A <select> row; `options` = [[value, label]]. Values round-trip as strings, so map them back in onChange. */
export function SelectRow({ label, value, options, onChange, placeholder = "Choose…" }) {
  const has = options.some(([v]) => String(v) === String(value ?? ""));
  return html`<label class="frow">
    <span class="row-label">${label}</span>
    <select class="row-value" value=${has ? String(value) : ""} onChange=${(e) => onChange(e.target.value === "" ? null : e.target.value)}>
      ${!has && html`<option value="">${placeholder}</option>`}
      ${options.map(([v, l]) => html`<option value=${String(v)}>${l}</option>`)}
    </select>
  </label>`;
}

export function Toggle({ label, checked, onChange }) {
  return html`<label class="frow">
    <span class="row-label">${label}</span>
    <input type="checkbox" class="switch" checked=${checked} onChange=${(e) => onChange(e.target.checked)} />
  </label>`;
}
