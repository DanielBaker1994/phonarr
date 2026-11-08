import Foundation
import MetricKit

/// Makes crashes visible in the debug report, with no third-party SDK.
///
/// - MetricKit (iOS 14+) hands the app a diagnostic payload for each crash, hang or excessive disk write,
///   usually on the next launch (sometimes up to a day later). Each payload is saved in the log folder as
///   `diagnostics-YYYYMMDD-HHmmss.log` (JSON inside), so Settings → Troubleshooting → Share debug report
///   includes it, and a summary line goes in app.log.
/// - Objective-C exceptions (rare in SwiftUI) are written to app.log synchronously as the app dies.
/// Swift traps (force unwraps, fatalError) can't be caught in-process; MetricKit is what reports those.
/// Xcode Organizer and TestFlight still show symbolicated crash logs as well.
final class CrashReporter: NSObject, MXMetricManagerSubscriber {
    static let shared = CrashReporter()

    func start() {
        MXMetricManager.shared.add(self)
        NSSetUncaughtExceptionHandler { exception in
            AppLog.writeNow("uncaught_exception", ["name": exception.name.rawValue, "reason": exception.reason ?? "-"])
        }
        // Payloads that arrived while the app wasn't subscribed yet.
        didReceive(MXMetricManager.shared.pastDiagnosticPayloads)
    }

    func didReceive(_ payloads: [MXDiagnosticPayload]) {
        for payload in payloads {
            let file = save(payload.jsonRepresentation())
            if let crashes = payload.crashDiagnostics, !crashes.isEmpty {
                let summary = crashes.map { c in
                    [c.exceptionType.map { "exception \($0)" }, c.signal.map { "signal \($0)" },
                     c.terminationReason].compactMap { $0 }.joined(separator: ", ")
                }.joined(separator: " | ")
                AppLog.error("crash_report", ["file": file, "summary": summary])
            } else {
                AppLog.warn("diagnostic_report", ["file": file])
            }
        }
    }

    private func save(_ json: Data) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd-HHmmss"
        let name = "diagnostics-\(f.string(from: Date()))-\(UUID().uuidString.prefix(4)).log"
        try? json.write(to: AppLog.directory.appendingPathComponent(name))
        return name
    }
}
