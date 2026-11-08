// "More like this" → See all (mirrors ios/Phonarr/Views/SimilarViews.swift).
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { api } from "../api.js";
import { seedFor } from "../router.js";
import { Empty, Notice, Page, PosterCell, Spinner, Toggle, Group } from "./components.js";

export function SimilarListView({ type, id }) {
  const [mix, setMix] = useState(true);
  const [r, setR] = useState({ items: [], candidates: 0, loading: true, error: null });
  const title = seedFor(type, id)?.title;

  useEffect(() => {
    let live = true;
    setR((x) => ({ ...x, loading: true }));
    api.similar(type, id, mix, 60)
      .then((res) => live && setR({ items: res.items, candidates: res.candidates, loading: false, error: null }))
      .catch((e) => live && setR((x) => ({ ...x, loading: false, error: e.message })));
    return () => { live = false; };
  }, [type, id, mix]);

  return html`<${Page} title=${title ? `Like ${title}` : "More like this"} backTo="" large=${false}>
    <${Group}><${Toggle} label=${type === "movie" ? "Include TV shows" : "Include films"} checked=${mix} onChange=${setMix} /><//>
    ${r.error && html`<${Notice}>${r.error}<//>`}
    ${r.loading && !r.items.length && html`<div class="center pad"><${Spinner} /></div>`}
    <div class="grid">${r.items.map((c) => html`<${PosterCell} key=${`${c.type}:${c.id}`} card=${c} caption=${c.reasons?.join(" · ")} />`)}</div>
    ${!r.loading && !r.items.length && html`<${Empty} icon="sparkles" title="Nothing similar yet">
      Compared against ${r.candidates} titles. More titles in Radarr Discover or your titles list give better matches.<//>`}
  <//>`;
}
