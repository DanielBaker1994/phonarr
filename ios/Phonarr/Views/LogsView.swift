import SwiftUI
import UIKit

/// Settings → Logs. Mirrors the web app's Settings → Logs (web/js/views/logs.js, web/js/logview.js):
/// the gateway's last log lines (GET /v1/debug/logs) and this app's own log, merged by UTC time with the
/// newest at the bottom. Filters by source, level, text and request id; Copy and Share.
struct LogsView: View {
    @Environment(AppModel.self) private var model
    @State private var source = "both"
    @State private var level = ""
    @State private var query = ""
    @State private var rid = ""
    @State private var fetched: DebugLogsFetch?
    @State private var device: [LogEntry] = []
    @State private var skew: Int?
    @State private var error: String?
    @State private var loading = false
    @State private var loads = 0
    @State private var copied = false

    private var lineCount: Int { AppConfig.int("logs.lines", default: 1000) }
    private var warnSeconds: Int { AppConfig.int("logs.clock_warn_seconds", default: 120) }

    var body: some View {
        let all = LogMerge.merge(gateway: fetched?.response.lines ?? [], device: device, source: source)
        let shown = LogMerge.filter(all, level: level, query: query, rid: rid)
        let clockOff = (skew.map { abs($0) } ?? 0) > warnSeconds
        ScrollViewReader { proxy in
            List {
                Section {
                    LabeledContent(AppConfig.text("logs.gateway_label"),
                                   value: "\(fetched?.response.gateway?.version ?? "…") · \(fetched?.response.gateway?.timezone ?? "…")")
                    LabeledContent(AppConfig.text("logs.device_label"), value: TimeZone.current.identifier)
                    LabeledContent(AppConfig.text("logs.clock")) {
                        Text(clockText).foregroundStyle(clockOff ? Color.red : Color.secondary)
                    }
                    if clockOff {
                        Label(AppConfig.text("logs.clock_warn", ["seconds": abs(skew ?? 0)]), systemImage: "clock.badge.exclamationmark")
                            .font(.footnote)
                            .foregroundStyle(Color.red)
                    }
                    if let error {
                        Text(AppConfig.text("logs.gateway_failed", ["error": error]))
                            .font(.footnote)
                            .foregroundStyle(Color.red)
                    }
                } footer: {
                    Text(AppConfig.text("logs.note"))
                }
                Section {
                    Picker(AppConfig.text("logs.source_label"), selection: $source) {
                        Text(AppConfig.text("logs.source_both")).tag("both")
                        Text(AppConfig.text("logs.source_gateway")).tag("gateway")
                        Text(AppConfig.text("logs.source_device")).tag("ios")
                    }
                    .pickerStyle(.segmented)
                    Picker(AppConfig.text("logs.level_label"), selection: $level) {
                        Text(AppConfig.text("logs.level_all")).tag("")
                        Text(AppConfig.text("logs.level_warn")).tag("WARNING")
                    }
                    .pickerStyle(.segmented)
                    if !rid.isEmpty {
                        Button {
                            rid = ""
                        } label: {
                            Label(AppConfig.text("logs.rid_filter", ["rid": rid]), systemImage: "xmark.circle.fill")
                        }
                    }
                } footer: {
                    if fetched?.response.truncated == true && source != "ios" {
                        Text(AppConfig.text("logs.count", ["shown": shown.count, "total": all.count]) + ". " + AppConfig.text("logs.truncated"))
                    } else {
                        Text(AppConfig.text("logs.count", ["shown": shown.count, "total": all.count]))
                    }
                }
                Section {
                    if shown.isEmpty {
                        if loading {
                            ProgressView()
                        } else {
                            Text(AppConfig.text("logs.empty")).foregroundStyle(.secondary)
                        }
                    }
                    ForEach(shown) { entry in
                        LogRow(entry: entry) { tapped in rid = tapped }
                    }
                    Color.clear.frame(height: 1).id(LogMerge.endMarker)
                }
            }
            .searchable(text: $query, prompt: AppConfig.text("logs.search"))
            .onChange(of: shown.count) { proxy.scrollTo(LogMerge.endMarker, anchor: .bottom) }
            .onChange(of: loads) { proxy.scrollTo(LogMerge.endMarker, anchor: .bottom) }
        }
        .navigationTitle(AppConfig.text("logs.title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    UIPasteboard.general.string = LogMerge.format(shown)
                    copied = true
                } label: {
                    Image(systemName: copied ? "checkmark" : "doc.on.doc")
                }
                .accessibilityLabel(AppConfig.text(copied ? "logs.copied" : "logs.copy"))
                .disabled(shown.isEmpty)
                ShareLink(item: LogMerge.format(shown)) {
                    Image(systemName: "square.and.arrow.up")
                }
                .accessibilityLabel(AppConfig.text("logs.share"))
                .disabled(shown.isEmpty)
                Button {
                    Task { await load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel(AppConfig.text("logs.refresh"))
                .disabled(loading)
            }
        }
        .refreshable { await load() }
        .task(id: level + "|" + rid) { await load() }
    }

    private var clockText: String {
        guard let skew else { return AppConfig.text(fetched == nil ? "…" : "logs.clock_unknown") }
        if skew == 0 { return AppConfig.text("logs.clock_same") }
        return AppConfig.text(skew > 0 ? "logs.clock_ahead" : "logs.clock_behind", ["seconds": abs(skew)])
    }

    private func load() async {
        loading = true
        defer {
            loading = false
            loads += 1
        }
        copied = false
        do {
            let result = try await model.api.debugLogs(lines: lineCount, level: level, rid: rid)
            let difference = LogMerge.clockSkew(sentAt: result.sentAt, receivedAt: result.receivedAt,
                                                gatewayNow: result.response.gateway?.now, dateHeader: result.dateHeader)
            fetched = result
            skew = difference
            error = nil
            AppLog.info("logs_loaded", ["gateway": result.response.lines.count, "device": device.count,
                                        "skew": difference.map { String($0) } ?? "?"])
            if let difference, abs(difference) > warnSeconds {
                AppLog.warn("logs_clock", ["skew": difference, "device_tz": TimeZone.current.identifier,
                                           "gateway_tz": result.response.gateway?.timezone ?? "?"])
            }
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
            AppLog.warn("logs_failed", ["error": error.localizedDescription])
        }
        device = await AppLog.entries(lineCount)
    }
}

/// One line: time, source, level, tappable request id, where, then the message (wraps, selectable).
private struct LogRow: View {
    let entry: LogEntry
    let onRid: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(LogMerge.shortTime(entry.ts)).foregroundStyle(.secondary)
                Text(AppConfig.text("logs.tag_\(entry.source)"))
                    .bold()
                    .padding(.horizontal, 3)
                    .background(tagColor.opacity(0.18), in: RoundedRectangle(cornerRadius: 3))
                Text(entry.level).bold().foregroundStyle(levelColor)
                if entry.rid != "-" {
                    Button(entry.rid) { onRid(entry.rid) }
                        .buttonStyle(.borderless)
                        .lineLimit(1)
                }
            }
            if !entry.location.isEmpty {
                Text(entry.location).foregroundStyle(.secondary)
            }
            Text(entry.message)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 11, design: .monospaced))
        .listRowBackground(rowBackground)
    }

    private var levelColor: Color {
        switch entry.level {
        case "ERROR", "CRITICAL": return Color.red
        case "WARNING": return Color.orange
        default: return Color.primary
        }
    }

    private var tagColor: Color { entry.source == "gateway" ? Color.gray : Color.accentColor }

    private var rowBackground: Color? {
        switch entry.level {
        case "ERROR", "CRITICAL": return Color.red.opacity(0.08)
        default: return nil
        }
    }
}

/// The pure parts (the web app's logview.js does the same): merge, filter, format, clock difference.
enum LogMerge {
    static let endMarker = "logs-end"

    private struct Keyed {
        let key: String
        let index: Int
        let entry: LogEntry
    }

    /// One list, oldest first. Both sides write UTC ISO with ms and Z, so the strings sort. A line without a
    /// time keeps its place after the one before it. `source` is "both", "gateway" or "ios".
    static func merge(gateway: [LogEntry], device: [LogEntry], source: String) -> [LogEntry] {
        var picked: [LogEntry] = []
        if source != "ios" { picked += gateway }
        if source != "gateway" { picked += device }
        var last = ""
        var keyed: [Keyed] = []
        for (index, entry) in picked.enumerated() {
            if let ts = entry.ts { last = ts }
            keyed.append(Keyed(key: entry.ts ?? last, index: index, entry: entry))
        }
        keyed.sort { a, b in a.key == b.key ? a.index < b.index : a.key < b.key }
        var out: [LogEntry] = []
        for (position, item) in keyed.enumerated() {
            var entry = item.entry
            entry.seq = position
            out.append(entry)
        }
        return out
    }

    private static let rank: [String: Int] = ["DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50]

    /// level "WARNING" keeps warnings and worse; query is a case-insensitive substring; rid one request id.
    static func filter(_ entries: [LogEntry], level: String, query: String, rid: String) -> [LogEntry] {
        let minimum = rank[level] ?? 0
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        return entries.filter { e in
            if minimum > 0 && (rank[e.level] ?? 0) < minimum { return false }
            if !rid.isEmpty && e.rid != rid && !e.message.contains(rid) { return false }
            if needle.isEmpty { return true }
            return "\(e.location) \(e.rid) \(e.message)".lowercased().contains(needle)
        }
    }

    /// Plain text for Copy and Share: one line per entry, the full UTC time first.
    static func format(_ entries: [LogEntry]) -> String {
        entries.map { e in
            "\(e.ts ?? "-") \(e.level.isEmpty ? "-" : e.level) \(e.source) [\(e.rid)] \(e.location): \(e.message)"
        }.joined(separator: "\n")
    }

    /// "11-10 08:00:01.123" (UTC) from "2026-11-10T08:00:01.123Z".
    static func shortTime(_ ts: String?) -> String {
        guard let ts, ts.count >= 23 else { return ts ?? "" }
        let chars = Array(ts)
        return String(chars[5..<10]) + " " + String(chars[11..<23])
    }

    private static let httpDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "GMT")
        f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return f
    }()

    /// Seconds this phone's clock is ahead of the gateway's (negative: behind), against the middle of the
    /// request. Uses the gateway's own time (ms precision) or else the HTTP Date header.
    static func clockSkew(sentAt: Date, receivedAt: Date, gatewayNow: String?, dateHeader: String?) -> Int? {
        var server: Date?
        if let gatewayNow { server = AppLog.parseStamp(gatewayNow) }
        if server == nil, let dateHeader { server = httpDate.date(from: dateHeader) }
        guard let server else { return nil }
        let middle = sentAt.addingTimeInterval(receivedAt.timeIntervalSince(sentAt) / 2)
        return Int(middle.timeIntervalSince(server).rounded())
    }
}
