// Hash routes, so the gateway can serve the app as plain static files with no server-side fallback.
//   #/discover  #/movies  #/tv  #/activity  #/settings[/radarr|/sonarr|/defaults|/compat|/status|/logs]
//   #/title/<movie|series>/<id>   #/similar/<movie|series>/<id>
import { emit } from "./store.js";

export const TABS = ["discover", "movies", "tv", "activity", "settings"];
const seeds = new Map();     // card shown instantly on details while the full record loads
const stack = [];            // hashes we navigated away from with go(), so Back knows what's behind
const scroll = new Map();    // hash -> scrollY, restored when coming back to a grid
let lastTab = "discover";

export function parse(hash = location.hash) {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [head = "discover", ...rest] = parts;
  if (TABS.includes(head)) lastTab = head;
  const known = TABS.includes(head) || head === "title" || head === "similar";
  return { name: known ? head : "discover", params: known ? rest : [], tab: lastTab };
}

const here = () => location.hash || "#/discover";

export function go(path, seed) {
  if (seed) seeds.set(`${seed.type}:${seed.id}`, seed);
  if (path === here()) return;
  stack.push(here());
  scroll.set(here(), window.scrollY);
  location.hash = path;
}

export const titlePath = (type, id) => `#/title/${type}/${id}`;
export const openTitle = (card) => go(titlePath(card.type, card.id), card);
export const seedFor = (type, id) => seeds.get(`${type}:${id}`);

/** A home-screen web app has no browser Back button, so pages draw their own. */
export function back(fallback = `#/${lastTab}`) {
  if (stack.length) history.back();
  else location.replace(fallback);
}

window.addEventListener("hashchange", () => {
  if (stack.length && stack[stack.length - 1] === here()) stack.pop();   // went back (ours or the browser's)
  const y = scroll.get(here()) || 0;
  emit();
  requestAnimationFrame(() => window.scrollTo(0, y));
});
