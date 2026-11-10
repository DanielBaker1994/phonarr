import Foundation
import OSLog
import UIKit

/// App logging: Xcode console / Console.app (via os.Logger) plus date-stamped files that the
/// debug report bundles up for sharing. Every line carries the calling function (and file):
///
///     AppLog.info("paired", ["host": host])      // key into strings.json "log.*"; falls back to the raw text
///     let done = AppLog.enter(); defer { done() }   // entry + single exit with duration
///
/// Files live in Caches/Logs: `app.log` is the main file to tail (every line); area files such as
/// `api.log` hold one area's lines only. At the first write after midnight the previous day's file is
/// renamed `app-YYYYMMDD.log` and files older than `logging.retention_days` are deleted.
/// The gateway token is never written: it is masked before anything hits disk.
enum AppLog {
    private static let logger = Logger(subsystem: AppConfig.string("logging.subsystem", default: "Phonarr"), category: "app")
    private static let queue = DispatchQueue(label: "Phonarr.log")
    private static let maxBytes = AppConfig.int("logging.max_bytes", default: 1_048_576)
    private static var secrets: [String] = []   // only touched on `queue`
    private static var lastRollCheck: [String: String] = [:]   // file name -> day string, only touched on `queue`

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = AppConfig.string("logging.date_format", default: "yyyyMMdd")
        return f
    }()

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HHmmss"
        return f
    }()

    /// Every line starts with UTC ISO 8601 with milliseconds and Z ("2026-11-10T08:00:00.123Z"), the same
    /// form as the gateway (KB-11) and the web app, so Settings → Logs and debug reports line up.
    private static let stampFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.timeZone = TimeZone(identifier: "UTC") ?? TimeZone(secondsFromGMT: 0) ?? .current
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let stampLock = NSLock()

    static func stamp(_ date: Date = Date()) -> String {
        stampLock.lock()
        defer { stampLock.unlock() }
        return stampFormatter.string(from: date)
    }

    static func parseStamp(_ text: String) -> Date? {
        stampLock.lock()
        defer { stampLock.unlock() }
        return stampFormatter.date(from: text)
    }

    /// Appends one line straight to app.log, bypassing the queue: for a dying process
    /// (uncaught exception), where an async write would never run.
    static func writeNow(_ key: String, _ values: [String: Any]) {
        let line = "\(stamp()) ERROR AppLog.writeNow: \(AppConfig.text("log.\(key)", values))\n"
        guard let data = line.data(using: .utf8) else { return }
        if let h = try? FileHandle(forWritingTo: current) {
            h.seekToEndOfFile()
            h.write(data)
            try? h.close()
        } else {
            try? data.write(to: current)
        }
    }

    static var directory: URL {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(AppConfig.string("logging.dir_name", default: "Logs"))
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static var current: URL { directory.appendingPathComponent(AppConfig.string("logging.main_file", default: "app.log")) }

    /// Every log file (rolled days first), for the debug report.
    static var allFiles: [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.pathExtension == "log" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static func registerSecret(_ s: String?) {
        guard let s, s.count >= AppConfig.int("logging.min_secret_length", default: 6) else { return }
        queue.async { if !secrets.contains(s) { secrets.append(s) } }
    }

    /// `key` is looked up in strings.json "log.<key>"; if absent it is logged as-is.
    static func info(_ key: String, _ values: [String: Any] = [:], area: String? = nil,
                     function: String = #function, file: String = #fileID) {
        write("INFO", key, values, area, function, file)
    }
    static func warn(_ key: String, _ values: [String: Any] = [:], area: String? = nil,
                     function: String = #function, file: String = #fileID) {
        write("WARN", key, values, area, function, file)
    }
    static func error(_ key: String, _ values: [String: Any] = [:], area: String? = nil,
                      function: String = #function, file: String = #fileID) {
        write("ERROR", key, values, area, function, file)
    }

    /// Logs entry now and returns a closure that logs the single exit with the duration:
    ///
    ///     let done = AppLog.enter(["key": key]); defer { done() }
    ///
    /// Use it in functions with many return paths instead of logging at each one.
    static func enter(_ args: [String: Any] = [:], area: String? = nil,
                      function: String = #function, file: String = #fileID) -> () -> Void {
        let started = Date()
        let rendered = args.isEmpty ? "()" : "(" + args.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: ", ") + ")"
        write("DEBUG", "trace_enter", ["function": function, "args": rendered], area, function, file)
        return {
            let ms = Int(Date().timeIntervalSince(started) * 1000)
            write("DEBUG", "trace_exit", ["function": function, "ms": ms], area, function, file)
        }
    }

    private static func write(_ level: String, _ key: String, _ values: [String: Any], _ area: String?,
                              _ function: String, _ file: String) {
        let message = AppConfig.hasText("log.\(key)") ? AppConfig.text("log.\(key)", values) : key
        let now = AppLog.stamp()
        let module = file.split(separator: "/").last.map { String($0).replacingOccurrences(of: ".swift", with: "") } ?? file
        queue.async {
            var line = "\(now) \(level) \(module).\(function): \(message)"
            for s in secrets { line = line.replacingOccurrences(of: s, with: "***") }
            switch level {
            case "ERROR": logger.error("\(line, privacy: .public)")
            case "WARN": logger.warning("\(line, privacy: .public)")
            case "DEBUG": logger.debug("\(line, privacy: .public)")
            default: logger.info("\(line, privacy: .public)")
            }
            append(line + "\n", to: AppConfig.string("logging.main_file", default: "app.log"))
            if let area, let name = AppConfig.dict("logging.area_files")[area] { append(line + "\n", to: name) }
        }
    }

    private static func append(_ text: String, to name: String) {
        let fm = FileManager.default
        let url = directory.appendingPathComponent(name)
        rollIfNewDay(url)
        if let size = (try? fm.attributesOfItem(atPath: url.path)[.size]) as? Int, size > maxBytes {
            // A very chatty day: move the full file aside (app-YYYYMMDD-HHmmss.log) and start a fresh one,
            // so the lines leading up to a problem are kept (and still go in the debug report).
            let stem = url.deletingPathExtension().lastPathComponent
            let stamp = dayFormatter.string(from: Date()) + "-" + timeFormatter.string(from: Date())
            let aside = url.deletingLastPathComponent().appendingPathComponent("\(stem)-\(stamp).\(url.pathExtension)")
            try? fm.moveItem(at: url, to: aside)
            let note = "\(AppLog.stamp()) INFO AppLog.append: " +
                AppConfig.text("log.log_rolled", ["bytes": maxBytes, "file": aside.lastPathComponent]) + "\n"
            try? note.data(using: .utf8)?.write(to: url)
        }
        guard let data = text.data(using: .utf8) else { return }
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile()
            h.write(data)
            try? h.close()
        } else {
            try? data.write(to: url)
        }
    }

    /// First write of a new day: app.log -> app-YYYYMMDD.log (dated by its last write), then prune old files.
    private static func rollIfNewDay(_ url: URL) {
        let today = dayFormatter.string(from: Date())
        guard lastRollCheck[url.lastPathComponent] != today else { return }
        lastRollCheck[url.lastPathComponent] = today
        let fm = FileManager.default
        if let modified = (try? fm.attributesOfItem(atPath: url.path)[.modificationDate]) as? Date {
            let day = dayFormatter.string(from: modified)
            if day != today {
                let stem = url.deletingPathExtension().lastPathComponent
                let rolled = url.deletingLastPathComponent().appendingPathComponent("\(stem)-\(day).\(url.pathExtension)")
                try? fm.removeItem(at: rolled)
                try? fm.moveItem(at: url, to: rolled)
            }
        }
        let cutoff = Date().addingTimeInterval(-Double(AppConfig.int("logging.retention_days", default: 14)) * 86400)
        for old in allFiles where old != url {
            if let m = (try? fm.attributesOfItem(atPath: old.path)[.modificationDate]) as? Date, m < cutoff {
                try? fm.removeItem(at: old)
            }
        }
    }

    /// Waits for pending writes, so a report includes the latest lines.
    static func flush() async {
        await withCheckedContinuation { cont in queue.async { cont.resume() } }
    }

    /// The last `count` lines of this app's own log, parsed into the shape GET /v1/debug/logs returns
    /// (Settings → Logs), oldest first. Reads app.log and, if that is short, the newest rolled app-*.log.
    static func entries(_ count: Int) async -> [LogEntry] {
        await flush()
        let main = current
        let stem = main.deletingPathExtension().lastPathComponent
        let rolled = allFiles.filter { $0.lastPathComponent.hasPrefix(stem + "-") }
        var text = (try? String(contentsOf: main, encoding: .utf8)) ?? ""
        var parsed = parse(text)
        if parsed.count < count, let previous = rolled.last {
            text = ((try? String(contentsOf: previous, encoding: .utf8)) ?? "") + text
            parsed = parse(text)
        }
        return Array(parsed.suffix(count))
    }

    /// "2026-11-10T08:00:00.123Z WARN APIClient.send(_:): GET /v1/status -> 503 … [req ios-1a2b3c4d]".
    /// A line that doesn't start with a time belongs to the entry above it.
    static func parse(_ text: String, source: String = "ios") -> [LogEntry] {
        var out: [LogEntry] = []
        let masks: [String] = queue.sync { secrets }
        for raw in text.components(separatedBy: "\n") {
            // Lines are masked when written; masking again covers lines from before a secret was registered.
            var line = raw
            for s in masks { line = line.replacingOccurrences(of: s, with: "***") }
            if let entry = parseLine(line, source: source) {
                out.append(entry)
            } else if !line.isEmpty {
                if out.isEmpty {
                    out.append(LogEntry(ts: nil, level: "", rid: "-", source: source, location: "", message: line))
                } else {
                    out[out.count - 1].message += "\n" + line
                }
            }
        }
        return out
    }

    private static func parseLine(_ line: String, source: String) -> LogEntry? {
        let parts = line.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0].hasSuffix("Z"), parseStamp(String(parts[0])) != nil,
              !parts[1].isEmpty, parts[1].allSatisfy({ $0.isUppercase }),
              let colon = parts[2].range(of: ": ") else { return nil }
        let rest = parts[2]
        let level = parts[1] == "WARN" ? "WARNING" : String(parts[1])
        let message = String(rest[colon.upperBound...])
        return LogEntry(ts: String(parts[0]), level: level, rid: requestId(in: message) ?? "-", source: source,
                        location: String(rest[..<colon.lowerBound]), message: message)
    }

    /// The id in "[req ios-1a2b3c4d]", which the gateway logs in its own lines as "[ios-1a2b3c4d]".
    static func requestId(in message: String) -> String? {
        guard let open = message.range(of: "[req ") else { return nil }
        let after = message[open.upperBound...]
        guard let close = after.firstIndex(of: "]") else { return nil }
        let rid = String(after[..<close])
        return rid.isEmpty ? nil : rid
    }
}

/// Builds a single zip with the app log, device/app info and the gateway's own
/// debug bundle, ready for the share sheet (AirDrop, Mail, Messages, Files…).
enum DebugReport {
    @MainActor
    static func build(model: AppModel) async throws -> URL {
        await AppLog.flush()
        let fm = FileManager.default
        let stamp = Date().formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false))
            .replacingOccurrences(of: ":", with: "")
        let root = fm.temporaryDirectory.appendingPathComponent("Phonarr-debug-\(stamp)")
        try? fm.removeItem(at: root)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)

        for url in AppLog.allFiles {
            try fm.copyItem(at: url, to: root.appendingPathComponent(url.lastPathComponent))
        }

        let info: [String: Any] = [
            "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?",
            "git_commit": Bundle.main.object(forInfoDictionaryKey: "AYRGitCommit") as? String ?? "?",
            "ios": UIDevice.current.systemVersion,
            "device": deviceModel(),
            "gateway_host": URL(string: model.gatewayURL)?.host ?? "not set",
            "paired": model.isPaired,
            "gateway_reachable": model.gatewayReachable,
            "radarr_configured": model.config?.radarr.configured ?? false,
            "sonarr_configured": model.config?.sonarr.configured ?? false,
            "services": model.services.mapValues { h -> [String: Any] in
                ["reachable": h.reachable.map { $0 as Any } ?? NSNull(), "error": (h.error?.message).map { $0 as Any } ?? NSNull()]
            },
            "tracked_titles": model.trackedCount,
            "generated_at": Date().formatted(.iso8601),
        ]
        let infoData = try JSONSerialization.data(withJSONObject: info, options: [.prettyPrinted, .sortedKeys])
        try infoData.write(to: root.appendingPathComponent("device.json"))

        do {
            let bundle = try await model.api.debugBundle()
            try bundle.write(to: root.appendingPathComponent("gateway-debug.zip"))
        } catch {
            try "Could not fetch gateway logs: \(error.localizedDescription)\n"
                .write(to: root.appendingPathComponent("gateway-unavailable.txt"), atomically: true, encoding: .utf8)
        }
        return try zip(root)
    }

    /// Zips a folder with the system's built-in archiver (no dependencies).
    private static func zip(_ folder: URL) throws -> URL {
        var result: Result<URL, Error> = .failure(CocoaError(.fileWriteUnknown))
        var coordError: NSError?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: [.forUploading], error: &coordError) { zipped in
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent(folder.lastPathComponent + ".zip")
            try? FileManager.default.removeItem(at: dest)
            result = Result { try FileManager.default.copyItem(at: zipped, to: dest); return dest }
        }
        if let coordError { throw coordError }
        return try result.get()
    }

    private static func deviceModel() -> String {
        var sys = utsname()
        uname(&sys)
        return withUnsafeBytes(of: &sys.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }
}
