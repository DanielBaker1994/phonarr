// node --test web/tests/*.test.mjs   (run by ../../test.sh). Checks the page shell that phones load first:
// iPhone Safari meta, and that index.html preloads every module so first load is one parallel wave.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";

const web = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, web), "utf8");
const jsFiles = async (dir) => (await readdir(new URL(dir, web))).filter((f) => f.endsWith(".js")).map((f) => `${dir}${f}`);

test("index.html modulepreloads every module", async () => {
  const html = await read("index.html");
  const modules = ["vendor/preact-htm.js", ...(await jsFiles("js/")), ...(await jsFiles("js/views/"))].filter((m) => m !== "js/main.js");
  const missing = modules.filter((m) => !html.includes(`<link rel="modulepreload" href="${m}" />`));
  assert.deepEqual(missing, [], "add these to index.html");
});

test("index.html has the iPhone viewport and home screen meta", async () => {
  const html = await read("index.html");
  assert.match(html, /name="viewport" content="[^"]*width=device-width[^"]*viewport-fit=cover/);
  assert.doesNotMatch(html, /user-scalable=no|maximum-scale=1/, "keep pinch zoom; 16 px inputs stop the auto-zoom instead");
  assert.match(html, /rel="apple-touch-icon"/);
  assert.match(html, /name="apple-mobile-web-app-capable" content="yes"/);
});

test("app.css keeps form fields at 16 px or more (iOS Safari zooms into smaller ones)", async () => {
  const css = await read("css/app.css");
  assert.match(css, /input, select, textarea \{ font-size: max\(16px, 1em\); \}/);
  for (const m of css.matchAll(/(input|select|textarea)[^{]*\{[^}]*font-size:\s*(\d+(?:\.\d+)?)px/g)) {
    assert.ok(Number(m[2]) >= 16, `field font-size ${m[2]}px in: ${m[0].slice(0, 80)}`);
  }
});
