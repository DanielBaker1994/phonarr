// Sign-in, first-run setup, Settings, server connections, request defaults, debug report
// (mirrors ios/Phonarr/Views/SettingsViews.swift).
// The web app is served by the gateway, so there's no gateway address to type: only the access token.
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { api } from "../api.js";
import { log, registerSecret } from "../log.js";
import { SEASON_MODES, seasonLabel, capitalize } from "../models.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { go, back } from "../router.js";
import { Group, Notice, Page, Row, SelectRow, Spinner, Toggle } from "./components.js";
import { CompatBadge } from "./compat.js";
import { downloadDebugReport } from "../debug.js";

// ------------------------------------------------------------------ sign in
export function SignInView() {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await S.pair(token); setError(null); } catch (err) {
      log.error("signin_failed", { error: err.message });
      setError(err.message);
    }
    setBusy(false);
  };
  const msg = error || state.pairingMessage;
  return html`<section class="page narrow">
    <div class="hero">
      <span class="accent"><${Icon} name="popcorn" size=${44} stroke=${1.6} /></span>
      <h1>Phonarr</h1>
      <p class="muted">Discover films and TV and add them to Radarr and Sonarr in one tap.</p>
    </div>
    <form onSubmit=${submit}>
      <${Group} header="Gateway" footer=${html`The <code>ARR_GATEWAY_TOKEN</code> set for the Phonarr container. It's kept in this browser only. This page comes from the gateway itself (<code>${location.host}</code>), so there's no address to enter.`}>
        <label class="frow"><input class="grow" type="password" placeholder="Access token" value=${token} autocomplete="current-password"
          onInput=${(e) => setToken(e.target.value)} autofocus /></label>
      <//>
      ${msg && html`<${Notice} tone="bad">${msg}<//>`}
      <${Group}><button class="frow accent strong" type="submit" disabled=${!token.trim() || busy}>
        <span class="row-label">Sign in</span><span class="row-value">${busy && html`<${Spinner} small />`}</span></button><//>
    </form>
  </section>`;
}

// ------------------------------------------------------------------ first-run setup
/** One screen for Radarr, one for Sonarr, either can be skipped, then defaults. */
export function SetupFlowView() {
  const [step, setStep] = useState(0);
  useEffect(() => { S.refreshConfig(); }, []);
  if (step < 2) {
    const service = step === 0 ? "radarr" : "sonarr";
    const cfg = state.config?.[service];
    return html`<${ServiceConnectionView} key=${service} service=${service} title=${capitalize(service)}
      intro=${`Step ${step + 1} of 2 · Connect ${capitalize(service)} to discover and add ${step === 0 ? "films" : "TV shows"}.`}
      onSaved=${() => setStep(step + 1)}
      actions=${html`<button class="link strong" onClick=${() => setStep(step + 1)}>${cfg?.configured ? "Next" : "Skip"}</button>`} />`;
  }
  return html`<${SetupSummary} back=${() => setStep(0)} />`;
}

function SetupSummary({ back: goBack }) {
  useEffect(() => { S.refreshOptions(); }, []);
  return html`<${Page} title="You're set">
    <${Group} footer=${S.needsServiceSetup()
        ? "Nothing is connected yet. You can still look around and connect a server later in Settings."
        : "You can connect or change servers any time in Settings."}>
      ${["radarr", "sonarr"].map((svc) => {
        const on = state.config?.[svc]?.configured;
        return html`<div class=${`frow ${on ? "good" : "muted"}`}><span class="row-label">
          <${Icon} name=${on ? "check-circle" : "circle"} size=${18} /> ${svc === "radarr" ? "Radarr · Films" : "Sonarr · TV Shows"}</span></div>`;
      })}
    <//>
    <${DefaultsSection} />
    <${Group}>
      <button class="frow accent strong" onClick=${() => { S.setOnboardingComplete(true); go("#/discover"); }}>Start browsing</button>
      <button class="frow accent" onClick=${goBack}>Back</button>
    <//>
  <//>`;
}

// ------------------------------------------------------------------ Settings tab
export function SettingsView() {
  useEffect(() => { S.refreshConfig(); S.refreshOptions(); }, []);
  return html`<${Page} title="Settings">
    <${Group}>
      <${Row} href="#/settings/status" label=${html`<span class="inline"><span class="accent"><${Icon} name="activity" /></span> System status</span>`}>
        <${HealthSummary} /><//>
    <//>
    <${Group} header="Servers">
      ${["radarr", "sonarr"].map((svc) => {
        const cfg = state.config?.[svc];
        return html`<${Row} key=${svc} href=${`#/settings/${svc}`} label=${html`<span class="inline">
            <span class="accent"><${Icon} name=${svc === "radarr" ? "film" : "tv"} /></span>
            <span><span>${capitalize(svc)}</span><div class="small muted clamp1">${cfg?.base_url || "Not configured"}</div></span></span>`}>
          <${ConnectionDot} cfg=${cfg} health=${state.services[svc]} /><//>`;
      })}
    <//>
    <${DefaultsSection} />
    <${Group} header="Discovery" footer="Discovery shows your Radarr import lists, Radarr's recommendations and the titles list on the gateway (titles.json). Search in Movies or TV Shows finds anything else Radarr or Sonarr can look up.">
      <${Toggle} label="Show titles already in library" checked=${state.includeTracked} onChange=${S.setIncludeTracked} />
    <//>
    <${Group} footer="Test API checks that your Radarr and Sonarr still offer everything this app uses, and shows what changed after an update.">
      <${Row} href="#/settings/compat" label=${html`<span class="inline"><span class="accent"><${Icon} name="stethoscope" /></span> API compatibility</span>`}>
        <${CompatBadge} config=${state.config} /><//>
    <//>
    <${DebugSection} />
    <${InstallSection} />
    <${Group} header="Gateway">
      <${Row} label="Address">${location.host}<//>
      <button class="frow bad" onClick=${() => { if (confirm("Sign out of the gateway in this browser?")) S.unpair(); }}>Sign out</button>
    <//>
  <//>`;
}

/** One-word summary for the System status row, from what the app already knows. */
function HealthSummary() {
  if (!state.gatewayReachable) return html`<span class="inline"><span class="small muted">Gateway unreachable</span><span class="dot red"></span></span>`;
  const down = ["radarr", "sonarr"].some((s) => state.config?.[s]?.configured && state.services[s]?.reachable === false);
  return html`<span class="inline"><span class="small muted">${down ? "Degraded" : "All up"}</span><span class=${`dot ${down ? "red" : "green"}`}></span></span>`;
}

function ConnectionDot({ cfg, health }) {
  let color = "gray", text = "Not set up";
  if (cfg?.configured) {
    const down = health?.reachable === false || cfg.reachable === false;
    color = down ? "red" : "green";
    text = down ? "Unreachable" : cfg.version ? `v${cfg.version}` : "Connected";
  }
  return html`<span class="inline"><span class="small muted">${text}</span><span class=${`dot ${color}`}></span></span>`;
}

// ------------------------------------------------------------------ server connection
export function ServiceConnectionView({ service, intro, onSaved, actions }) {
  const existing = state.config?.[service];
  const name = capitalize(service);
  const [url, setUrl] = useState(existing?.base_url || "");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  useEffect(() => { if (!url && existing?.base_url) setUrl(existing.base_url); }, [existing?.base_url]);

  const run = async (save) => {
    setBusy(true);
    registerSecret(apiKey);
    try {
      const t = await S.saveConnection(service, url, apiKey, save);
      setResult({ ok: true, t });
      if (save) {
        setApiKey("");
        if (onSaved) onSaved(); else back("#/settings");
      }
    } catch (e) {
      log.warn("connection_failed", { service, verb: save ? "save" : "test", error: e.message });
      setResult({ ok: false, message: e.message });
    }
    setBusy(false);
  };
  const remove = async () => {
    if (!confirm(`Remove the ${name} connection? The app keeps working with the other server.`)) return;
    await S.removeConnection(service);
    back("#/settings");
  };

  return html`<${Page} title=${name} backTo=${onSaved ? undefined : "#/settings"} large=${!!onSaved} actions=${actions}>
    ${intro && html`<p class="muted pad">${intro}</p>`}
    <${Group} header=${`${name} connection`} footer=${`Use the address the gateway container can reach: a Docker service name, LAN IP or *.ts.net host, including any URL base (e.g. https://host.ts.net/${service}). The API key is in ${name} → Settings → General. It is stored on the gateway, never in this browser.`}>
      <label class="frow"><input class="grow" type="url" inputmode="url" autocapitalize="off" spellcheck="false"
        placeholder=${service === "radarr" ? "http://radarr:7878" : "http://sonarr:8989"} value=${url} onInput=${(e) => setUrl(e.target.value)} /></label>
      <label class="frow"><input class="grow" type="password" autocomplete="off" autocapitalize="off" spellcheck="false"
        placeholder=${existing?.api_key_set ? "API key (saved, leave blank to keep)" : "API key"} value=${apiKey}
        onInput=${(e) => setApiKey(e.target.value)} /></label>
    <//>
    <${Group}>
      <button class="frow accent" disabled=${!url || busy} onClick=${() => run(false)}>
        <span class="row-label">Test Connection</span><span class="row-value">${busy && html`<${Spinner} small />`}</span></button>
      <button class="frow accent strong" disabled=${!url || busy || (!apiKey && !existing?.api_key_set)} onClick=${() => run(true)}>
        ${onSaved ? "Save and continue" : "Save"}</button>
    <//>
    ${result && (result.ok
      ? html`<${Notice} tone="good" icon="check-circle">Connected to ${result.t.app_name || name} ${result.t.version || ""} · ${result.t.quality_profiles || 0} quality profiles<//>`
      : html`<${Notice} tone="bad" icon="x-octagon">${result.message}<//>`)}
    ${!onSaved && existing?.configured && html`<${Group} footer=${`The app keeps working with the other server. Titles already in ${name} are not touched.`}>
      <button class="frow bad" onClick=${remove}>Remove ${name} connection</button><//>`}
  <//>`;
}

// ------------------------------------------------------------------ request defaults
export function DefaultsView() {
  useEffect(() => { S.refreshOptions(); }, []);
  return html`<${Page} title="Request defaults" backTo="#/settings" large=${false}><${DefaultsSection} /><//>`;
}

export function DefaultsSection() {
  const opts = state.options;
  if (!opts) return null;
  const d = opts.defaults;
  const save = (patch) => S.saveDefaults({ ...d, ...patch });
  const profile = (label, list, current, key) => {
    if (!list?.length) return null;
    if (list.length === 1) return html`<${Row} label=${label}>${list[0].name}<//>`;
    return html`<${SelectRow} label=${label} value=${current} options=${list.map((p) => [p.id, p.name])}
      onChange=${(v) => save({ [key]: v === null ? null : Number(v) })} />`;
  };
  const root = (label, list, current, key) => {
    if (!list?.length) return null;
    if (list.length === 1) return html`<${Row} label=${label}>${list[0].path}<//>`;
    return html`<${SelectRow} label=${label} value=${current} options=${list.map((r) => [r.path, r.path])}
      onChange=${(v) => save({ [key]: v })} />`;
  };
  return html`<${Group} header="Request defaults" footer="Every Add (the + on a poster, Add to Library, and Add selected) uses these. Customize Request changes them for one request or one batch only; these stay the same.">
    ${opts.radarr.error && state.config?.radarr?.configured && html`<div class="group-pad"><${Notice}>Radarr: ${opts.radarr.error.message}<//></div>`}
    ${profile("Radarr quality", opts.radarr.quality_profiles, d.radarr_profile_id, "radarr_profile_id")}
    ${root("Radarr folder", opts.radarr.root_folders, d.radarr_root, "radarr_root")}
    ${opts.sonarr.error && state.config?.sonarr?.configured && html`<div class="group-pad"><${Notice}>Sonarr: ${opts.sonarr.error.message}<//></div>`}
    ${profile("Sonarr quality", opts.sonarr.quality_profiles, d.sonarr_profile_id, "sonarr_profile_id")}
    ${root("Sonarr folder", opts.sonarr.root_folders, d.sonarr_root, "sonarr_root")}
    ${state.config?.sonarr?.configured && html`<${SelectRow} label="TV seasons" value=${d.season_monitor}
        options=${SEASON_MODES.map((m) => [m, seasonLabel(m)])} onChange=${(v) => save({ season_monitor: v || "all" })} />`}
  <//>`;
}

// ------------------------------------------------------------------ debug report
function DebugSection() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const build = async () => {
    setBusy(true);
    try {
      await downloadDebugReport();
      setError(null);
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return html`<${Group} header="Troubleshooting" footer="One zip with this browser's log and the gateway's logs and status. API keys and the access token are removed.">
    <button class="frow accent" disabled=${busy} onClick=${build}>
      <span class="row-label"><${Icon} name="bug" size=${18} /> Download debug report</span><span class="row-value">${busy && html`<${Spinner} small />`}</span></button>
    ${error && html`<div class="group-pad"><${Notice} tone="bad">${error}<//></div>`}
  <//>`;
}

/** How to put the app on the iPhone home screen, and what HTTPS adds. */
function InstallSection() {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  return html`<${Group} header="Home screen app" footer=${isSecureContext
      ? "Served over HTTPS, so the app shell and posters are cached for a fast start."
      : html`Works over plain HTTP. Serving it over HTTPS (for example with <code>tailscale serve</code>) also caches the app shell and posters for a faster start. See docs/DEPLOYMENT.md.`}>
    <div class="frow small">${standalone
      ? html`<span class="good"><${Icon} name="check-circle" size=${16} /></span> Running as a home screen app.`
      : html`<span>On iPhone: open this page in Safari, tap <${Icon} name="share" size=${15} /> Share, then <strong>Add to Home Screen</strong>. Sign in once inside the home screen app.</span>`}</div>
  <//>`;
}
