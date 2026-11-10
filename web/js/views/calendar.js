// Calendar (mirrors ios/Phonarr/Views/CalendarView.swift): upcoming and recent releases from Radarr and Sonarr
// as an agenda grouped by the viewer's local day. GET /v1/calendar with this device's IANA timezone.
// Self-contained (its own state, loading and toolbar), so it can move to a tab of its own later.
// Shown under Activity → Calendar. Requirements: docs/KNOWN_BUGS_PRD.md KB-12..16 and KB-30..36.
import { html, useEffect, useLayoutEffect, useReducer, useRef, useState } from "../../vendor/preact-htm.js";
import { api } from "../api.js";
import { cfg, text } from "../config.js";
import { log } from "../log.js";
import { Icon } from "../icons.js";
import * as S from "../store.js";
import { go, openTitle } from "../router.js";
import { capitalize, stateLabel } from "../models.js";
import {
  activeCalendarFilters, addDays, applyCalendarFilters, dayHeading, dayWatcher, deviceTimeZone, entryLine,
  mergeDays, milestone, normalizeCalendarFilters, todayKey,
} from "../calendar.js";
import { Empty, Notice, Poster, Spinner } from "./components.js";

const FILTERS_KEY = "calendarFilters";
const CHIP_STATES = new Set(["submitting", "queued", "downloading", "importing", "stalled", "failed", "complete"]);

// Kept for this page load (not saved): coming back from a title shows the same list at the same place.
const cal = { data: null, tz: deviceTimeZone(), loading: false, more: null, error: null, returnY: null, anchor: null };
const listeners = new Set();
function set(patch) {
  Object.assign(cal, patch);
  for (const fn of listeners) fn();
}

function failed(e, start, end) {
  log.warn("calendar_failed", { start, end, error: e.message });
  if (e.unauthorized) S.handleAuthFailure();
  set({ error: e });
}

async function reload(today, { quiet = false } = {}) {
  const start = addDays(today, -cfg("calendar.past_days", 7));
  const end = addDays(today, cfg("calendar.future_days", 30));
  if (!quiet) set({ loading: true });
  log.info("calendar_load", { start, end, tz: cal.tz });
  try {
    set({ data: await api.calendar(start, end, cal.tz), error: null });
  } catch (e) {
    failed(e, start, end);
  } finally {
    set({ loading: false });
  }
}

/** Show earlier / Show later: fetch the next few days and merge them in, keeping the list where it was. */
async function loadMore(dir) {
  if (!cal.data || cal.more) return;
  const n = cfg("calendar.more_days", 14);
  const [start, end] = dir === "past" ? [addDays(cal.data.start, -n), addDays(cal.data.start, -1)]
    : [addDays(cal.data.end, 1), addDays(cal.data.end, n)];
  set({ more: dir });
  log.info("calendar_load", { start, end, tz: cal.tz });
  try {
    const r = await api.calendar(start, end, cal.tz);
    const anchor = dir === "past" ? { height: document.documentElement.scrollHeight, y: window.scrollY } : null;
    set({
      anchor,
      error: null,
      data: { ...cal.data, start: dir === "past" ? r.start : cal.data.start, end: dir === "future" ? r.end : cal.data.end,
              days: mergeDays(cal.data.days, r.days), services: r.services },
    });
  } catch (e) {
    failed(e, start, end);
  } finally {
    set({ more: null });
  }
}

/** Put a day's header just under the sticky navigation bar. */
function scrollToDay(key, top) {
  const el = document.getElementById(`cal-${key}`);
  if (el) window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - top));
}

export function CalendarView() {
  const [, rerender] = useReducer((n) => n + 1, 0);
  const [filters, setFiltersState] = useState(() => normalizeCalendarFilters(S.pref.get(FILTERS_KEY, null)));
  const [today, setToday] = useState(() => todayKey(cal.tz));
  const [top, setTop] = useState(0);
  const root = useRef(null);
  const placed = useRef(false);     // scrolled to Today (or back to where we were) for this visit

  useEffect(() => {
    listeners.add(rerender);
    return () => listeners.delete(rerender);
  }, []);

  // First open, a range that no longer holds today, or a new timezone: load. Otherwise refresh quietly.
  useEffect(() => {
    const tz = deviceTimeZone();
    const stale = !cal.data || cal.tz !== tz || today < cal.data.start || today > cal.data.end;
    cal.tz = tz;
    if (stale) reload(today, { quiet: false });
    else if (cal.returnY === null) reload(today, { quiet: true });
  }, []);

  // KB-15: today comes from the clock, at the next local midnight and whenever the page is shown again.
  useEffect(() => {
    const watcher = dayWatcher({
      tz: cal.tz,
      onNewDay: (day) => { log.info("calendar_new_day", { day }); setToday(day); placed.current = false; reload(day); },
    });
    watcher.start();
    const onVisible = () => { if (!document.hidden) watcher.check(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { watcher.stop(); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  // Sticky day headers sit under the navigation bar, whose height depends on the device.
  useLayoutEffect(() => {
    const nav = root.current?.closest(".page")?.querySelector(".nav");
    const h = nav ? nav.getBoundingClientRect().height : 0;
    if (Math.abs(h - top) > 0.5) setTop(h);
  });

  // KB-35: open on Today without a visible jump. Runs before paint; repeated on the next frame because the
  // router restores the previous scroll position in a frame of its own after a route change.
  useLayoutEffect(() => {
    if (cal.anchor) {     // Show earlier added days above: keep what was on screen in place
      const { height, y } = cal.anchor;
      cal.anchor = null;
      window.scrollTo(0, y + document.documentElement.scrollHeight - height);
      return;
    }
    if (placed.current || !cal.data || !top) return;
    placed.current = true;
    const back = cal.returnY;
    cal.returnY = null;
    const place = () => (back !== null ? window.scrollTo(0, back) : scrollToDay(today, top));
    place();
    requestAnimationFrame(place);
  });

  const setFilters = (patch) => {
    const next = { ...filters, ...patch };
    setFiltersState(next);
    S.pref.set(FILTERS_KEY, next);
  };
  const open = (item) => {
    cal.returnY = window.scrollY;
    openTitle({ type: item.type, id: item.id, title: item.title, year: item.year, poster: item.poster, status: item.status });
  };

  const data = cal.data;
  const services = data?.services || {};
  const noneConnected = data && !services.radarr?.configured && !services.sonarr?.configured;
  const days = data ? applyCalendarFilters(data.days, filters).filter((d) => d.items.length || d.date === today) : [];
  const anything = days.some((d) => d.items.length);

  const toolbar = html`<div class="cal-toolbar">
    <div class="segmented small" role="radiogroup" aria-label=${text("calendar.title")}>
      ${["all", "movie", "series"].map((m) => html`<button role="radio" aria-checked=${filters.media === m}
          class=${filters.media === m ? "on" : ""} onClick=${() => setFilters({ media: m })}>${text(`calendar.media_${m}`)}</button>`)}
    </div>
    <button class=${`chip small-toggle ${filters.hideSpecials ? "on" : ""}`} aria-pressed=${filters.hideSpecials}
      onClick=${() => setFilters({ hideSpecials: !filters.hideSpecials })}>${text("calendar.hide_specials")}</button>
    <button class=${`chip small-toggle ${filters.monitoredOnly ? "on" : ""}`} aria-pressed=${filters.monitoredOnly}
      onClick=${() => setFilters({ monitoredOnly: !filters.monitoredOnly })}>${text("calendar.monitored_only")}</button>
    <span class="grow"></span>
    <button class="link" onClick=${() => scrollToDay(today, top)} disabled=${!data}>${text("calendar.jump_today")}</button>
    <button class="icon-btn" aria-label=${text("calendar.refresh")} onClick=${() => reload(today, { quiet: !!data })}>
      ${cal.loading && data ? html`<${Spinner} small />` : html`<${Icon} name="retry" />`}</button>
  </div>`;

  let body;
  if (!data && cal.error) {
    body = html`<${Empty} icon="calendar" title=${text("calendar.load_failed")}>${cal.error.message}<br />
      <button class="btn" onClick=${() => reload(today)}>${text("calendar.retry")}</button><//>`;
  } else if (!data) {
    body = html`<${Skeleton} />`;     // a slow first load is a placeholder, never an error (KB-35)
  } else if (noneConnected) {
    body = html`<${Empty} icon="calendar" title=${text("calendar.empty_title")}>${text("calendar.not_connected")}<br />
      <button class="btn" onClick=${() => go("#/settings")}>${text("calendar.open_settings")}</button><//>`;
  } else {
    body = html`
      <button class="btn ghost cal-more" disabled=${!!cal.more} onClick=${() => loadMore("past")}>
        ${cal.more === "past" ? html`<${Spinner} small />` : text("calendar.earlier")}</button>
      ${!anything && html`<${Empty} icon="calendar" title=${text("calendar.empty_title")}>
        ${activeCalendarFilters(filters) ? text("calendar.empty_filtered") : text("calendar.empty_body")}<//>`}
      ${days.map((d) => html`<${Day} key=${d.date} day=${d} today=${today} onOpen=${open} />`)}
      <button class="btn ghost cal-more" disabled=${!!cal.more} onClick=${() => loadMore("future")}>
        ${cal.more === "future" ? html`<${Spinner} small />` : text("calendar.later")}</button>`;
  }

  return html`<div class="calendar" ref=${root} style=${`--cal-top:${top}px`}>
    ${toolbar}
    ${data && cal.error && html`<${Notice} icon="offline">${cal.error.transport ? text("calendar.offline_cached") : cal.error.message}<//>`}
    ${["radarr", "sonarr"].filter((svc) => services[svc]?.ok === false).map((svc) => html`<${Notice} key=${svc} icon="offline">
      ${text("calendar.service_failed", { service: capitalize(svc), message: services[svc].error?.message || "" })}<//>`)}
    ${data?.tz_fallback && html`<${Notice} icon="info">${text("calendar.tz_fallback", { tz: cal.tz })}<//>`}
    ${body}
  </div>`;
}

function Day({ day, today, onOpen }) {
  const isToday = day.date === today;
  return html`<div class=${`group cal-day ${isToday ? "is-today" : ""} ${day.date < today ? "is-past" : ""}`}>
    <div class="group-header cal-day-head" id=${`cal-${day.date}`}>${dayHeading(day.date, today)}</div>
    <div class="group-body">
      ${day.items.length ? day.items.map((item) => html`<${Entry} key=${`${item.type}:${item.id}:${item.kind}:${item.season ?? ""}:${item.episode ?? ""}`}
          item=${item} onOpen=${onOpen} />`)
        : html`<div class="cal-none muted small">${text("calendar.nothing_today")}</div>`}
    </div>
  </div>`;
}

function Entry({ item, onOpen }) {
  const status = S.statusOf(item.type, item.id, item.status);    // live: a request from details shows here too (KB-36)
  const mark = milestone(item);
  let chip = null;
  if (item.has_file) chip = html`<span class="cal-chip good"><${Icon} name="check" size=${12} stroke=${3} />${text("calendar.downloaded")}</span>`;
  else if (CHIP_STATES.has(status.state)) {
    const pct = status.state === "downloading" && status.progress != null ? ` ${Math.round(status.progress * 100)}%` : "";
    chip = html`<span class=${`cal-chip ${status.state === "failed" ? "bad" : status.state === "complete" ? "good" : ""}`}>${stateLabel(status.state)}${pct}</span>`;
  }
  return html`<div class=${`activity-row cal-entry ${item.monitored ? "" : "unmonitored"}`} role="link" tabindex="0"
      onClick=${() => onOpen(item)} onKeyDown=${(e) => { if (e.key === "Enter") onOpen(item); }}>
    <div class="thumb cal-thumb"><${Poster} src=${item.poster} title="" /></div>
    <div class="grow">
      <strong class="clamp1">${item.title || "Untitled"}</strong>
      <div class="small muted clamp2">${entryLine(item)}</div>
      ${(chip || mark) && html`<div class="cal-tags">${mark && html`<span class="cal-chip accent">${mark}</span>`}${chip}</div>`}
    </div>
    <${Icon} name=${item.type === "movie" ? "film" : "tv"} size=${16} cls="muted" />
  </div>`;
}

function Skeleton() {
  const rows = Array.from({ length: cfg("calendar.skeleton_rows", 6) }, (_, i) => i);
  return html`<div class="group cal-day" aria-busy="true">
    <div class="group-header cal-day-head"><span class="skeleton-bar short"></span></div>
    <div class="group-body">${rows.map((i) => html`<div key=${i} class="activity-row cal-entry">
      <div class="thumb cal-thumb skeleton"></div>
      <div class="grow"><span class="skeleton-bar"></span><span class="skeleton-bar short"></span></div>
    </div>`)}</div>
  </div>`;
}
