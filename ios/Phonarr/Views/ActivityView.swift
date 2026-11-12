import SwiftUI

struct ActivityView: View {
    @Environment(AppModel.self) private var model
    @State private var loaded = false
    /// Downloads | Calendar, remembered per device.
    @AppStorage("activitySegment") private var segment = "downloads"

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Activity", selection: $segment) {
                    Text(AppConfig.text("calendar.downloads")).tag("downloads")
                    Text(AppConfig.text("calendar.title")).tag("calendar")
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.vertical, 8)
                if segment == "calendar" {
                    CalendarView()
                } else {
                    downloads
                }
            }
            .navigationTitle("Activity")
            .navigationDestination(for: ActivityItem.self) { TitleDetailView(type: $0.type, id: $0.id) }
            .navigationDestination(for: MediaCard.self) { TitleDetailView(type: $0.type, id: $0.id, seed: $0) }
            .task {
                await model.loadActivity()
                loaded = true
            }
            .onAppear { model.beginWatchingActivity() }
            .onDisappear { model.endWatchingActivity() }
        }
    }

    private var downloads: some View {
        List {
            ServiceBanner().listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
            let failed = model.activity.filter { live($0).state == .failed }
            let active = model.activity.filter { live($0).state.isDownloading }
            let done = model.activity.filter { live($0).state == .complete }
            let waiting = model.activity.filter {
                let s = live($0).state
                return s != .failed && s != .complete && !s.isDownloading
            }
            if !failed.isEmpty { Section("Needs attention") { rows(failed) } }
            if !active.isEmpty { Section("In progress") { rows(active) } }
            if !done.isEmpty { Section("Recently added") { rows(done) } }
            if !waiting.isEmpty { Section("Waiting") { rows(waiting) } }
        }
        .overlay {
            if loaded && model.activity.isEmpty {
                ContentUnavailableView("No activity", systemImage: "arrow.down.circle",
                                       description: Text("Requests and downloads show up here."))
            } else if !loaded {
                ProgressView()
            }
        }
        .refreshable { await model.loadActivity() }
    }

    private func live(_ item: ActivityItem) -> MediaStatus { model.status(item.type, item.id, fallback: item.status) }

    @ViewBuilder private func rows(_ items: [ActivityItem]) -> some View {
        ForEach(items, id: \.key) { item in
            NavigationLink(value: item) { ActivityRow(item: item, status: live(item)) }
        }
    }
}

struct ActivityRow: View {
    @Environment(AppModel.self) private var model
    let item: ActivityItem
    let status: MediaStatus

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RemoteImage(url: item.posterURL, maxPixel: 200)
                .frame(width: 48, height: 72)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.title ?? "Untitled").font(.headline).lineLimit(1)
                    if let y = item.year { Text(String(y)).foregroundStyle(.secondary) }
                }
                StatusLine(status: status)
                if status.state == .failed {
                    if let m = status.message { Text(m).font(.caption).foregroundStyle(.red).lineLimit(3) }
                    Button("Retry", systemImage: "arrow.clockwise") {
                        Task { await model.retry(item.type, id: item.id) }
                    }
                    .buttonStyle(.bordered).tint(.red).controlSize(.small)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
