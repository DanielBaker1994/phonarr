// node --test web/tests/*.test.mjs   (run by ../../test.sh). Checks the client-side filters behave like backend/app/discovery.py.
import test from "node:test";
import assert from "node:assert/strict";
import { applyFilters, buildShelves, emptyFilters, activeCount, sortCards } from "../js/filters.js";

const cards = [
  { type: "movie", id: 1, title: "Nosferatu", original_title: "Nosferatu, eine Symphonie des Grauens", year: 1922, decade: 1920,
    genres: ["Horror"], runtime: 94, rating: 7.9, language: "German", source: "list", added: "2026-01-02" },
  { type: "movie", id: 2, title: "The General", year: 1926, decade: 1920, genres: ["Comedy", "Action"], runtime: 79, rating: null, source: "list" },
  { type: "movie", id: 3, title: "Charade", year: 1963, decade: 1960, genres: ["Mystery", "Romance"], runtime: 113, rating: 7.4,
    source: "recommended", added: "2026-03-01" },
  { type: "movie", id: 4, title: "Detour", year: 1945, decade: 1940, genres: ["Crime"], runtime: 68, rating: 7.1, source: "import_list" },
];
const ids = (list) => list.map((c) => c.id);

test("no filters sorts by title", () => {
  assert.deepEqual(ids(applyFilters(cards, emptyFilters())), [3, 4, 1, 2]);
});

test("search matches the title and the original title", () => {
  assert.deepEqual(ids(applyFilters(cards, emptyFilters(), "symphonie")), [1]);
  assert.deepEqual(ids(applyFilters(cards, emptyFilters(), "CHAR")), [3]);
});

test("genres are any-of; decade, runtime band and rating narrow down", () => {
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), genres: ["Horror", "Crime"] })), [4, 1]);
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), decade: 1920 })), [1, 2]);
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), runtime: "brisk" })), [4, 2]);
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), min_rating: 7.3 })), [3, 1]);  // unrated hidden
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), source: "list" })), [1, 2]);
});

test("sorts put titles missing the key last", () => {
  assert.deepEqual(ids(sortCards(cards, "rating")), [1, 3, 4, 2]);
  assert.deepEqual(ids(sortCards(cards, "year_asc")), [1, 2, 4, 3]);
  assert.deepEqual(ids(sortCards(cards, "recent")), [3, 1, 4, 2]);
});

test("activeCount counts each filter once and every genre", () => {
  assert.equal(activeCount(emptyFilters("rating")), 0);
  assert.equal(activeCount({ ...emptyFilters(), genres: ["A", "B"], decade: 1920, min_rating: 5 }), 4);
});

test("shelves need at least three titles", () => {
  const shelves = buildShelves(cards);
  assert.deepEqual(shelves.map((s) => s.id), ["rated"]);
  assert.deepEqual(ids(shelves[0].items), [1, 3, 4]);
});
