// Settings → System status: is everything up? (the iPhone app has the same screen: SystemStatusView).
// Uses GET /v1/health (no token) for the gateway and server reachability, plus GET /v1/config for error details.
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { api } from "../api.js";
import { log } from "../log.js";
import { capitalize } from "../models.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { Group, Notice, Page, Row, Spinner } from "./components.js";

const STATUS_TEXT = {
  ok: ["good", "check-circle", "Everything is up"],
  degraded: ["warn", "warning", "Gateway is up, a server is unreachable"],
  setup: ["muted", "info", "Gateway is up, no server connected yet"],
};

function duration(s) {
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

export function StatusView() {
  const [h, setH] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { setH(await api.health()); setError(null); } catch (e) { setError(e.message); setH(null); }
    await S.refreshConfig();
    setBusy(false);
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const [tone, icon, text] = error ? ["bad", "x-octagon", "Gateway unreachable"] : STATUS_TEXT[h?.status] || ["muted", "question", "Checking…"];
  const refresh = html`<button class="icon-btn" aria-label="Refresh" onClick=${load}>${busy ? html`<${Spinner} small />` : html`<${Icon} name="retry" />`}</button>`;
  return html`<${Page} title="System status" backTo="#/settings" large=${false} actions=${refresh}>
    <${Group}>
      <div class="frow"><span class=${`row-label ${tone}`}><${Icon} name=${icon} size=${22} /> <strong>${text}</strong></span></div>
    <//>
    ${error && html`<${Notice} tone="bad">${error} The page itself came from the gateway, so it was up a moment ago. Check the container: <code>docker compose ps phonarr</code><//>`}
    ${h && html`<${Group} header="Gateway" footer="Docker marks the container healthy when this answers (HEALTHCHECK on /v1/health). Radarr or Sonarr being down shows here as degraded, it never restarts the gateway.">
      <${Row} label="Answering">${h.ms} ms<//>
      <${Row} label="Version">${h.version}<//>
      <${Row} label="Up for">${duration(h.uptime_s)}<//>
      <${Row} label="Status last refreshed">${h.last_status_refresh_s_ago == null ? "not yet" : `${duration(Math.round(h.last_status_refresh_s_ago))} ago`}<//>
      <${Row} label="Polling Radarr/Sonarr">${h.poll_interval ? `every ${h.poll_interval} s`
        : h.idle_health_check_s ? `idle: reachability check every ${duration(h.idle_health_check_s)}` : "paused (nothing to watch)"}<//>
      ${h.poller && h.poller !== "running" && html`<${Row} label="Background poller"><span class=${h.poller === "stopped" ? "bad" : "muted"}>${h.poller === "stopped" ? "stopped (see the gateway log)" : "off (POLLER=0)"}</span><//>`}
      ${h.last_poll_error_s_ago != null && html`<${Row} label="Last poll error"><span class="warn">${duration(h.last_poll_error_s_ago)} ago (gateway log has the details)</span><//>`}
      <${Row} label="Web app">${h.web_frontend ? "served" : "not served"}<//>
    <//>`}
    ${h && ["radarr", "sonarr"].map((svc) => {
      const s = h.services[svc], cfg = state.config?.[svc];
      const [t, label] = !s.configured ? ["muted", "Not connected"] : s.reachable === false ? ["bad", "Unreachable"]
        : s.reachable ? ["good", "Reachable"] : ["muted", "Not checked yet"];
      return html`<${Group} key=${svc} header=${capitalize(svc)}>
        <${Row} label="State"><span class=${t}>${label}</span><//>
        ${s.version && html`<${Row} label="Version">${s.version}<//>`}
        ${s.down_for_s != null && html`<${Row} label="Down for"><span class="bad">${duration(s.down_for_s)}</span><//>`}
        ${s.checked_s_ago != null && html`<${Row} label="Last checked">${duration(Math.round(s.checked_s_ago))} ago<//>`}
        ${cfg?.base_url && html`<${Row} label="Address">${cfg.base_url}<//>`}
        ${(state.services[svc]?.error || cfg?.error) && html`<div class="group-pad"><${Notice} tone="bad">${(state.services[svc]?.error || cfg.error).message}<//></div>`}
        ${!s.configured && html`<${Row} label=${`Connect ${capitalize(svc)}`} href=${`#/settings/${svc}`} />`}
      <//>`;
    })}
    ${(() => {
      const problems = log.recentProblems();
      return problems.length > 0 && html`<${Group} header="Recent problems in this browser" footer="The last warnings and errors since this page loaded. The debug report (Settings → Troubleshooting) has the full log, and the [req …] id finds the same call in the gateway's log.">
        <div class="group-pad"><pre class="log-lines">${problems.slice().reverse().join("\n")}</pre></div>
      <//>`;
    })()}
    <${Group} footer="Every endpoint with a copy-and-paste curl command and a Test button.">
      <a class="frow accent" href="/console/" target="_blank" rel="noopener"><span class="row-label"><${Icon} name="stethoscope" size=${18} /> Open the API console</span></a>
      <a class="frow accent" href="/v1/health" target="_blank" rel="noopener"><span class="row-label"><${Icon} name="info" size=${18} /> Raw /v1/health</span></a>
    <//>
  <//>`;
}
