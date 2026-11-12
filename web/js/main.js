// Entry point of the web frontend: root view, tab bar, routes, service worker.
// Mirrors ios/Phonarr/PhonarrApp.swift (RootView, MainTabs, ToastView).
import { Component, html, render, useEffect, useReducer } from "../vendor/preact-htm.js";
import { Icon } from "./icons.js";
import { text } from "./config.js";
import { installGlobalHandlers, log } from "./log.js";
import { downloadDebugReport } from "./debug.js";
import * as S from "./store.js";
import { state } from "./store.js";
import { parse } from "./router.js";
import { PosterMenu, Spinner, Toast } from "./views/components.js";
import { DiscoverView, MediaGridView } from "./views/browse.js";
import { CustomizeSheet, TitleDetailView } from "./views/detail.js";
import { SimilarListView } from "./views/similar.js";
import { ActivityView } from "./views/activity.js";
import { CompatView } from "./views/compat.js";
import { StatusView } from "./views/status.js";
import { LogsView } from "./views/logs.js";
import { DefaultsView, ServiceConnectionView, SettingsView, SetupFlowView, SignInView } from "./views/settings.js";

const TAB_ITEMS = [
  ["discover", "Discover", "sparkles"],
  ["movies", "Movies", "film"],
  ["tv", "TV Shows", "tv"],
  ["activity", "Activity", "activity"],
  ["settings", "Settings", "gear"],
];

function Screen({ route }) {
  const [a, b] = route.params;
  switch (route.name) {
    case "movies": return html`<${MediaGridView} type="movie" />`;
    case "tv": return html`<${MediaGridView} type="series" />`;
    case "activity": return html`<${ActivityView} />`;
    case "title": return html`<${TitleDetailView} key=${`${a}:${b}`} type=${a === "series" ? "series" : "movie"} id=${Number(b)} />`;
    case "similar": return html`<${SimilarListView} key=${`${a}:${b}`} type=${a === "series" ? "series" : "movie"} id=${Number(b)} />`;
    case "settings":
      if (a === "radarr" || a === "sonarr") return html`<${ServiceConnectionView} key=${a} service=${a} />`;
      if (a === "defaults") return html`<${DefaultsView} />`;
      if (a === "compat") return html`<${CompatView} />`;
      if (a === "status") return html`<${StatusView} />`;
      if (a === "logs") return html`<${LogsView} />`;
      return html`<${SettingsView} />`;
    default: return html`<${DiscoverView} />`;
  }
}

function TabBar({ current }) {
  return html`<nav class="tabbar" aria-label="Sections">
    <div class="tabbar-brand" aria-hidden="true"><${Icon} name="popcorn" size=${26} stroke=${1.7} /><span>${text("app.name")}</span></div>
    ${TAB_ITEMS.map(([id, label, icon]) => html`<a href=${`#/${id}`} class=${current === id ? "on" : ""}
        aria-current=${current === id ? "page" : null}><${Icon} name=${icon} size=${22} /><span>${label}</span></a>`)}
  </nav>`;
}

/** A render bug in one screen shows this panel instead of a blank page, and the error lands in the log
 *  (so in the debug report). The tab bar stays, so Settings is still reachable. */
class ScreenBoundary extends Component {
  componentDidCatch(error) {
    log.error("render_failed", { error: error?.message ?? String(error), stack: (error?.stack || "").split("\n").slice(0, 6).join(" | ") });
    this.setState({ error });
  }
  componentDidUpdate(prev) {
    if (prev.routeKey !== this.props.routeKey && this.state.error) this.setState({ error: null });   // navigating away clears it
  }
  render({ children }, { error, busy, sent }) {
    if (!error) return children;
    const report = async () => {
      this.setState({ busy: true });
      try { await downloadDebugReport({ crash: error?.message ?? String(error) }); this.setState({ sent: true }); }
      catch (e) { this.setState({ sent: e.message }); }
      this.setState({ busy: false });
    };
    return html`<div class="page"><div class="group"><div class="group-pad">
      <p><strong>${text("errors.screen_crashed")}</strong></p>
      <p class="muted">${text("errors.screen_crashed_help")}</p>
      <p class="muted"><code>${error?.message ?? String(error)}</code></p>
      <p><button class="btn" onClick=${() => location.reload()}>Reload</button>
         <button class="btn" disabled=${busy} onClick=${report}>${sent === true ? "Report saved" : "Download debug report"}</button></p>
      ${typeof sent === "string" && html`<p class="bad">${sent}</p>`}
    </div></div></div>`;
  }
}

function App() {
  const [, rerender] = useReducer((n) => n + 1, 0);
  useEffect(() => S.subscribe(rerender), []);
  const route = parse();
  let body;
  if (!state.paired && state.authChecking) body = html`<div class="center pad"><${Spinner} /></div>`;
  else if (!state.paired) body = html`<${SignInView} />`;
  else if (!state.onboardingComplete) body = html`<${SetupFlowView} />`;
  else body = html`<${ScreenBoundary} routeKey=${location.hash}><${Screen} route=${route} /><//><${TabBar} current=${route.tab} />`;
  return html`<div class="app">${body}<${CustomizeSheet} /><${PosterMenu} /><${Toast} /></div>`;
}

installGlobalHandlers();
log.info("launch", { agent: navigator.userAgent });
render(html`<${App} />`, document.getElementById("app"));
S.startSession();

// Service workers need a secure context (HTTPS or localhost). Over plain HTTP the app works the same,
// it just isn't cached for offline start. See docs/DEPLOYMENT.md "Web app over HTTPS".
if ("serviceWorker" in navigator && isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch((e) => log.warn("sw_failed", { error: e.message }));
}
