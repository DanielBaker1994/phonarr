// node --test web/tests/*.test.mjs: store.js actions and polling (docs/KNOWN_BUGS_PRD.md KB-01, KB-04).
// store.js touches document, localStorage, location and fetch when it loads, so they are stubbed
// on globalThis first and the module is imported dynamically. Timers are mocked, so polling is stepped by hand.
import test, { mock } from "node:test";
import assert from "node:assert/strict";

// ---- browser stubs -------------------------------------------------------------------------------
const storage = new Map([["ayr.token", "store-test-token"]]);
globalThis.localStorage = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
  key: (i) => [...storage.keys()][i] ?? null,
  get length() { return storage.size; },
};
const docListeners = {};
globalThis.document = {
  hidden: false,
  get visibilityState() { return this.hidden ? "hidden" : "visible"; },
  addEventListener: (type, fn) => { (docListeners[type] ||= []).push(fn); },
};
globalThis.location = { origin: "http://gateway.test" };
for (const level of ["info", "warn", "error", "debug"]) console[level] = () => {};   // keep test output readable

const calls = [];          // { method, path, headers, release }
let handler = null;        // (call) => Promise<Response-like>
globalThis.fetch = (url, init = {}) => {
  const u = new URL(url);
  const call = { method: init.method || "GET", path: u.pathname, headers: init.headers || {}, body: init.body };
  calls.push(call);
  return handler(call);
};
const respond = (status, json, headers = {}) => ({
  status,
  headers: { get: (k) => headers[k] ?? null },
  json: async () => json,
  text: async () => JSON.stringify(json),
});
const snapshot = () => respond(200, { items: {}, services: {}, next_poll_seconds: 5 }, { ETag: '"x"' });

mock.timers.enable({ apis: ["setTimeout"] });
const S = await import("../js/store.js");
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const statusCalls = () => calls.filter((c) => c.path === "/v1/status");

test("KB-01: a transport failure during add leaves the title requestable; the next tap re-sends the same request", async () => {
  calls.length = 0;
  handler = async (c) => {
    if (c.path === "/v1/requests") throw new TypeError("Failed to fetch");
    return snapshot();
  };
  await S.add("movie", 961);
  assert.equal(S.statusOf("movie", 961).state, "not_requested", "title shown as failed after a lost connection");
  assert.ok(S.state.toast, "a message says the gateway couldn't be reached");
  const first = calls.find((c) => c.path === "/v1/requests");

  handler = async (c) => {
    if (c.path === "/v1/requests") return respond(200, { type: "movie", id: 961, deduplicated: false, status: { state: "searching" } });
    return snapshot();
  };
  calls.length = 0;
  await S.add("movie", 961);
  const sent = calls.filter((c) => c.method === "POST").map((c) => c.path);
  assert.deepEqual(sent, ["/v1/requests"], "second tap should send the request again, not a retry");
  const second = calls.find((c) => c.path === "/v1/requests");
  assert.equal(second.headers["Idempotency-Key"], first.headers["Idempotency-Key"], "same idempotency key");
  assert.equal(S.statusOf("movie", 961).state, "searching");
  S.stopPolling();
});

test("KB-01: a gateway error (not a lost connection) still shows Failed", async () => {
  handler = async (c) => {
    if (c.path === "/v1/requests") return respond(502, { error: { kind: "download", message: "Radarr said no", request_id: "r1" } });
    return snapshot();
  };
  await S.add("movie", 4808);
  assert.equal(S.statusOf("movie", 4808).state, "failed");
  S.stopPolling();
});

test("KB-04: refreshNow during a slow poll leaves one loop, and hiding the tab stops all polling", async () => {
  S.stopPolling();
  mock.timers.tick(10_000);       // let anything left over from earlier tests run out
  await flush();
  calls.length = 0;
  const pending = [];
  handler = () => new Promise((resolve) => pending.push(() => resolve(snapshot())));

  S.refreshNow();
  mock.timers.tick(0);
  await flush();
  assert.equal(statusCalls().length, 1, "first poll in flight");
  S.refreshNow();
  S.refreshNow();
  mock.timers.tick(0);
  await flush();
  for (const release of pending.splice(0)) release();
  await flush();

  // Visible: exactly one loop polls on each interval from here on.
  calls.length = 0;
  handler = async () => snapshot();
  mock.timers.tick(5_000);
  await flush();
  assert.equal(statusCalls().length, 1, "two loops are polling");

  document.hidden = true;
  for (const fn of docListeners.visibilitychange) fn();
  const atHide = calls.length;
  for (let i = 0; i < 10; i++) { mock.timers.tick(60_000); await flush(); }
  assert.equal(calls.length, atHide, "fetch after the tab was hidden");
  document.hidden = false;
});
