import Foundation

/// Client-side filtering over the discovery pool for instant results.
/// Mirrors backend/app/discovery.py so the gateway's /v1/discover filters behave the same.
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

struct FilterState: Equatable, Codable {
    var sort: SortOption = .title
    var genres: Set<String> = []
    var decade: Int?
    var runtime: RuntimeBand?
    var minRating: Double = 0
    var language: String?
    var studio: String?
    var collection: String?
    var certification: String?
    var source: String?
    var seriesStatus: String?

    var activeCount: Int {
        genres.count + [decade != nil, runtime != nil, minRating > 0, language != nil, studio != nil,
                        collection != nil, certification != nil, source != nil, seriesStatus != nil].filter { $0 }.count
    }

    func apply(_ cards: [MediaCard], query: String) -> [MediaCard] {
        let q = query.trimmingCharacters(in: .whitespaces)
        let filtered = cards.filter { c in
            if !q.isEmpty, !(c.title ?? "").localizedCaseInsensitiveContains(q),
               !(c.originalTitle ?? "").localizedCaseInsensitiveContains(q) { return false }
            if !genres.isEmpty, genres.isDisjoint(with: c.genres) { return false }
            if let decade, c.decade != decade { return false }
            if let runtime { guard let r = c.runtime, runtime.range.contains(r) else { return false } }
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
