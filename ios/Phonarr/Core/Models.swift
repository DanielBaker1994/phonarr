import Foundation

enum MediaType: String, Codable, CaseIterable, Identifiable {
    case movie, series
    var id: String { rawValue }
    var label: String { self == .movie ? "Movie" : "TV Show" }
}

enum MediaState: String, Codable {
    case notRequested = "not_requested"
    case submitting, queued, searching, downloading, paused, stalled, importing, complete, failed
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = MediaState(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .notRequested: "Not requested"
        case .submitting: "Requesting…"
        case .queued: "Queued"
        case .searching: "Searching"
        case .downloading: "Downloading"
        case .paused: "Paused"
        case .stalled: "Stalled"
        case .importing: "Importing"
        case .complete: "In library"
        case .failed: "Failed"
        case .unknown: "Unknown"
        }
    }

    var systemImage: String {
        switch self {
        case .notRequested: "plus.circle"
        case .submitting: "arrow.up.circle"
        case .queued: "clock"
        case .searching: "magnifyingglass"
        case .downloading: "arrow.down.circle.fill"
        case .paused: "pause.circle.fill"
        case .stalled: "exclamationmark.circle"
        case .importing: "tray.and.arrow.down.fill"
        case .complete: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .unknown: "questionmark.circle"
        }
    }

    var isActive: Bool { [.submitting, .queued, .downloading, .importing, .paused, .stalled].contains(self) }
}

struct MediaStatus: Codable, Hashable {
    var state: MediaState
    var progress: Double?
    var message: String?
    var errorKind: String?
    var stale: Bool?
    var episodesHave: Int?
    var episodesWanted: Int?

    static let idle = MediaStatus(state: .notRequested)
    static let submitting = MediaStatus(state: .submitting)

    init(state: MediaState, progress: Double? = nil, message: String? = nil, errorKind: String? = nil) {
        self.state = state
        self.progress = progress
        self.message = message
        self.errorKind = errorKind
    }

    var isConfigProblem: Bool { errorKind == "config" }
}

struct MediaCard: Codable, Identifiable, Hashable {
    let type: MediaType
    let id: Int
    let title: String?
    let year: Int?
    let genres: [String]
    let poster: String?
    let overview: String?
    var status: MediaStatus
    // Discovery metadata (all optional: thin for old titles, see docs/METADATA.md)
    var originalTitle: String? = nil
    var decade: Int? = nil
    var runtime: Int? = nil
    var rating: Double? = nil
    var popularity: Double? = nil
    var language: String? = nil
    var studio: String? = nil
    var collection: String? = nil
    var certification: String? = nil
    var keywords: [String]? = nil
    var seriesStatus: String? = nil
    /// Where discovery found it: library, list (your titles list), import_list, recommended, search
    var source: String? = nil
    var added: String? = nil
    // Only on "More like this" results
    var score: Double? = nil
    var reasons: [String]? = nil

    var key: String { "\(type.rawValue):\(id)" }
    var posterURL: URL? { poster.flatMap(URL.init(string:)) }
}

struct DiscoverResponse: Codable {
    let items: [MediaCard]
    let genres: [String]
    let facets: Facets?
    let total: Int?
    let error: ServiceError?
    let configured: Bool?
    var searched: Bool? = nil
    var requestPlan: RequestPlan? = nil
}

/// What a one-tap Add uses right now, resolved by the gateway (defaults, or the only option).
struct RequestPlan: Codable, Hashable {
    struct Problem: Codable, Hashable { let kind: String; let message: String }
    let service: String
    let qualityProfileId: Int?
    let qualityProfile: String?
    let rootFolder: String?
    let seasonMonitor: String?
    let problem: Problem?

    /// "HD-1080p · /movies · All seasons"
    var summary: String {
        [qualityProfile, rootFolder, seasonMonitor.map(Defaults.seasonLabel)].compactMap { $0 }.joined(separator: " · ")
    }
}

enum DiscoverySource: String, CaseIterable {
    case library, list, importList = "import_list", recommended, search
    var label: String {
        switch self {
        case .library: "In your library"
        case .list: "Your titles list"
        case .importList: "Radarr import lists"
        case .recommended: "Recommended by Radarr"
        case .search: "Search result"
        }
    }
    static func label(_ raw: String) -> String { DiscoverySource(rawValue: raw)?.label ?? raw.capitalized }
}

struct FacetValue: Codable, Hashable, Identifiable {
    let value: String
    let count: Int
    var id: String { value }
}

/// Value counts for the unfiltered set; a facet is only sent when it has 2+ values.
struct Facets: Codable, Hashable {
    var genres: [FacetValue]? = nil
    var decades: [FacetValue]? = nil
    var runtime: [FacetValue]? = nil
    var languages: [FacetValue]? = nil
    var studios: [FacetValue]? = nil
    var collections: [FacetValue]? = nil
    var certifications: [FacetValue]? = nil
    var sources: [FacetValue]? = nil
    var statuses: [FacetValue]? = nil
    var rated: Int? = nil
}

struct SimilarResponse: Codable {
    let items: [MediaCard]
    let candidates: Int
}

struct ServiceError: Codable, Hashable, Error {
    let service: String?
    let kind: String
    let message: String
    /// The gateway's id for the call: "[<id>]" in gateway.log finds it. Absent in older gateways.
    var requestId: String? = nil
}

struct Episode: Codable, Hashable, Identifiable {
    let season: Int
    let episode: Int
    let title: String?
    let airDate: String?
    let state: MediaState
    let progress: Double?
    let message: String?
    var id: String { "\(season)x\(episode)" }
}

struct TitleDetail: Codable {
    let type: MediaType
    let id: Int
    let title: String?
    let originalTitle: String?
    let year: Int?
    let overview: String?
    let genres: [String]?
    let runtime: Int?
    let certification: String?
    let studio: String?
    let poster: String?
    let fanart: String?
    let seasonCount: Int?
    let seriesStatus: String?
    let ratings: [String: Double]?
    let language: String?
    let collection: String?
    let keywords: [String]?
    let status: MediaStatus
    var requestPlan: RequestPlan? = nil
    let episodes: [Episode]?
}

struct ServiceHealth: Codable, Hashable {
    let reachable: Bool?
    let error: ServiceError?
    let configured: Bool?
}

struct StatusSnapshot: Codable {
    let items: [String: MediaStatus]
    let services: [String: ServiceHealth]
    let nextPollSeconds: Double?
}

struct ActivityItem: Codable, Identifiable, Hashable {
    let type: MediaType
    let id: Int
    let title: String?
    let year: Int?
    let poster: String?
    let status: MediaStatus
    let requestedAt: Double?
    var key: String { "\(type.rawValue):\(id)" }
    var posterURL: URL? { poster.flatMap(URL.init(string:)) }
}

struct ActivityResponse: Codable {
    let items: [ActivityItem]
    let services: [String: ServiceHealth]
    let nextPollSeconds: Double?
}

struct ServiceConfig: Codable, Hashable {
    let configured: Bool
    let baseUrl: String?
    let apiKeySet: Bool
    let version: String?
    let reachable: Bool?
    let error: ServiceError?
    var apiCheck: CompatSummary? = nil
}

struct CompatSummary: Codable, Hashable {
    let status: String
    let checkedAt: Double
    let version: String?
    let changes: Int
}

// MARK: API compatibility report (GET /v1/compat, POST /v1/compat/check)

enum CompatLevel: String, Codable {
    case ok, info, warning, error
    init(from decoder: Decoder) throws {
        self = CompatLevel(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .warning
    }
    var systemImage: String {
        switch self {
        case .ok: "checkmark.circle.fill"
        case .info: "info.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        }
    }
    var label: String {
        switch self {
        case .ok: "Compatible"
        case .info: "Compatible (some optional data missing)"
        case .warning: "Check recommended"
        case .error: "Something the app needs has changed"
        }
    }
}

struct CompatCheck: Codable, Hashable, Identifiable {
    let name: String
    let method: String
    let path: String
    let status: CompatLevel
    let details: [String]
    var id: String { method + path + name }
}

struct CompatFeature: Codable, Hashable, Identifiable {
    let feature: String
    let status: CompatLevel
    let checks: [CompatCheck]
    var id: String { feature }
}

struct CompatReport: Codable, Hashable {
    let service: String
    let checkedAt: Double
    let durationMs: Int?
    let version: String?
    let reachable: Bool
    let status: CompatLevel
    let specSource: String?
    let specNote: String?
    let features: [CompatFeature]
    let previousVersion: String?
    let previousCheckedAt: Double?
    let changes: [String]
}

struct CompatReports: Codable {
    let radarr: CompatReport?
    let sonarr: CompatReport?
}

struct GatewayConfig: Codable {
    let radarr: ServiceConfig
    let sonarr: ServiceConfig
}

struct ConnectionTest: Codable {
    let ok: Bool
    let baseUrl: String
    let version: String?
    let appName: String?
    let qualityProfiles: Int?
}

struct QualityProfile: Codable, Hashable, Identifiable {
    let id: Int
    let name: String
}

struct RootFolder: Codable, Hashable, Identifiable {
    let path: String
    let freeSpace: Int64?
    var id: String { path }
}

struct ServiceOptions: Codable, Hashable {
    let qualityProfiles: [QualityProfile]?
    let rootFolders: [RootFolder]?
    let error: ServiceError?
}

struct Defaults: Codable, Hashable {
    var radarrProfileId: Int?
    var sonarrProfileId: Int?
    var radarrRoot: String?
    var sonarrRoot: String?
    var seasonMonitor: String

    static let seasonModes = ["all", "first", "latest", "future"]

    static func seasonLabel(_ mode: String) -> String {
        switch mode {
        case "first": "First season"
        case "latest": "Latest season"
        case "future": "Future episodes only"
        default: "All seasons"
        }
    }
}

struct OptionsResponse: Codable {
    let defaults: Defaults
    let radarr: ServiceOptions
    let sonarr: ServiceOptions

    func options(for type: MediaType) -> ServiceOptions { type == .movie ? radarr : sonarr }
}

struct RequestResult: Codable {
    let type: MediaType
    let id: Int
    let arrId: Int?
    let deduplicated: Bool
    let status: MediaStatus
}

/// POST /v1/requests/batch
struct BatchResult: Codable {
    struct Item: Codable {
        let type: MediaType
        let id: Int
        let ok: Bool
        let deduplicated: Bool
        let status: MediaStatus?
        let error: RequestPlan.Problem?
    }
    let results: [Item]
    let added: Int
    let already: Int
    let failed: Int
}

/// One-off choices for a request or batch. nil = use the default.
struct RequestOverride: Codable, Hashable {
    var qualityProfileId: Int? = nil
    var rootFolder: String? = nil
    var seasonMonitor: String? = nil
    var isEmpty: Bool { qualityProfileId == nil && rootFolder == nil && seasonMonitor == nil }
}

// MARK: GET /v1/health (no token): is the gateway up, and can it reach Radarr and Sonarr?

struct HealthResponse: Codable {
    struct Service: Codable, Hashable {
        let configured: Bool
        let reachable: Bool?
        let version: String?
        var checkedSAgo: Double? = nil
        var downForS: Double? = nil
    }
    let ok: Bool
    /// "ok" (every connected server answers), "degraded" (one is unreachable), "setup" (none connected)
    let status: String?
    let version: String?
    let uptimeS: Double?
    let webFrontend: Bool?
    let services: [String: Service]?
    let lastStatusRefreshSAgo: Double?
    let pollInterval: Double?
    /// "running", "off" (POLLER=0) or "stopped" (should run but ended: see the gateway log)
    var poller: String? = nil
    var lastPollErrorSAgo: Double? = nil
    var idleHealthCheckS: Double? = nil
}

// MARK: - Settings → Logs

/// One log line, the same shape on both sides: GET /v1/debug/logs (source "gateway") and this app's
/// own log parsed by AppLog.parse (source "ios"). `ts` is UTC ISO 8601 with milliseconds and Z.
struct LogEntry: Decodable, Hashable, Identifiable {
    var ts: String?
    var level: String
    var rid: String
    var source: String
    var location: String     // "where" in the JSON: logger.function:line on the gateway, Type.function here
    var message: String
    var seq: Int = 0         // position in the merged list, so equal lines still get their own row

    var id: Int { seq }

    enum CodingKeys: String, CodingKey {
        case ts, level, rid, source, message
        case location = "where"
    }
}

struct DebugLogsResponse: Decodable {
    struct Gateway: Decodable {
        var version: String?
        var timezone: String?
        var now: String?
    }
    let lines: [LogEntry]
    var truncated: Bool?
    var gateway: Gateway?
}

/// What the Logs screen needs besides the lines: the HTTP Date header and when the call went out and came back.
struct DebugLogsFetch {
    let response: DebugLogsResponse
    let dateHeader: String?
    let sentAt: Date
    let receivedAt: Date
}
