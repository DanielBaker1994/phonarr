import Foundation

// Calendar (GET /v1/calendar): models and pure helpers. Mirrors web/js/calendar.js.
// Day keys are "yyyy-MM-dd" strings and are never turned into instants in the device's timezone: Radarr's
// release dates are days (KB-12). Day arithmetic happens on UTC dates, which have no daylight saving (KB-16).

struct CalendarServiceState: Codable, Hashable {
    let configured: Bool
    let ok: Bool?
    let error: ServiceError?
}

struct CalendarEntry: Codable, Hashable {
    let type: MediaType
    let id: Int
    let title: String?
    let year: Int?
    let poster: String?
    let date: String
    let time: String?
    /// "cinema", "digital", "physical" (films) or "episode"
    let kind: String
    let monitored: Bool
    let hasFile: Bool
    let special: Bool
    var season: Int? = nil
    var episode: Int? = nil
    var episodes: [Int]? = nil
    var count: Int? = nil
    var episodeTitle: String? = nil
    var premiere: String? = nil
    var finale: String? = nil
    let status: MediaStatus

    /// Unique within a day (a film can have two release kinds; a show two seasons).
    var rowID: String { "\(type.rawValue):\(id):\(kind):\(season ?? -1):\(episode ?? -1)" }
    var posterURL: URL? { poster.flatMap(URL.init(string:)) }
}

struct CalendarDay: Codable, Hashable {
    let date: String
    var items: [CalendarEntry]
}

struct CalendarResponse: Codable {
    var start: String
    var end: String
    let tz: String
    let tzFallback: Bool
    var days: [CalendarDay]
    var services: [String: CalendarServiceState]
}

/// The filters on the Calendar, saved per device with @AppStorage.
struct CalendarFilters: Equatable {
    var media: String = "all"          // "all", "movie", "series"
    var hideSpecials = false
    var monitoredOnly = false

    func keeps(_ e: CalendarEntry) -> Bool {
        if media == "movie" && e.type != .movie { return false }
        if media == "series" && e.type != .series { return false }
        if hideSpecials && e.special { return false }
        if monitoredOnly && !e.monitored { return false }
        return true
    }

    /// Same days, fewer items: days stay even when empty, so Today always has a place.
    func apply(_ days: [CalendarDay]) -> [CalendarDay] {
        days.map { CalendarDay(date: $0.date, items: $0.items.filter(keeps)) }
    }

    var activeCount: Int { (media == "all" ? 0 : 1) + (hideSpecials ? 1 : 0) + (monitoredOnly ? 1 : 0) }
}

enum CalendarDates {
    private static let utc = TimeZone(secondsFromGMT: 0) ?? TimeZone.current

    private static func gregorian(_ tz: TimeZone) -> Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        return cal
    }

    private static func key(_ comps: DateComponents) -> String {
        String(format: "%04d-%02d-%02d", comps.year ?? 0, comps.month ?? 0, comps.day ?? 0)
    }

    /// Midnight UTC of a day key, used only for day arithmetic and formatting in UTC.
    private static func utcDate(_ key: String) -> Date? {
        let parts = key.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return gregorian(utc).date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    /// Today's key in a timezone, read from the clock every time (KB-15).
    static func todayKey(tz: TimeZone, now: Date = Date()) -> String {
        key(gregorian(tz).dateComponents([.year, .month, .day], from: now))
    }

    static func addDays(_ day: String, _ n: Int) -> String {
        let cal = gregorian(utc)
        guard let date = utcDate(day), let moved = cal.date(byAdding: .day, value: n, to: date) else { return day }
        return key(cal.dateComponents([.year, .month, .day], from: moved))
    }

    static func daysBetween(_ from: String, _ to: String) -> Int {
        guard let a = utcDate(from), let b = utcDate(to) else { return 0 }
        return gregorian(utc).dateComponents([.day], from: a, to: b).day ?? 0
    }

    /// Seconds until just after the next local midnight; never under a second.
    static func secondsUntilNextMidnight(tz: TimeZone, now: Date = Date()) -> Double {
        let cal = gregorian(tz)
        let start = cal.startOfDay(for: now)
        guard let next = cal.date(byAdding: .day, value: 1, to: start) else { return 3600 }
        return max(1, next.timeIntervalSince(now) + 0.25)
    }

    /// "Today", "Tomorrow", "Yesterday", otherwise "Thu 12 Nov" in the device's language.
    static func heading(_ day: String, today: String) -> String {
        switch daysBetween(today, day) {
        case 0: return AppConfig.text("calendar.today")
        case 1: return AppConfig.text("calendar.tomorrow")
        case -1: return AppConfig.text("calendar.yesterday")
        default:
            guard let date = utcDate(day) else { return day }
            let f = DateFormatter()
            f.timeZone = utc
            f.setLocalizedDateFormatFromTemplate(day.prefix(4) == today.prefix(4) ? "EEEdMMM" : "EEEdMMMy")
            return f.string(from: date)
        }
    }

    static func episodeCode(_ season: Int, _ episode: Int) -> String {
        String(format: "S%02dE%02d", season, episode)
    }

    /// "S02E05 · The Fifth · 21:00", "S02E01–E08 · 8 episodes · 08:00", "Special · S00E03 · Extras", "In cinemas".
    static func line(_ e: CalendarEntry) -> String {
        if e.type == .movie { return AppConfig.text("calendar.kind_\(e.kind)") }
        let season = e.season ?? 0
        let first = e.episodes?.first ?? e.episode ?? 0
        let last = e.episodes?.last ?? first
        var parts: [String] = []
        if e.special { parts.append(AppConfig.text("calendar.special")) }
        if (e.count ?? 1) > 1 {
            parts.append(episodeCode(season, first) + "–" + String(format: "E%02d", last))
            parts.append(AppConfig.text("calendar.episodes", ["count": e.count ?? 0]))
        } else {
            parts.append(episodeCode(season, first))
            if let t = e.episodeTitle, !t.isEmpty { parts.append(t) }
        }
        if let time = e.time { parts.append(time) }
        return parts.joined(separator: " · ")
    }

    /// "Season finale", "Series premiere" and so on, or nil.
    static func milestone(_ e: CalendarEntry) -> String? {
        if let f = e.finale {
            let key = "calendar.finale_\(f)"
            return AppConfig.hasText(key) ? AppConfig.text(key) : AppConfig.text("calendar.finale")
        }
        if let p = e.premiere {
            let key = "calendar.premiere_\(p)"
            return AppConfig.hasText(key) ? AppConfig.text(key) : nil
        }
        return nil
    }

    /// Load more: merge by date (the newer answer wins), sorted by date.
    static func merge(_ older: [CalendarDay], _ newer: [CalendarDay]) -> [CalendarDay] {
        var byDate: [String: CalendarDay] = [:]
        for d in older { byDate[d.date] = d }
        for d in newer { byDate[d.date] = d }
        return byDate.values.sorted { $0.date < $1.date }
    }
}
