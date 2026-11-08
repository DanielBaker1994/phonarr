import SwiftUI

/// Settings → API compatibility. "Test API" makes the gateway probe every Radarr/Sonarr endpoint
/// and field the app relies on, compare with the server's API spec, and report what changed
/// since the last check (for example after a Radarr or Sonarr update).
struct CompatView: View {
    @Environment(AppModel.self) private var model
    @State private var reports: CompatReports?
    @State private var running = false
    @State private var error: String?

    var body: some View {
        List {
            Section {
                Button {
                    Task { await run() }
                } label: {
                    HStack {
                        Label("Test API", systemImage: "stethoscope")
                        if running { Spacer(); ProgressView() }
                    }
                }
                .disabled(running)
            } footer: {
                Text("Checks every Radarr and Sonarr endpoint and field this app uses. Read-only: nothing is added or changed. It also runs automatically once a day and whenever a server is connected.")
            }
            if let error { Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) } }
            if let r = reports?.radarr { ReportSection(report: r) }
            if let r = reports?.sonarr { ReportSection(report: r) }
            if reports != nil && reports?.radarr == nil && reports?.sonarr == nil {
                Text("No checks yet. Tap Test API.").foregroundStyle(.secondary)
            }
        }
        .navigationTitle("API compatibility")
        .task { await load() }
        .refreshable { await run() }
    }

    private func load() async {
        do { reports = try await model.api.compatReports() } catch { self.error = error.localizedDescription }
    }

    private func run() async {
        running = true
        defer { running = false }
        AppLog.info("compat_run")
        do {
            reports = try await model.api.runCompatCheck()
            error = nil
            for r in [reports?.radarr, reports?.sonarr].compactMap({ $0 }) {
                AppLog.info("compat_result", ["service": r.service, "version": r.version ?? "?", "status": r.status.rawValue, "changes": r.changes.count])
            }
            await model.refreshConfig()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct ReportSection: View {
    let report: CompatReport

    var body: some View {
        Section {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: report.status.systemImage).foregroundStyle(report.status.tint).font(.title3)
                VStack(alignment: .leading, spacing: 3) {
                    Text(report.status.label).font(.headline)
                    Text("\(report.service.capitalized) \(report.version ?? "unreachable") · checked \(Date(timeIntervalSince1970: report.checkedAt).formatted(.relative(presentation: .named)))")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if !report.changes.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Changed since last check", systemImage: "arrow.triangle.2.circlepath").font(.subheadline.weight(.semibold))
                    ForEach(report.changes, id: \.self) { Text("• \($0)").font(.caption) }
                }
                .padding(.vertical, 4)
                .listRowBackground(Color.orange.opacity(0.12))
            } else if report.previousCheckedAt != nil {
                Label("No changes since the last check", systemImage: "equal.circle").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(report.features) { feature in
                DisclosureGroup {
                    ForEach(feature.checks) { check in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Image(systemName: check.status.systemImage).foregroundStyle(check.status.tint)
                                Text(check.name).font(.subheadline)
                                Spacer()
                                Text("\(check.method) \(check.path.replacingOccurrences(of: "/api/v3", with: ""))")
                                    .font(.caption2.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                            }
                            ForEach(check.details, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
                        }
                        .padding(.vertical, 2)
                    }
                } label: {
                    HStack {
                        Image(systemName: feature.status.systemImage).foregroundStyle(feature.status.tint)
                        Text(feature.feature)
                    }
                }
            }
            if let note = report.specNote {
                Text(note + (report.specSource.map { " Source: \($0)." } ?? "")).font(.caption2).foregroundStyle(.secondary)
            }
        } header: {
            Text(report.service.capitalized)
        }
    }
}

extension CompatLevel {
    var tint: Color {
        switch self {
        case .ok: .green
        case .info: .blue
        case .warning: .orange
        case .error: .red
        }
    }
}
