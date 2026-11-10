// Settings → Logs: the gateway's last log lines and this browser's own, merged by time, newest at the bottom
// (mirrors ios/Phonarr/Views/LogsView.swift). Filters by source, level, text and request id; Copy and Download.
import { html, useEffect, useLayoutEffect, useRef, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { api } from "../api.js";
import { cfg, text } from "../config.js";
import { log } from "../log.js";
import { clockSkew, filterEntries, formatEntries, mergeEntries, shortTime } from "../logview.js";
import * as S from "../store.js";
import { Notice, Page, Spinner } from "./components.js";

const SOURCES = [["both", "logs.source_both"], ["gateway", "logs.source_gateway"], ["web", "logs.source_device"]];
const LEVELS = [["", "logs.level_all"], ["WARNING", "logs.level_warn"]];
const deviceZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "?"; } catch { return "?"; } };

/** Plain HTTP has no navigator.clipboard (and it can refuse even over HTTPS), so fall back to a hidden
 *  textarea and execCommand. */
async function copyText(value) {
  if (navigator.clipboard && isSecureContext) {
    try { return await navigator.clipboard.writeText(value); } catch { /* fall through to execCommand */ }
  }
  const ta = Object.assign(document.createElement("textarea"), { value });
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.append(ta);
  ta.select();
  ta.setSelectionRange(0, value.length);   // iOS Safari ignores select() alone
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) throw new Error("execCommand");
}

function downloadText(value) {
  const stamp = new Date().toISOString().slice(0, 19).replace(/:/g, "");
  const blob = new Blob([value + "\n"], { type: "text/plain" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: text("logs.file_name", { stamp }) });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function clockText(skew) {
  if (skew == null) return text("logs.clock_unknown");
  if (skew === 0) return text("logs.clock_same");
  return text(skew > 0 ? "logs.clock_ahead" : "logs.clock_behind", { seconds: Math.abs(skew) });
}

function Segmented({ options, value, onChange, label }) {
  return html`<div class="chips" role="group" aria-label=${label}>
    ${options.map(([v, key]) => html`<button class=${`chip ${value === v ? "on" : ""}`} aria-pressed=${value === v}
      onClick=${() => onChange(v)}>${text(key)}</button>`)}
  </div>`;
}

function LogLine({ e, onRid }) {
  return html`<div class=${`logline lvl-${(e.level || "none").toLowerCase()}`}>
    <span class="ll-time">${shortTime(e.ts)}</span>
    <span class=${`ll-src ${e.source}`}>${text(`logs.tag_${e.source}`)}</span>
    <span class="ll-level">${e.level}</span>
    ${e.rid !== "-" && html`<button class="ll-rid" onClick=${() => onRid(e.rid)}>${e.rid}</button>`}
    <span class="ll-where">${e.where}</span>
    <span class="ll-msg">${e.message}</span>
  </div>`;
}

export function LogsView() {
  const [source, setSource] = useState("both");
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [rid, setRid] = useState("");
  const [data, setData] = useState(null);       // GET /v1/debug/logs answer + clock difference
  const [device, setDevice] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const box = useRef(null);
  const lines = cfg("logs.lines", 1000);

  const load = async () => {
    setBusy(true);
    const mine = log.entries(lines);
    try {
      const answer = await api.debugLogs({ lines, level, rid });
      const skew = clockSkew({ sentAt: answer.sentAt, receivedAt: answer.receivedAt, gatewayNow: answer.gateway?.now, dateHeader: answer.dateHeader });
      log.info("logs_loaded", { gateway: answer.lines.length, device: mine.length, skew });
      if (skew != null && Math.abs(skew) > cfg("logs.clock_warn_seconds", 120)) {
        log.warn("logs_clock", { skew, device_tz: deviceZone(), gateway_tz: answer.gateway?.timezone });
      }
      setData({ ...answer, skew });
      setError(null);
    } catch (e) {
      log.warn("logs_failed", { error: e.message });
      setError(e.message);
    }
    setDevice(log.entries(lines));   // read again so this load's own lines are in
    setBusy(false);
  };
  useEffect(() => { load(); }, [level, rid]);

  const all = mergeEntries(data?.lines || [], device, source);
  const shown = filterEntries(all, { level, q, rid });
  // Newest at the bottom: follow the end whenever the list changes.
  useLayoutEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [shown.length, data, source]);

  const copy = async () => {
    try { await copyText(formatEntries(shown)); S.toast(text("logs.copied")); } catch { S.toast(text("logs.copy_failed")); }
  };
  const gw = data?.gateway;
  const warnClock = data?.skew != null && Math.abs(data.skew) > cfg("logs.clock_warn_seconds", 120);
  const actions = html`
    <button class="icon-btn" aria-label=${text("logs.copy")} title=${text("logs.copy")} disabled=${!shown.length} onClick=${copy}>
      <${Icon} name="copy" /></button>
    <button class="icon-btn" aria-label=${text("logs.download")} title=${text("logs.download")} disabled=${!shown.length}
      onClick=${() => downloadText(formatEntries(shown))}><${Icon} name="download" /></button>
    <button class="icon-btn" aria-label=${text("logs.refresh")} title=${text("logs.refresh")} onClick=${load}>
      ${busy ? html`<${Spinner} small />` : html`<${Icon} name="retry" />`}</button>`;

  return html`<${Page} title=${text("logs.title")} backTo="#/settings" large=${false} actions=${actions}>
    <div class="logs-meta small">
      <div><span class="muted">${text("logs.gateway_label")}</span> ${gw?.version || "…"} · ${gw?.timezone || "…"}</div>
      <div><span class="muted">${text("logs.device_label")}</span> ${deviceZone()} · <span class="muted">${text("logs.clock")}</span>
        <span class=${warnClock ? "bad" : ""}> ${data ? clockText(data.skew) : "…"}</span></div>
    </div>
    ${warnClock && html`<${Notice} tone="bad" icon="clock">${text("logs.clock_warn", { seconds: Math.abs(data.skew) })}<//>`}
    ${error && html`<${Notice} tone="bad">${text("logs.gateway_failed", { error })}<//>`}
    <div class="logs-controls">
      <div class="logs-chips">
        <${Segmented} label=${text("logs.source_label")} options=${SOURCES} value=${source} onChange=${setSource} />
        <${Segmented} label=${text("logs.level_label")} options=${LEVELS} value=${level} onChange=${setLevel} />
      </div>
      <label class="searchbar logs-search"><${Icon} name="search" size=${16} />
        <input type="search" placeholder=${text("logs.search")} value=${q} onInput=${(e) => setQ(e.target.value)}
          autocapitalize="off" autocorrect="off" spellcheck="false" /></label>
      <div class="logs-status small muted">
        ${rid && html`<button class="chip small on" onClick=${() => setRid("")}>${text("logs.rid_filter", { rid })} <${Icon} name="x" size=${12} /></button>`}
        <span>${text("logs.count", { shown: shown.length, total: all.length })}${data?.truncated && source !== "web" ? ` · ${text("logs.truncated")}` : ""}</span>
      </div>
    </div>
    <div class="logview" ref=${box} role="log" aria-live="off">
      ${shown.length ? shown.map((e, i) => html`<${LogLine} key=${i} e=${e} onRid=${setRid} />`)
        : html`<p class="muted">${busy ? html`<${Spinner} small />` : text("logs.empty")}</p>`}
    </div>
    <p class="small muted pad">${text("logs.note")}</p>
  <//>`;
}
