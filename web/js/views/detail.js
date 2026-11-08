// Title details and the Customize Request sheet (mirrors ios/Phonarr/Views/TitleDetailView.swift).
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { Icon } from "../icons.js";
import { api } from "../api.js";
import { log } from "../log.js";
import { planSummary, ratingSource, ratingText, SEASON_MODES, seasonLabel, capitalize, isActive, STATE_ICON, stateLabel, MEDIA_TYPES } from "../models.js";
import * as S from "../store.js";
import { state } from "../store.js";
import { go, seedFor } from "../router.js";
import { Group, Notice, Page, PosterCard, PosterCell, Row, SelectRow, Sheet, Spinner, StatusLine } from "./components.js";

export function TitleDetailView({ type, id }) {
  const seed = seedFor(type, id);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const status = S.statusOf(type, id, detail?.status || seed?.status);

  const load = async () => {
    try { setDetail(await api.detail(type, id)); setError(null); } catch (e) { setError(e.message); }
  };
  useEffect(() => { setDetail(null); load(); }, [type, id]);
  useEffect(() => { S.beginWatching(); return S.endWatching; }, []);
  // Reload when the state changes (e.g. Downloading → In library) to pick up new episode details.
  useEffect(() => { if (detail) load(); }, [status.state]);

  const d = detail || {};
  const title = d.title || seed?.title || "";
  const card = seed || { type, id, title: d.title, year: d.year, genres: d.genres || [], poster: d.poster, overview: d.overview, status };
  // The app-wide plan is refreshed when defaults change, so it wins over the one loaded with the details.
  const plan = state.requestPlans[type] || d.request_plan;
  const customize = () => S.customize([card]);
  const overview = d.overview || seed?.overview;
  const backdrop = d.fanart || d.poster || seed?.poster;

  return html`<${Page} title=${title} backTo="" large=${false} cls="detail">
    ${backdrop && html`<div class="backdrop" style=${`background-image:url("${backdrop.replace(/"/g, "%22")}")`}></div>`}
    <div class="detail-body">
      <div class="detail-head">
        <${PosterCard} card=${{ ...card, poster: d.poster || seed?.poster }} status=${status} />
        <div class="detail-meta">
          <h2>${title}</h2>
          <div class="muted meta-line">
            ${(d.year || seed?.year) && html`<span>${d.year || seed?.year}</span>`}
            ${d.runtime > 0 && html`<span>${d.runtime} min</span>`}
            ${d.certification && html`<span class="cert">${d.certification}</span>`}
          </div>
          <${StatusLine} status=${status} />
        </div>
      </div>

      <div class="actions">
        ${status.state === "not_requested" && html`
          <button class="btn primary large" disabled=${S.isServiceDown(type)}
            onClick=${() => (plan?.problem ? customize() : S.add(type, id))}><${Icon} name="plus" /> Add to Library</button>
          <${PlanSummary} plan=${plan} change=${customize} />`}
        ${status.state === "submitting" && html`<button class="btn primary large" disabled><${Spinner} small /></button>`}
        ${status.state === "failed" && html`<${FailureBox} status=${status} retry=${() => S.retry(type, id)} />`}
      </div>

      ${overview && html`<p class="overview">${overview}</p>`}
      <${Metadata} type=${type} d=${d} seed=${seed} />
      ${type === "series" && d.episodes?.length > 0 && html`<${EpisodeList} episodes=${d.episodes} />`}
      <${MoreLikeThis} type=${type} id=${id} />
      ${error && html`<${Notice}>${error}<//>`}
    </div>
  <//>`;
}

function Metadata({ type, d, seed }) {
  const genres = d.genres || seed?.genres || [];
  const ratings = Object.entries(d.ratings || {}).sort(([a], [b]) => a.localeCompare(b));
  const rows = [
    genres.length && ["Genres", genres.join(", ")],
    d.studio && [type === "movie" ? "Studio" : "Network", d.studio],
    d.season_count > 0 && ["Seasons", String(d.season_count)],
    d.series_status && ["Status", capitalize(d.series_status)],
    d.language && ["Language", d.language],
    d.collection && ["Collection", d.collection],
    ratings.length && ["Ratings", ratings.map(([k, v]) => `${ratingSource(k)} ${ratingText(k, v)}`).join(" · ")],
    d.keywords?.length && ["Themes", d.keywords.slice(0, 6).join(", ")],
  ].filter(Boolean);
  return html`<dl class="meta">${rows.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>`;
}

/** "Uses HD-1080p · /movies · All seasons   Change" under Add to Library, so you know what a click will do. */
function PlanSummary({ plan, change }) {
  if (!plan) return null;
  if (plan.problem) {
    return html`<div class="plan">
      <${Notice}>${plan.problem.message}<//>
      <div class="plan-links"><button class="link strong" onClick=${change}>Choose for this one</button>
        <a class="link strong" href="#/settings/defaults" onClick=${(e) => { e.preventDefault(); go("#/settings/defaults"); }}>Set defaults</a></div>
    </div>`;
  }
  return html`<div class="plan">
    <div class="plan-line"><${Icon} name="sliders" size=${16} cls="muted" />
      <span><span class="muted">Uses </span>${planSummary(plan)}</span>
      <button class="link strong" onClick=${change}>Change</button></div>
    <div class="tiny muted">Change applies to this request only. Your defaults are in Settings.</div>
  </div>`;
}

function FailureBox({ status, retry }) {
  const config = status.error_kind === "config";
  return html`<div class="failure">
    <h3><${Icon} name=${config ? "gear" : "warning"} /> ${config ? "Needs attention in Settings" : "Download failed"}</h3>
    ${status.message && html`<p>${status.message}</p>`}
    <div class="row-buttons">
      <button class="btn danger" onClick=${retry}><${Icon} name="retry" size=${16} /> Retry</button>
      ${config && html`<a class="link" href="#/settings/defaults" onClick=${(e) => { e.preventDefault(); go("#/settings/defaults"); }}>Open request defaults</a>`}
    </div>
  </div>`;
}

function EpisodeList({ episodes }) {
  const seasons = [...new Set(episodes.map((e) => e.season))].sort((a, b) => a - b);
  return html`<section>
    <h3 class="section-title">Episodes</h3>
    ${seasons.map((season) => {
      const eps = episodes.filter((e) => e.season === season);
      const done = eps.filter((e) => e.state === "complete").length;
      return html`<details class="season">
        <summary><span>${season === 0 ? "Specials" : `Season ${season}`}</span><span class="muted">${done}/${eps.length}</span></summary>
        ${eps.map((ep) => html`<div class="episode">
          <span class="num muted ep-no">${ep.episode}</span>
          <div class="grow">
            <div class="clamp1">${ep.title || `Episode ${ep.episode}`}</div>
            ${ep.progress != null && isActive(ep.state) && html`<div class="bar"><i style=${`width:${Math.round(ep.progress * 100)}%`}></i></div>`}
            ${ep.state === "failed" && ep.message && html`<div class="small bad clamp2">${ep.message}</div>`}
          </div>
          <span class=${ep.state === "failed" ? "bad" : ep.state === "complete" ? "good" : "muted"} title=${stateLabel(ep.state)}>
            <${Icon} name=${STATE_ICON[ep.state] || "question"} size=${18} /></span>
        </div>`)}
      </details>`;
    })}
  </section>`;
}

/** "More like this": similar titles from the discovery pool, with a reason under each. */
function MoreLikeThis({ type, id }) {
  const [items, setItems] = useState([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    setLoaded(false);
    api.similar(type, id, true, 12)
      .then((r) => { if (live) setItems(r.items); })
      .catch((e) => log.warn("similar_failed", { type, id, error: e.message }))
      .finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [type, id]);
  return html`<section class="row flush">
    <div class="row-head"><h2>More like this</h2>
      ${items.length > 3 && html`<a class="link" href=${`#/similar/${type}/${id}`} onClick=${(e) => { e.preventDefault(); go(`#/similar/${type}/${id}`); }}>See all</a>`}</div>
    ${items.length === 0
      ? html`<p class="muted">${loaded ? "Nothing similar found yet. This fills in as Radarr recommends more titles or you add to your titles list." : "Finding similar titles…"}</p>`
      : html`<div class="scroller">${items.map((c) => html`<${PosterCell} key=${`${c.type}:${c.id}`} card=${c} size=${104} caption=${c.reasons?.[0]} />`)}</div>`}
  </section>`;
}

// ------------------------------------------------------------------ Customize Request
/** One-off choices for one title or a multi-select batch. Never changes the saved defaults. */
export function CustomizeSheet() {
  const target = state.customizing;
  const [choices, setChoices] = useState({});
  const cards = target?.cards || [];
  const types = MEDIA_TYPES.filter((t) => cards.some((c) => c.type === t));
  const single = cards.length === 1 ? cards[0] : null;
  const opts = state.options;

  useEffect(() => { if (target && !state.options) S.refreshOptions(); }, [target]);
  // Pre-fill after options load, so the pickers start on real values.
  useEffect(() => {
    if (!target) return;
    const next = {};
    for (const type of types) {
      const plan = state.requestPlans[type];
      const dflt = opts?.defaults || {};
      const o = opts?.[type === "movie" ? "radarr" : "sonarr"] || {};
      next[type] = {
        quality_profile_id: plan?.quality_profile_id ?? (type === "movie" ? dflt.radarr_profile_id : dflt.sonarr_profile_id) ?? o.quality_profiles?.[0]?.id ?? null,
        root_folder: plan?.root_folder ?? (type === "movie" ? dflt.radarr_root : dflt.sonarr_root) ?? o.root_folders?.[0]?.path ?? null,
        ...(type === "series" ? { season_monitor: plan?.season_monitor ?? dflt.season_monitor ?? "all" } : {}),
      };
    }
    setChoices(next);
  }, [target, opts]);

  if (!target) return null;
  const close = () => { state.customizing = null; S.emit(); };
  const ready = types.every((t) => choices[t]?.quality_profile_id != null && choices[t]?.root_folder != null);
  const set = (type, patch) => setChoices({ ...choices, [type]: { ...choices[type], ...patch } });

  const send = async () => {
    const { onSent } = target;
    close();
    if (single) await S.add(single.type, single.id, choices[single.type]);
    else await S.addBatch(cards, choices.movie || null, choices.series || null);
    onSent?.();
  };

  const right = html`<button class="link strong" disabled=${!ready} onClick=${send}>${single ? "Request" : `Add ${cards.length}`}</button>`;
  return html`<${Sheet} title=${single ? single.title || "Request" : `Add ${cards.length} titles`} onClose=${close} right=${right}>
    ${!opts && html`<div class="center pad"><${Spinner} /></div>`}
    ${opts && types.map((type) => {
      const o = opts[type === "movie" ? "radarr" : "sonarr"] || {};
      const n = cards.filter((c) => c.type === type).length;
      const profiles = o.quality_profiles || [], roots = o.root_folders || [];
      return html`<${Group} key=${type} header=${single ? null : type === "movie" ? `${n} film${n === 1 ? "" : "s"}` : `${n} show${n === 1 ? "" : "s"}`}>
        ${profiles.length === 1 ? html`<${Row} label="Quality profile">${profiles[0].name}<//>`
          : html`<${SelectRow} label="Quality profile" value=${choices[type]?.quality_profile_id}
              options=${profiles.map((p) => [p.id, p.name])} onChange=${(v) => set(type, { quality_profile_id: v === null ? null : Number(v) })} />`}
        ${roots.length === 1 ? html`<${Row} label="Root folder">${roots[0].path}<//>`
          : html`<${SelectRow} label="Root folder" value=${choices[type]?.root_folder}
              options=${roots.map((r) => [r.path, r.path])} onChange=${(v) => set(type, { root_folder: v })} />`}
        ${type === "series" && html`<${SelectRow} label="Seasons" value=${choices[type]?.season_monitor}
            options=${SEASON_MODES.map((m) => [m, seasonLabel(m)])} onChange=${(v) => set(type, { season_monitor: v })} />`}
        ${o.error && html`<div class="group-pad"><${Notice}>${o.error.message}<//></div>`}
      <//>`;
    })}
    <${Group} footer=${single ? "Applies to this request only. Your saved defaults stay the same."
        : `Applies to these ${cards.length} titles only. Titles already in your library are skipped, failed ones are retried. Your saved defaults stay the same.`}>
      <${Row} label="Change my defaults instead" onClick=${() => { close(); go("#/settings/defaults"); }} chevron />
    <//>
  <//>`;
}

