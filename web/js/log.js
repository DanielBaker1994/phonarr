// App log for the web frontend (mirrors ios Core/AppLog.swift and backend/app/logs.py).
// Lines go to the browser console and to date-stamped buffers in localStorage (one per day,
// key ayr.log.YYYYMMDD), so logs roll daily like the gateway's and the iPhone's. Days older than
// logging.retention_days are dropped. Settings → Troubleshooting sends log.text() to the gateway.
// Every line carries the calling function: "2026-11-07T10:00:00Z INFO store.add: Request movie:1 …".
// Secrets (the gateway token, Arr API keys typed into setup) are replaced with *** before storing.
//
//   log.info("request", { key })          key into strings.json "log.*"; falls back to the raw text
//   const done = log.enter({ key });      // many exits: log entry now, one exit (with duration) via done()
//   try { … } finally { done(); }

import { cfg, text, hasText } from "./config.js";

const PREFIX = cfg("logging.storage_prefix", "ayr.log.");
const MAX_CHARS = cfg("logging.max_chars_per_day", 262144);
const RETENTION = cfg("logging.retention_days", 14);
const MIN_SECRET = cfg("logging.min_secret_length", 6);
// DEBUG (entry/exit traces) goes to the console only unless logging.store_debug is on, so traces
// don't push the warnings and errors out of the daily buffer the debug report sends.
const STORE_DEBUG = cfg("logging.store_debug", false);
const RECENT_MAX = cfg("logging.recent_problems", 20);
let storageFailed = false;
const secrets = new Set();

const day = (d = new Date()) => d.toISOString().slice(0, 10).replaceAll("-", "");
const read = (d) => { try { return localStorage.getItem(PREFIX + d) || ""; } catch { return ""; } };
const days = () => {
  try { return Object.keys(localStorage).filter((k) => k.startsWith(PREFIX)).map((k) => k.slice(PREFIX.length)).sort(); }
  catch { return []; }
};

let today = day();
let buffer = read(today);
prune();

function prune() {
  const cutoff = day(new Date(Date.now() - RETENTION * 86400000));
  for (const d of days()) if (d < cutoff) { try { localStorage.removeItem(PREFIX + d); } catch { /* ignore */ } }
}

function save() {
  try {
    localStorage.setItem(PREFIX + today, buffer);
    storageFailed = false;
  } catch (e) {
    if (!storageFailed) console.warn(`${new Date().toISOString()} WARN log.save: ${text("log.storage_failed", { error: e.message })}`);
    storageFailed = true;   // storage full or blocked: console only; said once, not on every line
  }
}

export function registerSecret(value) {
  if (value && value.length >= MIN_SECRET) secrets.add(value);
}

export function redact(text) {
  let out = String(text);
  for (const s of secrets) out = out.split(s).join("***");
  return out
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1***")
    .replace(/(apikey=|api_key["':\s]+|X-Api-Key:\s*)[A-Za-z0-9]{8,}/gi, "$1***");
}

/** "module.function" of whoever called log.*: parsed from the stack, skipping this file's frames. */
export function caller(stack = new Error().stack || "") {
  for (const line of stack.split("\n").slice(1)) {
    if (line.includes("/log.js")) continue;
    const m = line.match(/at (?:async )?([^\s(]+) \(.*?([^/\s]+)\.js:\d+:\d+\)/) || line.match(/^([^@]*)@.*?([^/\s]+)\.js:\d+:\d+/);
    if (m) return `${m[2]}.${(m[1] || "anonymous").split(".").pop() || "anonymous"}`;
  }
  return "app";
}

function write(level, key, values, where = caller()) {
  const now = new Date();
  if (day(now) !== today) { today = day(now); buffer = ""; prune(); }
  const message = hasText(`log.${key}`) ? text(`log.${key}`, values) : key;
  const line = `${now.toISOString()} ${level} ${where}: ${redact(message)}`;
  (level === "ERROR" ? console.error : level === "WARN" ? console.warn : level === "DEBUG" ? console.debug : console.info)(line);
  if (level === "WARN" || level === "ERROR") {
    recent.push(line);
    if (recent.length > RECENT_MAX) recent.shift();
  }
  if (level === "DEBUG" && !STORE_DEBUG) return;
  buffer += line + "\n";
  if (buffer.length > MAX_CHARS) buffer = buffer.slice(buffer.length - MAX_CHARS * 0.75);
  save();
}

// Recent warnings and errors of this session, for Settings → System status ("Recent problems").
const recent = [];

export const log = {
  info: (k, v = {}) => write("INFO", k, v),
  warn: (k, v = {}) => write("WARN", k, v),
  error: (k, v = {}) => write("ERROR", k, v),
  /** Entry now, and a function that logs the single exit with the duration. */
  enter(args = {}) {
    const where = caller();
    const started = performance.now();
    const rendered = Object.entries(args).map(([k, v]) => `${k}=${v}`).join(", ");
    write("DEBUG", "trace_enter", { fn: where, args: rendered }, where);
    return () => write("DEBUG", "trace_exit", { fn: where, ms: Math.round(performance.now() - started) }, where);
  },
  /** Wraps a function (sync or async) so every call logs entry and exactly one exit, whatever path it takes.
   *  Use for functions with many returns: `export const add = log.traced("store.add", async (type, id) => {…})`. */
  traced: (name, fn) => function tracedFn(...args) {
    const started = performance.now();
    const ms = () => Math.round(performance.now() - started);
    const brief = args.map((a) => (typeof a === "object" && a !== null ? "…" : String(a))).join(", ");
    write("DEBUG", "trace_enter", { fn: name, args: brief }, name);
    const finish = () => write("DEBUG", "trace_exit", { fn: name, ms: ms() }, name);
    const fail = (e) => { write("DEBUG", "trace_failed", { fn: name, ms: ms(), error: e?.message ?? e }, name); throw e; };
    let result;
    try { result = fn.apply(this, args); } catch (e) { return fail(e); }
    if (result && typeof result.then === "function") return result.then((v) => { finish(); return v; }, fail);
    finish();
    return result;
  },
  /** All retained days, oldest first. */
  text: () => days().filter((d) => d !== today).map(read).join("") + buffer,
  /** This session's last warnings and errors, newest last. */
  recentProblems: () => [...recent],
};

/** Uncaught exceptions and rejected promises go into the log (and so into the debug report),
 *  not only the browser console. Called once from main.js. */
export function installGlobalHandlers(target = globalThis) {
  target.addEventListener?.("error", (e) => {
    const where = e.filename ? `${e.filename.split("/").pop()}:${e.lineno}:${e.colno}` : "unknown";
    write("ERROR", "uncaught", { error: e.message, where, stack: (e.error?.stack || "").split("\n").slice(0, 6).join(" | ") }, "window.onerror");
  });
  target.addEventListener?.("unhandledrejection", (e) => {
    const r = e.reason;
    write("ERROR", "unhandled_rejection", { error: r?.message ?? String(r), stack: (r?.stack || "").split("\n").slice(0, 6).join(" | ") }, "window.unhandledrejection");
  });
}
