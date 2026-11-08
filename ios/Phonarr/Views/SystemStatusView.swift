import SwiftUI

/// Settings → System status. Mirrors the web app's Settings → System status (web/js/views/status.js).
/// Reads GET /v1/health (gateway up, version, uptime, Radarr/Sonarr reachable) and refreshes every 15 s while open.
struct SystemStatusView: View {
    @Environment(AppModel.self) private var model
    @State private var health: HealthResponse?
    @State private var error: String?
    @State private var millis: Int?

    var body: some View {
        List {
            Section {
                Label(headline, systemImage: headlineIcon)
                    .font(.headline)
                    .foregroundStyle(headlineColor)
                if let error {
                    Text(error).font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let h = health {
                Section {
                    LabeledContent("Answering", value: millis.map { "\($0) ms" } ?? "–")
                    LabeledContent("Version", value: h.version ?? "–")
                    LabeledContent("Up for", value: Self.duration(h.uptimeS))
                    LabeledContent("Status last refreshed",
                                   value: h.lastStatusRefreshSAgo.map { "\(Self.duration($0)) ago" } ?? "not yet")
                    LabeledContent("Polling Radarr/Sonarr", value: Self.pollingText(h))
                    if let poller = h.poller, poller != "running" {
                        LabeledContent("Background poller") {
                            Text(poller == "stopped" ? "stopped (see the gateway log)" : "off (POLLER=0)")
                                .foregroundStyle(poller == "stopped" ? Color.red : Color.secondary)
                        }
                    }
                    if let ago = h.lastPollErrorSAgo {
                        LabeledContent("Last poll error") {
                            Text("\(Self.duration(ago)) ago").foregroundStyle(Color.orange)
                        }
                    }
                    LabeledContent("Web app", value: h.webFrontend == true ? "served" : "not served")
                } header: {
                    Text("Gateway")
                } footer: {
                    Text("Docker marks the container healthy when this answers. Radarr or Sonarr being down shows here as degraded; it never restarts the gateway.")
                }
                ForEach(["radarr", "sonarr"], id: \.self) { svc in
                    serviceSection(svc, h.services?[svc])
                }
            }
            Section("This app") {
                LabeledContent("Version", value: Self.appVersion)
            }
        }
        .navigationTitle("System status")
        .refreshable { await load() }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    @ViewBuilder private func serviceSection(_ svc: String, _ s: HealthResponse.Service?) -> some View {
        let cfg = svc == "radarr" ? model.config?.radarr : model.config?.sonarr
        Section(svc.capitalized) {
            LabeledContent("State") {
                Text(stateText(s)).foregroundStyle(stateColor(s))
            }
            if let v = s?.version { LabeledContent("Version", value: v) }
            if let down = s?.downForS {
                LabeledContent("Down for") { Text(Self.duration(down)).foregroundStyle(Color.red) }
            }
            if let checked = s?.checkedSAgo { LabeledContent("Last checked", value: "\(Self.duration(checked)) ago") }
            if let url = cfg?.baseUrl { LabeledContent("Address", value: url) }
            if let message = model.services[svc]?.error?.message ?? cfg?.error?.message {
                Text(message).font(.footnote).foregroundStyle(.red)
            }
        }
    }

    private static var appVersion: String {
        let info = Bundle.main.infoDictionary ?? [:]
        return "\(info["CFBundleShortVersionString"] as? String ?? "?") (build \(info["CFBundleVersion"] as? String ?? "?"), \(info["AYRGitCommit"] as? String ?? "?"))"
    }

    private static func pollingText(_ h: HealthResponse) -> String {
        if let p = h.pollInterval { return "every \(Int(p)) s" }
        if let idle = h.idleHealthCheckS { return "idle: reachability check every \(duration(idle))" }
        return "paused"
    }

    private func load() async {
        let started = Date()
        do {
            let h = try await model.api.health()
            health = h
            millis = Int(Date().timeIntervalSince(started) * 1000)
            error = nil
        } catch {
            health = nil
            self.error = error.localizedDescription
        }
        await model.refreshConfig()
    }

    private var headline: String {
        if error != nil { return "Gateway unreachable" }
        switch health?.status {
        case "ok": return "Everything is up"
        case "degraded": return "Gateway is up, a server is unreachable"
        case "setup": return "Gateway is up, no server connected yet"
        default: return "Checking…"
        }
    }

    private var headlineIcon: String {
        if error != nil { return "xmark.octagon.fill" }
        switch health?.status {
        case "ok": return "checkmark.circle.fill"
        case "degraded": return "exclamationmark.triangle.fill"
        default: return "info.circle.fill"
        }
    }

    private var headlineColor: Color {
        if error != nil { return Color.red }
        switch health?.status {
        case "ok": return Color.green
        case "degraded": return Color.orange
        default: return Color.secondary
        }
    }

    private func stateText(_ s: HealthResponse.Service?) -> String {
        guard let s, s.configured else { return "Not connected" }
        if s.reachable == false { return "Unreachable" }
        return s.reachable == true ? "Reachable" : "Not checked yet"
    }

    private func stateColor(_ s: HealthResponse.Service?) -> Color {
        guard let s, s.configured else { return Color.secondary }
        if s.reachable == false { return Color.red }
        return s.reachable == true ? Color.green : Color.secondary
    }

    static func duration(_ seconds: Double?) -> String {
        guard let s = seconds else { return "–" }
        if s < 90 { return "\(Int(s)) s" }
        if s < 5400 { return "\(Int((s / 60).rounded())) min" }
        if s < 172_800 { return "\(Int((s / 3600).rounded())) h" }
        return "\(Int((s / 86400).rounded())) days"
    }
}
