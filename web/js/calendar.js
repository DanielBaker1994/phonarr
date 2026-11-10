// Calendar helpers: day keys, today in a timezone, day lists, labels, filters, the midnight watcher.
// Mirrors ios/Phonarr/Core/CalendarModels.swift. Pure functions with no DOM access, so
// `node --test web/tests/*.test.mjs` can check them (web/tests/calendar.test.mjs).
//
// Day keys are "YYYY-MM-DD" strings and are never parsed as instants: new Date("2026-11-12") is midnight UTC,
// which is the 11th west of UTC (KB-12). Arithmetic on them is done on UTC dates, which have no daylight
// saving, so adding a day is always exactly one calendar day (KB-16).
import { hasText, text } from "./config.js";

const DAY_MS = 86_400_000;
const MIDNIGHT_MARGIN_MS = 250;      // fire just after midnight, never just before
const MIN_TIMER_MS = 1000;
const MAX_DAYS = 400;                // a runaway range never loops forever

const toUTC = (key) => {
  const [y, m, d] = key.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
};
const fromUTC = (ms) => new Date(ms).toISOString().slice(0, 10);

function clockIn(tz, now) {
  const opts = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23" };
  let fmt;
  try { fmt = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: tz || "UTC" }); } catch { fmt = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: "UTC" }); }
  const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
  return { key: `${p.year}-${p.month}-${p.day}`, h: Number(p.hour) % 24, m: Number(p.minute), s: Number(p.second) };
}

/** Today's key in an IANA timezone, from the clock every time (KB-15). An unknown zone is UTC. */
export const todayKey = (tz, now = new Date()) => clockIn(tz, now).key;

/** This device's IANA timezone, sent to the gateway as tz. */
export function deviceTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

export const addDays = (key, n) => fromUTC(toUTC(key) + n * DAY_MS);
export const daysBetween = (from, to) => Math.round((toUTC(to) - toUTC(from)) / DAY_MS);

export function dayRange(start, end) {
  const out = [];
  for (let k = start; k <= end && out.length < MAX_DAYS; k = addDays(k, 1)) out.push(k);
  return out;
}

/** Milliseconds until the next local midnight (just after it). Never below a second. */
export function msUntilNextMidnight(tz, now = new Date()) {
  const c = clockIn(tz, now);
  const elapsed = ((c.h * 60 + c.m) * 60 + c.s) * 1000 + now.getMilliseconds();
  return Math.max(MIN_TIMER_MS, DAY_MS - elapsed + MIDNIGHT_MARGIN_MS);
}

/** "Today", "Tomorrow", "Yesterday", otherwise "Thu 12 Nov" (plus the year when it isn't this year). */
export function dayHeading(key, today, locale = undefined) {
  const diff = daysBetween(today, key);
  if (diff === 0) return text("calendar.today");
  if (diff === 1) return text("calendar.tomorrow");
  if (diff === -1) return text("calendar.yesterday");
  const opts = { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" };
  if (key.slice(0, 4) !== today.slice(0, 4)) opts.year = "numeric";
  return new Intl.DateTimeFormat(locale, opts).format(new Date(toUTC(key))).replace(",", "");
}

export const epCode = (season, episode) => `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;

/** "S02E05 · The Fifth · 21:00", "S02E01–E08 · 8 episodes · 08:00", "Special · S00E03 · Extras", "In cinemas". */
export function entryLine(item) {
  if (item.type === "movie") return text(`calendar.kind_${item.kind}`);
  const [first, last] = item.episodes || [item.episode, item.episode];
  const parts = [];
  if (item.special) parts.push(text("calendar.special"));
  if (item.count > 1) {
    parts.push(`${epCode(item.season, first)}–E${String(last).padStart(2, "0")}`);
    parts.push(text("calendar.episodes", { count: item.count }));
  } else {
    parts.push(epCode(item.season, first));
    if (item.episode_title) parts.push(item.episode_title);
  }
  if (item.time) parts.push(item.time);
  return parts.join(" · ");
}

/** A short label for a premiere or finale, or null. */
export function milestone(item) {
  if (item.finale) return hasText(`calendar.finale_${item.finale}`) ? text(`calendar.finale_${item.finale}`) : text("calendar.finale");
  if (item.premiere) return hasText(`calendar.premiere_${item.premiere}`) ? text(`calendar.premiere_${item.premiere}`) : null;
  return null;
}

// ---------------------------------------------------------------- filters (remembered per device)
export const CALENDAR_MEDIA = ["all", "movie", "series"];
export const emptyCalendarFilters = () => ({ media: "all", hideSpecials: false, monitoredOnly: false });

export function normalizeCalendarFilters(saved) {
  const f = emptyCalendarFilters();
  if (!saved || typeof saved !== "object") return f;
  if (CALENDAR_MEDIA.includes(saved.media)) f.media = saved.media;
  f.hideSpecials = saved.hideSpecials === true;
  f.monitoredOnly = saved.monitoredOnly === true;
  return f;
}

export const activeCalendarFilters = (f) => (f.media !== "all" ? 1 : 0) + (f.hideSpecials ? 1 : 0) + (f.monitoredOnly ? 1 : 0);

const keep = (f) => (i) => (f.media === "all" || i.type === f.media) && !(f.hideSpecials && i.special) && !(f.monitoredOnly && !i.monitored);

/** Same days, fewer items. Days stay even when they end up empty, so Today always has a place. */
export const applyCalendarFilters = (days, f) => days.map((d) => ({ ...d, items: d.items.filter(keep(f)) }));

/** Load more: merge two day lists by date (the newer answer wins), sorted by date. */
export function mergeDays(older, newer) {
  const byDate = new Map(older.map((d) => [d.date, d]));
  for (const d of newer) byDate.set(d.date, d);
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

// ---------------------------------------------------------------- KB-15: the day changes under an open app
/**
 * Watches for the local day changing: a timer at the next local midnight, plus check() for the caller to run
 * when the page becomes visible again. today() always reads the clock; onNewDay(key) runs once per change.
 */
export function dayWatcher({ tz, now = () => new Date(), onNewDay, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let last = todayKey(tz, now());
  let timer = null;
  const check = () => {
    const current = todayKey(tz, now());
    if (current === last) return false;
    last = current;
    onNewDay(current);
    return true;
  };
  const schedule = () => {
    timer = setTimer(() => { check(); schedule(); }, msUntilNextMidnight(tz, now()));
  };
  return {
    today: () => todayKey(tz, now()),
    check,
    start() { if (timer === null) schedule(); },
    stop() { if (timer !== null) clearTimer(timer); timer = null; },
  };
}
