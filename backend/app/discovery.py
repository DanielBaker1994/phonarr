"""Filtering, sorting, facets and "More like this" over discovery cards.

Everything here works on the normalized metadata that Radarr/Sonarr lookups
return (see docs/METADATA.md for what is rich and what is thin). Cards come
from the discovery pool (see sources.py).
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Iterable

from .config import cfg

RUNTIME_BUCKETS = {          # minutes
    "short": (0, 59),        # shorts, serials, most TV episodes
    "brisk": (60, 89),
    "feature": (90, 119),
    "long": (120, 10_000),
}

SORTS = ("title", "trending", "year_desc", "year_asc", "rating", "popularity", "runtime_asc", "runtime_desc", "recent")


def decade(year: int | None) -> int | None:
    return (year // 10) * 10 if year else None


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def genre_neighbours(cards: Iterable[dict], min_overlap: float | None = None) -> dict[str, list[dict]]:
    """For each genre, the genres whose titles overlap with its titles (shared ÷ either, i.e. Jaccard) by at
    least `min_overlap`, strongest first. Worked out from the pool itself, so a broad genre such as Drama
    doesn't count as a neighbour of everything."""
    min_overlap = cfg("discovery.neighbours.min_overlap") if min_overlap is None else min_overlap
    titles: dict[str, set[int]] = {}
    for i, c in enumerate(cards):
        for g in c.get("genres") or []:
            titles.setdefault(g, set()).add(i)
    out: dict[str, list[dict]] = {}
    for a, ta in titles.items():
        near = []
        for b, tb in titles.items():
            if a != b and (both := len(ta & tb)):
                overlap = both / len(ta | tb)
                if overlap >= min_overlap:
                    near.append({"value": b, "overlap": round(overlap, 3)})
        if near:
            out[a] = sorted(near, key=lambda n: (-n["overlap"], n["value"]))
    return out


def neighbours_for(picks: Iterable[str], neighbour_map: dict[str, list[dict]], skip: Iterable[str] = ()) -> list[str]:
    """Neighbouring genres of the picked ones, minus the picks themselves and anything in `skip`."""
    picks, skip = set(picks), set(skip)
    best: dict[str, float] = {}
    for p in picks:
        for n in neighbour_map.get(p, []):
            if n["value"] not in picks and n["value"] not in skip:
                best[n["value"]] = max(best.get(n["value"], 0.0), n["overlap"])
    return [g for g, _ in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))]


def is_english(card: dict) -> bool:
    """English only keeps titles whose original language is English, and titles with no language at all
    (Radarr v3 and Sonarr v3 don't send one), so missing data never hides a title."""
    lang = (card.get("language") or "").casefold()
    return not lang or lang in {n.casefold() for n in cfg("discovery.english_names")}


def _truthy(value: Any) -> bool:
    return value is True or str(value).casefold() in ("1", "true", "yes", "on")


def apply_filters(cards: list[dict], f: dict[str, Any], neighbour_map: dict[str, list[dict]] | None = None) -> list[dict]:
    """f keys (all optional): q, genres (csv, any-of), exclude_genres (csv), neighbours (bool: also match the
    picked genres' neighbours), neighbours_off (csv of neighbours to leave out), english_only (bool),
    decade, decades (csv), year_min, year_max, runtime (bucket), runtime_min, runtime_max, rating_min,
    language, studio, collection, certification, status, source."""
    out = cards
    if f.get("q"):
        needle = f["q"].casefold()
        out = [c for c in out if needle in (c["title"] or "").casefold()
               or needle in (c.get("original_title") or "").casefold()]
    if _truthy(f.get("english_only")):
        out = [c for c in out if is_english(c)]
    excluded = set(_csv(f.get("exclude_genres")))
    if excluded:
        out = [c for c in out if not excluded & set(c["genres"])]
    genres = set(_csv(f.get("genres")))
    if genres:
        if _truthy(f.get("neighbours")):
            nmap = genre_neighbours(cards) if neighbour_map is None else neighbour_map
            genres |= set(neighbours_for(genres, nmap, excluded | set(_csv(f.get("neighbours_off")))))
        out = [c for c in out if genres & set(c["genres"])]
    if f.get("decade"):
        out = [c for c in out if c.get("decade") == int(f["decade"])]
    decades = {int(d) for d in _csv(f.get("decades"))}
    if decades:
        out = [c for c in out if c.get("decade") in decades]
    if f.get("year_min"):
        out = [c for c in out if (c.get("year") or 0) >= int(f["year_min"])]
    if f.get("year_max"):
        out = [c for c in out if (c.get("year") or 9999) <= int(f["year_max"])]
    if f.get("runtime") in RUNTIME_BUCKETS:
        lo, hi = RUNTIME_BUCKETS[f["runtime"]]
        out = [c for c in out if c.get("runtime") and lo <= c["runtime"] <= hi]
    if f.get("runtime_min"):
        out = [c for c in out if (c.get("runtime") or 0) >= int(f["runtime_min"])]
    if f.get("runtime_max"):
        out = [c for c in out if c.get("runtime") and c["runtime"] <= int(f["runtime_max"])]
    if f.get("rating_min"):
        out = [c for c in out if (c.get("rating") or 0) >= float(f["rating_min"])]
    for key, field in (("language", "language"), ("studio", "studio"), ("collection", "collection"),
                       ("certification", "certification"), ("status", "series_status"),
                       ("source", "source")):
        if f.get(key):
            out = [c for c in out if (c.get(field) or "").casefold() == f[key].casefold()]
    return out


def trusted_rating(sources: dict[str, dict], media_type: str) -> float | None:
    """One rating to sort and filter by. The configured sources (IMDb and TMDB for films) are averaged by vote
    count, then pulled towards `prior_mean` when there are few votes:
    (votes x average + prior_votes x prior_mean) / (votes + prior_votes).
    Sources with a value but no vote count are averaged plainly. Falls back to the other sources in order."""
    conf = cfg("discovery.trusted_rating")
    for group in (conf["sources"], conf["fallback_sources"]):
        found = [(float(sources[s]["value"]), int(sources[s].get("votes") or 0))
                 for s in group if (sources.get(s) or {}).get("value")]
        if not found:
            continue
        votes = sum(v for _, v in found)
        if not votes:
            return round(sum(x for x, _ in found) / len(found), 1)
        average = sum(x * v for x, v in found) / votes
        prior = conf["prior_votes"][media_type]
        return round((votes * average + prior * conf["prior_mean"]) / (votes + prior), 1)
    return None


def trending_score(card: dict, this_year: int | None = None) -> float | None:
    """New and trending: titles from the last `discovery.trending_years` years, scored by TMDB popularity
    (films) or total votes across sources (TV has no popularity). Older or unscored titles get None."""
    this_year = this_year or date.today().year
    if (card.get("year") or 0) <= this_year - cfg("discovery.trending_years"):
        return None
    if card.get("popularity") is not None:
        return float(card["popularity"])
    votes = sum((card.get("votes") or {}).values())
    return float(votes) if votes else None


def sort_cards(cards: list[dict], sort: str | None, this_year: int | None = None) -> list[dict]:
    title = lambda c: (c["title"] or "").casefold()  # noqa: E731
    if sort == "trending":   # new and trending first, everything else after by title
        scored = [(trending_score(c, this_year), c) for c in cards]
        return [c for _, c in sorted(scored, key=lambda sc: (sc[0] is None, -(sc[0] or 0), title(sc[1])))]
    if sort == "year_desc":   # newest year first, highest rated first within a year, unrated last
        return sorted(cards, key=lambda c: (-(c.get("year") or 0), c.get("rating") is None, -(c.get("rating") or 0),
                                            title(c)))
    if sort == "year_asc":
        return sorted(cards, key=lambda c: (c.get("year") or 9999, title(c)))
    if sort == "rating":   # unrated titles last
        return sorted(cards, key=lambda c: (c.get("rating") is None, -(c.get("rating") or 0), title(c)))
    if sort == "popularity":
        return sorted(cards, key=lambda c: (c.get("popularity") is None, -(c.get("popularity") or 0), title(c)))
    if sort == "runtime_asc":
        return sorted(cards, key=lambda c: (c.get("runtime") is None, c.get("runtime") or 0, title(c)))
    if sort == "runtime_desc":
        return sorted(cards, key=lambda c: (c.get("runtime") is None, -(c.get("runtime") or 0), title(c)))
    if sort == "recent":   # newest additions to the pool / library first
        return sorted(cards, key=lambda c: (c.get("added") or ""), reverse=True)
    return sorted(cards, key=title)


def facets(cards: Iterable[dict]) -> dict:
    """Value counts the UI can turn into filter chips. Facets with < 2 values are useless and omitted."""
    cards = list(cards)

    def count(values: Iterable[Any]) -> list[dict]:
        c = Counter(v for v in values if v not in (None, ""))
        return [{"value": str(k), "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))]

    out = {
        "genres": count(g for c in cards for g in c["genres"]),
        "decades": sorted(count(c.get("decade") for c in cards), key=lambda d: d["value"]),
        "runtime": [{"value": b, "count": n} for b in RUNTIME_BUCKETS
                    if (n := sum(1 for c in cards if c.get("runtime")
                                 and RUNTIME_BUCKETS[b][0] <= c["runtime"] <= RUNTIME_BUCKETS[b][1]))],
        "languages": count(c.get("language") for c in cards),
        "studios": count(c.get("studio") for c in cards),
        "collections": count(c.get("collection") for c in cards),
        "certifications": count(c.get("certification") for c in cards),
        "sources": count(c.get("source") for c in cards),
        "statuses": count(c.get("series_status") for c in cards),
    }
    out = {k: v for k, v in out.items() if len(v) >= 2 or k in ("genres", "decades")}
    out["rated"] = sum(1 for c in cards if c.get("rating") is not None)
    out["genre_neighbours"] = genre_neighbours(cards)
    years = [c["year"] for c in cards if c.get("year")]
    out["years"] = {"min": min(years), "max": max(years)} if years else None
    out["non_english"] = sum(1 for c in cards if not is_english(c))
    return out


# ------------------------------------------------------------------ similarity
def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = {x.casefold() for x in a}, {x.casefold() for x in b}
    return len(a & b) / len(a | b) if a and b else 0.0


def similarity(seed: dict, other: dict) -> tuple[float, list[str]]:
    """Weighted score over available metadata, plus human-readable reasons.

    Weights reflect how telling each signal is for older catalogues:
    genre overlap and shared collection/studio say most; era matters a lot for
    old films; rating/runtime/language are tie-breakers.
    """
    score, reasons = 0.0, []
    g = _jaccard(seed["genres"], other["genres"])
    if g:
        score += 4.0 * g
        shared = [x for x in other["genres"] if x.casefold() in {s.casefold() for s in seed["genres"]}]
        reasons.append(" · ".join(shared[:2]))
    k = _jaccard(seed.get("keywords") or [], other.get("keywords") or [])
    if k:
        score += 3.0 * k
        reasons.append("Similar themes")
    if seed.get("collection") and seed.get("collection") == other.get("collection"):
        score += 3.0
        reasons.append(f"Part of {other['collection']}")
    if seed.get("studio") and (seed.get("studio") or "").casefold() == (other.get("studio") or "").casefold():
        score += 1.5
        reasons.append(f"Also from {other['studio']}")
    if seed.get("year") and other.get("year"):
        gap = abs(seed["year"] - other["year"])
        score += 2.0 * max(0.0, 1 - gap / 25)
        if gap <= 5:
            reasons.append(f"Same era ({other['decade']}s)")
    if seed.get("language") and seed.get("language") == other.get("language"):
        score += 0.5
    if seed.get("rating") is not None and other.get("rating") is not None:
        score += 0.5 * max(0.0, 1 - abs(seed["rating"] - other["rating"]) / 5)
    if seed.get("runtime") and other.get("runtime"):
        score += 0.5 * max(0.0, 1 - abs(seed["runtime"] - other["runtime"]) / 60)
    if seed["type"] != other["type"]:
        score -= 0.75  # mixed results lean towards the same kind of media
        reasons.append("TV" if other["type"] == "series" else "Film")
    return round(score, 3), [r for r in reasons if r][:3]


def more_like_this(seed: dict, candidates: list[dict], limit: int = 20, min_score: float = 1.0) -> list[dict]:
    ranked = []
    for c in candidates:
        if c["type"] == seed["type"] and c["id"] == seed["id"]:
            continue
        score, reasons = similarity(seed, c)
        if score >= min_score:
            ranked.append({**c, "score": score, "reasons": reasons})
    ranked.sort(key=lambda c: (-c["score"], (c["title"] or "").casefold()))
    return ranked[:limit]
