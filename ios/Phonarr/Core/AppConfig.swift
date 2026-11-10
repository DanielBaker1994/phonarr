import Foundation

/// Reusable config manager. Everything tunable and every non-UI string lives in bundled JSON
/// (Resources/Config/settings.json and strings.json), never in code:
///
///     AppConfig.int("logging.max_bytes")
///     AppConfig.text("log.paired", ["host": host])   // "{host}" placeholders
///
/// Mirrors backend/app/config.py (`cfg` / `msg`) and web/js/config.js. A missing key returns the key
/// itself (text) or the supplied default so a typo never crashes the app; the tests catch typos.
enum AppConfig {
    private static let settings = load("settings")
    private static let strings = load("strings")

    private static func load(_ name: String, bundle: Bundle = .main) -> [String: Any] {
        guard let url = bundle.url(forResource: name, withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    private static func dig(_ root: [String: Any], _ dotted: String) -> Any? {
        var node: Any? = root
        for part in dotted.split(separator: ".") { node = (node as? [String: Any])?[String(part)] }
        return node
    }

    static func string(_ key: String, default fallback: String = "") -> String { dig(settings, key) as? String ?? fallback }
    static func int(_ key: String, default fallback: Int = 0) -> Int { (dig(settings, key) as? NSNumber)?.intValue ?? fallback }
    static func dict(_ key: String) -> [String: String] { dig(settings, key) as? [String: String] ?? [:] }
    static func double(_ key: String, default fallback: Double = 0) -> Double { (dig(settings, key) as? NSNumber)?.doubleValue ?? fallback }
    static func ints(_ key: String, default fallback: [Int] = []) -> [Int] {
        (dig(settings, key) as? [NSNumber])?.map(\.intValue) ?? fallback
    }
    static func strings(_ key: String, default fallback: [String] = []) -> [String] { dig(settings, key) as? [String] ?? fallback }

    /// Text by dotted key from strings.json with `{name}` placeholders filled in.
    static func text(_ key: String, _ values: [String: Any] = [:]) -> String {
        var out = dig(strings, key) as? String ?? key
        for (k, v) in values { out = out.replacingOccurrences(of: "{\(k)}", with: "\(v)") }
        return out
    }

    static func hasText(_ key: String) -> Bool { dig(strings, key) is String }
}
