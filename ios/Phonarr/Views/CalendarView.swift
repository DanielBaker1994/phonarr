import SwiftUI

/// Calendar: upcoming and recent releases from Radarr and Sonarr, grouped by local day (GET /v1/calendar).
/// Mirrors web/js/views/calendar.js. Self-contained (own state, loading and toolbar), shown under
/// Activity → Calendar, so it can become a tab of its own later. Requirements: docs/KNOWN_BUGS_PRD.md KB-12..16, KB-30..36.
@MainActor
struct CalendarView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("calendarMedia") private var media = "all"
    @AppStorage("calendarHideSpecials") private var hideSpecials = false
    @AppStorage("calendarMonitoredOnly") private var monitoredOnly = false

    @State private var data: CalendarResponse?
    @State private var errorText: String?
    @State private var offline = false
    @State private var loadingMore: String?
    @State private var today: String = CalendarDates.todayKey(tz: TimeZone.current)
    @State private var placed = false        // scrolled to Today for this load
    @State private var revealed = false      // the list stays invisible until it is on Today (KB-35)

    private var filters: CalendarFilters {
        CalendarFilters(media: media, hideSpecials: hideSpecials, monitoredOnly: monitoredOnly)
    }

    var body: some View {
        ScrollViewReader { proxy in
            List {
                notices
                if let data {
                    content(data)
                }
            }
            .listStyle(.plain)
            .opacity(data == nil || revealed ? 1 : 0)
            .overlay { overlay }
            .refreshable { await load() }
            .navigationDestination(for: CalendarEntry.self) { TitleDetailView(type: $0.type, id: $0.id) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(AppConfig.text("calendar.jump_today")) {
                        withAnimation { proxy.scrollTo(dayID(today), anchor: .top) }
                    }
                    .disabled(data == nil)
                }
                ToolbarItem(placement: .topBarTrailing) { filterMenu }
            }
            .task {
                if data == nil { await load() }
            }
            .onChange(of: data?.start) { _, _ in place(proxy) }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { Task { await checkDay() } }
            }
            // KB-15: a timer at the next local midnight; restarts whenever today changes.
            .task(id: today) {
                while !Task.isCancelled {
                    let wait = CalendarDates.secondsUntilNextMidnight(tz: TimeZone.current)
                    try? await Task.sleep(for: .seconds(wait))
                    if Task.isCancelled { break }
                    await checkDay()
                }
            }
        }
    }

    // MARK: Pieces

    @ViewBuilder private var notices: some View {
        if data != nil, let errorText {
            Label(offline ? AppConfig.text("calendar.offline_cached") : errorText, systemImage: "wifi.exclamationmark")
                .font(.footnote).foregroundStyle(.orange)
        }
        if let services = data?.services {
            ForEach(["radarr", "sonarr"], id: \.self) { svc in
                if let s = services[svc], s.ok == false {
                    Label(AppConfig.text("calendar.service_failed",
                                         ["service": svc.capitalized, "message": s.error?.message ?? ""]),
                          systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(.orange)
                }
            }
        }
        if let data, data.tzFallback {
            Label(AppConfig.text("calendar.tz_fallback", ["tz": TimeZone.current.identifier]), systemImage: "globe")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func content(_ data: CalendarResponse) -> some View {
        let days = filters.apply(data.days).filter { !$0.items.isEmpty || $0.date == today }
        moreButton(past: true)
        ForEach(days, id: \.date) { day in
            Section {
                if day.items.isEmpty {
                    Text(AppConfig.text("calendar.nothing_today")).font(.subheadline).foregroundStyle(.secondary)
                } else {
                    ForEach(day.items, id: \.rowID) { entry in
                        NavigationLink(value: entry) {
                            CalendarRow(entry: entry, status: model.status(entry.type, entry.id, fallback: entry.status))
                        }
                    }
                }
            } header: {
                Text(CalendarDates.heading(day.date, today: today))
                    .font(.subheadline.weight(day.date == today ? .bold : .regular))
                    .foregroundStyle(day.date == today ? Color.accentColor : Color.secondary)
            }
            .id(dayID(day.date))
        }
        moreButton(past: false)
    }

    private func moreButton(past: Bool) -> some View {
        Button {
            Task { await loadMore(past: past) }
        } label: {
            HStack {
                Spacer()
                if loadingMore == (past ? "past" : "future") {
                    ProgressView()
                } else {
                    Text(AppConfig.text(past ? "calendar.earlier" : "calendar.later"))
                }
                Spacer()
            }
        }
        .disabled(loadingMore != nil)
    }

    @ViewBuilder private var overlay: some View {
        if data == nil && errorText == nil {
            ProgressView()        // a slow first load is a placeholder, never an error (KB-35)
        } else if data == nil, let errorText {
            ContentUnavailableView {
                Label(AppConfig.text("calendar.load_failed"), systemImage: "calendar.badge.exclamationmark")
            } description: {
                Text(errorText)
            } actions: {
                Button(AppConfig.text("calendar.retry")) { Task { await load() } }
            }
        } else if let data, isEmpty(data) {
            ContentUnavailableView(AppConfig.text("calendar.empty_title"), systemImage: "calendar",
                                   description: Text(emptyText(data)))
        }
    }

    private var filterMenu: some View {
        Menu {
            Picker(AppConfig.text("calendar.title"), selection: $media) {
                Text(AppConfig.text("calendar.media_all")).tag("all")
                Text(AppConfig.text("calendar.media_movie")).tag("movie")
                Text(AppConfig.text("calendar.media_series")).tag("series")
            }
            Toggle(AppConfig.text("calendar.hide_specials"), isOn: $hideSpecials)
            Toggle(AppConfig.text("calendar.monitored_only"), isOn: $monitoredOnly)
        } label: {
            Image(systemName: filters.activeCount > 0
                  ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
        }
    }

    // MARK: Logic

    private func dayID(_ date: String) -> String { "day-" + date }

    private func isEmpty(_ data: CalendarResponse) -> Bool {
        !filters.apply(data.days).contains { !$0.items.isEmpty }
    }

    private func emptyText(_ data: CalendarResponse) -> String {
        let connected = data.services.values.contains { $0.configured }
        if !connected { return AppConfig.text("calendar.not_connected") }
        return AppConfig.text(filters.activeCount > 0 ? "calendar.empty_filtered" : "calendar.empty_body")
    }

    /// KB-35: jump to Today before the list is shown, so there is no visible scroll.
    private func place(_ proxy: ScrollViewProxy) {
        guard data != nil, !placed else { return }
        placed = true
        DispatchQueue.main.async {
            proxy.scrollTo(dayID(today), anchor: .top)
            DispatchQueue.main.async { revealed = true }
        }
    }

    private func checkDay() async {
        let now = CalendarDates.todayKey(tz: TimeZone.current)
        guard now != today else { return }
        AppLog.info("calendar_new_day", ["day": now])
        today = now
        placed = false
        revealed = false
        data = nil
        await load()
    }

    private func load() async {
        let tz = TimeZone.current
        today = CalendarDates.todayKey(tz: tz)
        let start = CalendarDates.addDays(today, -AppConfig.int("calendar.past_days", default: 7))
        let end = CalendarDates.addDays(today, AppConfig.int("calendar.future_days", default: 30))
        AppLog.info("calendar_load", ["start": start, "end": end, "tz": tz.identifier])
        do {
            let r = try await model.api.calendar(start: start, end: end, tz: tz.identifier)
            data = r
            errorText = nil
            offline = false
        } catch is CancellationError {
            return
        } catch {
            fail(error, start: start, end: end)
        }
    }

    private func loadMore(past: Bool) async {
        guard let current = data, loadingMore == nil else { return }
        let n = AppConfig.int("calendar.more_days", default: 14)
        let start = past ? CalendarDates.addDays(current.start, -n) : CalendarDates.addDays(current.end, 1)
        let end = past ? CalendarDates.addDays(current.start, -1) : CalendarDates.addDays(current.end, n)
        loadingMore = past ? "past" : "future"
        defer { loadingMore = nil }
        AppLog.info("calendar_load", ["start": start, "end": end, "tz": TimeZone.current.identifier])
        do {
            let r = try await model.api.calendar(start: start, end: end, tz: TimeZone.current.identifier)
            var merged = current
            merged.days = CalendarDates.merge(current.days, r.days)
            merged.services = r.services
            if past { merged.start = r.start } else { merged.end = r.end }
            data = merged
            errorText = nil
            offline = false
        } catch is CancellationError {
            return
        } catch {
            fail(error, start: start, end: end)
        }
    }

    private func fail(_ error: Error, start: String, end: String) {
        AppLog.warn("calendar_failed", ["start": start, "end": end, "error": error.localizedDescription])
        if let e = error as? APIError {
            if case .unauthorized = e { model.handleAuthFailure() }
            if case .transport(_) = e { offline = true }
        }
        errorText = error.localizedDescription
    }
}

struct CalendarRow: View {
    let entry: CalendarEntry
    let status: MediaStatus

    var body: some View {
        HStack(spacing: 12) {
            RemoteImage(url: entry.posterURL, maxPixel: 160)
                .frame(width: 40, height: 60)
                .clipShape(RoundedRectangle(cornerRadius: 5))
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.title ?? "Untitled").font(.headline).lineLimit(1)
                Text(CalendarDates.line(entry)).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                if CalendarDates.milestone(entry) != nil || chipText != nil {
                    HStack(spacing: 6) {
                        if let m = CalendarDates.milestone(entry) { tag(m, color: Color.orange) }
                        if let c = chipText { tag(c, color: chipColor) }
                    }
                }
            }
            Spacer(minLength: 0)
            Image(systemName: entry.type == .movie ? "film" : "tv")
                .font(.caption)
                .foregroundStyle(Color.secondary)
        }
        .opacity(entry.monitored ? 1 : 0.5)      // KB-34: unmonitored entries are faded
    }

    /// Downloaded (this release has its file), otherwise the live status when it says something useful.
    private var chipText: String? {
        if entry.hasFile { return AppConfig.text("calendar.downloaded") }
        switch status.state {
        case .submitting, .queued, .importing, .stalled, .failed, .complete:
            return status.state.label
        case .downloading:
            if let p = status.progress { return status.state.label + " " + String(Int((p * 100).rounded())) + "%" }
            return status.state.label
        default:
            return nil
        }
    }

    private var chipColor: Color {
        if entry.hasFile || status.state == .complete { return Color.green }
        if status.state == .failed { return Color.red }
        return Color.secondary
    }

    private func tag(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }
}
