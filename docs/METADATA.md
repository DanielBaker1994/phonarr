# What metadata Radarr and Sonarr give us

Phonarr gets all title metadata from Radarr's and Sonarr's own **lookup** endpoints. These proxy Servarr's metadata service: TMDB for Radarr, and TVDB (plus TMDB/IMDb IDs on v4) for Sonarr. No extra API key is needed. This file lists what those endpoints return, how reliable each field is, and what the app uses it for. Verify field names against your installed versions (`GET /api/v3/movie/lookup/tmdb?tmdbId=…`, `GET /api/v3/series/lookup?term=tvdb:…`). Every field is treated as optional in code.

Richness: ●●● present for nearly every title · ●●○ usually present, sometimes thin for obscure or very old titles · ●○○ often missing · ○○○ not exposed.

## Films (Radarr v4/v5, `MovieResource`)

| Field | Richness | Notes | Used for |
|---|---|---|---|
| `title`, `originalTitle`, `alternateTitles[]` | ●●● | Original-language titles help with silent and foreign films | Search |
| `year` (+ `inCinemas`, `physicalRelease`, `digitalRelease` dates) | ●●● / ●○○ | Release dates are often empty for pre-1950 films | Decade filter, era similarity |
| `overview` | ●●○ | Short synopsis. Sometimes empty for obscure shorts | Detail screen |
| `genres[]` | ●●○ | TMDB genres (Horror, Comedy, Crime…). Usually 1–3 per film | Genre filter, the main similarity signal |
| `runtime` (minutes) | ●●○ | 0 or missing for some silents | Runtime filter, tie-breaker |
| `certification` | ●○○ | US rating (G/PG/…). **Missing for most pre-1968 films**, since the MPAA system started in 1968 | Filter shown only when it has 2+ values |
| `studio` | ●●○ | Production company name | Studio filter, similarity |
| `originalLanguage {id,name}` | ●●● | v4+ | Language filter, similarity |
| `ratings {imdb, tmdb, metacritic, rottenTomatoes, trakt}` each `{value, votes}` | ●●○ / ●○○ | IMDb and TMDB usually present. RT and Metacritic rarer. Radarr v3 has a single `{value, votes}` | Trusted rating (IMDb + TMDB by votes), rating strip on posters, rating filter and sort |
| `popularity` | ●●○ | TMDB popularity score: recency-biased and noisy | "Popular" sort only |
| `collection {title, tmdbId}` | ●○○ | Franchises or series, e.g. "Nosferatu Collection". Rare for older titles | "Part of …" similarity, a strong signal when present |
| `keywords[]` | ●○○ | Present on recent Radarr builds only. Themes like "zombie", "film noir" | Similarity when present ("Similar themes") |
| `images[]` `poster`, `fanart`, `clearlogo` (`remoteUrl`) | ●●● / ●●○ | TMDB CDN URLs | Artwork |
| `youTubeTrailerId`, `website` | ●○○ | Trailers are rare for old films | Not used yet |
| `imdbId`, `tmdbId` | ●●● | Stable IDs | Matching, dedupe |
| `status` (`released`, `announced`…) | ●●● | Nearly always `released` for older titles | — |
| `tags[]` | — | *Your* Radarr tags (numeric IDs), not descriptive tags | Not used |
| Cast and crew | ○○○ in lookup | `GET /api/v3/credit?movieId=` exists only for films **already in your library** | Not usable for discovery |

## TV (Sonarr v3/v4, `SeriesResource`)

| Field | Richness | Notes | Used for |
|---|---|---|---|
| `title`, `sortTitle`, `alternateTitles[]` | ●●● | | Search |
| `year`, `firstAired`, `lastAired` (v4) | ●●● / ●●○ | | Decade filter, era |
| `overview` | ●●○ | | Detail |
| `genres[]` | ●●○ | TVDB genres. Often just one or two | Genre filter, similarity |
| `network` | ●●○ | DuMont, NBC… | Shown as studio or network, similarity |
| `runtime` (minutes per episode) | ●●○ | | Runtime filter |
| `certification` | ●○○ | TV ratings; mostly empty for 1950s TV | Filter if varied |
| `ratings {value, votes}` | ●○○ | Single TVDB/Trakt score. Few votes on old shows | Tie-breaker |
| `status` (`continuing`, `ended`, `upcoming`) | ●●● | Older shows are effectively all `ended` | Filter if varied |
| `seasons[]` (`seasonNumber`, `statistics`) | ●●● | Season list and counts | Season defaults, details |
| `seriesType` (`standard`, `daily`, `anime`) | ●●● | | — |
| `originalLanguage` | ●●○ | v4 only | Language filter |
| `images[]` `poster`, `fanart`, `banner` | ●●○ | Old shows often lack fanart | Artwork |
| `tvdbId`, `imdbId`, `tmdbId` (v4), `tvMazeId` | ●●● | | Matching |
| Collection, keywords, popularity | ○○○ | Not exposed by Sonarr | — |
| Cast | ○○○ | Not in the v3/v4 API | — |
| Episodes (`/api/v3/episode?seriesId=`) | ●●● for library shows | Titles, air dates, file status. Only after the show is added | Episode progress |

## Fields Phonarr adds

| Field | Values | Used for |
|---|---|---|
| `source` | `library`, `list` (titles.json), `import_list`, `recommended` (Radarr Discover), `search` | "Found in" filter, "Recommended by Radarr" shelf |
| `added` | Radarr/Sonarr `added` date, for library titles | "Recently added" sort |

Radarr's Discover feed (`GET /api/v3/importlist/movie?includeRecommendations=true`) returns the same `MovieResource` shape as a lookup, plus `isRecommendation`, `isExcluded` and `isExisting`. The gateway caches it for 10 minutes and stores each entry as lookup metadata, so opening or requesting one doesn't need another lookup.

## Calendar (`GET /api/v3/calendar`)

The Calendar asks both apps for `/api/v3/calendar?start=&end=&unmonitored=true` (Sonarr also `includeSeries=true`), over the UTC window of the viewer's local range padded by a day on each side. Both only return titles that are already in the library.

| App | Field | How rich | What the gateway does with it |
|---|---|---|---|
| Radarr | `inCinemas`, `digitalRelease`, `physicalRelease` | Usually set for new films from TMDB; often only one or two of the three, and none for old films | One entry per kind in range: `cinema`, `digital`, `physical`. They are **days** sent as `…T00:00:00Z`: the date part is used, never converted by timezone (KB-12) |
| Radarr | `tmdbId`, `title`, `year`, `images`, `monitored`, `hasFile` | Always | `id`, `title`, `year`, `poster`, `monitored`, `has_file` |
| Sonarr | `airDateUtc` | Set for nearly every episode with a known air time | Converted to the viewer's IANA timezone for the day and `HH:MM` (KB-13) |
| Sonarr | `airDate` | The air date in the network's timezone | Used, as a day with no time, only when `airDateUtc` is missing |
| Sonarr | `seasonNumber`, `episodeNumber`, `title`, `monitored`, `hasFile` | Always (episode titles are often "TBA" until close to the date) | `season`, `episodes [first, last]`, `count`, `episode_title` (single episodes only), `special` (season 0), `has_file` (every episode in the entry) |
| Sonarr | `finaleType` (v4) | `series`, `season` or `midseason`, only on finales and only on Sonarr v4 | `finale`; `premiere` is worked out from episode 1 (`series` for season 1, `season` otherwise) |
| Sonarr | `series.tvdbId`, `series.title`, `series.images`, `series.monitored` | With `includeSeries=true` | `id`, `title`, `poster`; `monitored` is the episode's flag **and** the series' (KB-34) |

Each entry also carries `status` from the gateway's status snapshot (the same one `/v1/status` serves), so a request or download shows on the Calendar straight away (KB-36). Episodes of one show, season and local day become one entry (KB-32).

## What this means in practice
- **Rich enough:** genre, decade or year, runtime, language, studio or network, and ratings for most films. These give useful filters and decent "More like this" results.
- **Thin:** certification (nearly always empty before 1968), keywords and collections (rare), TV ratings, and anything about cast or crew (not available before a title is in your library).
- **The pool's size matters more than the metadata.** "More like this" ranks the discovery pool (library, titles list, Radarr Discover). With a handful of titles it has little to choose from; with a few hundred it works well. Radarr's recommendations grow with your library, and a long `titles.json` helps most.

## Filters the frontends offer
Both frontends filter and sort the discovery pool on the device for instant results, with the same rules in three places that must stay in step: `backend/app/discovery.py` (the gateway's `/v1/discover` parameters), `web/js/filters.js` (web, tested by `web/tests/filters.test.mjs`) and `ios/Phonarr/Core/Filters.swift` (iPhone). Facets (which filters to show and their counts) always come from the gateway.

**Defaults:** highest rated first, English only on. Every match is reachable: the grids show a page at a time (`filters.page_size`, 60) and add the next page as you scroll; the gateway's `/v1/discover` takes `page` and `page_size` and always returns `matched`, the full count.

- **Trusted rating** (`rating` on every card, used by sorting, the minimum-rating filter, shelves and similarity). Worked out in one place, `discovery.trusted_rating`: IMDb and TMDB averaged by vote count, then pulled towards `prior_mean` (6.5) by `prior_votes` (25,000 for films, 20 for TV, whose single Sonarr score has far fewer votes): `(votes × average + prior_votes × prior_mean) ÷ (votes + prior_votes)`. Trakt, then TVDB, are the fallback when neither has a value; a score with no vote count is averaged plainly. All in `discovery.trusted_rating` in settings.json. Each card also carries `ratings` (every source's own score: IMDb, TMDB, Rotten Tomatoes, Metacritic, Trakt, TVDB) and `votes`; the poster shows the trusted rating and a small strip of the sources.
- **English only** keeps titles whose `originalLanguage` is English (`discovery.english_names`), and titles with no language at all (Radarr v3 and Sonarr v3 send none), so missing data never hides a title. When it's off, the Language picker appears.
- **Genres:** tap once to include (any-of), again to exclude. An excluded genre always wins.
- **Neighbouring genres:** once a genre is picked, genres whose titles overlap with it by at least 15% (`discovery.neighbours.min_overlap`; titles in both ÷ titles in either, worked out from the pool and sent as `facets.genre_neighbours`) count as matches too. Using overlap rather than "share of the pick's titles" stops a broad genre like Drama from being everyone's neighbour. On by default, each one can be dropped, and a card that matched only through one says "via Thriller".
- **Year:** Any, Decades (several at once) or an exact Range such as 2005–2010, inclusive; one at a time, and switching clears the other. `facets.years` gives the range's bounds.
- **Length:** at least / at most, in minutes (`filters.runtime_steps`). A title with no runtime is hidden only when "at most" is set.
- **Also:** minimum rating, studio or network, collection, certification, series status, where it was found. Sort can switch to title, newest, oldest, most popular, shortest, longest or recently added.

A filter is shown only when the current set has at least two values for it, so there are no dead filters. The gateway still accepts the older `decade` and `runtime` (band) parameters.

Discover also builds shelves automatically from the same data: "Before 1930", one per decade, the top genres, "Under 90 minutes" and "Highest rated".

## How "More like this" scores titles
`backend/app/discovery.py::similarity`. Weighted sum, each part between 0 and 1:

| Signal | Weight |
|---|---|
| Genre overlap (Jaccard) | 4.0 |
| Keyword overlap (when present) | 3.0 |
| Same collection | 3.0 |
| Era closeness (within 25 years) | 2.0 |
| Same studio or network | 1.5 |
| Same language, similar rating, similar runtime | 0.5 each |
| Different media type (film ↔ TV) | −0.75 |

Results need a score of at least 1.0, so unrelated titles don't pad the list. Each result carries up to three plain reasons ("Horror · Thriller", "Same era (1960s)", "Also from Image Ten"). Candidates are the discovery pool: library, titles list and Radarr Discover, for both films and TV when mixing is on. By default, titles already in your library or requested are left out, the same as Discover.
