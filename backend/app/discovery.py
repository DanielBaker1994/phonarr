"""Filtering, sorting, facets and "More like this" over discovery cards.

Everything here works on the normalized metadata that Radarr/Sonarr lookups
return (see docs/METADATA.md for what is rich and what is thin). Cards come
from the discovery pool (see sources.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

RUNTIME_BUCKETS = {          # minutes
    "short": (0, 59),        # shorts, serials, most TV episodes
    "brisk": (60, 89),
    "feature": (90, 119),
    "long": (120, 10_000),
}

SORTS = ("title", "year_desc", "year_asc", "rating", "popularity", "runtime_asc", "runtime_desc", "recent")


def decade(year: int | None) -> int | None:
    return (year // 10) * 10 if year else None


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def apply_filters(cards: list[dict], f: dict[str, Any]) -> list[dict]:
    """f keys (all optional): q, genres (csv, any-of), decade, year_min, year_max, runtime (bucket),
    rating_min, language, studio, collection, certification, status, source."""
    out = cards
    if f.get("q"):
        needle = f["q"].casefold()
        out = [c for c in out if needle in (c["title"] or "").casefold()
               or needle in (c.get("original_title") or "").casefold()]
    genres = set(_csv(f.get("genres")))
    if genres:
        out = [c for c in out if genres & set(c["genres"])]
    if f.get("decade"):
        out = [c for c in out if c.get("decade") == int(f["decade"])]
    if f.get("year_min"):
        out = [c for c in out if (c.get("year") or 0) >= int(f["year_min"])]
    if f.get("year_max"):
        out = [c for c in out if (c.get("year") or 9999) <= int(f["year_max"])]
    if f.get("runtime") in RUNTIME_BUCKETS:
        lo, hi = RUNTIME_BUCKETS[f["runtime"]]
        out = [c for c in out if c.get("runtime") and lo <= c["runtime"] <= hi]
    if f.get("rating_min"):
        out = [c for c in out if (c.get("rating") or 0) >= float(f["rating_min"])]
    for key, field in (("language", "language"), ("studio", "studio"), ("collection", "collection"),
                       ("certification", "certification"), ("status", "series_status"),
                       ("source", "source")):
        if f.get(key):
            out = [c for c in out if (c.get(field) or "").casefold() == f[key].casefold()]
    return out


def sort_cards(cards: list[dict], sort: str | None) -> list[dict]:
    title = lambda c: (c["title"] or "").casefold()  # noqa: E731
    if sort == "year_desc":
        return sorted(cards, key=lambda c: (-(c.get("year") or 0), title(c)))
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
