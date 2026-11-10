// node --test web/tests/*.test.mjs: Settings → Logs, the pure parts (parsing this browser's log,
// merging with the gateway's lines, filters, text for Copy/Download, clock difference).
import test from "node:test";
import assert from "node:assert/strict";
import { parseLogText, registerSecret } from "../js/log.js";
import { clockSkew, filterEntries, formatEntries, mergeEntries, shortTime } from "../js/logview.js";

const gw = (ts, level, rid, message) => ({ ts, level, rid, source: "gateway", where: "gateway.request_log:254", message });

test("parseLogText: same shape as the gateway, rid from [req …], WARN named WARNING, continuation lines kept", () => {
  registerSecret("abcdef-secret-99");
  const raw = [
    "2026-11-10T08:00:00.100Z INFO api.raw: GET /v1/status -> 200 in 12ms [req web-1a2b3c4d]",
    "2026-11-10T08:00:01.200Z WARN store.add: Request movie:1 failed: boom abcdef-secret-99",
    "    at add (store.js:1:1)",
    "",
  ].join("\n");
  const out = parseLogText(raw);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], { ts: "2026-11-10T08:00:00.100Z", level: "INFO", rid: "web-1a2b3c4d", source: "web",
                             where: "api.raw", message: "GET /v1/status -> 200 in 12ms [req web-1a2b3c4d]" });
  assert.equal(out[1].level, "WARNING");
  assert.equal(out[1].rid, "-");
  assert.equal(out[1].message, "Request movie:1 failed: boom ***\n    at add (store.js:1:1)");
});

test("mergeEntries: by time, newest last, and the source filter", () => {
  const g = [gw("2026-11-10T08:00:00.500Z", "INFO", "web-1", "a"), gw("2026-11-10T08:00:02.000Z", "ERROR", "-", "c")];
  const d = parseLogText("2026-11-10T08:00:01.000Z INFO api.raw: b");
  assert.deepEqual(mergeEntries(g, d).map((e) => e.message), ["a", "b", "c"]);
  assert.deepEqual(mergeEntries(g, d, "gateway").map((e) => e.message), ["a", "c"]);
  assert.deepEqual(mergeEntries(g, d, "web").map((e) => e.message), ["b"]);
});

test("filterEntries: level, text and request id on both sides", () => {
  const all = [
    gw("2026-11-10T08:00:00.000Z", "INFO", "web-1", "GET /v1/status -> 200"),
    gw("2026-11-10T08:00:01.000Z", "WARNING", "web-2", "POST /v1/requests -> 503"),
    ...parseLogText("2026-11-10T08:00:02.000Z ERROR api.raw: failed [req web-1]"),
  ];
  assert.deepEqual(filterEntries(all, { level: "WARNING" }).map((e) => e.level), ["WARNING", "ERROR"]);
  assert.deepEqual(filterEntries(all, { rid: "web-1" }).map((e) => e.source), ["gateway", "web"]);
  assert.deepEqual(filterEntries(all, { q: "REQUESTS" }).map((e) => e.rid), ["web-2"]);
});

test("formatEntries and shortTime", () => {
  const e = gw("2026-11-10T08:00:00.123Z", "INFO", "web-1", "hi");
  assert.equal(formatEntries([e]), "2026-11-10T08:00:00.123Z INFO gateway [web-1] gateway.request_log:254: hi");
  assert.equal(shortTime(e.ts), "11-10 08:00:00.123");
});

test("clockSkew: device minus gateway, from gateway.now or the Date header", () => {
  const server = Date.parse("2026-11-10T08:00:00.000Z");
  assert.equal(clockSkew({ sentAt: server + 299000, receivedAt: server + 301000, gatewayNow: "2026-11-10T08:00:00.000Z" }), 300);
  assert.equal(clockSkew({ sentAt: server - 5000, receivedAt: server - 5000, dateHeader: "Tue, 10 Nov 2026 08:00:00 GMT" }), -5);
  assert.equal(clockSkew({ sentAt: 1, receivedAt: 2 }), null);
});
