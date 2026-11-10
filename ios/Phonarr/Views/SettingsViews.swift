import SwiftUI
import UIKit

// MARK: - First launch

struct OnboardingView: View {
    var body: some View {
        NavigationStack { GatewayPairingView() }
    }
}

/// First-run setup: one screen for Radarr, one for Sonarr. Either can be skipped,
/// so a household with only films or only TV is never blocked.
struct SetupFlowView: View {
    @Environment(AppModel.self) private var model
    @State private var step = 0

    var body: some View {
        NavigationStack {
            Group {
                switch step {
                case 0: setupStep(service: "radarr", what: "films")
                case 1: setupStep(service: "sonarr", what: "TV shows")
                default: summary
                }
            }
            .animation(.default, value: step)
        }
        .task { await model.refreshConfig() }
    }

    private func setupStep(service: String, what: String) -> some View {
        let cfg = service == "radarr" ? model.config?.radarr : model.config?.sonarr
        return ServiceConnectionView(service: service, existing: cfg, intro: "Step \(step + 1) of 2 · Connect \(service.capitalized) to discover and add \(what).") {
            step += 1
        }
        .id(service)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(cfg?.configured == true ? "Next" : "Skip") { step += 1 }
            }
        }
    }

    private var summary: some View {
        Form {
            Section {
                ForEach(["radarr", "sonarr"], id: \.self) { svc in
                    let on = svc == "radarr" ? model.config?.radarr.configured == true : model.config?.sonarr.configured == true
                    Label(svc == "radarr" ? "Radarr · Films" : "Sonarr · TV Shows",
                          systemImage: on ? "checkmark.circle.fill" : "minus.circle")
                        .foregroundStyle(on ? Color.green : Color.secondary)
                }
            } footer: {
                Text(model.needsServiceSetup
                     ? "Nothing is connected yet. You can still look around and connect a server later in Settings."
                     : "You can connect or change servers any time in Settings.")
            }
            // How one-tap Add works, chosen once here so the first + doesn't stop to ask.
            DefaultsSection()
            Section {
                Button("Start browsing") { model.onboardingComplete = true }
                    .font(.headline)
                Button("Back") { step = 0 }
            }
        }
        .navigationTitle("You're set")
        .task { await model.refreshOptions() }
    }
}

struct GatewayPairingView: View {
    @Environment(AppModel.self) private var model
    @State private var url = ""
    @State private var token = ""
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Image(systemName: "popcorn.fill").font(.largeTitle).foregroundStyle(.tint)
                    Text("Phonarr").font(.largeTitle.bold())
                    Text("Discover films and TV and add them to Radarr and Sonarr in one tap.")
                        .foregroundStyle(.secondary)
                }
                .listRowBackground(Color.clear)
            }
            Section {
                TextField("http://192.168.1.20:8787", text: $url)
                    .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Access token", text: $token)
            } header: {
                Text("Gateway")
            } footer: {
                Text("The address of the Phonarr container and the ARR_GATEWAY_TOKEN you set for it. LAN IPs and Tailscale names (*.ts.net) both work.")
            }
            if let msg = error ?? model.pairingMessage {
                Section {
                    Label(msg, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    ShareLink(item: AppLog.current) { Label("Share app log", systemImage: "square.and.arrow.up") }
                        .font(.subheadline)
                }
            }
            Section {
                Button {
                    Task {
                        busy = true
                        defer { busy = false }
                        do { try await model.pair(url: url, token: token); error = nil }
                        catch {
                            AppLog.error("pairing_failed", ["error": error.localizedDescription])
                            self.error = error.localizedDescription
                        }
                    }
                } label: {
                    HStack { Text("Connect"); if busy { Spacer(); ProgressView() } }
                }
                .disabled(url.isEmpty || token.isEmpty || busy)
            }
        }
        .onAppear { if url.isEmpty { url = model.gatewayURL } }
    }
}

// MARK: - Settings tab

struct SettingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NavigationLink { SystemStatusView() } label: {
                        Label("System status", systemImage: "waveform.path.ecg")
                    }
                } footer: {
                    Text("Is the gateway up, and can it reach Radarr and Sonarr? The same check Docker's healthcheck uses.")
                }
                ServiceRows()
                DefaultsSection()
                Section {
                    Toggle("Show titles already in library", isOn: Binding(get: { model.includeTracked },
                                                                           set: { model.includeTracked = $0 }))
                } header: {
                    Text("Discovery")
                } footer: {
                    Text("Discovery shows your Radarr import lists, Radarr's recommendations and the titles list on the gateway (titles.json). Search in Movies or TV Shows finds anything else Radarr or Sonarr can look up.")
                }
                Section {
                    NavigationLink { CompatView() } label: {
                        HStack {
                            Label("API compatibility", systemImage: "stethoscope")
                            Spacer()
                            CompatBadge(config: model.config)
                        }
                    }
                } footer: {
                    Text("Test API checks that your Radarr and Sonarr still offer everything this app uses, and shows what changed after an update.")
                }
                DebugSection()
                Section("Gateway") {
                    LabeledContent("Address", value: model.gatewayURL)
                    Button("Disconnect", role: .destructive) { model.unpair() }
                }
            }
            .navigationTitle("Settings")
            .task {
                await model.refreshConfig()
                await model.refreshOptions()
            }
            .refreshable {
                await model.refreshConfig()
                await model.refreshOptions()
            }
        }
    }
}

struct ServiceRows: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Section("Servers") {
            ForEach(["radarr", "sonarr"], id: \.self) { service in
                let cfg = service == "radarr" ? model.config?.radarr : model.config?.sonarr
                NavigationLink {
                    ServiceConnectionView(service: service, existing: cfg)
                } label: {
                    HStack {
                        Image(systemName: service == "radarr" ? "film" : "tv")
                            .frame(width: 24).foregroundStyle(.tint)
                        VStack(alignment: .leading) {
                            Text(service.capitalized)
                            Text(cfg?.baseUrl ?? "Not configured").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                        ConnectionDot(cfg: cfg, health: model.services[service])
                    }
                }
            }
        }
    }
}

struct ConnectionDot: View {
    let cfg: ServiceConfig?
    let health: ServiceHealth?

    private var color: Color {
        guard cfg?.configured == true else { return .gray }
        return health?.reachable == false || cfg?.reachable == false ? .red : .green
    }

    private var text: String {
        guard cfg?.configured == true else { return "Not set up" }
        if health?.reachable == false || cfg?.reachable == false { return "Unreachable" }
        return cfg?.version.map { "v\($0)" } ?? "Connected"
    }

    var body: some View {
        HStack(spacing: 6) {
            Text(text).font(.caption).foregroundStyle(.secondary)
            Circle().fill(color).frame(width: 8, height: 8)
        }
    }
}

struct ServiceConnectionView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let service: String
    let existing: ServiceConfig?
    var intro: String? = nil
    var onSaved: (() -> Void)? = nil

    @State private var url = ""
    @State private var confirmRemove = false
    @State private var apiKey = ""
    @State private var busy = false
    @State private var result: Result<ConnectionTest, Error>?

    private var name: String { service.capitalized }
    private var placeholder: String { service == "radarr" ? "http://radarr:7878" : "http://sonarr:8989" }

    var body: some View {
        Form {
            if let intro {
                Section { Text(intro).font(.subheadline).foregroundStyle(.secondary) }
            }
            Section {
                TextField(placeholder, text: $url)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField(existing?.apiKeySet == true ? "API key (saved, leave blank to keep)" : "API key", text: $apiKey)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: {
                Text("\(name) connection")
            } footer: {
                Text("Use the address the gateway container can reach: a Docker service name, LAN IP or *.ts.net host, including any URL base (e.g. https://host.ts.net/\(service)). The API key is in \(name) → Settings → General. It is stored on the gateway, never on this phone.")
            }
            Section {
                Button { Task { await run(save: false) } } label: {
                    HStack { Text("Test Connection"); if busy { Spacer(); ProgressView() } }
                }
                .disabled(url.isEmpty || busy)
                Button(onSaved == nil ? "Save" : "Save and continue") { Task { await run(save: true) } }
                    .disabled(url.isEmpty || busy || (apiKey.isEmpty && existing?.apiKeySet != true))
            }
            if onSaved == nil, existing?.configured == true {
                Section {
                    Button("Remove \(name) connection", role: .destructive) { confirmRemove = true }
                } footer: {
                    Text("The app keeps working with the other server. Titles already in \(name) are not touched.")
                }
            }
            if let result {
                Section {
                    switch result {
                    case .success(let t):
                        Label("Connected to \(t.appName ?? name) \(t.version ?? "") · \(t.qualityProfiles ?? 0) quality profiles",
                              systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case .failure(let e):
                        Label(e.localizedDescription, systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                    }
                }
            }
        }
        .navigationTitle(name)
        .onAppear { if url.isEmpty { url = existing?.baseUrl ?? "" } }
        .confirmationDialog("Remove the \(name) connection?", isPresented: $confirmRemove) {
            Button("Remove", role: .destructive) {
                Task { await model.removeConnection(service); dismiss() }
            }
        }
    }

    private func run(save: Bool) async {
        busy = true
        defer { busy = false }
        let key = apiKey.isEmpty ? nil : apiKey
        do {
            let t = save ? try await model.api.saveConnection(service, url: url, apiKey: key)
                         : try await model.api.testConnection(service, url: url, apiKey: key)
            result = .success(t)
            AppLog.info("connection_done", ["verb": save ? "Saved" : "Tested", "service": service, "app": t.appName ?? "?", "version": t.version ?? ""])
            if save {
                apiKey = ""
                await model.refreshConfig()
                await model.refreshOptions()
                await model.reloadDiscovery()
                if let onSaved { onSaved() } else { dismiss() }
            }
        } catch {
            AppLog.warn("connection_failed", ["service": service, "verb": save ? "save" : "test", "error": error.localizedDescription])
            result = .failure(error)
        }
    }
}

/// Defaults on their own screen, linked from "Change" wherever a request summary is shown.
struct DefaultsView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        Form { DefaultsSection() }
            .navigationTitle("Request defaults")
            .task { await model.refreshOptions() }
    }
}

struct DefaultsSection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let opts = model.options {
            Section {
                defaultsPickers(opts)
            } header: {
                Text("Request defaults")
            } footer: {
                Text("Every Add (the + on a poster, Add to Library, and Add selected) uses these. Customize Request changes them for one request or one batch only; these stay the same.")
            }
        }
    }

    @ViewBuilder private func defaultsPickers(_ opts: OptionsResponse) -> some View {
        let d = opts.defaults
        if let err = opts.radarr.error, model.config?.radarr.configured == true {
            Label("Radarr: \(err.message)", systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.orange)
        }
        profilePicker("Radarr quality", opts.radarr.qualityProfiles ?? [], d.radarrProfileId) { var n = d; n.radarrProfileId = $0; return n }
        rootPicker("Radarr folder", opts.radarr.rootFolders ?? [], d.radarrRoot) { var n = d; n.radarrRoot = $0; return n }
        if let err = opts.sonarr.error, model.config?.sonarr.configured == true {
            Label("Sonarr: \(err.message)", systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.orange)
        }
        profilePicker("Sonarr quality", opts.sonarr.qualityProfiles ?? [], d.sonarrProfileId) { var n = d; n.sonarrProfileId = $0; return n }
        rootPicker("Sonarr folder", opts.sonarr.rootFolders ?? [], d.sonarrRoot) { var n = d; n.sonarrRoot = $0; return n }
        if model.config?.sonarr.configured == true {
            Picker("TV seasons", selection: Binding(get: { d.seasonMonitor }, set: { v in
                var n = d; n.seasonMonitor = v; Task { await model.saveDefaults(n) }
            })) {
                ForEach(Defaults.seasonModes, id: \.self) { Text(Defaults.seasonLabel($0)).tag($0) }
            }
        }
    }

    @ViewBuilder private func profilePicker(_ label: String, _ profiles: [QualityProfile], _ current: Int?,
                                            _ apply: @escaping (Int?) -> Defaults) -> some View {
        if profiles.count == 1 {
            LabeledContent(label, value: profiles[0].name)   // nothing to choose
        } else if profiles.count > 1 {
            Picker(label, selection: Binding(get: { current }, set: { v in Task { await model.saveDefaults(apply(v)) } })) {
                if current == nil { Text("Choose…").tag(Int?.none) }
                ForEach(profiles) { Text($0.name).tag(Int?.some($0.id)) }
            }
        }
    }

    @ViewBuilder private func rootPicker(_ label: String, _ roots: [RootFolder], _ current: String?,
                                         _ apply: @escaping (String?) -> Defaults) -> some View {
        if roots.count == 1 {
            LabeledContent(label, value: roots[0].path)
        } else if roots.count > 1 {
            Picker(label, selection: Binding(get: { current }, set: { v in Task { await model.saveDefaults(apply(v)) } })) {
                if current == nil { Text("Choose…").tag(String?.none) }
                ForEach(roots) { Text($0.path).tag(String?.some($0.path)) }
            }
        }
    }
}

// MARK: - Debug report

struct DebugSection: View {
    @Environment(AppModel.self) private var model
    @State private var building = false
    @State private var report: ShareableFile?
    @State private var error: String?

    var body: some View {
        Section {
            NavigationLink { LogsView() } label: {
                Label(AppConfig.text("logs.settings_row"), systemImage: "doc.text.magnifyingglass")
            }
            Button {
                Task {
                    building = true
                    defer { building = false }
                    do {
                        AppLog.info("debug_report")
                        report = ShareableFile(url: try await DebugReport.build(model: model))
                        error = nil
                    } catch {
                        self.error = error.localizedDescription
                    }
                }
            } label: {
                HStack {
                    Label("Share debug report", systemImage: "ladybug")
                    if building { Spacer(); ProgressView() }
                }
            }
            .disabled(building)
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
        } header: {
            Text("Troubleshooting")
        } footer: {
            Text("One zip with this app's log, device info and the gateway's logs and status. API keys and the access token are removed. Send it by AirDrop, Mail or Messages, or save it to Files.")
        }
        .sheet(item: $report) { file in ActivitySheet(items: [file.url]).ignoresSafeArea() }
    }
}

struct ShareableFile: Identifiable {
    let url: URL
    var id: String { url.path }
}

struct ActivitySheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// Worst API-check result across both servers, shown next to the Settings row.
struct CompatBadge: View {
    let config: GatewayConfig?

    var body: some View {
        let checks = [config?.radarr.apiCheck, config?.sonarr.apiCheck].compactMap { $0 }
        let order = ["ok": 0, "info": 1, "warning": 2, "error": 3]
        if let worst = checks.max(by: { (order[$0.status] ?? 0) < (order[$1.status] ?? 0) }) {
            let level = CompatLevel(rawValue: worst.status) ?? .warning
            let changes = checks.reduce(0) { $0 + $1.changes }
            HStack(spacing: 4) {
                if changes > 0 { Text("\(changes) changed").font(.caption).foregroundStyle(.secondary) }
                Image(systemName: level.systemImage).foregroundStyle(level.tint)
            }
        } else {
            Text("Not run").font(.caption).foregroundStyle(.secondary)
        }
    }
}
