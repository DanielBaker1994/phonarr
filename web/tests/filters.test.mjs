// node --test web/tests/*.test.mjs   (run by ../../test.sh). Checks the client-side filters behave like backend/app/discovery.py.
import test from "node:test";
import assert from "node:assert/strict";
import { applyFilters, buildShelves, emptyFilters, activeCount, sortCards, genreNeighbours, neighboursFor, viaNeighbour, ratingStrip } from "../js/filters.js";

const cards = [
  { type: "movie", id: 1, title: "Nosferatu", original_title: "Nosferatu, eine Symphonie des Grauens", year: 1922, decade: 1920,
    genres: ["Horror"], runtime: 94, rating: 7.9, language: "German", source: "list", added: "2026-01-02" },
  { type: "movie", id: 2, title: "The General", year: 1926, decade: 1920, genres: ["Comedy", "Action"], runtime: 79, rating: null, source: "list" },
  { type: "movie", id: 3, title: "Charade", year: 1963, decade: 1960, genres: ["Mystery", "Romance"], runtime: 113, rating: 7.4,
    source: "recommended", added: "2026-03-01" },
  { type: "movie", id: 4, title: "Detour", year: 1945, decade: 1940, genres: ["Crime"], runtime: 68, rating: 7.1, source: "import_list" },
];
const ids = (list) => list.map((c) => c.id);

// The old tests below check the shared rules with English only off and title order, as before.
const plain = (patch = {}) => ({ ...emptyFilters("title"), english_only: false, ...patch });

test("defaults: highest rated first, English only (titles with no language kept)", () => {
  assert.deepEqual(ids(applyFilters(cards, emptyFilters())), [3, 4, 2]);
  assert.deepEqual(ids(applyFilters(cards, { ...emptyFilters(), english_only: false })), [1, 3, 4, 2]);
});

test("no filters sorts by title", () => {
  assert.deepEqual(ids(applyFilters(cards, plain())), [3, 4, 1, 2]);
});

test("search matches the title and the original title", () => {
  assert.deepEqual(ids(applyFilters(cards, plain(), "symphonie")), [1]);
  assert.deepEqual(ids(applyFilters(cards, plain(), "CHAR")), [3]);
});

test("genres are any-of; decades, year range, length range and rating narrow down", () => {
  assert.deepEqual(ids(applyFilters(cards, plain({ genres: ["Horror", "Crime"] }))), [4, 1]);
  assert.deepEqual(ids(applyFilters(cards, plain({ exclude_genres: ["Horror", "Crime"] }))), [3, 2]);
  assert.deepEqual(ids(applyFilters(cards, plain({ year_mode: "decades", decades: [1920, 1960] }))), [3, 1, 2]);
  assert.deepEqual(ids(applyFilters(cards, plain({ year_mode: "range", year_min: 1945, year_max: 1922 }))), [4, 1, 2]);  // either order
  assert.deepEqual(ids(applyFilters(cards, plain({ year_mode: "any", year_min: 1945 }))), [3, 4, 1, 2]);   // range ignored unless chosen
  assert.deepEqual(ids(applyFilters(cards, plain({ runtime_min: 70, runtime_max: 100 }))), [1, 2]);
  assert.deepEqual(ids(applyFilters(cards, plain({ min_rating: 7.3 }))), [3, 1]);  // unrated hidden
  assert.deepEqual(ids(applyFilters(cards, plain({ source: "list" }))), [1, 2]);
});

test("neighbouring genres match too, can be dropped one by one, and lose to an exclusion", () => {
  const pool = [
    { id: 1, title: "A", genres: ["Horror", "Thriller"] }, { id: 2, title: "B", genres: ["Thriller", "Crime"] },
    { id: 3, title: "C", genres: ["Horror"] }, { id: 4, title: "D", genres: ["Drama"] }, { id: 5, title: "E", genres: ["Drama", "Horror"] },
    { id: 6, title: "F", genres: ["Drama"] }, { id: 7, title: "G", genres: ["Drama"] }, { id: 8, title: "H", genres: ["Drama"] },
  ];
  const map = genreNeighbours(pool, 0.15);
  // Horror {1,3,5} vs Thriller {1,2}: 1/4. Drama {4,5,6,7,8} vs Horror: 1/7, under 15%, so Drama isn't a neighbour.
  assert.deepEqual(neighboursFor(["Horror"], map).map((n) => n.value), ["Thriller"]);
  const f = plain({ genres: ["Horror"] });
  assert.deepEqual(ids(applyFilters(pool, f, "", map)), [1, 2, 3, 5]);
  assert.equal(viaNeighbour(pool[1], f, map), "Thriller");
  assert.equal(viaNeighbour(pool[0], f, map), null);
  assert.deepEqual(ids(applyFilters(pool, { ...f, neighbours_off: ["Thriller"] }, "", map)), [1, 3, 5]);
  assert.deepEqual(ids(applyFilters(pool, { ...f, neighbours: false }, "", map)), [1, 3, 5]);
  assert.deepEqual(ids(applyFilters(pool, { ...f, exclude_genres: ["Thriller"] }, "", map)), [3, 5]);
});

test("rating strip lists the sources a title has, in a fixed order", () => {
  assert.deepEqual(ratingStrip({ ratings: { tmdb: 7.5, metacritic: 89, imdb: 7.8, rottenTomatoes: 95 } }),
    [["IMDb", "7.8"], ["TMDB", "7.5"], ["RT", "95%"], ["Meta", "89"]]);
  assert.deepEqual(ratingStrip({}), []);
});

test("sorts put titles missing the key last", () => {
  assert.deepEqual(ids(sortCards(cards, "rating")), [1, 3, 4, 2]);
  assert.deepEqual(ids(sortCards(cards, "year_asc")), [1, 2, 4, 3]);
  assert.deepEqual(ids(sortCards(cards, "recent")), [3, 1, 4, 2]);
});

test("activeCount counts each filter once and every genre", () => {
  assert.equal(activeCount(emptyFilters("rating")), 0);
  assert.equal(activeCount({ ...emptyFilters(), genres: ["A", "B"], year_mode: "decades", decades: [1920, 1930], min_rating: 5 }), 4);
  assert.equal(activeCount({ ...emptyFilters(), year_mode: "decades", decades: [] }), 0);
});

test("shelves need at least three titles", () => {
  const shelves = buildShelves(cards);
  assert.deepEqual(shelves.map((s) => s.id), ["rated"]);
  assert.deepEqual(ids(shelves[0].items), [1, 3, 4]);
});
