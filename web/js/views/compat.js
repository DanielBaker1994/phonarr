// Settings → API compatibility (mirrors ios/Phonarr/Views/CompatView.swift).
// "Test API" makes the gateway probe every Radarr/Sonarr endpoint and field the app relies on,
// compare with the server's API spec, and report what changed since the last check.
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { api } from "../api.js";
import { log } from "../log.js";
import { COMPAT_ICON, COMPAT_LABEL, COMPAT_ORDER, capitalize, relativeTime } from "../models.js";
import * as S from "../store.js";
import { Group, Notice, Page, Spinner } from "./components.js";

const tone = { ok: "good", info: "info", warning: "warn", error: "bad" };

export function CompatView() {
  const [reports, setReports] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { api.compatReports().then(setReports).catch((e) => setError(e.message)); }, []);

  const run = async () => {
    setRunning(true);
    log.info("compat_run");
    try {
      const r = await api.runCompatCheck();
      setReports(r);
      setError(null);
      for (const x of [r.radarr, r.sonarr].filter(Boolean)) {
        log.info("compat_result", { service: x.service, version: x.version || "?", status: x.status, changes: x.changes.length });
      }
      await S.refreshConfig();
    } catch (e) { setError(e.message); }
    setRunning(false);
  };

  return html`<${Page} title="API compatibility" backTo="#/settings" large=${false}>
    <${Group} footer="Checks every Radarr and Sonarr endpoint and field this app uses. Read-only: nothing is added or changed. It also runs automatically once a day and whenever a server is connected.">
      <button class="frow" disabled=${running} onClick=${run}>
        <span class="row-label accent"><${Icon} name="stethoscope" size=${18} /> Test API</span>
        <span class="row-value">${running && html`<${Spinner} small />`}</span>
      </button>
    <//>
    ${error && html`<${Notice}>${error}<//>`}
    ${reports?.radarr && html`<${Report} report=${reports.radarr} />`}
    ${reports?.sonarr && html`<${Report} report=${reports.sonarr} />`}
    ${reports && !reports.radarr && !reports.sonarr && html`<p class="muted pad">No checks yet. Click Test API.</p>`}
  <//>`;
}

function Report({ report: r }) {
  return html`<${Group} header=${capitalize(r.service)} footer=${r.spec_note && `${r.spec_note}${r.spec_source ? ` Source: ${r.spec_source}.` : ""}`}>
    <div class="frow">
      <span class=${`row-label ${tone[r.status]}`}><${Icon} name=${COMPAT_ICON[r.status]} size=${22} /></span>
      <span class="grow"><strong>${COMPAT_LABEL[r.status]}</strong>
        <div class="small muted">${capitalize(r.service)} ${r.version || "unreachable"} · checked ${relativeTime(r.checked_at)}</div></span>
    </div>
    ${r.changes.length > 0
      ? html`<div class="frow changes"><div>
          <strong class="small"><${Icon} name="changes" size=${14} /> Changed since last check</strong>
          <ul class="small">${r.changes.map((c) => html`<li>${c}</li>`)}</ul></div></div>`
      : r.previous_checked_at && html`<div class="frow small muted"><${Icon} name="check-circle" size=${14} /> No changes since the last check</div>`}
    ${r.features.map((f) => html`<details class="frow feature">
      <summary><span class=${tone[f.status]}><${Icon} name=${COMPAT_ICON[f.status]} size=${16} /></span> ${f.feature}</summary>
      ${f.checks.map((c) => html`<div class="check">
        <div class="check-head"><span class=${tone[c.status]}><${Icon} name=${COMPAT_ICON[c.status]} size=${14} /></span>
          <span class="grow small">${c.name}</span>
          <code class="tiny muted">${c.method} ${c.path.replace("/api/v3", "")}</code></div>
        ${c.details.map((d) => html`<div class="tiny muted">${d}</div>`)}
      </div>`)}
    </details>`)}
  <//>`;
}

/** Worst API-check result across both servers, shown next to the Settings row. */
export function CompatBadge({ config }) {
  const checks = [config?.radarr?.api_check, config?.sonarr?.api_check].filter(Boolean);
  if (!checks.length) return html`<span class="small muted">Not run</span>`;
  const worst = checks.reduce((a, b) => (COMPAT_ORDER[b.status] > COMPAT_ORDER[a.status] ? b : a));
  const changes = checks.reduce((n, c) => n + c.changes, 0);
  return html`<span class="inline">${changes > 0 && html`<span class="small muted">${changes} changed</span>`}
    <span class=${tone[worst.status]}><${Icon} name=${COMPAT_ICON[worst.status]} size=${18} /></span></span>`;
}
