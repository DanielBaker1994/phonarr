import Foundation
import Observation
import SwiftUI
import UIKit

@MainActor
@Observable
final class AppModel {
    // MARK: Pairing
    var gatewayURL: String = UserDefaults.standard.string(forKey: "gatewayURL") ?? ""
    private(set) var isPaired: Bool = false
    var pairingMessage: String?
    var onboardingComplete: Bool = UserDefaults.standard.bool(forKey: "onboardingComplete") {
        didSet { UserDefaults.standard.set(onboardingComplete, forKey: "onboardingComplete") }
    }
    private(set) var gatewayReachable = true
    let api: APIClient

    // MARK: Server state
    var config: GatewayConfig?
    var options: OptionsResponse?
    var services: [String: ServiceHealth] = [:]
    private(set) var statuses: [String: MediaStatus] = [:]
    private var inFlight: [String: String] = [:]   // key -> idempotency key
    /// key -> idempotency key of an add whose answer never arrived (connection lost mid-request).
    /// The next tap sends the same key, so the gateway can tell it's the same request (KB-01).
    private var unansweredAdds: [String: String] = [:]

    // MARK: Discovery
    var catalog: [MediaType: [MediaCard]] = [:]
    var genres: [MediaType: [String]] = [:]
    var facets: [MediaType: Facets] = [:]
    /// Per-tab filters; kept here so they survive navigation and tab switches.
    var filters: [MediaType: FilterState] = [:]
    var loading: Set<MediaType> = []
    var discoverError: [MediaType: String] = [:]
    var includeTracked: Bool = UserDefaults.standard.bool(forKey: "includeTracked") {
        didSet {
            UserDefaults.standard.set(includeTracked, forKey: "includeTracked")
            Task { await reloadDiscovery() }
        }
    }

    /// What one-tap Add uses per type (profile · folder · seasons), shown on details and the selection bar.
    var requestPlans: [MediaType: RequestPlan] = [:]
    /// Customize Request sheet target (one title from details or a poster menu, or a multi-select batch).
    var customizing: CustomizeTarget?

    // MARK: Activity
    var activity: [ActivityItem] = []
    var toast: String?

    // MARK: Polling
    private var pollTask: Task<Void, Never>?
    private var etag: String?
    private var activeViewers = 0
    private var isForeground = true

    init() {
        let token = Keychain.get("token")
        let url = URL(string: UserDefaults.standard.string(forKey: "gatewayURL") ?? "")
        api = APIClient(baseURL: url, token: token)
        isPaired = url != nil && !(token ?? "").isEmpty
        loadCachedCatalog()
    }

    // MARK: - Pairing / config

    func pair(url: String, token: String) async throws {
        var trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.contains("://") { trimmed = "http://" + trimmed }
        guard let parsed = URL(string: trimmed), parsed.host != nil else {
            throw APIError.transport("Enter the gateway address, e.g. http://192.168.1.20:8787")
        }
        api.baseURL = parsed
        api.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            config = try await api.config()
        } catch {
            api.baseURL = nil
            api.token = nil
            throw error
        }
        gatewayURL = parsed.absoluteString
        UserDefaults.standard.set(gatewayURL, forKey: "gatewayURL")
        Keychain.set(api.token, for: "token")
        isPaired = true
        pairingMessage = nil
        gatewayReachable = true
        AppLog.info("paired", ["host": parsed.host ?? "?"])
        // Already-configured gateway (e.g. reinstall): skip the setup steps.
        if config?.radarr.configured == true && config?.sonarr.configured == true { onboardingComplete = true }
    }

    /// The gateway rejected our token (it was rotated): go back to pairing, keep the address.
    func handleAuthFailure() {
        guard isPaired else { return }
        AppLog.warn("token_rejected")
        let setupDone = onboardingComplete
        unpair()
        onboardingComplete = setupDone
        pairingMessage = "The gateway no longer accepts the saved access token. Enter the current ARR_GATEWAY_TOKEN."
    }

    func isConfigured(_ type: MediaType) -> Bool {
        type == .movie ? (config?.radarr.configured ?? false) : (config?.sonarr.configured ?? false)
    }

    /// Show a section unless we know its server isn't connected (config not loaded yet = assume yes).
    func hasSection(_ type: MediaType) -> Bool { config == nil || isConfigured(type) }

    func removeConnection(_ service: String) async {
        do {
            config = try await api.removeConnection(service)
            AppLog.info("removed_connection", ["service": service])
            await refreshOptions()
            await reloadDiscovery()
        } catch { toast = error.localizedDescription }
    }

    var trackedCount: Int { statuses.values.filter { $0.state != .notRequested }.count }

    func unpair() {
        pollTask?.cancel()
        onboardingComplete = false
        Keychain.set(nil, for: "token")
        api.token = nil
        isPaired = false
        config = nil
        options = nil
        catalog = [:]
        statuses = [:]
    }

    var needsServiceSetup: Bool {
        guard let config else { return true }
        return !config.radarr.configured && !config.sonarr.configured
    }

    func refreshConfig() async {
        do { config = try await api.config() } catch { report(error) }
    }

    /// Central error handling for background loads: auth failures re-pair, offline flips the banner.
    private func report(_ error: Error, toast show: Bool = true) {
        switch error {
        case APIError.unauthorized: handleAuthFailure()
        case APIError.transport: gatewayReachable = false
        default: if show { toast = error.localizedDescription }
        }
    }

    func refreshOptions() async {
        do { options = try await api.options() } catch { report(error) }
    }

    func saveDefaults(_ d: Defaults) async {
        do {
            let saved = try await api.saveDefaults(d)
            if let o = options { options = OptionsResponse(defaults: saved, radarr: o.radarr, sonarr: o.sonarr) }
            await refreshPlans()
        } catch { toast = error.localizedDescription }
    }

    func refreshPlans() async {
        for type in MediaType.allCases where isConfigured(type) {
            if let p = try? await api.requestPlan(type) { requestPlans[type] = p }
        }
    }

    // MARK: - Discovery

    func reloadDiscovery() async {
        async let m: Void = loadDiscover(.movie)
        async let s: Void = loadDiscover(.series)
        _ = await (m, s)
    }

    func loadDiscover(_ type: MediaType) async {
        loading.insert(type)
        defer { loading.remove(type) }
        do {
            let r = try await api.discover(type, includeTracked: includeTracked)
            catalog[type] = r.items
            genres[type] = r.genres
            facets[type] = r.facets ?? Facets()
            discoverError[type] = r.error?.message
            if let plan = r.requestPlan { requestPlans[type] = plan }
            gatewayReachable = true
            for item in r.items where statuses[item.key] == nil { statuses[item.key] = item.status }
            saveCachedCatalog()
        } catch {
            discoverError[type] = error.localizedDescription
            report(error, toast: false)
        }
    }

    /// Radarr/Sonarr title search for anything not already in discovery (2+ characters).
    func search(_ type: MediaType, query: String) async -> (items: [MediaCard], error: String?) {
        do {
            let r = try await api.discover(type, includeTracked: true, query: query)
            let found = r.items.filter { $0.source == DiscoverySource.search.rawValue }
            for item in found where statuses[item.key] == nil { statuses[item.key] = item.status }
            return (found, r.error?.message)
        } catch is CancellationError {
            return ([], nil)   // typing started a newer search, which answers instead (KB-05)
        } catch {
            report(error, toast: false)
            return ([], error.localizedDescription)
        }
    }

    func status(_ type: MediaType, _ id: Int, fallback: MediaStatus = .idle) -> MediaStatus {
        let key = "\(type.rawValue):\(id)"
        if inFlight[key] != nil { return .submitting }
        return statuses[key] ?? fallback
    }

    func status(for card: MediaCard) -> MediaStatus { status(card.type, card.id, fallback: card.status) }

    func isServiceDown(_ type: MediaType) -> Bool {
        services[type == .movie ? "radarr" : "sonarr"]?.reachable == false
    }

    // MARK: - Requests

    /// One-tap add with the saved defaults. `override` is one-off and never saved as a default.
    func add(_ type: MediaType, id: Int, override: RequestOverride? = nil) async {
        let key = "\(type.rawValue):\(id)"
        let done = AppLog.enter(["key": key]); defer { done() }   // many exits: log entry and one exit
        guard inFlight[key] == nil else { return }   // repeated taps
        let current = status(type, id)
        if current.state == .failed {
            await retry(type, id: id)
            return
        }
        guard current.state == .notRequested else { return }
        let before = statuses[key]
        let idem = unansweredAdds[key] ?? UUID().uuidString
        inFlight[key] = idem
        defer { inFlight[key] = nil }
        do {
            AppLog.info("request", ["key": key, "profile": override?.qualityProfileId.map(String.init) ?? "default",
                                   "root": override?.rootFolder ?? "default", "seasons": override?.seasonMonitor ?? "default"])
            let r = try await api.request(type, id: id, override: override, idempotencyKey: idem)
            unansweredAdds[key] = nil
            statuses[key] = r.status
            AppLog.info("request_result", ["key": key, "state": r.status.state.rawValue, "dedup": r.deduplicated ? " (already tracked)" : ""])
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            toast = r.deduplicated ? "Already in your library" : "Added to library"
        } catch {
            AppLog.error("request_failed", ["key": key, "error": error.localizedDescription])
            if case APIError.transport(_) = error {
                // We can't tell whether the gateway got it, so don't claim Failed: put the title back as it
                // was, keep the key for the next tap, and let the poll below show what really happened.
                unansweredAdds[key] = idem
                statuses[key] = before
                toast = AppConfig.text("errors.add_unreached")
                UINotificationFeedbackGenerator().notificationOccurred(.warning)
            } else {
                unansweredAdds[key] = nil
                statuses[key] = MediaStatus(state: .failed, message: error.localizedDescription,
                                            errorKind: (error as? APIError)?.kind)
                UINotificationFeedbackGenerator().notificationOccurred(.error)
                if case APIError.unauthorized = error { handleAuthFailure() }
            }
        }
        refreshNow()
    }

    /// Multi-select: add every not-yet-requested card in one call, no popup per title.
    /// Failed cards in the selection are retried instead. Returns the keys that failed.
    @discardableResult
    func addBatch(_ cards: [MediaCard], movie: RequestOverride? = nil, series: RequestOverride? = nil) async -> Set<String> {
        let done = AppLog.enter(["count": cards.count]); defer { done() }
        let fresh = cards.filter { inFlight[$0.key] == nil && status(for: $0).state == .notRequested }
        let failed = cards.filter { inFlight[$0.key] == nil && status(for: $0).state == .failed }
        var failedKeys: Set<String> = []
        if !fresh.isEmpty {
            for c in fresh { inFlight[c.key] = "batch" }
            defer { for c in fresh { inFlight[c.key] = nil } }
            AppLog.info("batch", ["count": fresh.count])
            do {
                let r = try await api.requestBatch(fresh, movie: movie, series: series)
                for item in r.results {
                    let key = "\(item.type.rawValue):\(item.id)"
                    if let st = item.status, item.ok { statuses[key] = st }
                    if !item.ok {
                        failedKeys.insert(key)
                        statuses[key] = MediaStatus(state: .failed, message: item.error?.message, errorKind: item.error?.kind)
                    }
                }
                var parts = ["Added \(r.added)"]
                if r.already > 0 { parts.append("\(r.already) already in library") }
                if r.failed > 0 { parts.append("\(r.failed) failed") }
                toast = parts.joined(separator: " · ")
                UINotificationFeedbackGenerator().notificationOccurred(r.failed == 0 ? .success : .warning)
            } catch {
                AppLog.error("batch_failed", ["error": error.localizedDescription])
                toast = error.localizedDescription
                failedKeys.formUnion(fresh.map(\.key))
                if case APIError.unauthorized = error { handleAuthFailure() }
            }
        }
        for c in failed { await retry(c.type, id: c.id) }
        refreshNow()
        return failedKeys
    }

    func retry(_ type: MediaType, id: Int) async {
        let key = "\(type.rawValue):\(id)"
        let done = AppLog.enter(["key": key]); defer { done() }
        guard inFlight[key] == nil else { return }
        inFlight[key] = UUID().uuidString
        defer { inFlight[key] = nil }
        do {
            AppLog.info("retry", ["key": key])
            let r = try await api.retry(type, id: id)
            statuses[key] = r.status
            toast = "Retrying"
        } catch {
            AppLog.error("retry_failed", ["key": key, "error": error.localizedDescription])
            statuses[key] = MediaStatus(state: .failed, message: error.localizedDescription,
                                        errorKind: (error as? APIError)?.kind)
        }
        refreshNow()
    }

    // MARK: - Activity

    func loadActivity() async {
        do {
            let r = try await api.activity()
            activity = r.items
            services = r.services
            for item in r.items where inFlight[item.key] == nil { statuses[item.key] = item.status }
        } catch {
            report(error)
        }
    }

    // MARK: - Polling (cadence is decided by the gateway via next_poll_seconds)

    func beginWatchingActivity() {
        activeViewers += 1
        if activeViewers == 1 { refreshNow() }
    }

    func endWatchingActivity() { activeViewers = max(0, activeViewers - 1) }

    func scenePhaseChanged(_ phase: ScenePhase) {
        AppLog.info("scene_phase", ["phase": String(describing: phase)])
        switch phase {
        case .active:
            isForeground = true
            refreshNow()
        case .background:
            isForeground = false
            pollTask?.cancel()   // suspend polling entirely while backgrounded
        default:
            break
        }
    }

    func refreshNow() {
        guard isPaired, isForeground else { return }
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let wait = await self.pollOnce()
                try? await Task.sleep(for: .seconds(wait))
            }
        }
    }

    private func pollOnce() async -> Double {
        let viewing = activeViewers > 0 ? "activity" : "browse"
        do {
            let (snap, newTag, after) = try await api.status(viewing: viewing, etag: etag)
            if !gatewayReachable { AppLog.info("gateway_back") }
            gatewayReachable = true
            if let snap {
                etag = newTag
                services = snap.services
                var merged = statuses
                for (k, v) in snap.items { merged[k] = v }
                // Items no longer tracked by the gateway revert to "not requested".
                for k in merged.keys where snap.items[k] == nil && merged[k]?.state != .notRequested
                    && merged[k]?.state != .failed {
                    merged[k] = .idle
                }
                statuses = merged
                if activeViewers > 0 { await loadActivity() }
            }
            return min(max(after ?? 30, 5), 300)
        } catch is CancellationError {
            return 30
        } catch {
            report(error, toast: false)
            return gatewayReachable ? 30 : 15
        }
    }

    // MARK: - Local cache for fast startup

    private var cacheURL: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("catalog.json")
    }

    private struct CachedCatalog: Codable { let movies: [MediaCard]; let series: [MediaCard] }

    private func saveCachedCatalog() {
        let c = CachedCatalog(movies: catalog[.movie] ?? [], series: catalog[.series] ?? [])
        if let data = try? JSONEncoder().encode(c) { try? data.write(to: cacheURL, options: .atomic) }
    }

    private func loadCachedCatalog() {
        guard let data = try? Data(contentsOf: cacheURL),
              let c = try? JSONDecoder().decode(CachedCatalog.self, from: data) else { return }
        catalog[.movie] = c.movies
        catalog[.series] = c.series
        for item in c.movies + c.series { statuses[item.key] = item.status }
    }
}

/// Who the Customize Request sheet is for.
struct CustomizeTarget: Identifiable {
    let id = UUID()
    let cards: [MediaCard]
    /// Called after the request is sent (e.g. to leave selection mode).
    var onSent: (() -> Void)? = nil
    var types: [MediaType] { MediaType.allCases.filter { t in cards.contains { $0.type == t } } }
}
