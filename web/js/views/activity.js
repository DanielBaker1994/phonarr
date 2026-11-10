// Activity tab (mirrors ios/Phonarr/Views/ActivityView.swift): Downloads | Calendar, last choice remembered.
// While it's open the gateway polls every 5 s when something is downloading (viewing=activity).
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { text } from "../config.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { openTitle } from "../router.js";
import { Empty, Page, Poster, ServiceBanner, Spinner, StatusLine } from "./components.js";
import { CalendarView } from "./calendar.js";

const SEGMENTS = ["downloads", "calendar"];
const SEGMENT_LABEL = { downloads: "calendar.downloads", calendar: "calendar.title" };
const SEGMENT_KEY = "activitySegment";

export function ActivityView() {
  const [segment, setSegmentState] = useState(() => {
    const saved = S.pref.get(SEGMENT_KEY, "downloads");
    return SEGMENTS.includes(saved) ? saved : "downloads";
  });
  const setSegment = (s) => { setSegmentState(s); S.pref.set(SEGMENT_KEY, s); };
  useEffect(() => {
    S.loadActivity();
    S.beginWatching();
    return S.endWatching;
  }, []);
  const refresh = segment === "downloads"
    && html`<button class="icon-btn" aria-label="Refresh" onClick=${S.loadActivity}><${Icon} name="retry" /></button>`;

  return html`<${Page} title="Activity" actions=${refresh}>
    <div class="segmented" role="tablist" aria-label="Activity">
      ${SEGMENTS.map((s) => html`<button role="tab" aria-selected=${segment === s} class=${segment === s ? "on" : ""}
          onClick=${() => setSegment(s)}>${text(SEGMENT_LABEL[s])}</button>`)}
    </div>
    ${segment === "calendar" ? html`<${CalendarView} />` : html`<${Downloads} />`}
  <//>`;
}

function Downloads() {
  const live = (item) => S.statusOf(item.type, item.id, item.status);
  const failed = state.activity.filter((a) => live(a).state === "failed");
  const active = state.activity.filter((a) => !["failed", "complete"].includes(live(a).state));
  const done = state.activity.filter((a) => live(a).state === "complete");
  const section = (title, items) => items.length > 0 && html`<div class="group">
    <div class="group-header">${title}</div>
    <div class="group-body">${items.map((a) => html`<${ActivityRow} key=${`${a.type}:${a.id}`} item=${a} status=${live(a)} />`)}</div>
  </div>`;
  return html`
    <${ServiceBanner} />
    ${!state.activityLoaded && html`<div class="center pad"><${Spinner} /></div>`}
    ${section("Needs attention", failed)}
    ${section("In progress", active)}
    ${section("Recently added", done)}
    ${state.activityLoaded && !state.activity.length && html`<${Empty} icon="activity" title="No activity">Requests and downloads show up here.<//>`}`;
}

function ActivityRow({ item, status }) {
  return html`<div class="activity-row" role="link" tabindex="0" onClick=${() => openTitle(item)}
      onKeyDown=${(e) => { if (e.key === "Enter") openTitle(item); }}>
    <div class="thumb"><${Poster} src=${item.poster} title="" /></div>
    <div class="grow">
      <div class="activity-title"><strong class="clamp1">${item.title || "Untitled"}</strong>${item.year && html`<span class="muted">${item.year}</span>`}</div>
      <${StatusLine} status=${status} />
      ${status.state === "failed" && html`
        ${status.message && html`<div class="small bad clamp3">${status.message}</div>`}
        <button class="btn small danger" onClick=${(e) => { e.stopPropagation(); S.retry(item.type, item.id); }}>
          <${Icon} name="retry" size=${14} /> Retry</button>`}
    </div>
    <${Icon} name="chevron" size=${16} cls="muted" />
  </div>`;
}
