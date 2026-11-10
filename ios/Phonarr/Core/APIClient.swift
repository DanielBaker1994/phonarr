import Foundation
import Security

/// Errors surfaced to the UI with a user-readable message.
enum APIError: LocalizedError {
    case notPaired
    case unauthorized
    case server(ServiceError, status: Int)
    case transport(String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .notPaired: "Connect to your gateway in Settings."
        case .unauthorized: "The gateway rejected the access token. Check it in Settings."
        case .server(let e, _): e.message
        case .transport(let m): m
        case .decoding: AppConfig.text("errors.decoding")
        }
    }

    var kind: String {
        switch self {
        case .server(let e, _): e.kind
        case .unauthorized, .notPaired: "config"
        default: "unavailable"
        }
    }
}

/// Talks only to the Phonarr gateway. Radarr/Sonarr API keys are sent once
/// when saving a connection and are never stored on the device.
final class APIClient: @unchecked Sendable {
    var baseURL: URL?
    var token: String? { didSet { AppLog.registerSecret(token) } }

    private let session: URLSession
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    init(baseURL: URL?, token: String?) {
        self.baseURL = baseURL
        self.token = token
        AppLog.registerSecret(token)
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 20
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    private func makeRequest(_ path: String, method: String = "GET", query: [URLQueryItem] = [],
                             body: Encodable? = nil, headers: [String: String] = [:]) throws -> URLRequest {
        guard let baseURL, let token, !token.isEmpty,
              var comps = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)
        else { throw APIError.notPaired }
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.notPaired }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        // The gateway logs this id on every line for the call, so one id finds it in both logs.
        req.setValue("ios-" + UUID().uuidString.prefix(8).lowercased(), forHTTPHeaderField: "X-Request-Id")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        if let body {
            req.httpBody = try encoder.encode(AnyEncodable(body))
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    private func send(_ req: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let started = Date()
        let label = "\(req.httpMethod ?? "GET") \(req.url?.path ?? "")"
        let rid = req.value(forHTTPHeaderField: "X-Request-Id") ?? "-"
        do {
            let (data, resp) = try await session.data(for: req)
            guard let http = resp as? HTTPURLResponse else { throw APIError.transport(AppConfig.text("errors.no_response")) }
            let ms = Int(Date().timeIntervalSince(started) * 1000)
            if http.statusCode >= 400 {
                AppLog.warn("api_error", ["label": label, "rid": rid, "status": http.statusCode, "ms": ms,
                                          "body": String(decoding: data.prefix(AppConfig.int("api.log_body_chars", default: 300)), as: UTF8.self)], area: "api")
            } else if !(req.url?.path.hasSuffix("/status") ?? false) || http.statusCode != 304 {
                AppLog.info("api_ok", ["label": label, "rid": rid, "status": http.statusCode, "ms": ms], area: "api")
            }
            if http.statusCode == 401 { throw APIError.unauthorized }
            if http.statusCode >= 400 {
                struct Envelope: Decodable { let error: ServiceError }
                if let env = try? decoder.decode(Envelope.self, from: data) {
                    throw APIError.server(env.error, status: http.statusCode)
                }
                throw APIError.server(ServiceError(service: nil, kind: "unavailable",
                                                   message: AppConfig.text("errors.http_status", ["status": http.statusCode])),
                                      status: http.statusCode)
            }
            return (data, http)
        } catch let e as APIError {
            throw e
        } catch let e as URLError {
            if e.code != .cancelled { AppLog.error("api_failed", ["label": label, "rid": rid, "code": e.code.rawValue, "error": e.localizedDescription], area: "api") }
            switch e.code {
            case .cannotFindHost, .dnsLookupFailed:
                throw APIError.transport(AppConfig.text("errors.dns"))
            case .cannotConnectToHost, .networkConnectionLost, .timedOut:
                throw APIError.transport(AppConfig.text("errors.unreachable"))
            case .notConnectedToInternet:
                throw APIError.transport(AppConfig.text("errors.offline"))
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateNotYetValid, .serverCertificateHasUnknownRoot:
                throw APIError.transport(AppConfig.text("errors.tls"))
            case .appTransportSecurityRequiresSecureConnection:
                throw APIError.transport(AppConfig.text("errors.ats"))
            case .cancelled:
                throw CancellationError()
            default:
                throw APIError.transport(e.localizedDescription)
            }
        }
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await decode(send(makeRequest(path, query: query)).0)
    }

    func send<T: Decodable>(_ path: String, method: String, body: Encodable? = nil,
                            headers: [String: String] = [:]) async throws -> T {
        try await decode(send(makeRequest(path, method: method, body: body, headers: headers)).0)
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        do { return try decoder.decode(T.self, from: data) } catch {
            // Usually the app and the gateway are different versions (a field was renamed or removed).
            AppLog.error("decode_failed", ["type": String(describing: T.self), "error": String(describing: error),
                                           "body": String(decoding: data.prefix(AppConfig.int("api.log_body_chars", default: 300)), as: UTF8.self)],
                         area: "api")
            throw APIError.decoding(String(describing: error))
        }
    }

    // MARK: Endpoints

    func config() async throws -> GatewayConfig { try await get("v1/config") }

    /// Liveness and Radarr/Sonarr reachability. The endpoint needs no token; sending it anyway is harmless.
    func health() async throws -> HealthResponse { try await get("v1/health") }

    struct ConnectionBody: Encodable { let baseUrl: String; let apiKey: String? }

    func testConnection(_ service: String, url: String, apiKey: String?) async throws -> ConnectionTest {
        try await send("v1/config/\(service)/test", method: "POST", body: ConnectionBody(baseUrl: url, apiKey: apiKey))
    }

    func saveConnection(_ service: String, url: String, apiKey: String?) async throws -> ConnectionTest {
        try await send("v1/config/\(service)", method: "PUT", body: ConnectionBody(baseUrl: url, apiKey: apiKey))
    }

    func removeConnection(_ service: String) async throws -> GatewayConfig {
        try await send("v1/config/\(service)", method: "DELETE")
    }

    /// Redacted gateway logs + state as a zip.
    func debugBundle() async throws -> Data {
        try await send(makeRequest("v1/debug/bundle")).0
    }

    /// The gateway's last log lines, parsed and redacted (Settings → Logs), with the HTTP Date header and the
    /// send/receive times so the screen can show how far this phone's clock is from the gateway's.
    func debugLogs(lines: Int, level: String?, rid: String?) async throws -> DebugLogsFetch {
        var q: [URLQueryItem] = [.init(name: "lines", value: String(lines))]
        if let level, !level.isEmpty { q.append(.init(name: "level", value: level)) }
        if let rid, !rid.isEmpty { q.append(.init(name: "rid", value: rid)) }
        let sentAt = Date()
        let (data, http) = try await send(makeRequest("v1/debug/logs", query: q))
        let receivedAt = Date()
        let body: DebugLogsResponse = try decode(data)
        return DebugLogsFetch(response: body, dateHeader: http.value(forHTTPHeaderField: "Date"),
                              sentAt: sentAt, receivedAt: receivedAt)
    }

    /// Last stored API compatibility reports.
    func compatReports() async throws -> CompatReports { try await get("v1/compat") }

    /// Probe Radarr/Sonarr now and compare with the previous run.
    func runCompatCheck() async throws -> CompatReports { try await send("v1/compat/check", method: "POST") }

    func options() async throws -> OptionsResponse { try await get("v1/options") }

    func saveDefaults(_ d: Defaults) async throws -> Defaults { try await send("v1/defaults", method: "PUT", body: d) }

    /// `query` (2+ characters) also searches Radarr/Sonarr, so titles outside discovery can be found and added.
    func discover(_ type: MediaType, includeTracked: Bool, query: String? = nil) async throws -> DiscoverResponse {
        var q: [URLQueryItem] = [.init(name: "type", value: type.rawValue),
                                 .init(name: "include_tracked", value: includeTracked ? "true" : "false")]
        if let query, !query.isEmpty { q.append(.init(name: "q", value: query)) }
        return try await get("v1/discover", query: q)
    }

    func detail(_ type: MediaType, id: Int) async throws -> TitleDetail {
        try await get("v1/titles/\(type.rawValue)/\(id)")
    }

    /// Ranked similar titles from the discovery pool. `mix` includes the other media type (film <-> TV).
    func similar(_ type: MediaType, id: Int, mix: Bool, limit: Int = 20) async throws -> SimilarResponse {
        try await get("v1/titles/\(type.rawValue)/\(id)/similar",
                      query: [.init(name: "mix", value: mix ? "true" : "false"), .init(name: "limit", value: String(limit))])
    }

    struct RequestBody: Encodable {
        let type: String
        let id: Int
        let qualityProfileId: Int?
        let rootFolder: String?
        let seasonMonitor: String?
    }

    func request(_ type: MediaType, id: Int, override o: RequestOverride?, idempotencyKey: String) async throws -> RequestResult {
        try await send("v1/requests", method: "POST",
                       body: RequestBody(type: type.rawValue, id: id, qualityProfileId: o?.qualityProfileId,
                                         rootFolder: o?.rootFolder, seasonMonitor: o?.seasonMonitor),
                       headers: ["Idempotency-Key": idempotencyKey])
    }

    func retry(_ type: MediaType, id: Int) async throws -> RequestResult {
        try await send("v1/requests/\(type.rawValue)/\(id)/retry", method: "POST")
    }

    /// Returns nil when the gateway answers 304 Not Modified.
    func status(viewing: String, etag: String?) async throws -> (StatusSnapshot?, etag: String?, pollAfter: Double?) {
        var headers: [String: String] = [:]
        if let etag { headers["If-None-Match"] = etag }
        let req = try makeRequest("v1/status", query: [.init(name: "viewing", value: viewing)], headers: headers)
        let (data, http) = try await send(req)
        if http.statusCode == 304 {
            return (nil, etag, http.value(forHTTPHeaderField: "X-Poll-After").flatMap(Double.init))
        }
        let snap: StatusSnapshot = try decode(data)
        return (snap, http.value(forHTTPHeaderField: "ETag"), snap.nextPollSeconds)
    }

    func activity() async throws -> ActivityResponse { try await get("v1/activity", query: [.init(name: "viewing", value: "activity")]) }

    func requestPlan(_ type: MediaType) async throws -> RequestPlan {
        try await get("v1/request-plan", query: [.init(name: "type", value: type.rawValue)])
    }

    struct BatchItem: Encodable { let type: String; let id: Int }
    struct BatchBody: Encodable { let items: [BatchItem]; let movie: RequestOverride?; let series: RequestOverride? }

    /// Add several titles in one call. Each item succeeds or fails on its own.
    func requestBatch(_ cards: [MediaCard], movie: RequestOverride?, series: RequestOverride?) async throws -> BatchResult {
        try await send("v1/requests/batch", method: "POST",
                       body: BatchBody(items: cards.map { BatchItem(type: $0.type.rawValue, id: $0.id) },
                                       movie: movie?.isEmpty == false ? movie : nil,
                                       series: series?.isEmpty == false ? series : nil))
    }
}

private struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

/// Minimal Keychain wrapper for the gateway access token.
enum Keychain {
    private static let service = "Phonarr.gateway"

    static func set(_ value: String?, for account: String) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service,
                                   kSecAttrAccount as String: account]
        SecItemDelete(base as CFDictionary)
        guard let value, let data = value.data(using: .utf8) else { return }
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(add as CFDictionary, nil)
    }

    static func get(_ account: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
