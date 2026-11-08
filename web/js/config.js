// Config manager (mirrors backend/app/config.py and ios Core/AppConfig.swift).
// Tunables live in config/settings.json and non-UI strings in config/strings.json, never in code:
//   cfg("logging.retention_days")            dotted lookup into settings.json
//   text("log.request", { key })            "{name}" placeholders filled from the values
// The files are fetched once at startup (top-level await). If that fails (offline first load)
// the lookups fall back to the supplied default / the key itself, so a missing file never breaks the app.

const dig = (root, dotted) => dotted.split(".").reduce((n, p) => (n == null ? undefined : n[p]), root);

export function makeConfig(settings = {}, strings = {}) {
  return {
    cfg: (key, fallback) => dig(settings, key) ?? fallback,
    text: (key, values = {}) => {
      let out = dig(strings, key);
      if (typeof out !== "string") return key;
      for (const [k, v] of Object.entries(values)) out = out.split(`{${k}}`).join(String(v));
      return out;
    },
    hasText: (key) => typeof dig(strings, key) === "string",
  };
}

async function loadJson(name) {
  const url = new URL(`../config/${name}.json`, import.meta.url);
  try {
    if (url.protocol === "file:") {   // node (tests): read from disk
      const { readFile } = await import("node:fs/promises");
      return JSON.parse(await readFile(url, "utf8"));
    }
    const res = await fetch(url);
    return res.ok ? await res.json() : {};
  } catch { return {}; }
}

const loaded = makeConfig(await loadJson("settings"), await loadJson("strings"));
export const { cfg, text, hasText } = loaded;
