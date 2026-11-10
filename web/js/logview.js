// Settings → Logs, the pure parts: merge the gateway's lines with this browser's, filter, format, and
// work out how far this device's clock is from the gateway's. Mirrors the iPhone's LogsView helpers.
// Entries are {ts, level, rid, source, where, message} on both sides (GET /v1/debug/logs and log.entries()).

const RANK = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

/** One list, oldest first (newest at the bottom). Both sides write UTC ISO with ms and Z, so the strings sort.
 *  `source` is "both", "gateway" or "web". Lines without a time keep their place after the one before. */
export function mergeEntries(gateway, device, source = "both") {
  const pick = [...(source !== "web" ? gateway : []), ...(source !== "gateway" ? device : [])];
  let last = "";
  const keyed = pick.map((e, i) => {
    last = e.ts || last;
    return { e, key: e.ts || last, i };
  });
  keyed.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.i - b.i));
  return keyed.map((k) => k.e);
}

/** level "WARNING" keeps warnings and worse; q is a case-insensitive substring; rid one request id. */
export function filterEntries(entries, { level = "", q = "", rid = "" } = {}) {
  const min = RANK[level] || 0;
  const needle = q.trim().toLowerCase();
  return entries.filter((e) => {
    if (min && (RANK[e.level] || 0) < min) return false;
    if (rid && e.rid !== rid && !e.message.includes(rid)) return false;
    return !needle || `${e.where} ${e.rid} ${e.message}`.toLowerCase().includes(needle);
  });
}

/** Plain text for Copy and Download: one line per entry, the full UTC time first. */
export function formatEntries(entries) {
  return entries.map((e) => `${e.ts || "-"} ${e.level || "-"} ${e.source} [${e.rid}] ${e.where}: ${e.message}`).join("\n");
}

/** Seconds this device's clock is ahead of the gateway's (negative: behind). Uses the gateway's own
 *  time (gateway.now, ms precision) or else the HTTP Date header, against the middle of the request. */
export function clockSkew({ sentAt, receivedAt, gatewayNow, dateHeader }) {
  const server = Date.parse(gatewayNow || "") || Date.parse(dateHeader || "");
  if (!server || !sentAt || !receivedAt) return null;
  return Math.round(((sentAt + receivedAt) / 2 - server) / 1000);
}

/** The short time shown in the list: "11-10 08:00:01.123" (UTC). */
export const shortTime = (ts) => (ts ? `${ts.slice(5, 10)} ${ts.slice(11, 23)}` : "");
