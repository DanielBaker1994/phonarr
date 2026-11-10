import Foundation

/// Client-side filtering over the discovery pool for instant results.
/// Mirrors backend/app/discovery.py and web/js/filters.js so the gateway's /v1/discover filters behave the same.
enum SortOption: String, CaseIterable, Identifiable, Codable {
    case title, yearDesc = "year_desc", yearAsc = "year_asc", rating, popularity
    case runtimeAsc = "runtime_asc", runtimeDesc = "runtime_desc", recent

    var id: String { rawValue }
    var label: String {
        switch self {
        case .title: "Title A–Z"
        case .yearDesc: "Newest first"
        case .yearAsc: "Oldest first"
        case .rating: "Highest rated"
        case .popularity: "Most popular"
        case .runtimeAsc: "Shortest"
        case .runtimeDesc: "Longest"
        case .recent: "Recently added"
        }
    }
}

enum RuntimeBand: String, CaseIterable, Identifiable, Codable {
    case short, brisk, feature, long
    var id: String { rawValue }
    var range: ClosedRange<Int> {
        switch self {
        case .short: 0...59
        case .brisk: 60...89
        case .feature: 90...119
        case .long: 120...10_000
        }
    }
    var label: String {
        switch self {
        case .short: "Under 1 h"
        case .brisk: "60–89 min"
        case .feature: "90–119 min"
        case .long: "2 h +"
        }
    }
}

enum YearMode: String, CaseIterable, Identifiable, Codable {
    case any, decades, range
    var id: String { rawValue }
    var label: String {
        switch self {
        case .any: "Any"
        case .decades: "Decades"
        case .range: "Range"
        }
    }
}

/// A genre whose titles overlap with another genre's (shared ÷ either), from the gateway's facets.
struct GenreNeighbour: Codable, Hashable, Identifiable {
    let value: String
    let overlap: Double
    var id: String { value }
}

enum GenreNeighbours {
    /// Same as discovery.py genre_neighbours: genres whose titles overlap by at least `minOverlap`, strongest first.
    static func build(_ cards: [MediaCard], minOverlap: Double = AppConfig.double("filters.neighbour_min_overlap", default: 0.15))
        -> [String: [GenreNeighbour]] {
        var titles: [String: Set<Int>] = [:]
        for (i, c) in cards.enumerated() { for g in c.genres { titles[g, default: []].insert(i) } }
        var out: [String: [GenreNeighbour]] = [:]
        for (a, ta) in titles {
            var near: [GenreNeighbour] = []
            for (b, tb) in titles where a != b {
                let both = ta.intersection(tb).count
                let overlap = Double(both) / Double(ta.count + tb.count - both)
                if both > 0 && overlap >= minOverlap { near.append(GenreNeighbour(value: b, overlap: (overlap * 1000).rounded() / 1000)) }
            }
            if !near.isEmpty {
                out[a] = near.sorted { $0.overlap == $1.overlap ? $0.value < $1.value : $0.overlap > $1.overlap }
            }
        }
        return out
    }

    /// Neighbours of the picked genres, strongest first, leaving out the picks and anything in `skip`.
    static func of(_ picks: Set<String>, in map: [String: [GenreNeighbour]], skip: Set<String> = []) -> [GenreNeighbour] {
        var best: [String: Double] = [:]
        for p in picks {
            for n in map[p] ?? [] where !picks.contains(n.value) && !skip.contains(n.value) {
                best[n.value] = max(best[n.value] ?? 0, n.overlap)
            }
        }
        return best.map { GenreNeighbour(value: $0.key, overlap: $0.value) }
            .sorted { $0.overlap == $1.overlap ? $0.value < $1.value : $0.overlap > $1.overlap }
    }
}

struct FilterState: Equatable, Codable {
    /// Highest rated first by default, English only on, neighbouring genres included once a genre is picked.
    var sort: SortOption = .rating
    var englishOnly = true
    var genres: Set<String> = []
    var excludeGenres: Set<String> = []
    var neighbours = true
    var neighboursOff: Set<String> = []
    var yearMode: YearMode = .any
    var decades: Set<Int> = []
    var yearMin: Int?
    var yearMax: Int?
    var runtimeMin = 0
    var runtimeMax = 0
    var minRating: Double = 0
    var language: String?
    var studio: String?
    var collection: String?
    var certification: String?
    var source: String?
    var seriesStatus: String?

    /// Filters that narrow the list beyond the defaults (English only is a default, so it isn't counted).
    var activeCount: Int {
        let year = yearMode == .decades ? (decades.isEmpty ? 0 : 1)
            : (yearMode == .range && (yearMin != nil || yearMax != nil) ? 1 : 0)
        return genres.count + excludeGenres.count + year
            + [runtimeMin > 0 || runtimeMax > 0, minRating > 0, language != nil, studio != nil,
               collection != nil, certification != nil, source != nil, seriesStatus != nil].filter { $0 }.count
    }

    var isDefault: Bool { activeCount == 0 && englishOnly && sort == .rating }

    static func isEnglish(_ c: MediaCard) -> Bool {
        guard let lang = c.language?.lowercased(), !lang.isEmpty else { return true }   // missing data never hides a title
        return AppConfig.strings("filters.english_names", default: ["English"]).contains { $0.lowercased() == lang }
    }

    /// The genres a title may carry to match: the picks plus their neighbours (unless switched off).
    func matchingGenres(_ map: [String: [GenreNeighbour]]) -> Set<String>? {
        if genres.isEmpty { return nil }
        guard neighbours else { return genres }
        let extra = GenreNeighbours.of(genres, in: map, skip: excludeGenres).map(\.value).filter { !neighboursOff.contains($0) }
        return genres.union(extra)
    }

    /// For a card that only matched through a neighbouring genre: that genre ("via Thriller").
    func viaNeighbour(_ card: MediaCard, _ map: [String: [GenreNeighbour]]) -> String? {
        if genres.isEmpty || !genres.isDisjoint(with: card.genres) { return nil }
        guard let match = matchingGenres(map) else { return nil }
        return card.genres.first { match.contains($0) }
    }

    func apply(_ cards: [MediaCard], query: String, neighbourMap: [String: [GenreNeighbour]]? = nil) -> [MediaCard] {
        let q = query.trimmingCharacters(in: .whitespaces)
        let map = neighbourMap ?? (genres.isEmpty || !neighbours ? [:] : GenreNeighbours.build(cards))
        let match = matchingGenres(map)
        var lo = yearMin, hi = yearMax
        if let a = yearMin, let b = yearMax { lo = min(a, b); hi = max(a, b) }
        let filtered = cards.filter { c in
            if !q.isEmpty, !(c.title ?? "").localizedCaseInsensitiveContains(q),
               !(c.originalTitle ?? "").localizedCaseInsensitiveContains(q) { return false }
            if englishOnly && !Self.isEnglish(c) { return false }
            if !excludeGenres.isDisjoint(with: c.genres) { return false }
            if let match, match.isDisjoint(with: c.genres) { return false }
            if yearMode == .decades, !decades.isEmpty { guard let d = c.decade, decades.contains(d) else { return false } }
            if yearMode == .range {
                if let lo, (c.year ?? 0) < lo { return false }
                if let hi, (c.year ?? 9999) > hi { return false }
            }
            if runtimeMin > 0, (c.runtime ?? 0) < runtimeMin { return false }
            if runtimeMax > 0 { guard let r = c.runtime, r > 0, r <= runtimeMax else { return false } }
            if minRating > 0, (c.rating ?? 0) < minRating { return false }
            if let language, c.language != language { return false }
            if let studio, c.studio != studio { return false }
            if let collection, c.collection != collection { return false }
            if let certification, c.certification != certification { return false }
            if let source, c.source != source { return false }
            if let seriesStatus, c.seriesStatus != seriesStatus { return false }
            return true
        }
        return sorted(filtered)
    }

    private func sorted(_ cards: [MediaCard]) -> [MediaCard] {
        let byTitle: (MediaCard, MediaCard) -> Bool = {
            ($0.title ?? "").localizedCaseInsensitiveCompare($1.title ?? "") == .orderedAscending
        }
        // Items missing the sort key always go last.
        func by<T: Comparable>(_ key: (MediaCard) -> T?, descending: Bool) -> [MediaCard] {
            cards.sorted { a, b in
                switch (key(a), key(b)) {
                case let (x?, y?): return x == y ? byTitle(a, b) : (descending ? x > y : x < y)
                case (.some, .none): return true
                case (.none, .some): return false
                default: return byTitle(a, b)
                }
            }
        }
        switch sort {
        case .title: return cards.sorted(by: byTitle)
        case .yearDesc: return by({ $0.year }, descending: true)
        case .yearAsc: return by({ $0.year }, descending: false)
        case .rating: return by({ $0.rating }, descending: true)
        case .popularity: return by({ $0.popularity }, descending: true)
        case .runtimeAsc: return by({ $0.runtime }, descending: false)
        case .runtimeDesc: return by({ $0.runtime }, descending: true)
        case .recent: return by({ $0.added }, descending: true)
        }
    }

    /// Chip text for the year filter, or nil when it's off.
    var yearLabel: String? {
        if yearMode == .decades && !decades.isEmpty { return decades.sorted().map { "\(String($0))s" }.joined(separator: " + ") }
        if yearMode == .range {
            switch (yearMin, yearMax) {
            case let (a?, b?): return "\(String(min(a, b)))–\(String(max(a, b)))"
            case let (a?, nil): return "\(String(a)) on"
            case let (nil, b?): return "Up to \(String(b))"
            default: return nil
            }
        }
        return nil
    }

    var lengthLabel: String? {
        switch (runtimeMin, runtimeMax) {
        case (0, 0): return nil
        case (let a, 0): return "\(a)+ min"
        case (0, let b): return "Up to \(b) min"
        case let (a, b): return "\(a)–\(b) min"
        }
    }
}

/// The rating sources a poster shows under it, in this order, with short labels.
enum RatingStrip {
    static let order: [(key: String, label: String)] = [("imdb", "IMDb"), ("tmdb", "TMDB"), ("rottenTomatoes", "RT"),
                                                         ("metacritic", "Meta"), ("tvdb", "TVDB")]
    static func items(_ card: MediaCard) -> [(label: String, value: String)] {
        let r = card.ratings ?? [:]
        return order.compactMap { entry -> (label: String, value: String)? in
            guard let v = r[entry.key] else { return nil }
            let text: String
            switch entry.key {
            case "rottenTomatoes": text = "\(Int(v.rounded()))%"
            case "metacritic": text = "\(Int(v.rounded()))"
            default: text = String(format: "%.1f", v)
            }
            return (entry.label, text)
        }
    }
}

/// Discover shelves generated from metadata, so the home screen isn't just "latest" and "popular".
struct Shelf: Identifiable {
    let id: String
    let title: String
    let items: [MediaCard]
}

enum ShelfBuilder {
    static func build(_ cards: [MediaCard], minimum: Int = 3) -> [Shelf] {
        var shelves: [Shelf] = []
        func add(_ id: String, _ title: String, _ items: [MediaCard]) {
            if items.count >= minimum { shelves.append(Shelf(id: id, title: title, items: Array(items.prefix(20)))) }
        }
        let rated = cards.filter { $0.rating != nil }.sorted { ($0.rating ?? 0) > ($1.rating ?? 0) }
        add("rated", "Highest rated", rated)
        add("early", "Before 1930", cards.filter { ($0.year ?? 9999) < 1930 }.sorted { ($0.year ?? 0) < ($1.year ?? 0) })
        add("short", "Under 90 minutes", cards.filter { ($0.runtime ?? 0) > 0 && ($0.runtime ?? 0) < 90 })
        let genreCounts = Dictionary(cards.flatMap { $0.genres }.map { ($0, 1) }, uniquingKeysWith: +)
        for genre in genreCounts.sorted(by: { $0.value > $1.value }).prefix(4).map(\.key) {
            add("genre-\(genre)", genre, cards.filter { $0.genres.contains(genre) })
        }
        let decades = Set(cards.compactMap(\.decade)).filter { $0 >= 1930 }.sorted()
        for d in decades {
            add("decade-\(d)", "The \(String(d))s", cards.filter { $0.decade == d })
        }
        return shelves
    }
}
