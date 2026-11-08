// node --test web/tests/*.test.mjs: the config manager and the logger's pure parts.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { makeConfig } from "../js/config.js";
import { redact, registerSecret, caller, log, installGlobalHandlers } from "../js/log.js";

const read = (f) => JSON.parse(readFileSync(new URL(`../config/${f}.json`, import.meta.url), "utf8"));

test("config: dotted lookup, defaults and placeholders", () => {
  const c = makeConfig({ a: { b: 3 } }, { log: { hi: "Hi {name}, {name}!" } });
  assert.equal(c.cfg("a.b"), 3);
  assert.equal(c.cfg("a.zzz", 7), 7);
  assert.equal(c.text("log.hi", { name: "Dan" }), "Hi Dan, Dan!");
  assert.equal(c.text("log.missing"), "log.missing");
});

test("config files: every log key used in code exists", () => {
  const strings = read("strings");
  const used = new Set();
  const walk = (dir) => {
    for (const f of readdirSync(dir, { withFileTypes: true })) {
      const p = new URL(`${f.name}${f.isDirectory() ? "/" : ""}`, dir);
      if (f.isDirectory()) walk(p);
      else if (f.name.endsWith(".js")) {
        for (const m of readFileSync(p, "utf8").matchAll(/log\.(?:info|warn|error)\(\s*"([a-z_]+)"/g)) used.add(m[1]);
      }
    }
  };
  walk(new URL("../js/", import.meta.url));
  assert.ok(used.size > 10);
  for (const k of used) assert.ok(strings.log[k], `missing strings.json log.${k}`);
});

test("log: redacts registered secrets and bearer tokens", () => {
  registerSecret("supersecret123");
  assert.equal(redact("token supersecret123 ok"), "token *** ok");
  assert.equal(redact("Authorization: Bearer abc.def-123"), "Authorization: Bearer ***");
});

test("log: caller finds module.function from a stack", () => {
  const chrome = "Error\n    at write (http://x/js/log.js:1:1)\n    at add (http://x/js/store.js:10:5)";
  assert.equal(caller(chrome), "store.add");
  const ff = "write@http://x/js/log.js:1:1\nadd@http://x/js/store.js:10:5";
  assert.equal(caller(ff), "store.add");
});

test("log: traced logs one exit, sync and async", async () => {
  const lines = [];
  const orig = console.debug; console.debug = (l) => lines.push(l);
  try {
    const f = log.traced("t.f", (x) => (x ? 1 : 2));
    assert.equal(f(0), 2);
    assert.equal(await log.traced("t.g", async () => 5)(), 5);
  } finally { console.debug = orig; }
  assert.equal(lines.filter((l) => l.includes("done in")).length, 2);
  assert.ok(lines.some((l) => l.includes("DEBUG t.f: -> t.f(0)")));
});

test("log: uncaught errors and rejections are captured into the log", () => {
  const target = new EventTarget();
  installGlobalHandlers(target);
  const orig = console.error; console.error = () => {};
  try {
    const err = new Event("error");
    Object.assign(err, { message: "boom", filename: "http://x/js/views/detail.js", lineno: 12, colno: 3, error: new Error("boom") });
    target.dispatchEvent(err);
    const rej = new Event("unhandledrejection");
    rej.reason = new Error("nope");
    target.dispatchEvent(rej);
  } finally { console.error = orig; }
  const problems = log.recentProblems().join("\n");
  assert.match(problems, /Uncaught error at detail\.js:12:3: boom/);
  assert.match(problems, /Unhandled promise rejection: nope/);
});

test("log: DEBUG traces stay out of the stored log by default", () => {
  const orig = console.debug; console.debug = () => {};
  try { log.traced("t.quiet", () => 1)(); } finally { console.debug = orig; }
  assert.ok(!log.text().includes("t.quiet"));
});

test("config files: every errors.* text used in code exists", () => {
  const strings = read("strings");
  const used = new Set();
  const walk = (dir) => {
    for (const f of readdirSync(dir, { withFileTypes: true })) {
      const p = new URL(`${f.name}${f.isDirectory() ? "/" : ""}`, dir);
      if (f.isDirectory()) walk(p);
      else if (f.name.endsWith(".js")) for (const m of readFileSync(p, "utf8").matchAll(/text\("errors\.([a-z_]+)"/g)) used.add(m[1]);
    }
  };
  walk(new URL("../js/", import.meta.url));
  assert.ok(used.size >= 8);
  for (const k of used) assert.ok(strings.errors[k], `missing strings.json errors.${k}`);
});
