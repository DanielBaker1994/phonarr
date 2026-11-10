// node --test web/tests/*.test.mjs   (run by ../../test.sh). The Calendar's pure helpers (web/js/calendar.js):
// docs/KNOWN_BUGS_PRD.md KB-12, KB-15, KB-16, KB-33, KB-34. Written before the helpers existed.
import test from "node:test";
import assert from "node:assert/strict";
import {
  todayKey, addDays, dayRange, dayHeading, entryLine, epCode, applyCalendarFilters, emptyCalendarFilters,
  normalizeCalendarFilters, activeCalendarFilters, mergeDays, msUntilNextMidnight, dayWatcher,
} from "../js/calendar.js";

const movie = (over = {}) => ({ type: "movie", id: 1, title: "Film", date: "2026-11-12", time: null, kind: "cinema",
  monitored: true, has_file: false, special: false, status: { state: "searching" }, ...over });
const episode = (over = {}) => ({ type: "series", id: 2, title: "Show", date: "2026-11-12", time: "21:00", kind: "episode",
  season: 2, episode: 5, episodes: [5, 5], count: 1, episode_title: "The Fifth", monitored: true, has_file: false,
  special: false, premiere: null, finale: null, status: { state: "searching" }, ...over });

// ---------------------------------------------------------------- KB-12: days are days
test("KB-12: a day key is shown as that day in every timezone (never parsed as an instant)", () => {
  const original = process.env.TZ;
  try {
    for (const tz of ["America/Los_Angeles", "Pacific/Honolulu", "UTC", "Pacific/Kiritimati"]) {
      process.env.TZ = tz;
      assert.match(dayHeading("2026-11-12", "2026-11-01", "en-GB"), /Thu.*12.*Nov/, tz);
    }
  } finally {
    if (original === undefined) delete process.env.TZ; else process.env.TZ = original;
  }
});

test("day headings say Today, Tomorrow and Yesterday, otherwise weekday and date", () => {
  assert.equal(dayHeading("2026-11-10", "2026-11-10", "en-GB"), "Today");
  assert.equal(dayHeading("2026-11-11", "2026-11-10", "en-GB"), "Tomorrow");
  assert.equal(dayHeading("2026-11-09", "2026-11-10", "en-GB"), "Yesterday");
  assert.match(dayHeading("2026-11-20", "2026-11-10", "en-GB"), /Fri.*20.*Nov/);
});

// ---------------------------------------------------------------- KB-15: today comes from the clock
test("KB-15: today is computed from the clock and the viewer's timezone", () => {
  const instant = new Date("2026-11-11T02:00:00Z");
  assert.equal(todayKey("America/New_York", instant), "2026-11-10");
  assert.equal(todayKey("Asia/Tokyo", instant), "2026-11-11");
  assert.equal(todayKey("Not/AZone", instant), "2026-11-11");     // unknown zone: UTC, never a throw
});

test("KB-15: the day watcher notices midnight and a resume on a later day, and never stores today", () => {
  let now = new Date("2026-11-10T23:59:30-05:00");
  const timers = [];
  const seen = [];
  const w = dayWatcher({
    tz: "America/New_York", now: () => now, onNewDay: (day) => seen.push(day),
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
  });
  assert.equal(w.today(), "2026-11-10");
  w.start();
  assert.ok(timers.at(-1).ms > 0 && timers.at(-1).ms <= 31_000, `timer for the next midnight: ${timers.at(-1).ms}`);
  assert.equal(w.check(), false);                         // still the same day
  now = new Date("2026-11-11T00:00:05-05:00");
  timers.at(-1).fn();                                     // the midnight timer fires
  assert.deepEqual(seen, ["2026-11-11"]);
  assert.equal(w.today(), "2026-11-11");
  now = new Date("2026-11-13T09:00:00-05:00");            // tab hidden for two days, then shown
  assert.equal(w.check(), true);
  assert.deepEqual(seen, ["2026-11-11", "2026-11-13"]);
  w.stop();
});

test("the next-midnight delay is never zero or negative", () => {
  assert.ok(msUntilNextMidnight("UTC", new Date("2026-11-10T23:59:59.999Z")) >= 1000);
  const ms = msUntilNextMidnight("UTC", new Date("2026-11-10T12:00:00Z"));
  assert.ok(ms > 11.9 * 3600_000 && ms <= 12 * 3600_000 + 1000, String(ms));
});

// ---------------------------------------------------------------- KB-16: step by calendar day
test("KB-16: day lists step by calendar day across the October and March DST weekends", () => {
  const original = process.env.TZ;
  try {
    for (const tz of ["Europe/London", "America/New_York", "UTC"]) {
      process.env.TZ = tz;
      assert.deepEqual(dayRange("2026-10-23", "2026-10-27"), ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]);
      assert.deepEqual(dayRange("2026-03-07", "2026-03-09"), ["2026-03-07", "2026-03-08", "2026-03-09"]);
      assert.deepEqual(dayRange("2026-10-31", "2026-11-02"), ["2026-10-31", "2026-11-01", "2026-11-02"]);
      assert.equal(addDays("2026-03-29", 1), "2026-03-30");
      assert.equal(addDays("2026-11-01", -1), "2026-10-31");
      assert.equal(addDays("2026-12-31", 1), "2027-01-01");
    }
  } finally {
    if (original === undefined) delete process.env.TZ; else process.env.TZ = original;
  }
  assert.equal(todayKey("Europe/London", new Date("2026-10-24T23:30:00Z")), "2026-10-25");   // BST: 00:30 on the 25th
  assert.equal(todayKey("Europe/London", new Date("2026-10-25T23:30:00Z")), "2026-10-25");   // GMT: still the 25th
});

// ---------------------------------------------------------------- entry text
test("entry lines: episode code, title and time; bundles; specials; movie release kinds", () => {
  assert.equal(epCode(2, 5), "S02E05");
  assert.equal(entryLine(episode()), "S02E05 · The Fifth · 21:00");
  assert.equal(entryLine(episode({ episodes: [1, 8], episode: 1, count: 8, episode_title: null, time: "08:00" })),
    "S02E01–E08 · 8 episodes · 08:00");
  assert.equal(entryLine(episode({ season: 0, episode: 3, episodes: [3, 3], special: true, episode_title: "Extras", time: null })),
    "Special · S00E03 · Extras");
  assert.equal(entryLine(movie()), "In cinemas");
  assert.equal(entryLine(movie({ kind: "digital" })), "Digital release");
  assert.equal(entryLine(movie({ kind: "physical" })), "Physical release");
});

// ---------------------------------------------------------------- KB-33 / KB-34: filters
const days = [
  { date: "2026-11-12", items: [movie(), episode(), episode({ id: 3, special: true, season: 0 }),
    episode({ id: 4, monitored: false }), movie({ id: 5, monitored: false })] },
  { date: "2026-11-13", items: [] },
];
const ids = (ds) => ds.flatMap((d) => d.items.map((i) => i.id));

test("KB-33/34: hide specials, monitored only, and films / TV / both", () => {
  assert.deepEqual(ids(applyCalendarFilters(days, emptyCalendarFilters())), [1, 2, 3, 4, 5]);
  assert.deepEqual(ids(applyCalendarFilters(days, { ...emptyCalendarFilters(), hideSpecials: true })), [1, 2, 4, 5]);
  assert.deepEqual(ids(applyCalendarFilters(days, { ...emptyCalendarFilters(), monitoredOnly: true })), [1, 2, 3]);
  assert.deepEqual(ids(applyCalendarFilters(days, { ...emptyCalendarFilters(), media: "movie" })), [1, 5]);
  assert.deepEqual(ids(applyCalendarFilters(days, { ...emptyCalendarFilters(), media: "series" })), [2, 3, 4]);
  assert.equal(applyCalendarFilters(days, emptyCalendarFilters()).length, 2, "days are kept, even when empty");
  assert.equal(activeCalendarFilters(emptyCalendarFilters()), 0);
  assert.equal(activeCalendarFilters({ hideSpecials: true, monitoredOnly: true, media: "series" }), 3);
});

test("saved filters from an older version or a typo fall back to the defaults", () => {
  assert.deepEqual(normalizeCalendarFilters(null), emptyCalendarFilters());
  assert.deepEqual(normalizeCalendarFilters({ media: "music", hideSpecials: "yes", extra: 1 }), emptyCalendarFilters());
  assert.deepEqual(normalizeCalendarFilters({ media: "series", monitoredOnly: true }),
    { ...emptyCalendarFilters(), media: "series", monitoredOnly: true });
});

test("load more merges day lists by date, newest answer wins, sorted", () => {
  const a = [{ date: "2026-11-10", items: [movie({ id: 1 })] }, { date: "2026-11-11", items: [] }];
  const b = [{ date: "2026-11-11", items: [movie({ id: 9 })] }, { date: "2026-11-09", items: [] }];
  const merged = mergeDays(a, b);
  assert.deepEqual(merged.map((d) => d.date), ["2026-11-09", "2026-11-10", "2026-11-11"]);
  assert.deepEqual(ids(merged), [1, 9]);
});
